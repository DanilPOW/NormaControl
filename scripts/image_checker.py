# scripts/image_checker.py
# Проверка рисунков: поля, центрирование, пустая строка сверху, сводки и вызов проверки подписей
import fitz
import math
import re
from scripts.figure_caption_checker import check_figure_captions
from const import *

def mm_to_pt(mm):  
    return mm * 2.8346456693

def pt_to_mm(pt):  
    return pt / 2.8346456693


# Примитивы над bbox
def bbox_union(b1, b2):  # объединение двух bbox-ов
    return (min(b1[0], b2[0]), min(b1[1], b2[1]),
            max(b1[2], b2[2]), max(b1[3], b2[3]))

def bboxes_intersect(b1, b2, tol=2.0):  # <зачем нужен> пересечение прямоугольников с допуском
    return not (b1[2] < b2[0] - tol or b1[0] > b2[2] + tol or
                b1[3] < b2[1] - tol or b1[1] > b2[3] + tol)

def bbox_area(b):  # площадь bbox
    return max(0.0, b[2]-b[0]) * max(0.0, b[3]-b[1])

def bbox_intersection(b1, b2):  # пересечение двух bbox
    x0 = max(b1[0], b2[0]); y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2]); y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0, 0)
    return (x0, y0, x1, y1)

def bbox_iou(b1, b2):  
    inter = bbox_intersection(b1, b2)
    ai = bbox_area(inter)
    if ai == 0:
        return 0.0
    a1 = bbox_area(b1); a2 = bbox_area(b2)
    return ai / (a1 + a2 - ai + 1e-9)

def bbox_center(b):  
    return ((b[0]+b[2])/2.0, (b[1]+b[3])/2.0)

def center_distance(b1, b2):  
    c1 = bbox_center(b1); c2 = bbox_center(b2)
    return math.hypot(c1[0]-c2[0], c1[1]-c2[1])


# Стили, видимость и сравнение графики
def color_distance(c1, c2):  #евклидова метрика между цветами для группировки путей
    if not c1 or not c2:
        return 1e9
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

def same_style(a, b, color_tol=0.08, lw_rel_tol=0.35):
    def repr_color(d): return d.get("stroke") or d.get("fill")
    ca = repr_color(a); cb = repr_color(b)
    cdist = color_distance(ca, cb)

    lwa = float(a.get("width") or a.get("linewidth") or 0)
    lwb = float(b.get("width") or b.get("linewidth") or 0)
    lw_ratio = 0.0 if max(lwa, lwb) == 0 else abs(lwa - lwb) / max(lwa, lwb)

    return (cdist <= color_tol) and (lw_ratio <= lw_rel_tol)

def is_visible_path(d):
    stroke = d.get("stroke")
    fill = d.get("fill")
    lw = d.get("width") or d.get("linewidth") or 0
    so = d.get("stroke_opacity", 1.0)
    fo = d.get("fill_opacity", 1.0)
    stroked = bool(stroke) and lw > 0 and (so is None or so > 0)
    filled  = bool(fill) and (fo is None or fo > 0)
    return stroked or filled


# Сбор текстовых строк
_PUNCT_ONLY_RE = re.compile(r"^[\s\.\,\-\–\—\·•:;…]+$")

def _is_noise_line(line):  # отсев «мусорных» строк
    fs = float(line.get("fontsize") or 0)
    x0,y0,x1,y1 = line["bbox"]
    h = max(0.0, y1 - y0)
    txt = (line.get("text") or "").strip()
    if fs < 6.0:      
        return True
    if h < mm_to_pt(2.0):     
        return True
    if len(txt) == 0:                
        return True
    if _PUNCT_ONLY_RE.match(txt):      # только пунктуаци
        return True
    return False

def collect_text_lines(page):
    lines = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            if not spans:
                continue
            xs0 = [s["bbox"][0] for s in spans]
            ys0 = [s["bbox"][1] for s in spans]
            xs1 = [s["bbox"][2] for s in spans]
            ys1 = [s["bbox"][3] for s in spans]
            bx = (min(xs0), min(ys0), max(xs1), max(ys1))
            fs = sum(float(s.get("size", 0)) for s in spans) / max(1, len(spans))  # средний кегль
            txt = "".join(s.get("text", "") for s in spans)  # склейка спанов
            line = {"bbox": bx, "fontsize": fs, "text": txt}
            if not _is_noise_line(line):
                lines.append(line)
    return lines


# Поиск ближайшей строки выше фигуры
def _nearest_valid_line_above(fig_bbox, lines, *, y_tol_pt=1.5):
    """Вернуть ближайшую сверху строку (учитывая вертикальный допуск)."""
    x0f, y0f, x1f, y1f = fig_bbox
    best, best_dy = None, None
    for ln in lines:
        x0, y0, x1, y1 = ln["bbox"]
        if y1 > (y0f + y_tol_pt):  # строка не выше фигуры
            continue
        dy = max(0.0, y0f - y1)
        if best_dy is None or dy < best_dy:
            best, best_dy = ln, dy
    return best


def check_empty_line_above(
    page,
    fig_bbox,
    *,
    work_top_pt,
    font_min_pt=12.0,
    font_max_pt=14.0,
    first_elem_top_thresh_mm=5.0,
    lines=None,
):
    """Проверка пустой строки перед фигурой (≈ 1.5 * fontsize)."""
    x0, y0, x1, y1 = fig_bbox
    if lines is None:
        lines = collect_text_lines(page)

    above = _nearest_valid_line_above(fig_bbox, lines, y_tol_pt=1.5)

    # Если фигура в самом верху рабочей области — не ругаемся
    if not above:
        if (y0 - work_top_pt) <= mm_to_pt(first_elem_top_thresh_mm):
            return None
        return None

    # Требуемый зазор = 1.5 * fontsize, с ограничением кегля в [12..14]
    fs = float(above["fontsize"]) if above["fontsize"] > 0 else font_min_pt
    fs = max(font_min_pt, min(font_max_pt, fs))
    required_gap_pt = 1.5 * fs

    actual_gap_pt = y0 - above["bbox"][3]
    if actual_gap_pt + 1e-6 < required_gap_pt:
        return (f"Нет пустой строки перед рисунком: расстояние "
                f"{pt_to_mm(actual_gap_pt):.1f} мм, требуется ≥ {pt_to_mm(required_gap_pt):.1f} мм (межстрочник 1.5)")
    return None


# --- Группировка путей (векторная графика) ---
def choose_repr(a, b):  # <зачем нужен> выбрать «представителя» группы по приоритету обводки/заливки
    def priority(d):
        stroke = bool(d.get("stroke")) and (d.get("width") or d.get("linewidth") or 0) > 0
        fill = bool(d.get("fill"))
        if stroke and fill: return 3
        if stroke: return 2
        if fill: return 1
        return 0
    return b if priority(b) > priority(a) else a

def group_paths(paths):  # <зачем нужен> склеить близкие по IoU/стилю пути в логические объекты
    groups = []
    iou_thr_high = 0.65
    iou_thr_lo   = 0.30
    dist_factor  = 0.6  # масштабир. порог по центрам

    for p in paths:
        pb = tuple(p["bbox"])
        placed = False

        diag_p = math.hypot(pb[2]-pb[0], pb[3]-pb[1])
        for g in groups:
            gb = g["bbox"]
            diag_g = math.hypot(gb[2]-gb[0], gb[3]-gb[1])
            iou = bbox_iou(pb, gb)

            # масштабируемый порог расстояния
            dist_thr = dist_factor * max(4.0, min(diag_p, diag_g))
            dist = center_distance(pb, gb)

            style_ok = same_style(p, g["repr"])

            if iou >= iou_thr_high and dist <= dist_thr * 0.75:
                g["paths"].append(p)
                g["bbox"] = bbox_union(g["bbox"], pb)
                g["repr"] = choose_repr(g["repr"], p)
                placed = True
                break

            if (iou >= iou_thr_lo or dist <= dist_thr) and style_ok:
                g["paths"].append(p)
                g["bbox"] = bbox_union(g["bbox"], pb)
                g["repr"] = choose_repr(g["repr"], p)
                placed = True
                break

        if not placed:
            groups.append({"paths": [p], "bbox": pb, "repr": p})

    return groups


# --- Группировка растров по строкам ---
def group_rasters_by_row(raster_blocks, y_tol_pt):  # <зачем нужен> склеить соседние по Y растры в «ряды»
    if not raster_blocks:
        return []

    raster_blocks_sorted = sorted(raster_blocks, key=lambda b: b["bbox"][1])

    groups = []
    cur_items = []
    cur_y_ref = None

    def flush_group():
        if not cur_items:
            return
        bx = cur_items[0]["bbox"]
        for it in cur_items[1:]:
            bx = bbox_union(bx, it["bbox"])
        groups.append({"bbox": bx, "items": cur_items.copy()})

    for blk in raster_blocks_sorted:
        y0 = blk["bbox"][1]
        if cur_y_ref is None:
            cur_items = [blk]
            cur_y_ref = y0
            continue

        if abs(y0 - cur_y_ref) <= y_tol_pt:
            cur_items.append(blk)
            cur_y_ref = sum(b["bbox"][1] for b in cur_items) / len(cur_items)
        else:
            flush_group()
            cur_items = [blk]
            cur_y_ref = y0

    flush_group()
    return groups


# --- Проверка центрирования ---
def centered_status(group_bbox, page_rect,
                    left_pt=LEFT_MARGIN_PT, right_pt=RIGHT_MARGIN_PT,
                    tol_cm=CENTER_TOL_CM):
    """Проверка центрирования относительно рабочей области страницы."""
    work_left  = page_rect.x0 + left_pt
    work_right = page_rect.x1 - right_pt
    work_center = (work_left + work_right) / 2.0

    gx, _ = bbox_center(group_bbox)
    dx_pt  = gx - work_center
    tol_pt = tol_cm * CM_TO_PT

    is_centered = abs(dx_pt) <= tol_pt
    dx_mm = pt_to_mm(dx_pt)
    return is_centered, dx_mm


# --- Выделение кандидатов на «рисунок» и нумерация ---
def _reading_key_single_column(bbox, y_snap_mm=0.3):  # <зачем нужен> ключ сортировки: сверху-вниз, слева-направо
    x0,y0,x1,y1 = bbox
    y_snap_pt = mm_to_pt(y_snap_mm)
    y0s = round(y0 / y_snap_pt) * y_snap_pt
    return (y0s, x0)

def _collect_page_fig_candidates_single(page_rect, raster_rows, vector_groups):  # <зачем нужен> набор крупных объектов
    def _is_figure(bx):
        x0,y0,x1,y1 = bx
        w = x1 - x0; h = y1 - y0
        area_pct = (w*h) / (page_rect.width * page_rect.height + 1e-9) * 100.0
        return (
            w >= mm_to_pt(MIN_W_MM) and
            h >= mm_to_pt(MIN_H_MM) and
            area_pct >= MIN_AREA_PCT and
            h >= mm_to_pt(THIN_LINE_MM) and
            w >= mm_to_pt(THIN_LINE_MM)
        )

    items = []
    for row in raster_rows:
        if _is_figure(row["bbox"]):
            items.append({"bbox": row["bbox"], "kind": "raster", "src": row})
    for g in vector_groups:
        if _is_figure(g["bbox"]):
            items.append({"bbox": g["bbox"], "kind": "vector", "src": g})
    return items

def _enumerate_figures_single_column(pdf_document, raster_rows_by_page, vector_groups_by_page):
    """Плоский список всех фигур (растров/векторов) с глобальной нумерацией."""
    all_items = []
    for i, page in enumerate(pdf_document):
        page_num = i + 1
        rect = page.rect
        rows = raster_rows_by_page.get(page_num, [])
        vgs  = vector_groups_by_page.get(page_num, [])

        items = _collect_page_fig_candidates_single(rect, rows, vgs)
        items.sort(key=lambda it: _reading_key_single_column(it["bbox"]))
        for it in items:
            it["page"] = page_num
            all_items.append(it)

    for idx, it in enumerate(all_items, start=1):
        it["fig_index"] = idx

    return all_items  # <упрощено> возвращаем только то, что реально используется


# --- Фильтры и сборка векторных групп на странице ---
def _vector_groups(page, table_bboxes, debug_draw=False, table_exclude_mode="intersect", iou_threshold=0.30):
    entries = []
    try:
        drawings = page.get_drawings(extended=True)
    except TypeError:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    for d in drawings:
        bbox = d.get("rect") or d.get("bbox")
        if not bbox:
            continue
        entry = {
            "bbox": (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            "stroke": d.get("stroke"),
            "fill": d.get("fill"),
            "width": d.get("width") or d.get("linewidth") or 0.0,
            "stroke_opacity": d.get("stroke_opacity", 1.0),
            "fill_opacity": d.get("fill_opacity", 1.0),
        }
        if is_visible_path(entry):
            entries.append(entry)

    # Исключаем объекты внутри таблиц
    filtered = []
    for e in entries:
        eb = e["bbox"]
        if not table_bboxes:
            filtered.append(e)
            continue
        if table_exclude_mode == "iou":
            max_iou = max((bbox_iou(eb, tb) for tb in table_bboxes), default=0.0)
            if max_iou > iou_threshold:
                continue
            filtered.append(e)
        else:
            if any(bboxes_intersect(eb, tb) for tb in table_bboxes):
                continue
            filtered.append(e)

    groups = group_paths(filtered)

    # Убираем слишком мелкие группы
    cleaned = []
    for g in groups:
        x0, y0, x1, y1 = g["bbox"]
        if (x1-x0) >= 8 or (y1-y0) >= 8:
            cleaned.append(g)

    if debug_draw:
        for g in cleaned:
            x0, y0, x1, y1 = g["bbox"]
            page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(1, 0, 0), width=1)

    return cleaned


def _row_in_tables(row_bbox, table_bboxes, *, mode="intersect", iou_threshold=0.30):  # <зачем нужен> проверка «ряд в таблице»
    if not table_bboxes:
        return False
    if mode == "iou":
        max_iou = max((bbox_iou(row_bbox, tb) for tb in table_bboxes), default=0.0)
        return max_iou > iou_threshold
    return any(bboxes_intersect(row_bbox, tb) for tb in table_bboxes)


# --- Главная функция проверки ---
def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None, debug_draw=False,
                 table_exclude_mode="intersect", iou_threshold=0.30, vector_annotate_center=True):
    """
    Основные проверки по графике:
      - выход за поля (рабочую область);
      - центрирование крупных объектов;
      - пустая строка перед рисунком (≈1.5*fontsize);
      - сводки по растровым/векторным объектам;
      - вызов проверки подрисуночных подписей.
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_raster_images = 0
    page_raster_counts = []
    vector_summary_lines = []
    raster_rows_by_page = {}
    vector_groups_by_page = {}

    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        rect = page.rect
        pw, ph = rect.width, rect.height
        has_error = False
        text_lines = collect_text_lines(page)

        # Рабочая область страницы с учётом полей
        work_left   = rect.x0 + LEFT_MARGIN_PT
        work_right  = rect.x1 - RIGHT_MARGIN_PT
        work_top    = rect.y0 + TOP_MARGIN_PT
        work_bottom = rect.y1 - BOTTOM_MARGIN_PT

        # --- Сбор растров ---
        raster_blocks = []
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1 and "bbox" in block:
                    x0, y0, x1, y1 = block["bbox"]
                    raster_blocks.append({"bbox": (x0, y0, x1, y1)})
        except Exception as e:
            admin_lines.append(f"[image_checker] raster collect error on page {page_num}: {e}")

        raster_count = len(raster_blocks)
        y_tol_pt = mm_to_pt(50.7)  # исходное допущение «рядовость» по Y
        raster_rows = group_rasters_by_row(raster_blocks, y_tol_pt=y_tol_pt)
        raster_rows_by_page[page_num] = raster_rows
        grouped_raster_count = len(raster_rows)

        # Сводка по растру
        vector_summary_lines.append(
            f"[Raster][Стр. {page_num}] найдено {raster_count} растровых объектов, "
            f"сгруппировано в {grouped_raster_count} ряд(ов)"
        )

        tbl_bboxes = table_bboxes_by_page.get(page_num, [])

        # --- Проверки по растровым рядам ---
        try:
            for ri, row in enumerate(raster_rows, 1):
                row_bbox = row["bbox"]

                # Пропускаем, если ряд внутри таблицы
                if _row_in_tables(row_bbox, tbl_bboxes, mode=table_exclude_mode, iou_threshold=iou_threshold):
                    admin_lines.append(f"[Стр. {page_num}] Растровый ряд #{ri}: пропущен (внутри таблицы)")
                    continue

                # Если в ряду >1 картинки — проверка «каждая в своей строке»
                items_sorted = sorted(row["items"], key=lambda it: it["bbox"][0])
                if len(items_sorted) >= 2:
                    y_misaligned_tol_pt = mm_to_pt(1.0)
                    base_y0 = items_sorted[0]["bbox"][1]
                    for idx_in_row, it in enumerate(items_sorted[1:], start=2):
                        x0i, y0i, x1i, y1i = it["bbox"]
                        if abs(y0i - base_y0) > y_misaligned_tol_pt:
                            has_error = True
                            warn = "Каждый рисунок должен находиться в отдельной строке и иметь свою подрисуночную подпись."
                            admin_lines.append(
                                f"[Стр. {page_num}] Растровый ряд #{ri}: элемент #{idx_in_row} смещён по вертикали "
                                f"(Δy={pt_to_mm(abs(y0i - base_y0)):.1f} мм). {warn}"
                            )
                            ann = page.add_text_annot(fitz.Point(x0i, y0i), warn)
                            ann.set_info(title="Сервис нормоконтроля", content=warn)
                            ann.update()

                x0, y0, x1, y1 = row_bbox
                errs = []

                # Выход за поля
                if (x0 < work_left or x1 > work_right or y0 < work_top or y1 > work_bottom):
                    errs.append("Рисунок выходит за поля.")

                # Центрирование по рабочему полю (строгий порог в pt)
                work_cx = (work_left + work_right) / 2.0
                obj_cx  = (x0 + x1) / 2.0
                if abs(obj_cx - work_cx) > 2:
                    errs.append("Рисунок должен быть выровнен по центру без абзацного отступа.")

                # Пустая строка перед картинкой
                gap_err = check_empty_line_above(
                    page, (x0, y0, x1, y1),
                    work_top_pt=work_top,
                    font_min_pt=12.0, font_max_pt=14.0,
                    first_elem_top_thresh_mm=5.0,
                    lines=text_lines,
                )
                if gap_err:
                    errs.append(gap_err)

                if errs:
                    has_error = True
                    msg = "; ".join(errs)
                    admin_lines.append(msg)
                    ann = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errs))
                    ann.set_info(title="Сервис нормоконтроля", content=msg)
                    ann.update()
        except Exception as e:
            admin_lines.append(f"[image_checker] raster group pass error on page {page_num}: {e}")

        total_raster_images += raster_count
        page_raster_counts.append((page_num, raster_count, grouped_raster_count))

        # --- Векторные группы (с исключением по таблицам) ---
        groups = _vector_groups(page, tbl_bboxes, debug_draw=debug_draw,
                                table_exclude_mode=table_exclude_mode, iou_threshold=iou_threshold)
        vector_groups_by_page[page_num] = groups

        vector_summary_lines.append(
            f"[VectorMuPDF][Стр. {page_num}] видимых путей сгруппировано: {len(groups)} логических объектов"
        )

        page_area = pw * ph
        admin_lines.append(
            f"[VectorMuPDF][Стр. {page_num}] объекты: {len(groups)} (размеры: w×h pt | w×h мм | area %)"
        )

        small_objs = []
        figure_objs = []

        # Диагностическая сводка по векторным
        for gi, g in enumerate(groups, 1):
            x0, y0, x1, y1 = g["bbox"]
            w = x1 - x0
            h = y1 - y0
            area_pct = (w * h) / (page_area + 1e-9) * 100.0

            is_figure = (
                w >= mm_to_pt(MIN_W_MM) and
                h >= mm_to_pt(MIN_H_MM) and
                area_pct >= MIN_AREA_PCT and
                h >= mm_to_pt(THIN_LINE_MM) and
                w >= mm_to_pt(THIN_LINE_MM)
            )

            is_marker_like = (w <= mm_to_pt(MARKER_MAX_W_MM) and h <= mm_to_pt(MARKER_MAX_H_MM))

            line = (
                f"  • G#{gi}: "
                f"{w:.1f}×{h:.1f} pt | {pt_to_mm(w):.1f}×{pt_to_mm(h):.1f} мм | "
                f"{area_pct:.3f}% | {'FIGURE' if is_figure else 'small'}"
                f"{' | marker-like' if is_marker_like else ''}"
            )

            if is_figure:
                figure_objs.append(line)
            else:
                small_objs.append(line)

        if figure_objs:
            admin_lines.append("    Кандидаты на рисунок:")
            admin_lines.extend(figure_objs)
        if small_objs:
            admin_lines.append("    Мелкие/маркерные объекты:")
            admin_lines.extend(small_objs)

        # Основные проверки по векторным группам
        for g in groups:
            x0, y0, x1, y1 = g["bbox"]
            w = x1 - x0
            h = y1 - y0
            area_pct = (w * h) / (page_area + 1e-9) * 100.0

            is_marker_like = (w <= mm_to_pt(MARKER_MAX_W_MM) and h <= mm_to_pt(MARKER_MAX_H_MM))
            if is_marker_like:
                continue  # мелкие «маркеры» пропускаем

            errs = []

            # Выход за поля
            if (x0 < work_left or x1 > work_right or y0 < work_top or y1 > work_bottom):
                errs.append("Графический объект выходит за поля")

            # Центровка и пустая строка — только для крупных фигур
            is_figure = (
                w >= mm_to_pt(MIN_W_MM) and
                h >= mm_to_pt(MIN_H_MM) and
                area_pct >= MIN_AREA_PCT and
                h >= mm_to_pt(THIN_LINE_MM) and
                w >= mm_to_pt(THIN_LINE_MM)
            )

            if is_figure:
                is_center, dx_mm = centered_status(g["bbox"], rect,
                                                   LEFT_MARGIN_PT, RIGHT_MARGIN_PT, CENTER_TOL_CM)
                if not is_center:
                    errs.append(
                        f"Графический объект должен быть выровнен по центру "
                        f"(смещение {dx_mm:+.1f} мм, допуск ±{CENTER_TOL_CM*10:.0f} мм)"
                    )

                gap_err = check_empty_line_above(
                    page, g["bbox"],
                    work_top_pt=work_top,
                    font_min_pt=12.0, font_max_pt=14.0,
                    first_elem_top_thresh_mm=5.0,
                    lines=text_lines,
                )
                if gap_err:
                    errs.append(gap_err)

            if errs:
                has_error = True
                msg = f"[Стр. {page_num}] Векторный объект: " + "; ".join(errs)
                admin_lines.append(msg)

                cx, cy = bbox_center(g["bbox"])
                ann_point = fitz.Point(cx, cy) if vector_annotate_center else fitz.Point(x0, y0)
                ann = page.add_text_annot(ann_point, "\n".join(errs))
                ann.set_info(title="Сервис нормоконтроля", content=msg)
                ann.update()

        if has_error:
            error_pages.append(page_num)

    # --- Глобальная нумерация найденных фигур (растров/вектора) ---
    all_figs = _enumerate_figures_single_column(pdf_document, raster_rows_by_page, vector_groups_by_page)

    # --- Проверка подрисуночных подписей ---
    fig_caption_result = check_figure_captions(pdf_document, all_figs) or {}
    cap_admin   = fig_caption_result.get("admin_details", "Нет деталей по подписям (пустой ответ)")
    cap_summary = fig_caption_result.get("user_summary", "✅Проверка подрисуночных подписей")
    cap_err_pages = set(fig_caption_result.get("error_pages", []))

    # Сводка по нумерации
    if all_figs:
        admin_lines.append("\n[Нумерация рисунков]")
        for it in all_figs:
            x0, y0, x1, y1 = it["bbox"]
            admin_lines.append(
                f"  • Рис. #{it['fig_index']} — стр. {it['page']} — "
                f"{'растровый' if it['kind']=='raster' else 'векторный'} — "
                f"bbox=({pt_to_mm(x0):.1f},{pt_to_mm(y0):.1f})–({pt_to_mm(x1):.1f},{pt_to_mm(y1):.1f}) мм"
            )

    # Сводка по количеству растров
    counts_lines = [
        f"Стр. {n}: растровых картинок {orig}, рядов (после склейки) {grp}"
        for n, orig, grp in page_raster_counts
    ]
    counts_summary = (
        f"Найдено {total_raster_images} растровых картинок в документе (до склейки)\n" +
        "\n".join(counts_lines)
    )

    admin_details = (
        counts_summary +
        ("\n\n" + "\n".join(vector_summary_lines)) +
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\n\nНарушений по графике не найдено.") +
        ("\n\n[FigureCaptions]\n" + cap_admin)
    )

    if cap_err_pages:
        error_pages = sorted(set(error_pages) | cap_err_pages)

    graphics_ok = (len(error_pages) == 0)
    captions_ok = not cap_summary.startswith("⚠️")

    if graphics_ok and captions_ok:
        user_summary = "✅Проверка рисунков и подрисуночных подписей"
    else:
        parts = []
        if not graphics_ok:
            parts.append(f"⚠️Проверка рисунков: нарушения на страницах {', '.join(map(str, error_pages))}")
        parts.append(cap_summary)  # статус из модуля подписей
        user_summary = " | ".join(parts)

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
