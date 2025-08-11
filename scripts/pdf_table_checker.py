# scripts/pdf_table_checker.py
import camelot
import time
import pdfplumber

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT = 2

def check_tables(pdf_path, pdf_document, start_page=2):
    admin_lines = []
    error_pages = set()
    table_bboxes_by_page = {}  # <--- важно

    total_pages = len(pdf_document)

    t0 = time.perf_counter()
    plumber_table_pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                if page_idx < start_page:
                    continue
                hlines = [l for l in page.lines if abs(l['y0'] - l['y1']) < 1]
                vlines = [l for l in page.lines if abs(l['x0'] - l['x1']) < 1]
                rects = page.rects
                curves = page.curves
                total_graphics = len(hlines) + len(vlines) + len(rects) + len(curves)
                if total_graphics >= 10:
                    plumber_table_pages.append(page_idx)
                    admin_lines.append(
                        f"[pdfplumber][Стр. {page_idx}] Графических объектов: {total_graphics} "
                        f"(гор.:{len(hlines)}, верт.:{len(vlines)}, прям.:{len(rects)}, крив.:{len(curves)})"
                    )
    except Exception as e:
        admin_lines.append(f"[pdfplumber] Ошибка: {e}")
    admin_lines.append(f"[pdfplumber] Найдено {len(plumber_table_pages)} страниц с таблицами за {time.perf_counter()-t0:.2f} сек.")

    t1 = time.perf_counter()
    camelot_tables_count = 0
    if plumber_table_pages:
        valid_pages = [p for p in plumber_table_pages if 1 <= p <= total_pages]
        if valid_pages:
            try:
                tables = camelot.read_pdf(pdf_path, flavor="lattice", pages=",".join(map(str, valid_pages)))
                camelot_tables_count = len(tables)
                for t in tables:
                    page_num = int(t.page)
                    page = pdf_document[page_num - 1]
                    page_width, page_height = page.rect.width, page.rect.height
                    x0, y0, x1, y1 = t._bbox

                    # копим bbox таблиц
                    table_bboxes_by_page.setdefault(page_num, []).append((
                        float(x0), float(y0), float(x1), float(y1)
                    ))


                    errors = []
                    if (x0 < LEFT_MARGIN_PT - TOLERANCE_PT or
                        x1 > page_width - RIGHT_MARGIN_PT + TOLERANCE_PT or
                        y0 < TOP_MARGIN_PT - TOLERANCE_PT or
                        y1 > page_height - BOTTOM_MARGIN_PT + TOLERANCE_PT):
                        errors.append("Таблица выходит за пределы полей")

                    work_w = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                    work_cx = LEFT_MARGIN_PT + work_w/2
                    tbl_cx = (x0 + x1)/2
                    if abs(tbl_cx - work_cx) > 2:
                        errors.append("Таблица не по центру относительно полей")

                    msg = f"[Camelot][Стр. {page_num}] bbox={t._bbox}"
                    if errors:
                        msg += " | " + "; ".join(errors)
                        error_pages.add(page_num)
                    else:
                        msg += " | ✅Таблица корректно расположена"
                    admin_lines.append(msg)
            except Exception as e:
                admin_lines.append(f"[Camelot] Ошибка: {e}")
    admin_lines.append(f"[Camelot] Обработано {camelot_tables_count} таблиц за {time.perf_counter()-t1:.2f} сек.")

    user_summary = (f"⚠️ Проверка таблиц: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
                    if error_pages else "✅ Проверка таблиц: нарушений не найдено")

    return {
        "user_summary": user_summary,
        "admin_details": "\n".join(admin_lines),
        "table_bboxes_by_page": table_bboxes_by_page,   # <--- важно
    }
