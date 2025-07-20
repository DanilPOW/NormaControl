import fitz  # PyMuPDF
import re
import os
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PDFQuoteAnalyzer:
    def __init__(self):
        # Паттерны для поиска неправильных кавычек
        self.wrong_quotes_pattern = re.compile(r'["\']')
        self.correct_quotes = {'open': '«', 'close': '»'}
        
    def analyze_pdf(self, pdf_path):
        """Анализ PDF файла на наличие неправильных кавычек"""
        try:
            doc = fitz.open(pdf_path)
            violations = []
            total_violations = 0
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                
                # Поиск неправильных кавычек
                matches = list(self.wrong_quotes_pattern.finditer(text))
                
                for match in matches:
                    violation = {
                        'page': page_num + 1,
                        'position': match.start(),
                        'quote': match.group(),
                        'context': self._get_context(text, match.start())
                    }
                    violations.append(violation)
                    total_violations += 1
                    
                    # Добавляем аннотацию на страницу
                    self._add_annotation(page, match, text)
            
            doc.close()
            
            logger.info(f"Анализ завершен. Найдено {total_violations} нарушений")
            
            return {
                'violations': violations,
                'total_count': total_violations,
                'status': 'success'
            }
            
        except Exception as e:
            logger.error(f"Ошибка при анализе PDF: {str(e)}")
            return {
                'violations': [],
                'total_count': 0,
                'status': 'error',
                'error_message': str(e)
            }
    
    def _get_context(self, text, position, context_length=50):
        """Получение контекста вокруг найденной кавычки"""
        start = max(0, position - context_length)
        end = min(len(text), position + context_length)
        return text[start:end].strip()
    
    def _add_annotation(self, page, match, text):
        """Добавление аннотации на страницу PDF"""
        try:
            # Получаем координаты текста (упрощенная версия)
            text_instances = page.search_for(match.group())
            
            if text_instances:
                # Берем первое вхождение
                rect = text_instances[0]
                
                # Создаем аннотацию
                annotation = page.add_text_annot(
                    rect.tl,  # top-left точка
                    'Кавычки указаны не верно, используйте кавычки «ёлочки».'
                )
                annotation.set_info(
                    title="Проверка кавычек",
                    content='Кавычки указаны не верно, используйте кавычки «ёлочки».'
                )
                annotation.update()
                
        except Exception as e:
            logger.warning(f"Не удалось добавить аннотацию: {str(e)}")
    
    def process_pdf(self, input_path, output_dir="/tmp"):
        """Обработка PDF файла с анализом и сохранением результата"""
        try:
            # Анализируем PDF
            analysis_result = self.analyze_pdf(input_path)
            
            if analysis_result['status'] == 'error':
                return analysis_result
            
            # Открываем PDF для добавления аннотаций
            doc = fitz.open(input_path)
            
            # Генерируем имя выходного файла
            original_name = os.path.splitext(os.path.basename(input_path))[0]
            current_time = datetime.now()
            date_str = current_time.strftime("%d.%m.%Y")
            time_str = current_time.strftime("%H:%M")
            
            output_filename = f"{original_name}_Проверено_{date_str}_в_{time_str}.pdf"
            output_path = os.path.join(output_dir, output_filename)
            
            # Сохраняем PDF с аннотациями
            doc.save(output_path)
            doc.close()
            
            # Формируем отчет для пользователя
            user_message = self._generate_user_report(analysis_result)
            admin_logs = self._generate_admin_logs(analysis_result, input_path, output_path)
            
            return {
                'status': 'success',
                'output_path': output_path,
                'user_message': user_message,
                'admin_logs': admin_logs,
                'violations_count': analysis_result['total_count']
            }
            
        except Exception as e:
            logger.error(f"Ошибка при обработке PDF: {str(e)}")
            return {
                'status': 'error',
                'error_message': str(e),
                'user_message': f"Ошибка при обработке файла: {str(e)}",
                'admin_logs': f"ERROR: {str(e)}"
            }
    
    def _generate_user_report(self, analysis_result):
        """Генерация отчета для пользователя"""
        count = analysis_result['total_count']
        
        if count == 0:
            return "✅ В документе не обнаружено нарушений с кавычками. Документ соответствует требованиям."
        elif count == 1:
            return f"⚠️ В документе обнаружено {count} случай нарушения кавычек. Проверьте аннотации в PDF файле."
        elif 2 <= count <= 4:
            return f"⚠️ В документе обнаружено {count} случая нарушений кавычек. Проверьте аннотации в PDF файле."
        else:
            return f"⚠️ В документе обнаружено {count} случаев нарушений кавычек. Проверьте аннотации в PDF файле."
    
    def _generate_admin_logs(self, analysis_result, input_path, output_path):
        """Генерация логов для администраторов"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        logs = [
            f"[{timestamp}] Начата обработка файла: {os.path.basename(input_path)}",
            f"[{timestamp}] Найдено нарушений: {analysis_result['total_count']}",
            f"[{timestamp}] Создан файл: {os.path.basename(output_path)}"
        ]
        
        if analysis_result['violations']:
            logs.append(f"[{timestamp}] Детали нарушений:")
            for i, violation in enumerate(analysis_result['violations'][:10], 1):  # Показываем первые 10
                logs.append(f"  {i}. Страница {violation['page']}: '{violation['quote']}' в контексте: {violation['context'][:50]}...")
        
        return "\n".join(logs)

# Создаем экземпляр анализатора
analyzer = PDFQuoteAnalyzer()
