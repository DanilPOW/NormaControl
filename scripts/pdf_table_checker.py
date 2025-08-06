import camelot
import time
import pdfplumber

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT = 2

def check_tables(pdf_path, pdf_document):
    admin_lines = []
    error_pages = set()
    total_tables = 0
    page_table_dict = {}

    # CAMEL0T
    t_start_camelot = time.perf_counter()
    try:
        tables = camelot.read_pdf(pdf_path, flavor="lattice", pages="all")
        total_tables = len(tables)
        for t in tables:
            page_idx = int(t.page)
            page_table_dict.setdefault(page_idx, 0)
            page_table_dict[page_idx] += 1

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
                admin_lines.append(msg)
                error_pages.add(page_num)
            else:
                msg += " | ✅Таблица корректно расположена"
                admin_lines.append(msg)
        camelot_ok = True
    except Exception as e:
        admin_lines.append(f"[Camelot] Ошибка: {str(e)}")
        camelot_ok = False

    t_end_camelot = time.perf_counter()
    camelot_time = t_end_camelot - t_start_camelot

    # PDFPLUMBER
    t_start_plumber = time.perf_counter()
    plumber_tables_total = 0
    plumber_lines = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                hlines = page.lines
                vlines = [l for l in hlines if abs(l['x0']-l['x1']) < 1]
                hlines = [l for l in hlines if abs(l['y0']-l['y1']) < 1]
                # Простая эвристика: если есть хотя бы 2 горизонтальных и 2 вертикальных линии — вероятно таблица
                if len(hlines) >= 2 and len(vlines) >= 2:
                    plumber_tables_total += 1
                    plumber_lines.append(
                        f"[pdfplumber][Стр. {page_idx+1}]: Найдена таблица ({len(hlines)} гориз. линий, {len(vlines)} верт. линий)"
                    )
    except Exception as e:
        plumber_lines.append(f"[pdfplumber] Ошибка: {str(e)}")

    t_end_plumber = time.perf_counter()
    plumber_time = t_end_plumber - t_start_plumber

    # Формируем финальные логи
    camelot_status = "успешно" if camelot_ok else "с ошибкой"
    admin_details = (
        f"[Camelot] Найдено {total_tables} таблиц ({camelot_status}) за {camelot_time:.2f} сек.\n"
        + "\n".join(admin_lines)
        + "\n\n"
        + f"[pdfplumber] Найдено {plumber_tables_total} страниц с таблицами за {plumber_time:.2f} сек.\n"
        + "\n".join(plumber_lines)
    )

    # Формируем user_summary по Camelot
    if error_pages:
        user_summary = (
            f"⚠️ Проверка таблиц (Camelot): нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
        )
    else:
        user_summary = "✅ Проверка таблиц (Camelot): нарушений не найдено"

    return {"user_summary": user_summary, "admin_details": admin_details}
