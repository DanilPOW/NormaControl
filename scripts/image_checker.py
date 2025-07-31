import fitz

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35

def check_images(pdf_document):
    admin_lines = []
    error_pages = []
    total_images = 0
    page_image_counts = []  # [(page_num, count)]

    for idx, page in enumerate(pdf_document):
        page_num = idx + 1
        page_width, page_height = page.rect.width, page.rect.height
        has_error = False

        image_count = 0

        # Проверка только растровых изображений
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") == 1:
                image_count += 1
                bbox = block.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = bbox
                errors = []
                # Проверка выхода за поля
                if x0 < LEFT_MARGIN_PT or x1 > page_width - RIGHT_MARGIN_PT or \
                   y0 < TOP_MARGIN_PT or y1 > page_height - BOTTOM_MARGIN_PT:
                    errors.append("Изображение выходит за поля")
                # Проверка центрирования по горизонтали относительно полей (точность 2pt)
                work_width = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                work_center = LEFT_MARGIN_PT + work_width / 2
                img_center = (x0 + x1) / 2
                if abs(img_center - work_center) > 2:
                    errors.append("Изображение не по центру относительно полей")
                if errors:
                    has_error = True
                    msg = f"[page_{page_num}] Нарушения у картинки: {'; '.join(errors)}; bbox={bbox}"
                    admin_lines.append(msg)
                    annot = page.add_text_annot(fitz.Point(x0, y0), "\n".join(errors))
                    annot.set_info(
                        title="Сервис нормоконтроля",
                        content=msg
                    )
                    annot.update()

        total_images += image_count
        page_image_counts.append((page_num, image_count))
        if has_error:
            error_pages.append(page_num)

    counts_lines = [f"Страница {num}: картинок {count}" for num, count in page_image_counts]
    counts_summary = (
        f"Найдено {total_images} картинок в документе\n" +
        "\n".join(counts_lines)
    )
    admin_details = (
        counts_summary +
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\nНарушений по картинкам не найдено.")
    )
    
    if error_pages:
        user_summary = f"⚠️ Картинки вне полей или не по центру: страницы {', '.join(map(str, error_pages))}"
    else:
        user_summary = "✅ Нарушений по картинкам не найдено"
    
    return {"user_summary": user_summary, "admin_details": admin_details}
