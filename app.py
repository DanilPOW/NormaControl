import gradio as gr
import subprocess
import os
import shutil
import uuid

def convert_docx_to_pdf(docx_file):
    """Конвертация DOCX в PDF с помощью LibreOffice"""
    if docx_file is None:
        return None

    # Уникальная директория для каждого запуска
    temp_dir = f"/tmp/docx2pdf_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    # Сохраняем загруженный DOCX в temp-файл
    input_path = os.path.join(temp_dir, "input.docx")
    with open(input_path, "wb") as f:
        f.write(docx_file.read())

    # Команда LibreOffice для конвертации
    cmd = [
        "libreoffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", temp_dir,
        input_path
    ]

    try:
        subprocess.run(cmd, check=True)

        # Получаем путь к сконвертированному PDF
        pdf_path = os.path.join(temp_dir, "input.pdf")
        if not os.path.exists(pdf_path):
            return "Ошибка: PDF не создан"

        return pdf_path
    except subprocess.CalledProcessError as e:
        return f"Ошибка конвертации: {e}"
    finally:
        # Удалить временные файлы позже, если нужно (опционально)
        pass

# Интерфейс Gradio
iface = gr.Interface(
    fn=convert_docx_to_pdf,
    inputs=gr.File(label="Загрузите DOCX файл", file_types=[".docx"]),
    outputs=gr.File(label="Скачать PDF"),
    title="DOCX to PDF Converter",
    description="Загрузите DOCX файл для конвертации в PDF"
)

if __name__ == "__main__":
    iface.launch(server_name="0.0.0.0", server_port=7860)
