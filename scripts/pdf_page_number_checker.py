import fitz

def is_times_new_roman(font):
                return "times" in font.lower()

def get_work_area_center(page_rect, is_landscape):
    width = page_rect.width
    height = page_rect.height
    if is_landscape:
        left = 2 * 28.35
        right = 2 * 28.35
        top = 3 * 28.35
        bottom = 1.5 * 28.35
    else:
        left = 3 * 28.35
        right = 1.5 * 28.35
        top = 2 * 28.35
        bottom = 2 * 28.35
    work_width = width - left - right
    work_height = height - top - bottom
    center_x = left + work_width / 2
    center_y = top + work_height / 2
    return center_x, center_y

def get_bottom_zone_spans(page, height, bottom_zone_mm):
    def mm_to_pt(mm): return mm * 2.834646
    spans_in_bottom = []
    blocks = page.get_text("dict")["blocks"]
    for b in blocks:
        if b["type"] == 0:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    y1 = span["bbox"][3]
                    if (height - y1) <= mm_to_pt(bottom_zone_mm):
                        spans_in_bottom.append(span)
    return spans_in_bottom

def plural_ru(n, forms):
    # forms = ('нарушение', 'нарушения', 'нарушений')
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20:
        return forms[2]
    if 1 < n1 < 5:
        return forms[1]
    if n1 == 1:
        return forms[0]
    return forms[2]


def get_page_number_candidates(page, height, width, bottom_zone_mm):
    def mm_to_pt(mm): return mm * 2.834646
    candidates = []
    # Слова (words)
    '''for x0, y0, x1, y1, text, *rest in page.get_text("words"):
        if text.isdigit() and 1 <= len(text) <= 3:
            if (height - y1) <= mm_to_pt(bottom_zone_mm):
                center_x = (x0 + x1) / 2
                center_dev = abs(center_x - width / 2)
                candidates.append({
                    "text": text,
                    "center_x": center_x,
                    "center_dev": center_dev,
                    "bbox": (x0, y0, x1, y1),
                })'''
    # Спаны (spans)
    blocks = page.get_text("dict")["blocks"]
    for b in blocks:
        if b["type"] == 0:
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    text_clean = text.strip()
                    if text_clean.isdigit() and 1 <= len(text_clean) <= 3:
                        y1 = span["bbox"][3]
                        if (height - y1) <= mm_to_pt(bottom_zone_mm):
                            x0, y0, x1, y1 = span["bbox"]
                            center_x = (x0 + x1) / 2
                            center_x_work, _ = get_work_area_center(page.rect, width > height)
                            center_dev = abs(center_x - center_x_work)
                            candidates.append({
                                    "text": text_clean,
                                    "center_x": center_x,
                                    "center_dev": center_dev,
                                    "bbox": (x0, y0, x1, y1),
                                    "font":       span.get("font", ""),
                                    "size":       span.get("size", 0)
                            })
    return candidates

def check_page_numbering_and_annotate(pdf_document, 
                                      bottom_zone_mm=25, 
                                      center_tolerance_mm=10):
    admin_lines = []
    error_pages = []
    missing_numbers = []
    found_any_number = False
    total_pages = len(pdf_document)

    def mm_to_pt(mm):
        return mm * 2.834646

    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        width = page.rect.width
        height = page.rect.height
        candidates = get_page_number_candidates(page, height, width, bottom_zone_mm)
        admin_lines.append(
            f"[page_{page_num}] Кандидаты: {candidates}"
        )

        issues = []
        expected_num = str(page_num)

        # Титульный лист
        if page_num == 1:
            if candidates:
                issues.append(f"Титульный лист: найден номер {', '.join([c['text'] for c in candidates])}, хотя не должен быть напечатан.")
                for c in candidates:
                    rect = fitz.Rect(*c["bbox"])
                    annotation = page.add_text_annot(
                        rect.tl,
                        "Номер страницы не должен быть на титульном листе"
                    )
                    annotation.set_info(
                        title="Сервис нормоконтроля",
                        content="Номер страницы не допускается на титульном листе"
                    )
                    annotation.update()
            if issues:
                error_pages.append(page_num)
                admin_lines.append(f"page_{page_num}: " + "; ".join(issues))
            continue

        if not candidates:
            missing_numbers.append(page_num)
            continue  # пока не добавляем аннотацию
        else:
            found_any_number = True
            # Берём самый центрированный как номер страницы
            best = min(candidates, key=lambda c: c["center_dev"])
            actual_num = best["text"]
            rect = fitz.Rect(*best["bbox"])
            font_name = best.get("font", "")
            font_size = best.get("size", 0)

            num_issues = []
            
            # Проверка номера
            if actual_num != expected_num:
                num_issues.append(f"Ожидается номер '{expected_num}' (найден '{actual_num}').")
            # Проверка центра
            if best["center_dev"] > mm_to_pt(center_tolerance_mm):
                num_issues.append("Номер не по центру.")

            # Проверка шрифта/размера
            if not is_times_new_roman(font_name) or not (12 <= font_size <= 14.1):
                num_issues.append(f"Шрифт: '{font_name}' {font_size:.1f}pt — требуется Times New Roman 12–14pt.")
            
            if len(candidates) > 1:
                nums = [c["text"] for c in candidates]
                num_issues.append(f"Найдено несколько цифровых блоков: {nums}.")

            # ---- Проверка на пустую строку после номера ----
            spans_in_bottom = get_bottom_zone_spans(page, height, bottom_zone_mm)
            for i, span in enumerate(spans_in_bottom):
                text_clean = span.get("text", "").strip()
                if text_clean == actual_num:
                    if i + 1 < len(spans_in_bottom):
                        next_span = spans_in_bottom[i + 1]
                        if next_span.get("text", "").strip() == "":
                            num_issues.append("После номера страницы обнаружена пустая строка (спан).")
                    break  # Проверяем только первое совпадение номера

        if num_issues:
                issues.extend(num_issues)  # для логов/отчёта
                error_pages.append(page_num)
                admin_lines.append(f"page_{page_num}: " + "; ".join(num_issues))
                ann_text = "Ошибки оформления номера страницы:\n" + "\n".join(num_issues)
                annotation = page.add_text_annot(
                    rect.tl,
                    ann_text
                )
                annotation.set_info(
                    title="Сервис нормоконтроля",
                    content=ann_text
                )
                annotation.update()


    if not found_any_number and missing_numbers:
        error_pages.extend(missing_numbers)
        admin_lines.append("Ни на одной из страниц (кроме титульной) не найден номер страницы.")
        # Аннотация только на второй странице
        page2 = pdf_document[1]
        width2 = page2.rect.width
        height2 = page2.rect.height
        annotation = page2.add_text_annot(
            fitz.Point(width2 / 2 - 50, height2 - mm_to_pt(bottom_zone_mm)),
            "Нет нумерации страниц"
        )
        annotation.set_info(
            title="Сервис нормоконтроля",
            content="Ни на одной из страниц (кроме титульного листа) не обнаружена нумерация страниц по ГОСТ."
        )
        annotation.update()                                    
    # Итоговые сообщения для пользователя
    user_summary = ""
    if error_pages:
        count = len(error_pages)
        user_summary += (
            f"⚠️Проверка нумерации: обнаружено {count} {plural_ru(count, ('нарушение','нарушения','нарушений'))} "
            f"на страницах: {', '.join(map(str, error_pages))}."
        )
        if 1 in error_pages:
            user_summary += "\n⚠️На титульном листе найден номер страницы."
    if not error_pages:
        user_summary = "✅Проверка нумерации"

    admin_details = "\n".join(admin_lines)
    return {"user_summary": user_summary.strip(), "admin_details": admin_details}

