import gradio as gr
print("Текущая версия Gradio:", gr.__version__)
import os
import tempfile
from scripts.tree_analyzer import analyzer

# Функция обработки PDF файла
def process_pdf_file(pdf_file):
    """Обработка загруженного PDF файла"""
    if not pdf_file:
        return None, "Пожалуйста, загрузите PDF файл", "ERROR: Файл не загружен", None, None
    try:
        temp_dir = tempfile.mkdtemp()
        result = analyzer.process_pdf(pdf_file.name, temp_dir)
        if result['status'] == 'success':
            output_path = result['output_path']
            filename = os.path.basename(output_path)
            return (
                output_path,
                result['user_message'],
                result['admin_logs'],
                output_path,   # для DownloadButton.file
                filename       # для DownloadButton.file_name
            )
        else:
            return (
                None,
                result['user_message'],
                result['admin_logs'],
                None,
                None
            )
    except Exception as e:
        error_msg = f"Произошла ошибка при обработке файла: {e}"
        return None, error_msg, f"ERROR: {e}", None, None

# Функция аутентификации администратора
def authenticate_admin(password):
    """Показывает админ-логи при верном пароле"""
    if password == os.getenv("ADMIN_PW", "secret123"):
        return gr.update(visible=True)
    else:
        return gr.update(visible=False)

with gr.Blocks(title="Анализатор кавычек в PDF", theme=gr.themes.Soft()) as iface:
    gr.Markdown("# 📄 Анализатор кавычек в PDF документах")
    gr.Markdown("Загрузите PDF файл для проверки правильности использования кавычек")

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
                variant="primary",
                size="lg"
            )
        with gr.Column(scale=1):
            gr.Markdown("### Результат проверки")
            pdf_output = gr.File(
                label="",
                interactive=True
            )
            download_btn = gr.DownloadButton(
                label="📥 Скачать аннотированный файл",
                file=None,
                file_name=None,
                visible=False
            )

    # Нижняя часть — заметки пользователя
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

    # Блок админ-логов
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

    # Авторизация администратора
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

    # Колбэки
    process_btn.click(
        fn=process_pdf_file,
        inputs=[pdf_input],
        outputs=[pdf_output, user_notes, admin_logs, download_btn, download_btn]
    )
    login_btn.click(
        fn=authenticate_admin,
        inputs=[admin_pwd],
        outputs=[admin_logs]
    )

    # Информация о проверке
    with gr.Accordion("ℹ️ Информация о проверке", open=False):
        gr.Markdown("""
        ### Что проверяется:
        - **Неправильные кавычки**: " и '
        - **Правильные кавычки**: « и »

        ### Что происходит при проверке:
        1. Анализируется весь текст в PDF документе
        2. Находятся все случаи использования неправильных кавычек
        3. В местах нарушений добавляются аннотации с рекомендациями
        4. Создается новый PDF файл с пометкой о дате и времени проверки

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
