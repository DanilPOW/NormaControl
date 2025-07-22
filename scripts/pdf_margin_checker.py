import fitz

MARGINS_CM = {'left': 3, 'right': 1.5, 'top': 2, 'bottom': 2}
MARGIN_PT = {k: v * 28.35 for k, v in MARGINS_CM.items()}

def check_margins_and_annotate(pdf_document, margin_pt=MARGIN_PT, margin_cm=MARGINS_CM, tolerance=3):
    user_lines = []
    admin_lines = []
    error_lines = []
    landscape_pages = []
    margins_errors = {}

    for page_num, page in enumerate(pdf_document, 1):
        page_rect = page.rect

        # Проверка ориентации
        if page_rect.width > page_rect.height:
            landscape_pages.append(page_num)
            page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 40),
                "❌ Ошибка: Страница альбомной ориентации (ГОСТ 7.32-2017 требует вертикальной ориентации)."
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
            user_lines.append(f"Страница {page_num}: ❌ Нет содержимого для анализа полей.")
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
        for k, v in zip(['left', 'right', 'top', 'bottom'], [left, right, top, bottom]):
            required = margin_pt[k]
            # Для правого и нижнего поля: ошибка только если поле меньше нормы!
            if k in ["right", "bottom"]:
                ok = v >= required - tolerance
            else:
                ok = abs(v - required) <= tolerance
            verdict[k] = {
                "actual_cm": round(v / 28.35, 2),
                "required_cm": margin_cm[k],
                "ok": ok
            }

        error_pages = set()
        # Сводим результаты по странице
        error_this_page = []
        for side, info in verdict.items():
            mark = "✅" if info["ok"] else "❌"
            '''user_lines.append(
                f"Страница {page_num}, {side.title()}: {info['actual_cm']} см (норма: {info['required_cm']} см) — {mark}"
            )'''
            admin_lines.append(
                f"page_{page_num}, {side}: {info['actual_cm']} см (норма: {info['required_cm']} см) — {'OK' if info['ok'] else 'FAIL'}"
            )
            if not info["ok"]:
                error_this_page.append(f"{side.title()}: {info['actual_cm']} см (норма: {info['required_cm']} см)")

        # Если на странице есть ошибки — аннотируем
        if error_this_page:
            page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 80),
                "❗ Нарушены требования к полям:\n" + "\n".join(error_this_page)
            )
            error_lines.append(f"Стр. {page_num}: " + "; ".join(error_this_page))
            error_pages.add(page_num)

    # Формируем итоговые сообщения
    user_summary = ""
    admin_details = ""
    if landscape_pages:
        user_summary += (
            f"❌ В документе обнаружены альбомные (горизонтальные) страницы: {landscape_pages}.\n"
            "ГОСТ 7.32-2017 требует вертикальной (книжной) ориентации!\n"
        )
        admin_details += f"Альбомные страницы: {landscape_pages}\n"

    if error_lines:
        user_summary += (
            "❗ В документе нарушены требования к полям ГОСТ 7.32-2017:\n"
            + "\n".join(error_lines) + "\n"
        )
    else:
        user_summary += "✅ Все поля на всех страницах соответствуют ГОСТ 7.32-2017.\n"
    #user_summary += "\n".join(user_lines)

    admin_details += "\n".join(admin_lines)
    return {"user_summary": user_summary, "admin_details": admin_details}
