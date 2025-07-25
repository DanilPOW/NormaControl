import fitz

def is_times_new_roman(font):
                return "times" in font.lower()

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
                    if text.isdigit() and 1 <= len(text) <= 3:
                        y1 = span["bbox"][3]
                        if (height - y1) <= mm_to_pt(bottom_zone_mm):
                            x0, y0, x1, y1 = span["bbox"]
                            center_x = (x0 + x1) / 2
                            center_dev = abs(center_x - width / 2)
                            candidates.append({
                                    "text": text,
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
    """
    Проверяет номера страниц по ГОСТ 7.32-2017 и оставляет аннотации на страницах с нарушениями.
    Возвращает user_summary, admin_details.
    """
    admin_lines = []
    error_pages = []
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
                        content="ГОСТ: Номер страницы не допускается на титульном листе"
                    )
                    annotation.update()
            if issues:
                error_pages.append(page_num)
                admin_lines.append(f"page_{page_num}: " + "; ".join(issues))
            continue

        expected_num = str(page_num)

        if not candidates:
            issues.append("Номер страницы не найден в нижней зоне.")
            # Аннотация внизу страницы (справа)
            annotation = page.add_text_annot(
                fitz.Point(width - 100, height - mm_to_pt(bottom_zone_mm)),
                "Не найден номер страницы в нижней части"
            )
            annotation.set_info(
                title="Сервис нормоконтроля",
                content="ГОСТ: Внизу страницы должен быть номер"
            )
            annotation.update()
        else:
            # Берём самый центрированный
            best = min(candidates, key=lambda c: c["center_dev"])
            actual_num = best["text"]
            rect = fitz.Rect(*best["bbox"])
            font_name = best.get("font", "")
            font_size = best.get("size", 0)

            if actual_num != expected_num:
                issues.append(f"Найден номер '{actual_num}', ожидается '{expected_num}'.")
                annotation = page.add_text_annot(
                    rect.tl,
                    f"Ожидается номер '{expected_num}'"
                )
                annotation.set_info(
                    title="Сервис нормоконтроля",
                    content=f"ГОСТ: Номер страницы должен быть '{expected_num}'"
                )
                annotation.update()

            if best["center_dev"] > mm_to_pt(center_tolerance_mm):
                issues.append(f"Номер '{actual_num}' не по центру (отклонение {best['center_dev']:.1f} pt).")
                annotation = page.add_text_annot(
                    rect.tl,
                    "Номер страницы не по центру"
                )
                annotation.set_info(
                    title="Сервис нормоконтроля",
                    content="ГОСТ: Номер страницы должен быть строго по центру"
                )
                annotation.update()
            
            # === НОВОЕ: проверка шрифта/размера ===
            if not is_times_new_roman(font_name) or not (12 <= font_size <= 14):
                issues.append(f"Шрифт '{font_name}' {font_size:.1f}pt — ожидается Times New Roman 12–14pt.")
                annot = page.add_text_annot(
                    rect.tl,
                    "Шрифт номера страницы должен быть Times New Roman 12–14pt"
                )
                if annot:
                    annot.set_info(
                        title="Сервис нормоконтроля",
                        content="ГОСТ: Шрифт номера страницы Times New Roman, размер 12–14pt"
                    )
                    annot.update()
            
            if len(candidates) > 1:
                nums = [c["text"] for c in candidates]
                issues.append(f"Найдено несколько цифровых блоков {nums} внизу, возможна ошибка вёрстки.")

        if issues:
            error_pages.append(page_num)
            admin_lines.append(f"page_{page_num}: " + "; ".join(issues))

    # Итоговые сообщения для пользователя
    user_summary = ""
    if error_pages:
        count = len(error_pages)
        user_summary += (
            f"⚠️Проверка нумерации: обнаружено {count} {plural_ru(count, ('нарушение','нарушения','нарушений'))} "
            f"на страницах: {', '.join(map(str, error_pages))}."
        )
        if 1 in error_pages:
            user_summary += "\n⚠️На титульном листе найден номер страницы — по ГОСТ номер не должен отображаться на титульнике."
    if not error_pages:
        user_summary = "✅Проверка нумерации"

    admin_details = "\n".join(admin_lines)
    return {"user_summary": user_summary.strip(), "admin_details": admin_details}
