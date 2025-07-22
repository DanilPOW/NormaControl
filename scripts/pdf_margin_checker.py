import fitz

MARGINS_CM = {'left': 3, 'right': 1.5, 'top': 2, 'bottom': 2}
MARGIN_PT = {k: v * 28.35 for k, v in MARGINS_CM.items()}

def check_margins_and_annotate(pdf_document, margin_pt=MARGIN_PT, margin_cm=MARGINS_CM, tolerance=3):
    admin_lines = []
    landscape_pages = []
    error_pages = set()

    for page_num, page in enumerate(pdf_document, 1):
        page_rect = page.rect

        # Проверка ориентации
        if page_rect.width > page_rect.height:
            landscape_pages.append(page_num)
            page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 40),
                "Неверная ориентация страницы"
            )

        # Проверка содержимого
        blocks = page.get_text("dict")["blocks"]
        content_rects = []
        for b in blocks:
            if b["type"] == 0:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        bbox = span["bbox"]
                        content_rects.append(fitz.Rect(bbox))
            elif b["type"] == 1:
                content_rects.append(fitz.Rect(b["bbox"]))

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
            page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 80),
                "Поля оформлены неверно"
            )

    # Формируем итоговые сообщения для пользователя
    user_summary = ""
    if landscape_pages:
        user_summary += (
            f"В документе обнаружены альбомные (горизонтальные) страницы: {landscape_pages}.\n"
        )
    if error_pages:
        user_summary += (
            f"В документе нарушены требования к полям ГОСТ 7.32-2017 на страницах: {sorted(error_pages)}.\n"
        )
    if not error_pages and not landscape_pages:
        user_summary += "Все поля на всех страницах соответствуют ГОСТ 7.32-2017.\n"

    admin_details = "\n".join(admin_lines)
    return {"user_summary": user_summary.strip(), "admin_details": admin_details}
