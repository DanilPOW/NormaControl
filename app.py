import gradio as gr
import os
import tempfile
from scripts.pdf_analyzer import analyzer

def process_pdf_file(pdf_file):
    """Обработка загруженного PDF файла"""
    if not pdf_file:
        return None, "Пожалуйста, загрузите PDF файл", "ERROR: Файл не загружен"
    
    try:
        # Создаем временную директорию для результатов
        temp_dir = tempfile.mkdtemp()
        
        # Обрабатываем PDF
        result = analyzer.process_pdf(pdf_file.name, temp_dir)
        
        if result['status'] == 'success':
            return (
                result['output_path'],
                result['user_message'],
                result['admin_logs']
            )
        else:
            return (
                None,
                result['user_message'],
                result['admin_logs']
            )
            
    except Exception as e:
        error_msg = f"Произошла ошибка при обработке файла: {str(e)}"
        return None, error_msg, f"ERROR: {str(e)}"

# Создаем интерфейс Gradio
with gr.Blocks(title="Анализатор кавычек в PDF", theme=gr.themes.Soft()) as iface:
    gr.Markdown("# 📄 Анализатор кавычек в PDF документах")
    gr.Markdown("Загрузите PDF файл для проверки правильности использования кавычек")
    
    with gr.Row():
        with gr.Column(scale=1):
            # Левая колонка - загрузка файла
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
            # Правая колонка - результат
            gr.Markdown("### Результат проверки")
            pdf_output = gr.File(
                label="📥 Скачать проверенный файл",
                interactive=False
            )
    
    # Нижняя часть - отчеты
    with gr.Row():
        with gr.Column():
            gr.Markdown("### 👤 Заметки для пользователя")
            user_notes = gr.Textbox(
                label="Результат проверки",
                lines=3,
                interactive=False,
                placeholder="Здесь появится информация о результатах проверки..."
            )
        
        with gr.Column():
            gr.Markdown("### 🔧 Логи для администраторов")
            admin_logs = gr.Textbox(
                label="Системные логи",
                lines=3,
                interactive=False,
                placeholder="Здесь появятся технические детали обработки..."
            )
    
    # Обработчик события
    process_btn.click(
        fn=process_pdf_file,
        inputs=[pdf_input],
        outputs=[pdf_output, user_notes, admin_logs]
    )
    
    # Информационная секция
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
        `ИмяФайла_Проверено_ДД.ММ.ГГГГ_в_ЧЧ:ММ.pdf`
        """)

if __name__ == "__main__":
    iface.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )