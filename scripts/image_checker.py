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

def bbox_from_points(pts):
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
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

def bboxes_intersect(b1, b2, tol=2):
    return not (b1[2] < b2[0] - tol or b1[0] > b2[2] + tol or
                b1[3] < b2[1] - tol or b1[1] > b2[3] + tol)

def is_bullet_candidate(
    b,
    page_width,
    max_size_pt=8,
    left_band_x0=LEFT_MARGIN_PT - 20,
    left_band_x1=LEFT_MARGIN_PT + 50
):
    w = abs(b[2] - b[0])
    h = abs(b[3] - b[1])
    return (w <= max_size_pt and h <= max_size_pt and
            b[0] >= left_band_x0 and b[2] <= left_band_x1)

def drawing_bbox_mupdf(drawing):
    """
    Грубая оценка bbox для объекта из page.get_drawings().
    Собираем точки из элементов ('l','re','c','m') и считаем по ним bbox.
    """
    pts = []
    for it in drawing.get("items", []):
        try:
            op = it[0]
            if op == "l":            # ('l', p1, p2)
                p1, p2 = it[1], it[2]
                pts.extend([p1, p2])
            elif op == "re":         # ('re', x, y, w, h)
                x, y, w, h = it[1], it[2], it[3], it[4]
                pts.extend([(x, y), (x+w, y), (x, y+h), (x+w, y+h)])
            elif op == "c":          # ('c', p1, p2, p3)
                p1, p2, p3 = it[1], it[2], it[3]
                pts.extend([p1, p2, p3])
            elif op == "m":          # ('m', p)
                p = it[1]
                pts.append(p)
            # Другие оп-коды ('q','Q','h','f','S' и т.д.) для bbox не нужны
        except Exception:
            continue
    return bbox_from_points(pts)


# ========== main ==========
def check_images(pdf_document, pdf_path=None, table_bboxes_by_page=None, debug_draw=False):
    """
    РЕЖИМ СРАВНЕНИЯ ВЕКТОРОВ:
      • Не объединяем и не валидируем векторные объекты — только считаем,
        что нашёл pdfplumber и что нашёл PyMuPDF (до/после фильтрации).
      • По желанию рисуем рамки:
          - pdfplumber — СИНИЕ
          - PyMuPDF — ЗЕЛЁНЫЕ

    Что продолжаем проверять:
      • Растровые изображения — правила полей/центрирования (как раньше).

    Возвращаемое:
      user_summary / admin_details — с суммарной статистикой по растру
      и пометками [VectorSummaryPlumber]/[VectorSummaryMuPDF] по страницам.
    """
    if table_bboxes_by_page is None:
        table_bboxes_by_page = {}

    admin_lines = []
    error_pages = []
    total_raster_images = 0
    page_raster_counts = []

    # --- Подготовка: заранее прочитаем plumber-страницы (если pdf_path дан)
    plumber_pages = None
    if pdf_path:
        try:
            pl = pdfplumber.open(pdf_path)
            plumber_pages = pl.pages
        except Exception as e:
            admin_lines.append(f"[image_checker] pdfplumber open error: {e}")
            plumber_pages = None

    # --- Идём по страницам
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        pw, ph = page.rect.width, page.rect.height
        has_error = False

        # 1) РАСТР: проверки как раньше
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

        # 2) ВЕКТОРА: считаем отдельно pdfplumber и PyMuPDF (без объединения/кластеризации/проверок)
        # 2.1 pdfplumber
        plumber_raw = 0
        plumber_filtered = 0
        if plumber_pages and 0 <= idx < len(plumber_pages):
            try:
                p = plumber_pages[idx]
                all_vec_pl = (
                    [bbox_of_line(l)  for l in p.lines] +
                    [bbox_of_rect(r)  for r in p.rects] +
                    [bbox_of_curve(c) for c in p.curves]
                )
                plumber_raw = len(all_vec_pl)

                tbl_bboxes = table_bboxes_by_page.get(page_num, [])
                filtered_pl = []
                for b in all_vec_pl:
                    if any(bboxes_intersect(b, tb, tol=TOLERANCE_PT) for tb in tbl_bboxes):
                        continue
                    if is_bullet_candidate(b, p.width):
                        continue
                    # отсечь крошечный мусор
                    w = b[2] - b[0]
                    h = b[3] - b[1]
                    if w < 0.5 and h < 0.5:
                        continue
                    filtered_pl.append(b)
                plumber_filtered = len(filtered_pl)

                # Нарисовать (синие)
                if debug_draw:
                    for b in filtered_pl:
                        ra = fitz.Rect(*b)
                        rect_annot = page.add_rect_annot(ra)
                        rect_annot.set_colors(stroke=(0, 0, 1))
                        rect_annot.set_border(width=0.5)
                        rect_annot.update()

                admin_lines.append(
                    f"[VectorSummaryPlumber][Стр. {page_num}] pdfplumber: {plumber_raw} raw → {plumber_filtered} filtered"
                )
            except Exception as e:
                admin_lines.append(f"[image_checker] plumber vector pass error on page {page_num}: {e}")

        # 2.2 PyMuPDF
        mupdf_raw = 0
        mupdf_filtered = 0
        try:
            drawings = page.get_drawings()
            # получаем bbox для каждого drawing
            bxs = []
            for d in drawings:
                b = drawing_bbox_mupdf(d)
                if b is None:
                    continue
                bxs.append(b)
            mupdf_raw = len(bxs)

            tbl_bboxes = table_bboxes_by_page.get(page_num, [])
            filtered_mu = []
            for b in bxs:
                if any(bboxes_intersect(b, tb, tol=TOLERANCE_PT) for tb in tbl_bboxes):
                    continue
                # приблизительный аналог буллет-фильтра
                if is_bullet_candidate(b, pw):
                    continue
                w = b[2] - b[0]
                h = b[3] - b[1]
                if w < 0.5 and h < 0.5:
                    continue
                filtered_mu.append(b)
            mupdf_filtered = len(filtered_mu)

            # Нарисовать (зелёные)
            if debug_draw:
                for b in filtered_mu:
                    ra = fitz.Rect(*b)
                    rect_annot = page.add_rect_annot(ra)
                    rect_annot.set_colors(stroke=(0, 1, 0))
                    rect_annot.set_border(width=0.5)
                    rect_annot.update()

            admin_lines.append(
                f"[VectorSummaryMuPDF][Стр. {page_num}] PyMuPDF: {mupdf_raw} raw → {mupdf_filtered} filtered"
            )
        except Exception as e:
            admin_lines.append(f"[image_checker] mupdf vector pass error on page {page_num}: {e}")

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
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\nВекторная сводка пуста.")
    )

    if error_pages:
        user_summary = f"⚠️Проверка рисунков: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
    else:
        user_summary = "✅Проверка рисунков (растр) • Вектор — режим сравнения (без проверок)"

    # Закрыть pdfplumber, если открывали
    try:
        if plumber_pages:
            pl.close()
    except Exception:
        pass

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
