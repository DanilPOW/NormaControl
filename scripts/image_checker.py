# scripts/image_checker.py
import fitz
import math

#Поля 
LEFT_MARGIN_PT   = 3 * 28.35
RIGHT_MARGIN_PT  = 1.5 * 28.35
TOP_MARGIN_PT    = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT     = 2


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

def bboxes_intersect(b1, b2, tol=TOLERANCE_PT):
    # Пересечение bbox с допуском
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


#сбор векторных bbox
def _mupdf_vector_bboxes(page):
    """
    Сбор bbox для каждого векторного примитива через page.get_drawings().
    Возвращает список (x0,y0,x1,y1).
    """
    bboxes = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return bboxes

    for d in drawings:
        # Если есть готовый прямоугольник — используем его
        rect = d.get("rect")
        if rect is not None:
            bboxes.append((rect.x0, rect.y0, rect.x1, rect.y1))
            continue

        xs, ys = [], []
        for it in d.get("items", []):
            # it может быть dict разных типов: lines, beziers, curves, etc.
            pts = it.get("points")
            if pts:
                for p in pts:
                    # p может быть (x,y) или fitz.Point
                    try:
                        x, y = p
                    except Exception:
                        x, y = float(p.x), float(p.y)
                    xs.append(float(x))
                    ys.append(float(y))
                continue

            # некоторые элементы имеют x0,y0,x1,y1 напрямую
            x0 = it.get("x0"); y0 = it.get("y0"); x1 = it.get("x1"); y1 = it.get("y1")
            if None not in (x0, y0, x1, y1):
                xs.extend([float(x0), float(x1)])
                ys.extend([float(y0), float(y1)])

        if xs and ys:
            bboxes.append((min(xs), min(ys), max(xs), max(ys)))

    return bboxes


def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None, debug_draw=False):
    """
    Проверка графики:
      1) Растровые изображения (через PyMuPDF):
         - проверка выхода за поля;
         - проверка центрирования по рабочему полю.
      2) Векторная графика (Только PyMuPDF, get_drawings()):
         - собираем bbox всех примитивов;
         - исключаем всё, что пересекается с bbox-ами таблиц;
         - объединяем в кластеры (как «рисунки»);
         - проверяем выход за поля и центрирование.

    Параметры:
      pdf_document            - fitz.Document (PyMuPDF)
      pdf_path                - НЕ используется в этой версии (оставлен для совместимости)
      table_bboxes_by_page    - {page_num: [(x0,y0,x1,y1), ...]} из pdf_table_checker
      debug_draw              - если True, рисуем рамки вокруг кластеров (аннотации)
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_raster_images = 0
    page_raster_counts = []  # [(page_num, count)]

    # для сводки по вектору
    vector_summary_lines = []

    # --- Проходим по страницам
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        pw, ph = page.rect.width, page.rect.height
        has_error = False

        # ---------- Растровые изображения ----------
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

        # ---------- Векторные объекты (PyMuPDF) ----------
        raw_vec = _mupdf_vector_bboxes(page)

        # исключаем пересечение с таблицами
        tbl_bboxes = table_bboxes_by_page.get(page_num, [])
        filtered_vec = [b for b in raw_vec if not any(bboxes_intersect(b, tb) for tb in tbl_bboxes)]

        # кластеризация как «рисунки»
        clusters = cluster_bboxes(filtered_vec, max_dist=6)

        # отсекаем совсем мелкие кластеры (шум)
        clusters = [b for b in clusters if (b[2] - b[0] >= 8 or b[3] - b[1] >= 8)]

        # в админ-лог краткую сводку по странице
        vector_summary_lines.append(
            f"[VectorMuPDF][Стр. {page_num}] PyMuPDF: {len(raw_vec)} raw → {len(filtered_vec)} filtered; clusters: {len(clusters)}"
        )

        # Проверки по каждому кластеру
        for (x0, y0, x1, y1) in clusters:
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
                # рисуем в контент страницы — красный контур, без заливки
                page.draw_rect(ra, color=(1, 0, 0), width=1)  # width в пунктах

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
