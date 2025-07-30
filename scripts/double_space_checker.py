import fitz


def log_all_spans(pdf_document):
    logs = []
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        logs.append(f"\n===== Страница {page_num} =====")
        blocks = page.get_text("dict").get("blocks", [])
        for b_idx, b in enumerate(blocks):
            if b.get("type") == 0:
                for l_idx, line in enumerate(b.get("lines", [])):
                    for s_idx, span in enumerate(line.get("spans", [])):
                        text = span.get("text", "")
                        bbox = span.get("bbox", [])
                        font = span.get("font", "")
                        size = span.get("size", "")
                        logs.append(
                            f"[page {page_num}] block {b_idx} line {l_idx} span {s_idx}: "
                            f"text='{text}' bbox={bbox} font='{font}' size={size}"
                        )
    return "\n".join(logs)

def check_double_spaces(pdf_document):
    admin_lines = []
    error_pages = []
    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        has_double_space = False
        blocks = page.get_text("dict").get("blocks", [])
        for b in blocks:
            if b.get("type") == 0:
                for line in b.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join([span.get("text", "") for span in spans])
                    double_idx = line_text.find("  ")
                    if double_idx != -1:
                        has_double_space = True
                        admin_lines.append(f"[page_{page_num}] Двойной пробел в строке: «{line_text}»")
                        # -- Поиск спана, где двойной пробел
                        char_count = 0
                        for span in spans:
                            span_text = span.get("text", "")
                            span_len = len(span_text)
                            if char_count <= double_idx < char_count + span_len:
                                # Нашли спан с двойным пробелом!
                                # Примерно первая координата двойного пробела
                                rel_pos = double_idx - char_count
                                # Возьмём левый край спана (точнее нельзя без fonttools, но обычно ок)
                                x, y, _, _ = span["bbox"]
                                annotation = page.add_text_annot(
                                    fitz.Point(x, y),
                                    "Двойной пробел"
                                )
                                annotation.set_info(
                                    title="Сервис нормоконтроля",
                                    content=f"В этом месте строки найден двойной пробел: «{line_text.strip()}»"
                                )
                                annotation.update()
                                break
                            char_count += span_len
        if has_double_space:
            error_pages.append(page_num)

    spans_log = log_all_spans(pdf_document)
    if error_pages:
        user_summary = (
            f"⚠️Обнаружены двойные пробелы на страницах: {', '.join(map(str, error_pages))}."
        )
    else:
        user_summary = "✅Двойные пробелы не обнаружены"
    admin_details = ("\n".join(admin_lines) if admin_lines else "Двойных пробелов не найдено.") + \
                    "\n\n==== Все спаны ====\n" + spans_log
    return {"user_summary": user_summary, "admin_details": admin_details}
