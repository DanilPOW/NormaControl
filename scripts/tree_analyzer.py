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
        # Паттерны для поиска неправильных кавычек (включая различные типы)
        self.wrong_quotes_pattern = re.compile(r'[\"\'""'']')
        self.correct_quotes = {'open': '«', 'close': '»'}
        
    def _get_context(self, text, position, context_length=50):
        """Получение контекста вокруг найденной кавычки"""
        start = max(0, position - context_length)
        end = min(len(text), position + context_length)
        return text[start:end].strip()
    
    def _find_quote_coordinates(self, page, quote_char, char_position):
        """Более точный поиск координат символа в тексте"""
        try:
            # Получаем детальную информацию о тексте
            text_dict = page.get_text("dict")
            
            char_count = 0
            for block in text_dict["blocks"]:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            span_text = span["text"]
                            for i, char in enumerate(span_text):
                                if char_count == char_position and char == quote_char:
                                    # Вычисляем приблизительные координаты символа
                                    bbox = span["bbox"]
                                    char_width = (bbox[2] - bbox[0]) / len(span_text)
                                    char_x = bbox[0] + (i * char_width)
                                    
                                    return fitz.Rect(
                                        char_x, bbox[1],
                                        char_x + char_width, bbox[3]
                                    )
                                char_count += 1
        
        return None
        
    except Exception as e:
        logger.warning(f"Не удалось найти точные координаты: {str(e)}")
        return None
    
    def _add_annotation_improved(self, page, quote_char, page_text, char_position):
        """Улучшенное добавление аннотации с более точным позиционированием"""
        try:
            # Ищем все вхождения символа на странице
            # quote_instances = page.search_for(quote_char)
            rect = self._find_quote_coordinates(page, quote_char, char_position)
            
            if rect:
            # Берем первое найденное вхождение (можно улучшить логику выбора)
            # rect = quote_instances[0]
            
            # Расширяем прямоугольник для лучшей видимости аннотации
                expanded_rect = fitz.Rect(
                    rect.x0 - 2, rect.y0 - 2,
                    rect.x1 + 2, rect.y1 + 2
                )
                
                # Создаем текстовую аннотацию
                annotation = page.add_text_annot(
                    expanded_rect.tl,
                    'Кавычки указаны не верно, используйте кавычки «ёлочки».'
                )
                
                # Настраиваем свойства аннотации
                annotation.set_info(
                    title="❌ Проверка кавычек",
                    content='Кавычки указаны не верно, используйте кавычки «ёлочки».\n\nНайденный символ: "' + quote_char + '"'
                )
                
                # Устанавливаем цвет и иконку
                annotation.set_colors(stroke=[1, 0, 0])  # Красная рамка
                annotation.update()
                
                logger.debug(f"Добавлена аннотация для символа '{quote_char}' на позиции {rect}")
                
            else:
                # Если точное позиционирование не удалось, добавляем аннотацию в углу страницы
                page_rect = page.rect
                fallback_point = fitz.Point(page_rect.width - 100, 50)
                
                annotation = page.add_text_annot(
                    fallback_point,
                    f'Найдена неправильная кавычка: "{quote_char}"'
                )
                annotation.set_info(
                    title="❌ Проверка кавычек",
                    content=f'Обнаружена неправильная кавычка "{quote_char}". Используйте кавычки «ёлочки».'
                )
                annotation.set_colors(stroke=[1, 0, 0])
                annotation.update()
                
                logger.warning(f"Использовано резервное позиционирование для символа '{quote_char}'")
                
        except Exception as e:
            logger.error(f"Не удалось добавить аннотацию для '{quote_char}': {str(e)}")
    
    def process_pdf(self, input_path, output_dir="/tmp"):
        """Обработка PDF файла с анализом и сохранением результата"""
        try:
            # Открываем PDF один раз для анализа и аннотирования
            doc = fitz.open(input_path)
            violations = []
            total_violations = 0
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Получаем детальную информацию о тексте с координатами
                text_dict = page.get_text("dict")
                page_text = page.get_text()
                
                # Поиск неправильных кавычек
                matches = list(self.wrong_quotes_pattern.finditer(page_text))
                
                for match in matches:
                    violation = {
                        'page': page_num + 1,
                        'position': match.start(),
                        'quote': match.group(),
                        'context': self._get_context(page_text, match.start())
                    }
                    violations.append(violation)
                    total_violations += 1
                    
                    # Добавляем аннотацию на страницу (улучшенная версия)
                    self._add_annotation_improved(page, match.group(), page_text, match.start())
        
        # Генерируем имя выходного файла
        original_name = os.path.splitext(os.path.basename(input_path))[0]
        current_time = datetime.now()
        date_str = current_time.strftime("%d.%m.%Y")
        time_str = current_time.strftime("%H:%M")
        
        output_filename = f"{original_name}_Проверено_{date_str}_в_{time_str}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        
        # Сохраняем уже проаннотированный документ
        doc.save(output_path)
        doc.close()
        
        # Формируем отчеты
        analysis_result = {
            'violations': violations,
            'total_count': total_violations,
            'status': 'success'
        }
        
        user_message = self._generate_user_report(analysis_result)
        admin_logs = self._generate_admin_logs(analysis_result, input_path, output_path)
        
        logger.info(f"Обработка завершена. Найдено {total_violations} нарушений. Файл сохранен: {output_path}")
        
        return {
            'status': 'success',
            'output_path': output_path,
            'user_message': user_message,
            'admin_logs': admin_logs,
            'violations_count': total_violations
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
