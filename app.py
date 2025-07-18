import gradio as gr
import subprocess
import os
import shutil
import uuid

def convert_docx_to_pdf(docx_file_path):
    """Конвертация DOCX в PDF с помощью LibreOffice"""
    if not docx_file_path or not os.path.exists(docx_file_path):
        return "Ошибка: файл не найден"

    # Создаем уникальную временную директорию
    temp_dir = f"/tmp/docx2pdf_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    # Копируем загруженный файл
    input_path = os.path.join(temp_dir, "input.docx")
    shutil.copy(docx_file_path, input_path)

    # Конвертация через LibreOffice
    cmd = [
        "/usr/bin/libreoffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", temp_dir,
        input_path
    ]

    try:
        subprocess.run(cmd, check=True)

        output_pdf = os.path.join(temp_dir, "input.pdf")
        if os.path.exists(output_pdf):
            return output_pdf
        else:
            return "Ошибка: PDF не создан"
    except subprocess.CalledProcessError as e:
        return f"Ошибка конвертации: {e}"

# Интерфейс Gradio
iface = gr.Interface(
    fn=convert_docx_to_pdf,
    inputs=gr.File(label="Загрузите docx файл", file_types=[".docx"]),
    outputs=gr.File(label="Скачать pdf файл"),
    title="Конвертация из DOCX в DPF",
    description="Загрузите docx файл для конвертации в PDF"
)

if __name__ == "__main__":
    iface.launch(server_name="0.0.0.0", server_port=7860)
