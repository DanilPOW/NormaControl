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

    total_pages = len(pdf_document)  # всего страниц в PDF

    # ЭТАП 1 — Быстрый поиск страниц с таблицами через pdfplumber
    t_start_plumber = time.perf_counter()
    plumber_table_pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                if page_idx < start_page:  # пропускаем первые страницы
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
                        f"(гор. линии: {len(hlines)}, верт. линии: {len(vlines)}, "
                        f"прямоугольники: {len(rects)}, кривые: {len(curves)})"
                    )
    except Exception as e:
        admin_lines.append(f"[pdfplumber] Ошибка: {str(e)}")

    t_end_plumber = time.perf_counter()
    admin_lines.append(
        f"[pdfplumber] Найдено {len(plumber_table_pages)} страниц с таблицами за {t_end_plumber - t_start_plumber:.2f} сек."
    )

    # ЭТАП 2 — Camelot только на найденных страницах
    camelot_tables_count = 0
    t_start_camelot = time.perf_counter()
    if plumber_table_pages:
        # гарантируем, что страницы существуют
        valid_pages = [p for p in plumber_table_pages if 1 <= p <= total_pages]
        if valid_pages:
            pages_str = ",".join(map(str, valid_pages))
            try:
                tables = camelot.read_pdf(pdf_path, flavor="lattice", pages=pages_str)
                camelot_tables_count = len(tables)

                for t in tables:
                    page_num = int(t.page)
                    page = pdf_document[page_num - 1]
                    page_width, page_height = page.rect.width, page.rect.height
                    x0, y0, x1, y1 = t._bbox

                    errors = []
                    if (
                        x0 < LEFT_MARGIN_PT - TOLERANCE_PT or
                        x1 > page_width - RIGHT_MARGIN_PT + TOLERANCE_PT or
                        y0 < TOP_MARGIN_PT - TOLERANCE_PT or
                        y1 > page_height - BOTTOM_MARGIN_PT + TOLERANCE_PT
                    ):
                        errors.append("Таблица выходит за пределы полей")

                    work_width = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                    work_center = LEFT_MARGIN_PT + work_width / 2
                    tbl_center = (x0 + x1) / 2
                    if abs(tbl_center - work_center) > 2:
                        errors.append("Таблица не по центру относительно полей")

                    msg = f"[Camelot][Стр. {page_num}] bbox={t._bbox}"
                    if errors:
                        msg += " | " + "; ".join(errors)
                        error_pages.add(page_num)
                    else:
                        msg += " | ✅Таблица корректно расположена"
                    admin_lines.append(msg)
            except Exception as e:
                admin_lines.append(f"[Camelot] Ошибка: {str(e)}")
    t_end_camelot = time.perf_counter()
    admin_lines.append(f"[Camelot] Обработано {camelot_tables_count} таблиц за {t_end_camelot - t_start_camelot:.2f} сек.")

    # Итог для пользователя
    if error_pages:
        user_summary = f"⚠️ Проверка таблиц: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
    else:
        user_summary = "✅ Проверка таблиц: нарушений не найдено"

    return {
        "user_summary": user_summary,
        "admin_details": "\n".join(admin_lines)
    }
