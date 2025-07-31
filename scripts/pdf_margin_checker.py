import fitz
import time

MARGINS_CM = {'left': 3, 'right': 1.5, 'top': 2, 'bottom': 2}
MARGIN_PT = {k: v * 28.35 for k, v in MARGINS_CM.items()}

def plural_ru(n, forms):
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
    landscape_pages_ok = []
    landscape_pages_error = []
    error_pages = set()

    def mm_to_pt(mm):
        return mm * 2.834646

    BOTTOM_ZONE_MM = 25  # Нижняя зона для поиска номеров страниц

    total_start = time.perf_counter()

    for page_num, page in enumerate(pdf_document, 1):
        page_rect = page.rect
        height = page_rect.height

        is_landscape = page_rect.width > page_rect.height

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

        number_bboxes = []
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        bbox = fitz.Rect(span["bbox"])
                        y1 = bbox.y1
                        if (height - y1) <= mm_to_pt(BOTTOM_ZONE_MM):
                            number_bboxes.append(bbox)
            elif b["type"] == 1:
                bbox = fitz.Rect(b["bbox"])
                y1 = bbox.y1
                if (height - y1) <= mm_to_pt(BOTTOM_ZONE_MM):
                    number_bboxes.append(bbox)

        # Новый фильтр: только видимые (непустые и не пробельные) спаны!
        content_rects = []
        for b in blocks:
            if b["type"] == 0:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue  # Пропускаем пустые и пробельные спаны
                        bbox = fitz.Rect(span["bbox"])
                        # Пропускаем, если bbox ниже 20 мм
                        if any(abs(bbox.y0 - nb.y0) < 1 and abs(bbox.y1 - nb.y1) < 1 for nb in number_bboxes):
                            continue
                        content_rects.append(bbox)
            elif b["type"] == 1:
                bbox = fitz.Rect(b["bbox"])
                if any(abs(bbox.y0 - nb.y0) < 1 and abs(bbox.y1 - nb.y1) < 1 for nb in number_bboxes):
                    continue
                content_rects.append(bbox)

        if not content_rects:
            continue  # Нет контента, пропускаем

        union = fitz.Rect(content_rects[0])
        for r in content_rects[1:]:
            union |= r
        left = union.x0 - page_rect.x0
        right = page_rect.x1 - union.x1
        top = union.y0 - page_rect.y0
        bottom = page_rect.y1 - union.y1

        if is_landscape:
            visual_fields = {
                'left': top,
                'right': bottom,
                'top': right,
                'bottom': left
            }
        else:
            visual_fields = {
                'left': left,
                'right': right,
                'top': top,
                'bottom': bottom
            }

        verdict = {}
        has_error = False
        for k in ['left', 'right', 'top', 'bottom']:
            v = visual_fields[k]
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
            admin_lines.append(
                f"page_{page_num}, {k}: {verdict[k]['actual_cm']} см (норма: {verdict[k]['required_cm']} см) — {'OK' if verdict[k]['ok'] else 'FAIL'}"
            )
            if not ok:
                has_error = True

        if has_error:
            error_pages.add(page_num)
            annotation = page.add_text_annot(
                fitz.Point(page_rect.x0 + 40, page_rect.y0 + 80),
                "Поля оформлены неверно"
            )
            annotation.set_info(
                title="Сервис нормоконтроля",
                content="Поля оформлены неверно"
            )
            annotation.update()
        if is_landscape:
            if has_error:
                landscape_pages_error.append(page_num)
            else:
                landscape_pages_ok.append(page_num)

    total_elapsed = time.perf_counter() - total_start
    admin_lines.append(f" Проверка всех полей заняла: {total_elapsed:.3f} сек.")

    user_summary = ""
    if error_pages:
        count = len(error_pages)
        user_summary += (
            f"⚠️Проверка полей: обнаружено {count} {plural_ru(count, ('нарушение','нарушения','нарушений'))} "
            f"на страницах: {', '.join(map(str, sorted(error_pages)))}.\n"
        )
    if landscape_pages:
        msg = f"⚠️В документе обнаружены горизонтальные страницы: {', '.join(map(str, landscape_pages))}."
        if landscape_pages_ok and not landscape_pages_error:
            msg += " Поля соответствуют ГОСТ."
        elif landscape_pages_error:
            msg += " Обнаружены ошибки полей на страницах: " + ", ".join(map(str, landscape_pages_error)) + "."
        user_summary += msg
    if not error_pages and not landscape_pages:
        user_summary += "✅Проверка полей"

    admin_details = "\n".join(admin_lines)
    return {"user_summary": user_summary.strip(), "admin_details": admin_details}
