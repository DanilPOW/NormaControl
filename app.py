import gradio as gr
import subprocess
import os

def convert_docx_to_pdf(docx_file):
    """Конвертация DOCX в PDF с помощью LibreOffice"""
    if docx_file is None:
        return None
    
    # Сохранение загруженного файла
    input_path = docx_file.name
    output_dir = "/tmp"
    
    # Конвертация с помощью LibreOffice
    cmd = [
        "libreoffice", 
        "--headless", 
        "--convert-to", "pdf", 
        "--outdir", output_dir,
        input_path
    ]
    
    try:
        subprocess.run(cmd, check=True)
        pdf_filename = os.path.splitext(os.path.basename(input_path))[0] + ".pdf"
        pdf_path = os.path.join(output_dir, pdf_filename)
        return pdf_path
    except subprocess.CalledProcessError as e:
        return f"Ошибка конвертации: {e}"

# Создание интерфейса Gradio
iface = gr.Interface(
    fn=convert_docx_to_pdf,
    inputs=gr.File(label="Загрузите DOCX файл", file_types=[".docx"]),
    outputs=gr.File(label="Скачать PDF"),
    title="DOCX to PDF Converter",
    description="Загрузите DOCX файл для конвертации в PDF"
)

if __name__ == "__main__":
    iface.launch(server_name="0.0.0.0", server_port=7860)
