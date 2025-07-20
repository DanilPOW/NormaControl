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
        # Все неправильные кавычки (все кроме русских ёлочек « »)
        BAD_QUOTES = '"\'""''„‚‹›'
        
        # Создаем паттерн с правильным экранированием
        self.wrong_quotes_pattern = re.compile(f"[{re.escape(BAD_QUOTES)}]")
        
        logger.info(f"Паттерн для поиска: {self.wrong_quotes_pattern.pattern}")
        
    def _get_context(self, text, position, context_length=30):
        """Получение контекста вокруг найденной кавычки"""
        start = max(0, position - context_length)
        end = min(len(text), position + context_length)
        return text[start:end].strip()
    
    def _add_annotation(self, page, quote_char, page_text, char_position):
        """Добавление аннотации на страницу PDF"""
        try:
            # Ищем символ на странице
            quote_instances = page.search_for(quote_char)
            
            if quote_instances:
                # Берем первое найденное вхождение
                rect = quote_instances[0]
                
                # Создаем текстовую аннотацию
                annotation = page.add_text_annot(
                    rect.tl,
                    'Кавычки указаны не верно, используйте кавычки «ёлочки».'
                )
                
                # Настраиваем аннотацию
                annotation.set_info(
                    title="❌ Неправильные кавычки",
                    content=f'Найден символ: "{quote_char}" (U+{ord(quote_char):04X})\nИспользуйте кавычки «ёлочки».'
                )
                
                # Красная рамка для видимости
                annotation.set_colors(stroke=[1, 0, 0])
                annotation.update()
                
                logger.info(f"✅ Добавлена аннотация для '{quote_char}' (U+{ord(quote_char):04X})")
                
            else:
                # Если не нашли точное место, добавляем в угол страницы
                page_rect = page.rect
                fallback_point = fitz.Point(page_rect.width - 100, 50 + (char_position % 10) * 20)
                
                annotation = page.add_text_annot(
                    fallback_point,
                    f'Найдена неп��авильная кавычка: "{quote_char}"'
                )
                annotation.set_info(
                    title="❌ Неправильные кавычки",
                    content=f'Обнаружен символ "{quote_char}" (U+{ord(quote_char):04X}). Используйте «ёлочки».'
                )
                annotation.set_colors(stroke=[1, 0, 0])
                annotation.update()
                
                logger.warning(f"⚠️ Резервное размещение для '{quote_char}' (U+{ord(quote_char):04X})")
                
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении аннотации для '{quote_char}': {str(e)}")
    
    def process_pdf(self, input_path, output_dir="/tmp"):
        """Обработка PDF файла"""
        try:
            # Открываем PDF
            doc = fitz.open(input_path)
            violations = []
            total_violations = 0
            
            logger.info(f"📄 Начинаем анализ PDF: {os.path.basename(input_path)}")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_text = page.get_text()
                
                # Отладка: показываем все найденные кавычки
                page_quotes = set()
                for char in page_text:
                    if char in '"\'""''„‚‹›«»':
                        page_quotes.add(f"{char} (U+{ord(char):04X})")
                
                if page_quotes:
                    logger.info(f"📖 Страница {page_num + 1}: {', '.join(sorted(page_quotes))}")
                
                # Поиск неправильных кавычек с детальным логированием
                matches = list(self.wrong_quotes_pattern.finditer(page_text))
                logger.info(f"🔍 Страница {page_num + 1}: найдено {len(matches)} совпадений")
                
                for match in matches:
                    quote_char = match.group()
                    logger.info(f"🎯 Найдена неправильная кавычка: '{quote_char}' (U+{ord(quote_char):04X}) на позиции {match.start()}")
                    
                    violation = {
                        'page': page_num + 1,
                        'position': match.start(),
                        'quote': quote_char,
                        'unicode': f"U+{ord(quote_char):04X}",
                        'context': self._get_context(page_text, match.start())
                    }
                    violations.append(violation)
                    total_violations += 1
                    
                    # Добавляем аннотацию
                    self._add_annotation(page, quote_char, page_text, match.start())
            
            # Генерируем имя файла
            original_name = os.path.splitext(os.path.basename(input_path))[0]
            current_time = datetime.now()
            date_str = current_time.strftime("%d.%m.%Y")
            time_str = current_time.strftime("%H:%M")
            
            output_filename = f"{original_name}_Проверено_{date_str}_в_{time_str}.pdf"
            output_path = os.path.join(output_dir, output_filename)
            
            # Сохраняем PDF с аннотациями
            doc.save(output_path)
            doc.close()
            
            # Формируем отчеты
            user_message = self._generate_user_report(total_violations)
            admin_logs = self._generate_admin_logs(violations, input_path, output_path)
            
            logger.info(f"✅ Обработка завершена. Найдено {total_violations} нарушений")
            
            return {
                'status': 'success',
                'output_path': output_path,
                'user_message': user_message,
                'admin_logs': admin_logs,
                'violations_count': total_violations
            }
            
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке PDF: {str(e)}")
            return {
                'status': 'error',
                'error_message': str(e),
                'user_message': f"Ошибка при обработке файла: {str(e)}",
                'admin_logs': f"ERROR: {str(e)}"
            }
    
    def _generate_user_report(self, count):
        """Генерация отчета для пользователя"""
        if count == 0:
            return "✅ В документе не обнаружено нарушений с кавычками."
        elif count == 1:
            return f"⚠️ В документе обнаружен {count} случай нарушения кавычек. Проверьте аннотации в PDF."
        elif 2 <= count <= 4:
            return f"⚠️ В документе обнаружено {count} случая нарушений кавычек. Проверьте аннотации в PDF."
        else:
            return f"⚠️ В документе обнаружено {count} случаев нарушений кавычек. Проверьте аннотации в PDF."
    
    def _generate_admin_logs(self, violations, input_path, output_path):
        """Генерация логов для администраторов"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        logs = [
            f"[{timestamp}] Файл: {os.path.basename(input_path)}",
            f"[{timestamp}] Найдено нарушений: {len(violations)}",
            f"[{timestamp}] Создан: {os.path.basename(output_path)}"
        ]
        
        if violations:
            logs.append(f"[{timestamp}] Детали нарушений:")
            # Группируем по типам кавычек
            quote_stats = {}
            for violation in violations:
                quote = violation['quote']
                unicode_code = violation['unicode']
                key = f"'{quote}' ({unicode_code})"
                quote_stats[key] = quote_stats.get(key, 0) + 1
            
            for quote_type, count in quote_stats.items():
                logs.append(f"  • {quote_type}: {count} раз")
            
            # Показываем первые 5 примеров
            logs.append(f"[{timestamp}] Примеры:")
            for i, violation in enumerate(violations[:5], 1):
                logs.append(f"  {i}. Стр.{violation['page']}: {violation['quote']} → ...{violation['context']}...")
        
        return "\n".join(logs)

# Создаем экземпляр анализатора
analyzer = PDFQuoteAnalyzer()
