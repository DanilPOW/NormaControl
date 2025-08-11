# scripts/image_checker.py
import fitz
import pdfplumber
import math

# --- Поля (в пунктах) ---
LEFT_MARGIN_PT   = 3 * 28.35
RIGHT_MARGIN_PT  = 1.5 * 28.35
TOP_MARGIN_PT    = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT     = 2


# ========== utils ==========
def bbox_of_line(l):
    return (min(l["x0"], l["x1"]), min(l["y0"], l["y1"]),
            max(l["x0"], l["x1"]), max(l["y0"], l["y1"]))

def bbox_of_rect(r):
    return (float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))

def bbox_of_curve(c):
    xs = [p[0] for p in c["pts"]]
    ys = [p[1] for p in c["pts"]]
    return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))

def bbox_union(b1, b2):
    return (min(b1[0], b2[0]), min(b1[1], b2[1]),
            max(b1[2], b2[2]), max(b1[3], b2[3]))

def bbox_distance(b1, b2):
    # Евклидово расстояние между рамками (0, если пересекаются)
    ax0, ay0, ax1, ay1 = b1
    bx0, by0, bx1, by1 = b2
    dx = max(0, max(bx0 - ax1, ax0 - bx1))
    dy = max(0, max(by0 - ay1, ay0 - by1))
    return math.hypot(dx, dy)

def bboxes_intersect(b1, b2, tol=2):
    # Пересекаются ли два bbox (с небольшим допуском)
    return not (b1[2] < b2[0] - tol or b1[0] > b2[2] + tol or
                b1[3] < b2[1] - tol or b1[1] > b2[3] + tol)

def cluster_bboxes(bboxes, max_dist=6):
    # Простая агломеративная кластеризация bbox-ов по близости
    bboxes = bboxes[:]
    clusters = []
    while bboxes:
        cur = bboxes.pop(0)
        changed = True
        while changed:
            changed = False
            for other in bboxes[:]:
                if bbox_distance(cur, other) <= max_dist:
                    cur = bbox_union(cur, other)
                    bboxes.remove(other)
                    changed = True
        clusters.append(cur)
    return clusters

def is_bullet_candidate(
    b,
    page_width,
    max_size_pt=8,                    # небольшой размер
    left_band_x0=LEFT_MARGIN_PT - 20, # полоса слева от поля
    left_band_x1=LEFT_MARGIN_PT + 50  # полоса внутри поля
):
    # Маркеры-«пули» обычно маленькие и находятся слева у начала строки
    w = abs(b[2] - b[0])
    h = abs(b[3] - b[1])
    return (w <= max_size_pt and h <= max_size_pt and
            b[0] >= left_band_x0 and b[2] <= left_band_x1)


# ========== main ==========
def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None, debug_draw=False):
    """
    Проверка графики:
      1) Растровые изображения (через PyMuPDF):
         - проверка выхода за поля;
         - проверка центрирования по рабочему полю.
      2) Векторная графика (через pdfplumber):
         - соберём линии/прямоугольники/кривые;
         - исключим всё, что пересекается с bbox-ами таблиц;
         - исключим маркеры списков (маленькие слева);
         - сгруппируем оставшиеся в кластеры (как «рисунки») и проверим по тем же правилам.

    Параметры:
      pdf_document            - fitz.Document (PyMuPDF)
      pdf_path                - путь к PDF (нужен для pdfplumber, чтобы прочитать векторные элементы)
      table_bboxes_by_page    - {page_num: [(x0,y0,x1,y1), ...]} из pdf_table_checker
      debug_draw              - если True, рисуем рамки вокруг векторных кластеров (аннотации)
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_raster_images = 0
    page_raster_counts = []     # [(page_num, count)]
    page_vectors = {}           # {page_num: [bbox, ...]}

    # --- Собираем векторные объекты через pdfplumber (если есть путь к файлу)
    if pdf_path:
        try:
            with pdfplumber.open(pdf_path) as pdfp:
                for page_idx, p in enumerate(pdfp.pages, start=1):
                    # все векторы страницы
                    all_vec = (
                        [bbox_of_line(l)  for l in p.lines] +
                        [bbox_of_rect(r)  for r in p.rects] +
                        [bbox_of_curve(c) for c in p.curves]
                    )

                    # исключаем всё, что пересекается с таблицами
                    tbl_bboxes = table_bboxes_by_page.get(page_idx, [])
                    filtered = []
                    for b in all_vec:
                        if not any(bboxes_intersect(b, tb, tol=TOLERANCE_PT) for tb in tbl_bboxes):
                            filtered.append(b)

                    # исключаем маркеры списков (мелкие слева)
                    filtered2 = [b for b in filtered if not is_bullet_candidate(b, p.width)]

                    # можно отсеять очень маленький мусор (тонкие линии и т. п.)
                    filtered3 = []
                    for b in filtered2:
                        w = b[2] - b[0]
                        h = b[3] - b[1]
                        if w < 0.5 and h < 0.5:
                            continue
                        filtered3.append(b)

                    page_vectors[page_idx] = filtered3
        except Exception as e:
            admin_lines.append(f"[image_checker] pdfplumber error: {e}")

    # --- Идём по страницам PyMuPDF и проверяем растровые + векторные кластеры
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        pw, ph = page.rect.width, page.rect.height
        has_error = False

        # 1) Растровые изображения (blocks type == 1)
        raster_count = 0
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1:
                    raster_count += 1
                    x0, y0, x1, y1 = block["bbox"]
                    errs = []

                    # Выход за поля
                    if (x0 < LEFT_MARGIN_PT or x1 > pw - RIGHT_MARGIN_PT or
                        y0 < TOP_MARGIN_PT or  y1 > ph - BOTTOM_MARGIN_PT):
                        errs.append("Изображение выходит за поля")

                    # Центрирование по рабочему полю
                    work_w  = pw - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                    work_cx = LEFT_MARGIN_PT + work_w / 2
                    obj_cx  = (x0 + x1) / 2
                    if abs(obj_cx - work_cx) > 2:
                        errs.append("Рисунок должен быть выровнен по центру без абзацного отступа")

                    if errs:
                        has_error = True
                        msg = f"[Стр. {page_num}] Растровый рисунок: " + "; ".join(errs)
                        admin_lines.append(msg)
                        ann = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errs))
                        ann.set_info(title="Сервис нормоконтроля", content=msg)
                        ann.update()
        except Exception as e:
            admin_lines.append(f"[image_checker] raster pass error on page {page_num}: {e}")

        total_raster_images += raster_count
        page_raster_counts.append((page_num, raster_count))

        # 2) Векторные «рисунки» (кластеры)
        vecs = page_vectors.get(page_num, [])
        if vecs:
            clusters = cluster_bboxes(vecs, max_dist=6)
            for b in clusters:
                x0, y0, x1, y1 = b
                # отсечём очень маленькие кластеры
                if (x1 - x0) < 8 and (y1 - y0) < 8:
                    continue

                errs = []
                # Выход за поля
                if (x0 < LEFT_MARGIN_PT or x1 > pw - RIGHT_MARGIN_PT or
                    y0 < TOP_MARGIN_PT or  y1 > ph - BOTTOM_MARGIN_PT):
                    errs.append("Графический объект выходит за поля")

                # Центрирование по рабочему полю
                work_w  = pw - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                work_cx = LEFT_MARGIN_PT + work_w / 2
                obj_cx  = (x0 + x1) / 2
                if abs(obj_cx - work_cx) > 2:
                    errs.append("Графический объект должен быть выровнен по центру без абзацного отступа")

                if errs:
                    has_error = True
                    msg = f"[Стр. {page_num}] Векторный объект: " + "; ".join(errs)
                    admin_lines.append(msg)
                    ann = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errs))
                    ann.set_info(title="Сервис нормоконтроля", content=msg)
                    ann.update()

                # Отладочная рамка вокруг кластера
                if debug_draw:
                    ra = fitz.Rect(x0, y0, x1, y1)
                    rect_annot = page.add_rect_annot(ra)
                    rect_annot.set_colors(stroke=(1, 0, 0))
                    rect_annot.set_border(width=0.5)
                    rect_annot.update()

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
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\nНарушений по графике не найдено.")
    )

    if error_pages:
        user_summary = f"⚠️Проверка рисунков: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
    else:
        user_summary = "✅Проверка рисунков"

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
