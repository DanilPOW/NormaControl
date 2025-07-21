import gradio as gr
import os
import tempfile
import time
from scripts.tree_analyzer import analyzer

TEMP_DIR = "/opt/gradio-app/tmp"

def cleanup_old_files(folder, max_age_seconds=24 * 60 * 60):
    """Удаляет файлы старше max_age_seconds"""
    now = time.time()
    removed = 0
    if not os.path.exists(folder):
        os.makedirs(folder)
    for filename in os.listdir(folder):
        filepath = os.path.join(folder, filename)
        if os.path.isfile(filepath):
            age = now - os.path.getmtime(filepath)
            if age > max_age_seconds:
                try:
                    os.remove(filepath)
                    removed += 1
                except Exception as e:
                    print(f"⚠️ Ошибка удаления {filepath}: {e}")
    if removed:
        print(f"🧹 Очистка временных файлов: удалено {removed}")

def process_pdf_file(pdf_file):
    """Обработка загруженного PDF файла"""
    cleanup_old_files(TEMP_DIR)
    if not pdf_file:
        # Скрыть download_btn и предупреждение при ошибке
        return None, gr.update(visible=False, value=None), gr.update(visible=False), "Пожалуйста, загрузите PDF файл", "ERROR: Файл не загружен"
    try:
        if not os.path.exists(TEMP_DIR):
            os.makedirs(TEMP_DIR)
        # Копируем файл во временную папку с уникальным именем
        ext = os.path.splitext(pdf_file.name)[-1]
        temp_path = os.path.join(TEMP_DIR, f"{next(tempfile._get_candidate_names())}{ext}")
        with open(pdf_file.name, "rb") as fsrc, open(temp_path, "wb") as fdst:
            fdst.write(fsrc.read())
        result = analyzer.process_pdf(temp_path, TEMP_DIR)
        if result['status'] == 'success':
            output_path = result['output_path']
            return (
                output_path,  # gr.File
                gr.update(visible=True, value=output_path),  # gr.DownloadButton
                gr.update(visible=True),  # warning_msg
                result['user_message'],
                result['admin_logs']
            )
        else:
            # Скрыть download_btn и предупреждение при ошибке
            return (
                None, gr.update(visible=False, value=None), gr.update(visible=False),
                result['user_message'],
                result['admin_logs']
            )
    except Exception as e:
        error_msg = f"Произошла ошибка при обработке файла: {e}"
        return None, gr.update(visible=False, value=None), gr.update(visible=False), error_msg, f"ERROR: {e}"

def authenticate_admin(password):
    if password == os.getenv("ADMIN_PW", "secret123"):
        return gr.update(visible=True)
    else:
        return gr.update(visible=False)

with gr.Blocks(title="Анализатор кавычек в PDF", theme=gr.themes.Soft()) as iface:
    gr.Markdown("# 📄 Анализатор документов в соответствии с ГОСТ 7.32-2017")
    gr.Markdown("Загрузите PDF файл для проверки файла")

    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### Загрузка файла")
            pdf_input = gr.File(
                label="Выберите PDF файл",
                file_types=[".pdf"],
                type="filepath"
            )
            process_btn = gr.Button(
                "🔍 Проверить кавычки",
                variant="secondary",
                size="lg"
            )
        with gr.Column(scale=1):
            gr.Markdown("### Результат проверки")
            pdf_output = gr.File(
                label="Стандартное скачивание",
                interactive=True
            )
            download_btn = gr.DownloadButton(
                label="📥 СКАЧАТЬ ПРОВЕРЕННЫЙ ФАЙЛ",
                visible=False,
                size="lg"
            )
            warning_msg = gr.Markdown(
                "⚠️ <span style='color: #D32F2F;'>Рекомендуем открывать итоговый PDF-файл именно в программе Adobe Acrobat Reader.<br>В браузерах возможны проблемы с отображением аннотаций!</span>",
                elem_id="download-comment",
                visible=False  # Скрыто по умолчанию
            )

    with gr.Row():
        with gr.Column():
            gr.Markdown("### 👤 Заметки для пользователя")
            user_notes = gr.Textbox(
                label="Результат проверки",
                lines=10,
                max_lines=100,
                interactive=False,
                placeholder="Здесь появится информация о результатах проверки..."
            )

    with gr.Row():
        with gr.Column():
            gr.Markdown("### 🔧 Логи для администраторов")
            admin_logs = gr.Textbox(
                label="Системные логи",
                lines=10,
                max_lines=100,
                interactive=False,
                placeholder="Здесь появятся технические детали обработки...",
                visible=False
            )

    with gr.Row():
        with gr.Column(scale=1):
            admin_pwd = gr.Textbox(
                label="Пароль администратора",
                type="password"
            )
        with gr.Column(scale=1):
            login_btn = gr.Button(
                "Войти как администратор",
                variant="secondary"
            )

    process_btn.click(
        fn=process_pdf_file,
        inputs=[pdf_input],
        outputs=[pdf_output, download_btn, warning_msg, user_notes, admin_logs]
    )
    login_btn.click(
        fn=authenticate_admin,
        inputs=[admin_pwd],
        outputs=[admin_logs]
    )

    with gr.Accordion("ℹ️ Информация о проверке", open=False):
        gr.Markdown("""
        ### Что проверяется:
        - **Правильность написания кавычек**

        ### Формат имени выходного файла:
        `ИмяФайла_Проверено_ДД.MM.ГГГГ_в_ЧЧ:ММ.pdf`
        """
        )

if __name__ == "__main__":
    iface.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )
