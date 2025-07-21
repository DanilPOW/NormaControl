import gradio as gr
import os
import tempfile
import time
from scripts.tree_analyzer import analyzer

# Куда сохранять временные файлы
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
    2) Запускает анализатор, передавая basename=имя_файла_без_расширения
    3) Возвращает tuple для Gradio: (gr.File, download_btn, warning_msg, user_notes, admin_logs)
    """
    cleanup_old_files(TEMP_DIR)

    # скрываем все кнопки и предупреждения по умолчанию
    hide_btn = gr.update(visible=False, value=None)
    hide_warn = gr.update(visible=False)

    if not pdf_path:
        return None, hide_btn, hide_warn, "⚠️ Загрузите PDF", "ERROR: нет файла"

    # убедимся, что папка существует
    os.makedirs(TEMP_DIR, exist_ok=True)

    # скопировать в TEMP_DIR
    ext = os.path.splitext(pdf_path)[1]
    tmp_name = next(tempfile._get_candidate_names()) + ext
    tmp_path = os.path.join(TEMP_DIR, tmp_name)
    with open(pdf_path, "rb") as src, open(tmp_path, "wb") as dst:
        dst.write(src.read())

    # исходное имя без расширения
    basename = os.path.splitext(os.path.basename(pdf_path))[0]

    # запускаем анализ (tree_analyzer.process_pdf_file)
    result = analyzer.process_pdf_file(tmp_path, TEMP_DIR, basename=basename)

    if result["status"] != "success":
        return None, hide_btn, hide_warn, result.get("user_message","Ошибка"), result.get("admin_logs","ERROR")

    out_path = result["output_path"]
    # показываем все нужные элементы
    return (
        out_path,                       # gr.File
        gr.update(visible=True, value=out_path),  # gr.DownloadButton
        gr.update(visible=True),        # warning_msg
        result["user_message"],
        result["admin_logs"]
    )

def authenticate_admin(pw: str):
    return gr.update(visible=(pw == os.getenv("ADMIN_PW","secret123")))


with gr.Blocks(title="Анализатор кавычек в PDF", theme=gr.themes.Soft()) as iface:
    gr.Markdown("# 📄 Анализатор кавычек (ГОСТ 7.32-2017)")
    gr.Markdown("Загрузите PDF для проверки кавычек и автоматической аннотации")

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
        
        Формат выходного файла:
        `<ИсходноеИмя>_Проверено_DD.MM.YYYY_в_HH:MM.pdf`
        """)

if __name__ == "__main__":
    iface.launch(server_name="0.0.0.0", server_port=7860, share=False)
