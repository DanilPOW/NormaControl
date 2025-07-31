import fitz

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35

def check_images(pdf_document):
    admin_lines = []
    error_pages = []

    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        page_width, page_height = page.rect.width, page.rect.height
        has_error = False

        # Растровые изображения
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                bbox = block.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = bbox
                errors = []
                # Проверка выхода за поля
                if x0 < LEFT_MARGIN_PT or x1 > page_width - RIGHT_MARGIN_PT or \
                   y0 < TOP_MARGIN_PT or y1 > page_height - BOTTOM_MARGIN_PT:
                    errors.append("Изображение выходит за поля")
                # Проверка центрирования по горизонтали (с точностью до 2pt)
                center = (x0 + x1) / 2
                page_center = page_width / 2
                if abs(center - page_center) > 2:
                    errors.append("Изображение не по центру страницы")

                if errors:
                    has_error = True
                    msg = f"[page_{page_num}] Нарушения у картинки: {'; '.join(errors)}; bbox={bbox}"
                    admin_lines.append(msg)
                    # Аннотация на PDF
                    annot = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errors))
                    annot.set_info(
                        title="Сервис нормоконтроля",
                        content=msg
                    )
                    annot.update()

        if has_error:
            error_pages.append(page_num)

    if error_pages:
        user_summary = f"⚠️ Картинки вне полей или не по центру: страницы {', '.join(map(str, error_pages))}"
    else:
        user_summary = "✅ Нарушений по картинкам не найдено"

    admin_details = "\n".join(admin_lines) if admin_lines else "Нарушений по картинкам не найдено."
    return {"user_summary": user_summary, "admin_details": admin_details}
