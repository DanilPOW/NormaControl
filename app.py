import gradio as gr
import os
import tempfile
from scripts.tree_analyzer import analyzer

# Глобальный стейт для хранения пути к результату
RESULT_PATH = ""

def process_pdf_file(pdf_file):
    global RESULT_PATH
    if not pdf_file:
        RESULT_PATH = ""
        # Кнопка не показывается, файл не передаётся
        return None, "Пожалуйста, загрузите PDF файл", "ERROR: Файл не загружен", gr.HTML.update(value=hide_html_btn()), None
    try:
        temp_dir = tempfile.mkdtemp()
        result = analyzer.process_pdf(pdf_file.name, temp_dir)
        if result['status'] == 'success':
            output_path = result['output_path']
            RESULT_PATH = output_path
            # Сформировать html-кнопку
            btn_html = custom_html_btn()
            return (
                output_path,
                result['user_message'],
                result['admin_logs'],
                gr.HTML.update(value=btn_html, visible=True),
                output_path   # file для скачивания
            )
        else:
            RESULT_PATH = ""
            return (
                None,
                result['user_message'],
                result['admin_logs'],
                gr.HTML.update(value=hide_html_btn()),
                None
            )
    except Exception as e:
        RESULT_PATH = ""
        error_msg = f"Произошла ошибка при обработке файла: {e}"
        return None, error_msg, f"ERROR: {e}", gr.HTML.update(value=hide_html_btn()), None

def authenticate_admin(password):
    if password == os.getenv("ADMIN_PW", "secret123"):
        return gr.update(visible=True)
    else:
        return gr.update(visible=False)

# Генерация html-кнопки, которая через js-клик вызывает клик по скрытому gr.File
def custom_html_btn():
    return '''
    <div style="display: flex; flex-direction: column; align-items: start;">
      <button id="customDownloadBtn" style="
        background: linear-gradient(90deg, #5d65f1 0%, #8475fa 100%);
        color: white; font-size: 1.25rem; border-radius: 10px; padding: 18px 48px;
        border: none; margin-top: 18px; margin-bottom: 8px; cursor: pointer; font-weight: bold; letter-spacing: 0.5px;">
        📥 Скачать аннотированный файл
      </button>
      <small>Файл откроется или сохранится в вашей папке загрузок</small>
    </div>
    <script>
      // при клике на нашу кнопку ищем настоящий gr.File с label "hidden_file_download"
      document.getElementById("customDownloadBtn").onclick = function() {
        // ищем скрытый input (gr.File) и кликаем по нему
        const el = [...document.querySelectorAll("label")]
          .find(l => l.textContent.includes("hidden_file_download"))
        if (el) el.click();
      };
    </script>
    '''

def hide_html_btn():
    # Пустая заглушка — ничего не показываем
    return ""

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
                interactive=True,
                visible=False   # скрытый стандартный file
            )
            # Большая кастомная кнопка
            download_html = gr.HTML(value=hide_html_btn(), visible=False)
            # Скрытый gr.File с уникальным label
            hidden_file = gr.File(
                label="hidden_file_download",
                interactive=True,
                visible=False
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
        outputs=[pdf_output, user_notes, admin_logs, download_html, hidden_file]
    )
    login_btn.click(
        fn=authenticate_admin,
        inputs=[admin_pwd],
        outputs=[admin_logs]
    )

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
