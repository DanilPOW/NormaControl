import gradio as gr
import os
import tempfile
import time
import fitz  # PyMuPDF
from scripts.tree_analyzer import analyzer
from scripts.pdf_margin_checker import check_margins_and_annotate, MARGIN_PT, MARGINS_CM
from scripts.pdf_handler import PDFHandler
from scripts.pdf_page_number_checker import check_page_numbering_and_annotate
from scripts.double_space_checker import check_double_spaces
from scripts.image_checker import check_images

TEMP_DIR = "/opt/gradio-app/tmp"

def cleanup_old_files(folder: str, max_age_seconds: int = 24*60*60):
    """Удаляет из папки folder все файлы старше max_age_seconds."""
    now = time.time()
    if not os.path.exists(folder):
        os.makedirs(folder)
    removed = 0
    for fn in os.listdir(folder):
        path = os.path.join(folder, fn)
        if os.path.isfile(path):
            if now - os.path.getmtime(path) > max_age_seconds:
                try:
                    os.remove(path)
                    removed += 1
                except:
                    pass
    if removed:
        print(f"🧹 Удалено старых файлов: {removed}")

def reset_form():
    return (
        None,  # pdf_input сбросить
        None,  # pdf_output
        gr.update(visible=False),  # download_btn
        gr.update(visible=False),  # warning_msg
        "",  # user_notes
        "",  # admin_logs
        gr.update(visible=False),  # next_btn
    )

def process_pdf_file(pdf_path: str):
    cleanup_old_files(TEMP_DIR)
    hide_btn = gr.update(visible=False, value=None)
    hide_warn = gr.update(visible=False)

    if not pdf_path:
        return None, hide_btn, hide_warn, "⚠️ Загрузите PDF", "ERROR: нет файла"

    os.makedirs(TEMP_DIR, exist_ok=True)
    ext = os.path.splitext(pdf_path)[1]
    tmp_name = next(tempfile._get_candidate_names()) + ext
    tmp_path = os.path.join(TEMP_DIR, tmp_name)
    with open(pdf_path, "rb") as src, open(tmp_path, "wb") as dst:
        dst.write(src.read())
    basename = os.path.splitext(os.path.basename(pdf_path))[0]

    pdf_doc = fitz.open(tmp_path)
    from datetime import datetime
    now = datetime.now()
    out_filename = f"{basename} Проверено {now.strftime('%d.%m.%Y')} в {now.strftime('%H:%M')}.pdf"
    out_path = os.path.join(TEMP_DIR, out_filename)

    with PDFHandler() as pdf_handler:
        pdf_handler.document = pdf_doc
        pdf_handler.input_path = tmp_path

        analysis = analyzer.analyze_document(pdf_doc, pdf_handler)
        if 'violations' not in analysis:
            error_msg = analysis.get('error_message', 'Ошибка при анализе кавычек.')
            return None, hide_btn, hide_warn, error_msg, error_msg

        viol_count = analysis['violations_count']
        quote_user_message = analyzer._generate_short_user_report(analysis['violations'])
        quote_admin_logs = analyzer._generate_admin_logs(
            analysis['violations'], input_path=tmp_path, output_path=out_path
        )
        
        margins = check_margins_and_annotate(pdf_doc)
        margin_user = margins['user_summary']
        margin_admin = margins['admin_details']

        page_numbers = check_page_numbering_and_annotate(pdf_doc)
        page_num_user = page_numbers['user_summary']
        page_num_admin = page_numbers['admin_details']

        double_spaces = check_double_spaces(pdf_doc)
        double_space_user = double_spaces['user_summary']
        double_space_admin = double_spaces['admin_details']

        image_results = check_images(pdf_doc)
        image_user = image_results['user_summary']
        image_admin = image_results['admin_details']

        pdf_doc.save(out_path)

    user_notes = f"{quote_user_message}\n{margin_user}\n{page_num_user}\n{double_space_user}\n{image_user}"
    admin_logs = (
        quote_admin_logs + "\n\n"
        "[MarginCheck]\n" + margin_admin +
        "\n\n[PageNumbering]\n" + page_num_admin +
        "\n\n[DoubleSpaceCheck]\n" + double_space_admin +
        "\n\n[ImageCheck]\n" + image_admin
    )
    return (
        out_path,
        gr.update(visible=True, value=out_path),
        gr.update(visible=True),
        user_notes,
        admin_logs,
        gr.update(visible=True)
    )
"""
def authenticate_admin(pw: str):
    return gr.update(visible=(pw == os.getenv("ADMIN_PW","secret123")))"""

with gr.Blocks(title="Нормоконтроль", theme=gr.themes.Soft()) as iface:
    gr.Markdown("# 📄 Проверка документов на соответствие ГОСТ 7.32-2017")
    gr.Markdown("Загрузите PDF для проверки документа")

    with gr.Row():
        with gr.Column():
            pdf_input = gr.File(label="Выберите PDF", file_types=[".pdf"], type="filepath")
            check_btn = gr.Button("🔍 Проверить документ", variant="secondary", size="lg")
        with gr.Column():
            pdf_output = gr.File(label="Стандартное скачивание", interactive=True)
            with gr.Row():
                download_btn = gr.DownloadButton(
                    label="📥 Скачать проверенный документ",
                    visible=False,
                    size="lg"
                )
                next_btn = gr.Button("🔄 Проверить следующий документ", visible=False, variant="secondary", size="lg")
            warning_msg = gr.Markdown(
                "⚠️ <span style='color:#E58383;'>Рекомендуем открывать PDF в Adobe Acrobat Reader —<br>в некоторых браузерах комментарии могут отображаться некорректно.</span>",
                visible=False
            )
                
    with gr.Row():
        user_notes = gr.Textbox(
            label="Результат проверки",
            lines=6, interactive=False
        )

    with gr.Row():
        admin_logs = gr.Textbox(
            label="Админ-логи",
            lines=6, interactive=False
        )

    """with gr.Row():
        admin_pw = gr.Textbox(label="Пароль администратора", type="password")
        login_btn = gr.Button("Войти как администратор", variant="secondary")"""

    # привязываем колбэки
    check_btn.click(
        fn=process_pdf_file,
        inputs=[pdf_input],
        outputs=[pdf_output, download_btn, warning_msg, user_notes, admin_logs, next_btn]
    )
    """login_btn.click(
        fn=authenticate_admin,
        inputs=[admin_pw],
        outputs=[admin_logs]
    )"""

    next_btn.click(
    fn=reset_form,
    inputs=[],
    outputs=[
        pdf_input,   # <-- ДОБАВИЛИ!
        pdf_output,
        download_btn,
        warning_msg,
        user_notes,
        admin_logs,
        next_btn,    # <-- И его теперь скрываем
    ]
)

    with gr.Accordion("ℹ️ Информация о проверке", open=False):
        gr.Markdown("""
        - Проверка кавычек
        - Проверка, что элементы не выходят за поля. *Проверка, что поля шире пока не реализована.
        - Проверка нумерации страниц
        - Проверка на двойные пробелы
        
        Формат выходного файла:
        `<ИсходноеИмя>_Проверено_DD.MM.YYYY_в_HH:MM.pdf`
        """)

if __name__ == "__main__":
    iface.launch(server_name="0.0.0.0", server_port=7860, share=False)
