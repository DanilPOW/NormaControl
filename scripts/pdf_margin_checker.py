import fitz
import time

MARGINS_CM = {'left': 3, 'right': 1.5, 'top': 2, 'bottom': 2}
MARGIN_PT = {k: v * 28.35 for k, v in MARGINS_CM.items()}

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


def check_margins_and_annotate(pdf_document, margin_pt=MARGIN_PT, margin_cm=MARGINS_CM, tolerance=3):
    admin_lines = []
    landscape_pages = []
    error_pages = set()

    def mm_to_pt(mm):
        return mm * 2.834646

    BOTTOM_ZONE_MM = 25  # Нижняя зона для поиска номеров страниц
    
    total_start = time.perf_counter()

    for page_num, page in enumerate(pdf_document, 1):
        page_rect = page.rect

        # Проверка ориентации
        if page_rect.width > page_rect.height:
            landscape_pages.append(page_num)
            comment = page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 40),
                "Неверная ориентация страницы"
            )
            comment.set_info(
                title="Сервис нормоконтроля",
                content="Неверная ориентация страницы"
                        )
            comment.update()

        words = page.get_text("words")
        number_bboxes = []
        height = page_rect.height
        for x0, y0, x1, y1, text, *rest in words:
            if text.isdigit() and 1 <= len(text) <= 3:
                if (height - y1) <= mm_to_pt(BOTTOM_ZONE_MM):
                    number_bboxes.append(fitz.Rect(x0, y0, x1, y1))
        
        # Проверка содержимого
        blocks = page.get_text("dict")["blocks"]
        content_rects = []
        for b in blocks:
            if b["type"] == 0:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        bbox = fitz.Rect(span["bbox"])
                        # Пропустить, если bbox пересекается с номером страницы
                        if any(bbox.intersects(nb) for nb in number_bboxes):
                            continue
                        content_rects.append(bbox)
            elif b["type"] == 1:
                bbox = fitz.Rect(b["bbox"])
                if any(bbox.intersects(nb) for nb in number_bboxes):
                    continue
                content_rects.append(bbox)

        if not content_rects:
            admin_lines.append(f"page_{page_num}: Нет содержимого для анализа.")
            continue

        union = fitz.Rect(content_rects[0])
        for r in content_rects[1:]:
            union |= r
        left = union.x0 - page_rect.x0
        right = page_rect.x1 - union.x1
        top = union.y0 - page_rect.y0
        bottom = page_rect.y1 - union.y1

        verdict = {}
        has_error = False
        for k, v in zip(['left', 'right', 'top', 'bottom'], [left, right, top, bottom]):
            required = margin_pt[k]
            if k in ["right", "bottom"]:
                ok = v >= required - tolerance
            else:
                ok = abs(v - required) <= tolerance
            verdict[k] = {
                "actual_cm": round(v / 28.35, 2),
                "required_cm": margin_cm[k],
                "ok": ok
            }
            # Подробные логи только для админа
            admin_lines.append(
                f"page_{page_num}, {k}: {verdict[k]['actual_cm']} см (норма: {verdict[k]['required_cm']} см) — {'OK' if verdict[k]['ok'] else 'FAIL'}"
            )
            if not ok:
                has_error = True

        if has_error:
            error_pages.add(page_num)
            # Аннотация только для PDF, для пользователя не отображается текстом
            annotation = page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 80),
                "Поля оформлены неверно"
            )
            annotation.set_info(
                title="Сервис нормоконтроля",
                content="Поля оформлены неверно"
                        )
            annotation.update()
    
    total_elapsed = time.perf_counter() - total_start  # конец проверки
    admin_lines.append(f" Проверка всех полей заняла: {total_elapsed:.3f} сек.")
    
    # Формируем итоговые сообщения для пользователя
    user_summary = ""
    if error_pages:
        count = len(error_pages)
        user_summary += (
            f"⚠️Проверка полей: обнаружено {count} {plural_ru(count, ('нарушение','нарушения','нарушений'))} "
            f"на страницах: {', '.join(map(str, sorted(error_pages)))}.\n"
        )
    if landscape_pages:
        user_summary += (
            f"⚠️В документе обнаружены горизонтальные страницы: {', '.join(map(str, landscape_pages))}."
        )
    if not error_pages and not landscape_pages:
        user_summary += "✅Проверка полей"

    admin_details = "\n".join(admin_lines)
    return {"user_summary": user_summary.strip(), "admin_details": admin_details}
