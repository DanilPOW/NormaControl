import fitz

MARGINS_CM = {'left': 3, 'right': 1.5, 'top': 2, 'bottom': 2}
MARGIN_PT = {k: v * 28.35 for k, v in MARGINS_CM.items()}

def check_margins_and_annotate(pdf_document, margin_pt=MARGIN_PT, margin_cm=MARGINS_CM, tolerance=3):
    page = pdf_document[0]
    page_rect = page.rect
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
        return {"user_summary": "❌ Нет содержимого для анализа полей.", "admin_details": ""}
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
        # Для правого поля: ошибка только если поле меньше нормы!
        if k in ["right", "bottom"]:
            ok = v >= required - tolerance  # только "меньше нормы" — ошибка
        else:
            ok = abs(v - required) <= tolerance  # для остальных как раньше (можно добавить отдельную логику)
        verdict[k] = {
            "actual_cm": round(v / 28.35, 2),
            "required_cm": margin_cm[k],
            "ok": ok
        }

    user_lines = []
    admin_lines = []
    error_lines = []
    for side, info in verdict.items():
        mark = "" if info["ok"] else ""
        user_lines.append(f"{side.title()}: {info['actual_cm']} см (норма: {info['required_cm']} см) — {mark}")
        admin_lines.append(f"{side}: {info['actual_cm']} см (норма: {info['required_cm']} см) — {'OK' if info['ok'] else 'FAIL'}")
        if not info["ok"]:
            error_lines.append(f"{side.title()}: {info['actual_cm']} см (норма: {info['required_cm']} см)")

    if error_lines:
        page.add_text_annot(
            fitz.Point(page_rect.x0 + 40, page_rect.y0 + 40),
            "Нарушены требования к полям документа по ГОСТ 7.32-2017:\n" + "\n".join(error_lines)
        )
        user_summary = "Нарушены поля ГОСТ 7.32-2017.\n" + "\n".join(user_lines)
    else:
        user_summary = "Все поля соответствуют ГОСТ 7.32-2017.\n" + "\n".join(user_lines)

    admin_details = "\n".join(admin_lines)
    return {"user_summary": user_summary, "admin_details": admin_details}
