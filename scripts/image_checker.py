# scripts/image_checker.py
import fitz
import math
import re  # для фильтра "шумных" строк

# Константы
CM_TO_PT = 28.35
MM_TO_PT = 2.834646

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# Допуск центровки
CENTER_TOL_CM = 0.2

# Порог «кандидата на рисунок» (для центровки)
MIN_W_MM      = 30      # мин. ширина 3 см
MIN_H_MM      = 15      # мин. высота 1.5 см
MIN_AREA_PCT  = 0.30    # площадь ≥ 0.30% площади страницы
THIN_LINE_MM  = 1.0     # отсечь совсем тонкие линии

# Эвристика для пометки «похоже на маркер»
MARKER_MAX_W_MM = 8.0
MARKER_MAX_H_MM = 8.0

# Вспомогательные
def mm_to_pt(mm): return mm * MM_TO_PT
def pt_to_mm(pt): return pt / MM_TO_PT

# ===== Геометрия =====
def bbox_union(b1, b2):
    return (min(b1[0], b2[0]), min(b1[1], b2[1]),
            max(b1[2], b2[2]), max(b1[3], b2[3]))

def bbox_distance(b1, b2):
    ax0, ay0, ax1, ay1 = b1
    bx0, by0, bx1, by1 = b2
    dx = max(0, max(bx0 - ax1, ax0 - bx1))
    dy = max(0, max(by0 - ay1, ay0 - by1))
    return math.hypot(dx, dy)

def bboxes_intersect(b1, b2, tol=2.0):
    # Пересечение bbox с допуском
    return not (b1[2] < b2[0] - tol or b1[0] > b2[2] + tol or
                b1[3] < b2[1] - tol or b1[1] > b2[3] + tol)

def bbox_area(b):
    return max(0.0, b[2]-b[0]) * max(0.0, b[3]-b[1])

def bbox_intersection(b1, b2):
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

def x_overlap(a, b):
    """Горизонтальное перекрытие bbox'ов в pt."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))

def x_gap(a, b):
    """Горизонтальный зазор между интервалами по X (0, если пересекаются)."""
    return max(0.0, max(a[0]-b[2], b[0]-a[2]))

# ===== Стиль/видимость =====
def color_distance(c1, c2):
    if not c1 or not c2:
        return 1e9
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

def same_style(a, b, color_tol=0.08, lw_rel_tol=0.35):
    # цвет берём stroke приоритетно, иначе fill
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

# ===== Сбор «чистых» текстовых строк =====
_PUNCT_ONLY_RE = re.compile(r"^[\s\.\,\-\–\—\·•:;…]+$")

def _is_noise_line(line):
    """Отбрасываем микролинии/мусор."""
    fs = float(line.get("fontsize") or 0)
    x0,y0,x1,y1 = line["bbox"]
    h = max(0.0, y1 - y0)
    txt = (line.get("text") or "").strip()
    # критерии шума:
    if fs < 6.0:                        # слишком маленький кегль
        return True
    if h < mm_to_pt(2.0):               # слишком низкий bbox
        return True
    if len(txt) == 0:                   # пусто
        return True
    if _PUNCT_ONLY_RE.match(txt):       # только знаки/пробелы
        return True
    return False

def collect_text_lines(page):
    """
    Собирает строки текста:
      - bbox строки,
      - средний кегль по span'ам,
      - объединённый текст строки.
    """
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
            # средний кегль
            fs = sum(float(s.get("size", 0)) for s in spans) / max(1, len(spans))
            # текст строки (склеим спаны)
            txt = "".join(s.get("text", "") for s in spans)
            line = {"bbox": bx, "fontsize": fs, "text": txt}
            if not _is_noise_line(line):
                lines.append(line)
    return lines

def _nearest_valid_line_above(
    fig_bbox,
    lines,
    *,
    mode="global",           # "global" = игнорировать X, искать по всей ширине
    max_x_gap_mm=15.0,       # оставлены на случай mode="local"
    max_center_dx_mm=25.0,   # —//—
    max_edge_dx_mm=50.0,     # —//—
    y_tol_pt=1.5             # вертикальный допуск (~0.53 мм) на микроперекрытие по Y
):
    """
    Возвращает ближайшую по вертикали валидную строку над картинкой.
    mode="global": игнорируем X полностью, берём по минимальному dy среди всех строк с y1 <= y0+tol.
    mode="local":  (резерв) учитывает близость по X.
    """
    x0f, y0f, x1f, y1f = fig_bbox

    # --- ГЛОБАЛЬНЫЙ РЕЖИМ (вся ширина) ---
    if mode == "global":
        best, best_dy = None, None
        for ln in lines:
            x0, y0, x1, y1 = ln["bbox"]
            if y1 > (y0f + y_tol_pt):
                continue  # строка не выше (с учётом допуска)
            dy = max(0.0, y0f - y1)
            if best_dy is None or dy < best_dy:
                best, best_dy = ln, dy
        return best, (best_dy or 0.0)

    # --- ЛОКАЛЬНЫЙ РЕЖИМ (на всякий случай сохраним прежнюю логику) ---
    def mm_to_pt(mm): return mm * 2.834646
    max_gap_pt  = mm_to_pt(max_x_gap_mm)
    max_cdx_pt  = mm_to_pt(max_center_dx_mm)
    max_edge_pt = mm_to_pt(max_edge_dx_mm)
    fx_c = (x0f + x1f) / 2.0

    def x_gap(a, b):
        return max(0.0, max(a[0]-b[2], b[0]-a[2]))

    near_cand, any_cand = [], []
    for ln in lines:
        x0, y0, x1, y1 = ln["bbox"]
        if y1 > (y0f + y_tol_pt):
            continue
        gap = x_gap(ln["bbox"], fig_bbox)
        lx_c = (x0 + x1) / 2.0
        cdx = abs(lx_c - fx_c)
        edge_dx = min(abs(fx_c - x0), abs(fx_c - x1))
        dy = max(0.0, y0f - y1)
        if (gap <= max_gap_pt) or (cdx <= max_cdx_pt) or (edge_dx <= max_edge_pt):
            near_cand.append((dy, ln))
        else:
            any_cand.append((dy, ln))
    near_cand.sort(key=lambda t: t[0]); any_cand.sort(key=lambda t: t[0])
    if near_cand: return near_cand[0][1], near_cand[0][0]
    if any_cand:  return any_cand[0][1], any_cand[0][0]
    return None, 0.0

# ===== Проверка «пустой строки» ПЕРЕД картинкой (1 колонка, 1.5 межстрочник) =====
def check_empty_line_above(page, fig_bbox, page_rect, work_top_pt,
                           work_left_pt, work_right_pt,
                           font_min_pt=12.0, font_max_pt=14.0,
                           first_elem_top_thresh_mm=5.0,
                           lines=None):
    """
    Возвращает None, если всё ок/неприменимо, иначе — текст ошибки.

    Алгоритм:
      1) Собираем «чистые» строки (без мусора).
      2) Ищем ближайшую ВЫШЕ картинки:
         a) с перекрытием по X (≥ 10 мм);
         b) если нет — рядом по X (x_gap ≤ 15 мм ИЛИ центры ближе 25 мм);
         c) если нет — любую валидную строку (антишум уже отсеял мусор).
      3) Требуем gap >= 1.5 * fontsize, fontsize ∈ [12;14] pt.
      4) Если строк сверху нет и картинка близко к верхнему полю (≤ 5 мм) — пропускаем.
    """
    x0, y0, x1, y1 = fig_bbox

    if lines is None:
        lines = collect_text_lines(page)

    above, best_dy = _nearest_valid_line_above(fig_bbox, lines, mode="global", y_tol_pt=1.5)

    # Нет валидной строки сверху — допускаем «первый элемент» при близости к верху
    if not above:
        if (y0 - work_top_pt) <= mm_to_pt(first_elem_top_thresh_mm):
            return None
        # строк нет — мягко пропускаем
        return None

    # Требуемый зазор = 1.5 * fontsize (fontsize в [12;14] pt)
    fs = float(above["fontsize"]) if above["fontsize"] > 0 else font_min_pt
    fs = max(font_min_pt, min(font_max_pt, fs))
    required_gap_pt = 1.5 * fs

    actual_gap_pt = y0 - above["bbox"][3]
    if actual_gap_pt + 1e-6 < required_gap_pt:
        return (f"Нет пустой строки перед рисунком: расстояние "
                f"{pt_to_mm(actual_gap_pt):.1f} мм, требуется ≥ {pt_to_mm(required_gap_pt):.1f} мм (межстрочник 1.5)")
    return None

def choose_repr(a, b):
    # репрезентативный путь группы
    def priority(d):
        stroke = bool(d.get("stroke")) and (d.get("width") or d.get("linewidth") or 0) > 0
        fill = bool(d.get("fill"))
        if stroke and fill: return 3
        if stroke: return 2
        if fill: return 1
        return 0
    return b if priority(b) > priority(a) else a

# ===== Группировка векторов =====
def group_paths(paths):
    """
    Склеиваем составные фигуры по IoU, расстоянию центров (масштабируемому) и сходству стиля.
    """
    groups = []
    iou_thr_high = 0.65
    iou_thr_lo   = 0.30
    dist_factor  = 0.6  # множитель для порога по центрам

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

def group_rasters_by_row(raster_blocks, y_tol_pt):
    """
    Группирует растровые картинки в «ряды» по близости верхней координаты (y0).
    Возвращает список групп, где у каждой есть:
      - "bbox": объединённый bbox,
      - "items": список исходных блоков (каждый блок — dict с "bbox").
    """
    if not raster_blocks:
        return []

    # сортируем по y0 (верх)
    raster_blocks_sorted = sorted(raster_blocks, key=lambda b: b["bbox"][1])

    groups = []
    # опорный y для текущей группы (будем вести как среднее по группе)
    cur_items = []
    cur_y_ref = None

    def flush_group():
        if not cur_items:
            return
        # объединяем bbox-ы
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

        # если верх нового блока близок к опорному y — кладём в текущую группу
        if abs(y0 - cur_y_ref) <= y_tol_pt:
            cur_items.append(blk)
            # обновим опорный как среднее (чуть устойчивее к шуму)
            cur_y_ref = sum(b["bbox"][1] for b in cur_items) / len(cur_items)
        else:
            flush_group()
            cur_items = [blk]
            cur_y_ref = y0

    flush_group()
    return groups

# ===== Центровка относительно рабочей области =====
def centered_status(group_bbox, page_rect,
                    left_pt=LEFT_MARGIN_PT, right_pt=RIGHT_MARGIN_PT,
                    tol_cm=CENTER_TOL_CM):
    work_left  = page_rect.x0 + left_pt
    work_right = page_rect.x1 - right_pt
    work_center = (work_left + work_right) / 2.0

    gx, _ = bbox_center(group_bbox)
    dx_pt  = gx - work_center
    tol_pt = tol_cm * CM_TO_PT

    is_centered = abs(dx_pt) <= tol_pt
    dx_mm = pt_to_mm(dx_pt)  # 1 мм = 2.834646 pt
    return is_centered, dx_mm

# ===== Вектор: сбор групп с исключением таблиц =====
def _vector_groups(page, table_bboxes, debug_draw=False, table_exclude_mode="intersect", iou_threshold=0.30):
    """
    Возвращает сгруппированные логические объекты (bbox-группы),
    исключая пути, попадающие в bbox таблиц.

    table_exclude_mode:
      - "intersect": исключать при любом пересечении (как было у тебя);
      - "iou": исключать, если IoU с таблицей > iou_threshold (мягче).
    """
    entries = []
    try:
        drawings = page.get_drawings(extended=True)
    except TypeError:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    # Собираем только видимые пути
    for d in drawings:
        bbox = d.get("rect") or d.get("bbox")
        if not bbox:
            continue
        entry = {
            "bbox": (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            "stroke": d.get("stroke"),
            "fill": d.get("fill"),
            "width": d.get("width") or d.get("linewidth") or 0.0,
            "stroke_opacity": d.get("stroke_opacity", 1.0) if hasattr(d, "get") else None,
            "fill_opacity": d.get("fill_opacity", 1.0) if hasattr(d, "get") else None,
        }
        if is_visible_path(entry):
            entries.append(entry)

    # Исключаем пути, которые попадают в таблицы
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

    # Группируем
    groups = group_paths(filtered)

    # Отсекаем мелкий шум (по минимуму размеров в pt)
    cleaned = []
    for g in groups:
        x0, y0, x1, y1 = g["bbox"]
        if (x1-x0) >= 8 or (y1-y0) >= 8:
            cleaned.append(g)

    # Отрисовка отладочных рамок
    if debug_draw:
        for g in cleaned:
            x0, y0, x1, y1 = g["bbox"]
            page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(1, 0, 0), width=1)

    return cleaned

# ===== Основная функция =====
def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None, debug_draw=False,
                 table_exclude_mode="intersect", iou_threshold=0.30, vector_annotate_center=True):
    """
    Проверка графики:
      1) Растровые изображения (blocks type==1): выход за поля + центровка.
      2) Вектор: видимые пути -> группировка -> исключение таблиц ->
         проверка выхода за поля и (для крупных фигур) центровка.

    В админ-лог выводятся размеры всех сгруппированных векторных объектов.
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_raster_images = 0
    page_raster_counts = []
    vector_summary_lines = []

    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        rect = page.rect
        pw, ph = rect.width, rect.height
        has_error = False
        text_lines = collect_text_lines(page)

        # Рабочая область страницы с учетом реальных границ
        work_left   = rect.x0 + LEFT_MARGIN_PT
        work_right  = rect.x1 - RIGHT_MARGIN_PT
        work_top    = rect.y0 + TOP_MARGIN_PT
        work_bottom = rect.y1 - BOTTOM_MARGIN_PT

                # ---------- Растры (с группировкой по строкам) ----------
        raster_blocks = []
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1 and "bbox" in block:
                    x0, y0, x1, y1 = block["bbox"]
                    raster_blocks.append({"bbox": (x0, y0, x1, y1)})
        except Exception as e:
            admin_lines.append(f"[image_checker] raster collect error on page {page_num}: {e}")

        raster_count = len(raster_blocks)
        # допуск схожести верхней координаты: ~0.7 мм
        y_tol_pt = mm_to_pt(0.7)
        raster_rows = group_rasters_by_row(raster_blocks, y_tol_pt=y_tol_pt)
        grouped_raster_count = len(raster_rows)

        # Лог по количеству
        vector_summary_lines.append(
            f"[Raster][Стр. {page_num}] найдено {raster_count} растровых объектов, "
            f"сгруппировано в {grouped_raster_count} ряд(ов)"
        )

        try:
            for ri, row in enumerate(raster_rows, 1):
                # --- NEW: проверка "в одной строке две картинки с разными y" ---
                # сортируем картинки в ряду слева-направо
                items_sorted = sorted(row["items"], key=lambda it: it["bbox"][0])
                if len(items_sorted) >= 2:
                    y_misaligned_tol_pt = mm_to_pt(1.0)  # допуск по "разным y", можно 0.7–1.5 мм
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
                # --- /NEW ---

                x0, y0, x1, y1 = row["bbox"]
                errs = []

                # Выход за поля
                if (x0 < work_left or x1 > work_right or
                    y0 < work_top  or y1 > work_bottom):
                    errs.append("Рисунок(и) выходит(ят) за поля.")

                # Центрирование по рабочему полю (допуск в pt)
                work_cx = (work_left + work_right) / 2.0
                obj_cx  = (x0 + x1) / 2.0
                if abs(obj_cx - work_cx) > 2:
                    errs.append("Рисунок(и) должен(ы) быть выровнен(ы) по центру без абзацного отступа.")

                # ПУСТАЯ СТРОКА ПЕРЕД КАРТИНКОЙ — проверка над объединённым bbox
                gap_err = check_empty_line_above(
                    page, (x0, y0, x1, y1), rect,
                    work_top_pt=work_top,
                    work_left_pt=work_left,
                    work_right_pt=work_right,
                    font_min_pt=12.0, font_max_pt=14.0,
                    first_elem_top_thresh_mm=5.0,
                    lines=text_lines,
                )
                if gap_err:
                    errs.append(gap_err)

                if errs:
                    has_error = True
                    msg = (f""
                           + "; ".join(errs))
                    admin_lines.append(msg)
                    ann_point = fitz.Point(x0, y0)
                    ann = page.add_text_annot(ann_point, "\n".join(errs))
                    ann.set_info(title="Сервис нормоконтроля", content=msg)
                    ann.update()
        except Exception as e:
            admin_lines.append(f"[image_checker] raster group pass error on page {page_num}: {e}")

        total_raster_images += raster_count
        page_raster_counts.append((page_num, raster_count, grouped_raster_count))


        # ---------- Вектор ----------
        tbl_bboxes = table_bboxes_by_page.get(page_num, [])
        groups = _vector_groups(page, tbl_bboxes, debug_draw=debug_draw,
                                table_exclude_mode=table_exclude_mode, iou_threshold=iou_threshold)

        vector_summary_lines.append(
            f"[VectorMuPDF][Стр. {page_num}] видимых путей сгруппировано: {len(groups)} логических объектов"
        )

        # ===== ДОП. ЛОГ РАЗМЕРОВ ВЕКТОРНЫХ ОБЪЕКТОВ =====
        page_area = pw * ph
        admin_lines.append(
            f"[VectorMuPDF][Стр. {page_num}] объекты: {len(groups)} (размеры: w×h pt | w×h мм | area %)"
        )

        small_objs = []
        figure_objs = []

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

        # Основные проверки/аннотации по группам
        for g in groups:
            x0, y0, x1, y1 = g["bbox"]
            w = x1 - x0
            h = y1 - y0
            area_pct = (w * h) / (page_area + 1e-9) * 100.0

            is_marker_like = (w <= mm_to_pt(MARKER_MAX_W_MM) and h <= mm_to_pt(MARKER_MAX_H_MM))
            if is_marker_like:
                continue

            errs = []

            # Выход за поля
            if (x0 < work_left or x1 > work_right or
                y0 < work_top  or y1 > work_bottom):
                errs.append("Графический объект выходит за поля")

            # Центровку проверяем ТОЛЬКО для крупных фигур
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
                    page, g["bbox"], rect,
                    work_top_pt=work_top,
                    work_left_pt=work_left,
                    work_right_pt=work_right,
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

                # точка аннотации
                cx, cy = bbox_center(g["bbox"])
                ann_point = fitz.Point(cx, cy) if vector_annotate_center else fitz.Point(x0, y0)
                ann = page.add_text_annot(ann_point, "\n".join(errs))
                ann.set_info(title="Сервис нормоконтроля", content=msg)
                ann.update()

        if has_error:
            error_pages.append(page_num)

    # --- отчёты
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
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\n\nНарушений по графике не найдено.")
    )

    if error_pages:
        user_summary = f"⚠️Проверка рисунков: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
    else:
        user_summary = "✅Проверка рисунков"

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
