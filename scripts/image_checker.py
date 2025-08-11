# scripts/image_checker.py
import fitz
import pdfplumber
import math

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT = 2

# ---------- utils ----------
def bbox_inside(b, c, tol=2):
    x0, y0, x1, y1 = b
    X0, Y0, X1, Y1 = c
    return (x0 >= X0 - tol and y0 >= Y0 - tol and
            x1 <= X1 + tol and y1 <= Y1 + tol)

def bbox_of_line(l):
    return (min(l['x0'], l['x1']), min(l['y0'], l['y1']),
            max(l['x0'], l['x1']), max(l['y0'], l['y1']))

def bbox_of_rect(r):
    return (r['x0'], r['y0'], r['x1'], r['y1'])

def bbox_of_curve(c):
    xs = [p[0] for p in c['pts']]
    ys = [p[1] for p in c['pts']]
    return (min(xs), min(ys), max(xs), max(ys))

def bbox_union(b1, b2):
    return (min(b1[0], b2[0]), min(b1[1], b2[1]),
            max(b1[2], b2[2]), max(b1[3], b2[3]))

def bbox_distance(b1, b2):
    ax0, ay0, ax1, ay1 = b1
    bx0, by0, bx1, by1 = b2
    dx = max(0, max(bx0 - ax1, ax0 - bx1))
    dy = max(0, max(by0 - ay1, ay0 - by1))
    return math.hypot(dx, dy)

def is_bullet_candidate(b, page_width,
                        max_size_pt=6,
                        left_band_x0=LEFT_MARGIN_PT-15,
                        left_band_x1=LEFT_MARGIN_PT+45):
    # маленький bbox в зоне левого поля
    w = abs(b[2] - b[0])
    h = abs(b[3] - b[1])
    return (w <= max_size_pt and h <= max_size_pt and
            b[0] >= left_band_x0 and b[2] <= left_band_x1)

def cluster_bboxes(bboxes, max_dist=6):
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

# ---------- main ----------
def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None, debug_draw=False):
    """
    - исключаем векторы, находящиеся внутри bbox-ов таблиц;
    - исключаем «маркеры списков» (мелкие слева);
    - группируем оставшиеся векторы в кластеры и проверяем их как рисунки;
    - растровые блоки проверяем как раньше.
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_images = 0
    page_image_counts = []
    page_vectors = {}  # {page_num: [bbox, ...]}

    # собрать векторы
    if pdf_path:
        with pdfplumber.open(pdf_path) as pdfp:
            for page_idx, p in enumerate(pdfp.pages, start=1):
                all_vec = [bbox_of_line(l) for l in p.lines] + \
                          [bbox_of_rect(r) for r in p.rects] + \
                          [bbox_of_curve(c) for c in p.curves]

                # исключить всё, что внутри таблиц
                filtered = []
                tbl_bboxes = table_bboxes_by_page.get(page_idx, [])
                for b in all_vec:
                    if not any(bbox_inside(b, tb, tol=2) for tb in tbl_bboxes):
                        filtered.append(b)

                # исключить маркеры слева
                filtered2 = [b for b in filtered if not is_bullet_candidate(b, p.width)]
                page_vectors[page_idx] = filtered2

    # проверка по страницам
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        pw, ph = page.rect.width, page.rect.height
        has_error = False

        # 1) растровые блоки
        image_count = 0
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                image_count += 1
                x0, y0, x1, y1 = block["bbox"]
                errs = []
                if (x0 < LEFT_MARGIN_PT or x1 > pw - RIGHT_MARGIN_PT or
                    y0 < TOP_MARGIN_PT or y1 > ph - BOTTOM_MARGIN_PT):
                    errs.append("Изображение выходит за поля")
                work_w = pw - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                work_cx = LEFT_MARGIN_PT + work_w/2
                obj_cx = (x0 + x1)/2
                if abs(obj_cx - work_cx) > 2:
                    errs.append("Рисунок должен быть выровнен по центру без абзацного отступа")
                if errs:
                    has_error = True
                    msg = f"[Стр. {page_num}] Растровый рисунок: " + "; ".join(errs)
                    admin_lines.append(msg)
                    ann = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errs))
                    ann.set_info(title="Сервис нормоконтроля", content=msg)
                    ann.update()

        total_images += image_count
        page_image_counts.append((page_num, image_count))

        # 2) векторные «рисунки» (кластеры)
        vecs = page_vectors.get(page_num, [])
        if vecs:
            clusters = cluster_bboxes(vecs, max_dist=6)
            for b in clusters:
                x0, y0, x1, y1 = b
                # отсекаем мусор
                if (x1 - x0) < 8 and (y1 - y0) < 8:
                    continue

                errs = []
                if (x0 < LEFT_MARGIN_PT or x1 > pw - RIGHT_MARGIN_PT or
                    y0 < TOP_MARGIN_PT or y1 > ph - BOTTOM_MARGIN_PT):
                    errs.append("Графический объект выходит за поля")
                work_w = pw - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                work_cx = LEFT_MARGIN_PT + work_w/2
                obj_cx = (x0 + x1)/2
                if abs(obj_cx - work_cx) > 2:
                    errs.append("Графический объект должен быть выровнен по центру без абзацного отступа")

                if errs:
                    has_error = True
                    msg = f"[Стр. {page_num}] Векторный объект: " + "; ".join(errs)
                    admin_lines.append(msg)
                    ann = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errs))
                    ann.set_info(title="Сервис нормоконтроля", content=msg)
                    ann.update()

                # опционально рисуем рамку кластера (для отладки)
                if debug_draw:
                    ra = fitz.Rect(x0, y0, x1, y1)
                    rect_annot = page.add_rect_annot(ra)
                    rect_annot.set_colors(stroke=(1, 0, 0))
                    rect_annot.set_border(width=0.5)
                    rect_annot.update()

        if has_error:
            error_pages.append(page_num)

    counts_lines = [f"Стр. {n}: растровых картинок {c}" for n, c in page_image_counts]
    counts_summary = f"Найдено {total_images} растровых картинок в документе\n" + "\n".join(counts_lines)
    admin_details = counts_summary + ("\n\n" + "\n".join(admin_lines) if admin_lines else "\nНарушений по графике не найдено.")

    user_summary = (f"⚠️Проверка рисунков: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
                    if error_pages else "✅Проверка рисунков")

    return {"user_summary": user_summary, "admin_details": admin_details}
