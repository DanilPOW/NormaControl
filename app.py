import gradio as gr
import os
import tempfile
import time
import fitz  # PyMuPDF
from scripts.tree_analyzer import analyzer
from scripts.pdf_margin_checker import check_margins_and_annotate, MARGIN_PT, MARGINS_CM

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

def process_pdf_file(pdf_path: str):
    """
    1) Копирует загруженный pdf в наш TEMP_DIR
    2) Открывает pdf один раз и передает в оба анализатора
    3) Возвращает tuple для Gradio: (gr.File, download_btn, warning_msg, user_notes, admin_logs)
    """
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

    # ОТКРЫВАЕМ PDF ОДИН РАЗ
    pdf_doc = fitz.open(tmp_path)

    # --- АНАЛИЗ КАВЫЧЕК ---
    # ВАЖНО: analyze_document должен работать с pdf_doc!
    # Если твой analyzer.process_pdf_file открывает pdf внутри, нужно переделать
    # analyzer.analyze_document(pdf_doc, pdf_handler) — как у тебя реализовано

    try:
        from scripts.pdf_handler import PDFHandler  # Импортируем здесь, чтобы не было циклических зависимостей
        with PDFHandler() as pdf_handler:
            quote_result = analyzer.analyze_document(pdf_doc, pdf_handler)
    except Exception as e:
        pdf_doc.close()
        return None, hide_btn, hide_warn, f"Ошибка анализа кавычек: {e}", str(e)

    # --- АНАЛИЗ ПОЛЕЙ (и аннотирование, если надо) ---
    try:
        margin_report = check_margins_and_annotate(pdf_doc, MARGIN_PT, MARGINS_CM)
    except Exception as e:
        margin_report = f"Ошибка проверки полей: {e}"

    # --- СОХРАНЯЕМ ФИНАЛЬНЫЙ PDF ---
    from datetime import datetime
    now = datetime.now()
    out_filename = f"{basename}_Проверено_{now.strftime('%d.%m.%Y')}_в_{now.strftime('%H:%M')}.pdf"
    out_path = os.path.join(TEMP_DIR, out_filename)
    try:
        pdf_doc.save(out_path)
    except Exception as e:
        pdf_doc.close()
        return None, hide_btn, hide_warn, f"Ошибка сохранения файла: {e}", str(e)
    pdf_doc.close()

    # --- Сбор текста для пользователя и админа ---
    user_notes = (quote_result.get("user_message", "") or "") + "\n\n" + margin_report
    admin_logs = (quote_result.get("admin_logs", "") or "") + "\n\n[MarginCheck]\n" + margin_report

    return (
        out_path,
        gr.update(visible=True, value=out_path),
        gr.update(visible=True),
        user_notes,
        admin_logs
    )

def authenticate_admin(pw: str):
    return gr.update(visible=(pw == os.getenv("ADMIN_PW","secret123")))

with gr.Blocks(title="Анализатор документов на соответствие ГОСТ 7.32-2017", theme=gr.themes.Soft()) as iface:
    gr.Markdown("# 📄 Анализатор документов на соответствие ГОСТ 7.32-2017")
    gr.Markdown("Загрузите PDF для проверки кавычек, полей и автоматической аннотации")

    with gr.Row():
        with gr.Column():
            pdf_input = gr.File(label="Выберите PDF", file_types=[".pdf"], type="filepath")
            check_btn = gr.Button("🔍 Проверить документ", variant="secondary", size="lg")
        with gr.Column():
            pdf_output = gr.File(label="Стандартное скачивание", interactive=True)
            download_btn = gr.DownloadButton(
                label="📥 СКАЧАТЬ ПРОВЕРЕННЫЙ ФАЙЛ",
                visible=False,
                size="lg"
            )
            warning_msg = gr.Markdown(
                "⚠️ <span style='color:#E58383;'>Рекомендуем открывать PDF в Adobe Acrobat Reader —<br>в браузерах аннотации могут отображаться некорректно.</span>",
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
            lines=6, interactive=False, visible=False
        )

    with gr.Row():
        admin_pw = gr.Textbox(label="Пароль администратора", type="password")
        login_btn = gr.Button("Войти как администратор", variant="secondary")

    # привязываем колбэки
    check_btn.click(
        fn=process_pdf_file,
        inputs=[pdf_input],
        outputs=[pdf_output, download_btn, warning_msg, user_notes, admin_logs]
    )
    login_btn.click(
        fn=authenticate_admin,
        inputs=[admin_pw],
        outputs=[admin_logs]
    )

    with gr.Accordion("ℹ️ Информация о проверке", open=False):
        gr.Markdown("""
        - Проверка кавычек
        - Проверка полей

        Формат выходного файла:
        `<ИсходноеИмя>_Проверено_DD.MM.YYYY_в_HH:MM.pdf`
        """)

if __name__ == "__main__":
    iface.launch(server_name="0.0.0.0", server_port=7860, share=False)
