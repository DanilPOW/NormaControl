import camelot
import time

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

    t_start = time.perf_counter()

    try:
        tables = camelot.read_pdf(pdf_path, flavor="lattice", pages="all")
        total_tables = len(tables)

        # Считаем кол-во таблиц на каждой странице
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
            # Проверка выхода за поля
            
            if (
                x0 < LEFT_MARGIN_PT - TOLERANCE_PT or
                x1 > page_width - RIGHT_MARGIN_PT + TOLERANCE_PT or
                y0 < TOP_MARGIN_PT - TOLERANCE_PT or
                y1 > page_height - BOTTOM_MARGIN_PT + TOLERANCE_PT
            ):
                errors.append("Таблица выходит за пределы полей")

            # Проверка центрирования по горизонтали
            work_width = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
            work_center = LEFT_MARGIN_PT + work_width / 2
            tbl_center = (x0 + x1) / 2
            if abs(tbl_center - work_center) > 2:
                errors.append("Таблица не по центру относительно полей")

            msg = f"[Стр. {page_num}] bbox={t._bbox}"
            if errors:
                msg += " | " + "; ".join(errors)
                admin_lines.append(msg)
                error_pages.add(page_num)
                # (опционально) page.add_text_annot(fitz.Point(x0, y0), "\n".join(errors))
            else:
                msg += " | ✅Таблица корректно расположена"
                admin_lines.append(msg)

        t_end = time.perf_counter()
        duration = t_end - t_start

        if error_pages:
            user_summary = f"⚠️ Проверка таблиц: обнаружены нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
        else:
            user_summary = "✅ Проверка таблиц: нарушений не найдено"

        page_counts_lines = [
            f"Страница {pg}: таблиц {count}" for pg, count in sorted(page_table_dict.items())
        ]
        counts_summary = (
            f"Найдено {total_tables} таблиц в документе\n" +
            "\n".join(page_counts_lines)
        )

        admin_details = (
            counts_summary +
            ("\n\n" + "\n".join(admin_lines) if admin_lines else "\nНарушений по таблицам не найдено.") +
            f"\n\n⏱ Время анализа таблиц: {duration:.2f} сек."
        )

        return {"user_summary": user_summary, "admin_details": admin_details}
    except Exception as e:
        return {
            "user_summary": "Ошибка при анализе таблиц.",
            "admin_details": f"[Camelot] Ошибка: {str(e)}"
        }
