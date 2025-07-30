import fitz

def check_double_spaces(pdf_document):
    admin_lines = []
    error_pages = []
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        has_double_space = False
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0:
                for line in b.get("lines", []):
                    line_text = "".join([span.get("text", "") for span in line.get("spans", [])])
                    if "  " in line_text:
                        has_double_space = True
                        admin_lines.append(f"[page_{page_num}] Двойной пробел в строке: «{line_text}»")
        if has_double_space:
            error_pages.append(page_num)
            # Аннотируем первую строку с двойным пробелом
            for b in blocks:
                if b["type"] == 0:
                    for line in b.get("lines", []):
                        line_text = "".join([span.get("text", "") for span in line.get("spans", [])])
                        if "  " in line_text:
                            x0, y0, x1, y1 = line["bbox"]
                            annotation = page.add_text_annot(
                                fitz.Point(x0, y0),
                                "Двойной пробел обнаружен"
                            )
                            annotation.set_info(
                                title="Сервис нормоконтроля",
                                content=f"В строке найден двойной пробел: «{line_text.strip()}»"
                            )
                            annotation.update()
                            break
                    break

    if error_pages:
        user_summary = (
            f"⚠️Обнаружены двойные пробелы на страницах: {', '.join(map(str, error_pages))}."
        )
    else:
        user_summary = "✅Двойные пробелы не обнаружены"
    admin_details = "\n".join(admin_lines) if admin_lines else "Двойных пробелов не найдено."
    return {"user_summary": user_summary, "admin_details": admin_details}
