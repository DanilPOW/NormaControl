import pytest
import fitz
from scripts.image_checker import check_images

def test_image_centered():
    pdf_path = "tests/test_image_iscenter.pdf"  # путь к тестовому PDF
    doc = fitz.open(pdf_path)
    result = check_images(doc, pdf_path)

    print(result["user_summary"])
    print(result["admin_details"])

    # Тест пройден, если хотя бы один объект центрирован (или нет ошибок)
    assert "✅" in result["user_summary"], "Картинки не центрированы или есть другие нарушения"
