import fitz

def check_margins_and_annotate(pdf_document, margin_pt, margin_cm, tolerance=3):
    """
    Проверяет и аннотирует, если есть нарушения в полях на первой странице.
    Возвращает текстовый отчёт.
    """
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
        return "Нет содержимого для анализа полей."
    union = fitz.Rect(content_rects[0])
    for r in content_rects[1:]:
        union |= r
    left = union.x0 - page_rect.x0
    right = page_rect.x1 - union.x1
    top = union.y0 - page_rect.y0
    bottom = page_rect.y1 - union.y1
    verdicts = []
    errors = []
    for k, v in zip(['left', 'right', 'top', 'bottom'], [left, right, top, bottom]):
        required = margin_pt[k]
        actual_cm = round(v / 28.35, 2)
        ok = abs(v - required) <= tolerance
        verdicts.append(f"{k.title()}: {actual_cm} см (норма: {margin_cm[k]} см) — {'✅' if ok else '❌'}")
        if not ok:
            errors.append(f"Проверьте {k} поле: {actual_cm} см вместо {margin_cm[k]} см")
    if errors:
        # Оставляем аннотацию в верхнем левом углу первой страницы
        page.add_text_annot(
            fitz.Point(page_rect.x0 + 40, page_rect.y0 + 40),
            "❗ Нарушены требования к полям документа по ГОСТ 7.32-2017:\n" + "\n".join(errors)
        )
        verdicts.append("Нарушены поля ГОСТ 7.32-2017.")
    else:
        verdicts.append("Все поля соответствуют ГОСТ 7.32-2017.")
    return "\n".join(verdicts)
