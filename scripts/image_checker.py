# scripts/image_checker.py
import fitz
import math

# ===== Константы полей и допусков =====
CM_TO_PT = 28.35
MM_TO_PT = 2.834646

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# Допуск центровки в сантиметрах (±2 мм)
CENTER_TOL_CM = 0.2

# Порог «кандидата на рисунок» (для центровки)
MIN_W_MM      = 30      # мин. ширина 3 см
MIN_H_MM      = 15      # мин. высота 1.5 см
MIN_AREA_PCT  = 0.30    # площадь ≥ 0.30% площади страницы
THIN_LINE_MM  = 1.0     # отсечь совсем тонкие линии

# Вспомогательные
def mm_to_pt(mm): return mm * MM_TO_PT

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

# ===== Стиль/видимость =====
def color_distance(c1, c2):
    if not c1 or not c2:
        return 1e9
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2)))

def same_style(a, b, color_tol=0.08, lw_rel_tol=0.35):
    # цвет берем stroke приоритетно, иначе fill
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
    dx_mm = dx_pt / MM_TO_PT  # 1 мм = 2.834646 pt
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
            "stroke_opacity": d.get("stroke_opacity", 1.0),
            "fill_opacity": d.get("fill_opacity", 1.0),
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
            # strict intersect
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
      1) Растровые изображения (blocks type==1): выход за поля + центровка (как было).
      2) Вектор: видимые пути (extended=True) -> группировка -> исключение таблиц ->
         проверка выхода за поля и (для крупных фигур) центровка.

    table_exclude_mode: "intersect" (как раньше) или "iou" (мягче, с iou_threshold).
    vector_annotate_center: ставить аннотацию по центру bbox (True) или в левый верхний угол (False).
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

        # Рабочая область страницы с учетом реальных границ
        work_left   = rect.x0 + LEFT_MARGIN_PT
        work_right  = rect.x1 - RIGHT_MARGIN_PT
        work_top    = rect.y0 + TOP_MARGIN_PT
        work_bottom = rect.y1 - BOTTOM_MARGIN_PT

        # ---------- Растры ----------
        raster_count = 0
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1:
                    raster_count += 1
                    x0, y0, x1, y1 = block["bbox"]
                    errs = []

                    # Выход за поля (с учетом page.rect)
                    if (x0 < work_left or x1 > work_right or
                        y0 < work_top  or y1 > work_bottom):
                        errs.append("Рисунок выходит за поля")

                    # Центрирование по рабочему полю (допуск в pt)
                    work_cx = (work_left + work_right) / 2.0
                    obj_cx  = (x0 + x1) / 2.0
                    if abs(obj_cx - work_cx) > 2:  # как у тебя
                        errs.append("Рисунок должен быть выровнен по центру без абзацного отступа")

                    if errs:
                        has_error = True
                        msg = f"[Стр. {page_num}] Растровый рисунок: " + "; ".join(errs)
                        admin_lines.append(msg)
                        # аннотация в верхний левый угол растра
                        ann_point = fitz.Point(x0, y0)
                        ann = page.add_text_annot(ann_point, "\n".join(errs))
                        ann.set_info(title="Сервис нормоконтроля", content=msg)
                        ann.update()
        except Exception as e:
            admin_lines.append(f"[image_checker] raster pass error on page {page_num}: {e}")

        total_raster_images += raster_count
        page_raster_counts.append((page_num, raster_count))

        # ---------- Вектор ----------
        tbl_bboxes = table_bboxes_by_page.get(page_num, [])
        groups = _vector_groups(page, tbl_bboxes, debug_draw=debug_draw,
                                table_exclude_mode=table_exclude_mode, iou_threshold=iou_threshold)

        vector_summary_lines.append(
            f"[VectorMuPDF][Стр. {page_num}] видимых путей сгруппировано: {len(groups)} логических объектов"
        )

        page_area = pw * ph
        for g in groups:
            x0, y0, x1, y1 = g["bbox"]
            w = x1 - x0
            h = y1 - y0
            area_pct = (w * h) / (page_area + 1e-9) * 100.0
            errs = []

            # Выход за поля (учитываем page.rect)
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

            if errs:
                has_error = True
                msg = f"[Стр. {page_num}] Векторный объект: " + "; ".join(errs)
                admin_lines.append(msg)

                # точка аннотации: центр bbox или верхний левый угол — на выбор
                if vector_annotate_center:
                    cx, cy = bbox_center(g["bbox"])
                    ann_point = fitz.Point(cx, cy)
                else:
                    ann_point = fitz.Point(x0, y0)

                ann = page.add_text_annot(ann_point, "\n".join(errs))
                ann.set_info(title="Сервис нормоконтроля", content=msg)
                ann.update()

        if has_error:
            error_pages.append(page_num)

    # --- отчёты
    counts_lines = [f"Стр. {n}: растровых картинок {c}" for n, c in page_raster_counts]
    counts_summary = (
        f"Найдено {total_raster_images} растровых картинок в документе\n" +
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
