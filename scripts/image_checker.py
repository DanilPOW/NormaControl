# scripts/image_checker.py
import fitz
import pdfplumber
import math

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT = 2

# ---------- утилиты ----------

def bbox_inside(b, c, tol=2):
    """b inside c with tolerance"""
    x0, y0, x1, y1 = b
    X0, Y0, X1, Y1 = c
    return (x0 >= X0 - tol and y0 >= Y0 - tol and
            x1 <= X1 + tol and y1 <= Y1 + tol)

def bbox_of_line(line):
    return (min(line['x0'], line['x1']), min(line['y0'], line['y1']),
            max(line['x0'], line['x1']), max(line['y0'], line['y1']))

def bbox_of_rect(r):
    return (r['x0'], r['y0'], r['x1'], r['y1'])

def bbox_of_curve(c):
    # curves у pdfplumber уже со списком pts; берём bbox
    xs = [p[0] for p in c['pts']]
    ys = [p[1] for p in c['pts']]
    return (min(xs), min(ys), max(xs), max(ys))

def bbox_union(b1, b2):
    return (min(b1[0], b2[0]), min(b1[1], b2[1]),
            max(b1[2], b2[2]), max(b1[3], b2[3]))

def bbox_center_x(b):
    return (b[0] + b[2]) / 2.0

def bbox_distance(b1, b2):
    # кратчайшее расстояние между прямоугольниками (0 если пересекаются/касаются)
    ax0, ay0, ax1, ay1 = b1
    bx0, by0, bx1, by1 = b2
    dx = max(0, max(bx0 - ax1, ax0 - bx1))
    dy = max(0, max(by0 - ay1, ay0 - by1))
    return math.hypot(dx, dy)

def is_bullet_candidate(b, page_width, max_size_pt=6, left_band_ratio=0.4):
    """Эвристика для маркеров: маленький bbox, слева (в левой части полосы)."""
    w = abs(b[2] - b[0])
    h = abs(b[3] - b[1])
    is_small = (w <= max_size_pt and h <= max_size_pt)
    is_left = (b[0] < page_width * left_band_ratio)
    return is_small and is_left

def cluster_bboxes(bboxes, max_dist=6):
    """Простая кластеризация: слипать bbox-ы по близости/пересечению."""
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

# ---------- основная проверка ----------

def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None):
    """
    Дополнено:
    - исключаем векторные объекты, лежащие внутри bbox таблиц (Camelot),
    - группируем оставшиеся векторы и проверяем их как рисунки,
    - исключаем «маркеры списков» по эвристике.
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_images = 0
    page_image_counts = []  # [(page_num, count)]

    # === соберём векторные элементы через pdfplumber ===
    page_vectors = {}  # {page_num: [bbox, ...]} после фильтрации таблиц и маркеров

    if pdf_path:
        with pdfplumber.open(pdf_path) as pdfp:
            for page_idx, p in enumerate(pdfp.pages, start=1):
                lines = []
                for l in p.lines:
                    # горизонтальные/верт. линии уже не важны — берём bbox линии
                    lines.append(bbox_of_line(l))
                rects = [bbox_of_rect(r) for r in p.rects]
                curves = [bbox_of_curve(c) for c in p.curves]

                all_vec = lines + rects + curves

                # исключаем всё, что внутри bbox таблиц
                tbl_bboxes = table_bboxes_by_page.get(page_idx, [])
                filtered = []
                for b in all_vec:
                    inside_any_table = any(bbox_inside(b, tb, tol=2) for tb in tbl_bboxes)
                    if not inside_any_table:
                        filtered.append(b)

                # исключаем "маркеры" (мелкие слева)
                page_width = p.width
                filtered2 = [b for b in filtered if not is_bullet_candidate(b, page_width)]

                page_vectors[page_idx] = filtered2

    # === проверяем страницы: растровые + векторные кластеры как рисунки ===
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        page_width, page_height = page.rect.width, page.rect.height
        has_error = False

        # 1) Растровые изображения (как раньше)
        image_count = 0
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                image_count += 1
                bbox = block.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = bbox
                errors = []
                # выход за поля
                if (x0 < LEFT_MARGIN_PT or x1 > page_width - RIGHT_MARGIN_PT or
                    y0 < TOP_MARGIN_PT or y1 > page_height - BOTTOM_MARGIN_PT):
                    errors.append("Изображение выходит за поля")
                # центрирование по рабочей области
                work_width = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                work_center = LEFT_MARGIN_PT + work_width / 2
                obj_center = (x0 + x1) / 2
                if abs(obj_center - work_center) > 2:
                    errors.append("Рисунок должен быть выровнен по центру без абзацного отступа")

                if errors:
                    has_error = True
                    msg = f"[Стр. {page_num}] Растровый рисунок: " + "; ".join(errors)
                    admin_lines.append(msg)
                    annot = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errors))
                    annot.set_info(title="Сервис нормоконтроля", content=msg)
                    annot.update()

        total_images += image_count
        page_image_counts.append((page_num, image_count))

        # 2) Векторные «рисунки»: кластеры из оставшихся векторов
        vec_bboxes = page_vectors.get(page_num, [])
        if vec_bboxes:
            # группируем
            clusters = cluster_bboxes(vec_bboxes, max_dist=6)
            for b in clusters:
                x0, y0, x1, y1 = b
                # мелкие «точки»/мусор пропустим
                if (x1 - x0) < 8 and (y1 - y0) < 8:
                    continue

                errors = []
                # поля
                if (x0 < LEFT_MARGIN_PT or x1 > page_width - RIGHT_MARGIN_PT or
                    y0 < TOP_MARGIN_PT or y1 > page_height - BOTTOM_MARGIN_PT):
                    errors.append("Графический объект выходит за поля")
                # центрирование по рабочей области
                work_width = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                work_center = LEFT_MARGIN_PT + work_width / 2
                obj_center = (x0 + x1) / 2
                if abs(obj_center - work_center) > 2:
                    errors.append("Графический объект должен быть выровнен по центру без абзацного отступа")

                if errors:
                    has_error = True
                    msg = f"[Стр. {page_num}] Векторный объект: " + "; ".join(errors)
                    admin_lines.append(msg)
                    annot = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errors))
                    annot.set_info(title="Сервис нормоконтроля", content=msg)
                    annot.update()

        if has_error:
            error_pages.append(page_num)

    counts_lines = [f"Страница {num}: растровых картинок {count}" for num, count in page_image_counts]
    counts_summary = (
        f"Найдено {total_images} растровых картинок в документе\n" +
        "\n".join(counts_lines)
    )
    admin_details = (
        counts_summary +
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\nНарушений по графическим объектам не найдено.")
    )

    if error_pages:
        user_summary = f"⚠️Проверка рисунков: обнаружены нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
    else:
        user_summary = "✅Проверка рисунков"

    return {"user_summary": user_summary, "admin_details": admin_details}
