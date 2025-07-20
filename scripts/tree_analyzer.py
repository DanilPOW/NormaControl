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
        
    def _extract_text_multiple_methods(self, page):
        """Извлечение текста разными методами для сравнения"""
        methods = {}
        
        try:
            # Метод 1: Стандартный get_text()
            methods['standard'] = page.get_text()
        except Exception as e:
            methods['standard'] = f"ERROR: {str(e)}"
        
        try:
            # Метод 2: get_text("text") - явно указываем формат
            methods['text_format'] = page.get_text("text")
        except Exception as e:
            methods['text_format'] = f"ERROR: {str(e)}"
        
        try:
            # Метод 3: get_text("blocks") - блоками
            blocks = page.get_text("blocks")
            methods['blocks'] = "\n".join([block[4] for block in blocks if len(block) > 4])
        except Exception as e:
            methods['blocks'] = f"ERROR: {str(e)}"
        
        try:
            # Метод 4: get_text("dict") - детальная структура
            text_dict = page.get_text("dict")
            text_parts = []
            for block in text_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text_parts.append(span.get("text", ""))
            methods['dict_method'] = "".join(text_parts)
        except Exception as e:
            methods['dict_method'] = f"ERROR: {str(e)}"
        
        try:
            # Метод 5: get_text("rawdict") - сырые данные
            raw_dict = page.get_text("rawdict")
            text_parts = []
            for block in raw_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text_parts.append(span.get("text", ""))
            methods['rawdict'] = "".join(text_parts)
        except Exception as e:
            methods['rawdict'] = f"ERROR: {str(e)}"
        
        return methods
    
    def _analyze_text_methods(self, methods, page_num):
        """Анализ результатов разных методов извлечения"""
        logger.info(f"🔍 СРАВНЕНИЕ МЕТОДОВ ИЗВЛЕЧЕНИЯ ТЕКСТА (страница {page_num}):")
        
        best_method = None
        max_quotes = 0
        
        for method_name, text in methods.items():
            if isinstance(text, str):
                # Подсчитываем кавычки
                quote_count = 0
                found_quotes = []
                
                for char in text:
                    if char in '"\'""''„‚‹›«»':
                        quote_count += 1
                        found_quotes.append(f"'{char}'(U+{ord(char):04X})")
                
                logger.info(f"📖 {method_name}: {len(text)} символов, {quote_count} кавычек")
                if found_quotes:
                    logger.info(f"   Найденные кавычки: {', '.join(found_quotes[:10])}")
                
                # Показываем превью текста
                preview = text[:100].replace('\n', '\\n').replace('\r', '\\r')
                logger.info(f"   Превью: {preview}...")
                
                if quote_count > max_quotes:
                    max_quotes = quote_count
                    best_method = method_name
            else:
                logger.error(f"❌ {method_name}: {text}")
        
        logger.info(f"🏆 Лучший метод: {best_method} ({max_quotes} кавычек)")
        return best_method, methods.get(best_method, "")
    
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
                return True
                
            else:
                # Если не нашли точное место, добавляем в угол страницы
                page_rect = page.rect
                fallback_point = fitz.Point(page_rect.width - 100, 50 + (char_position % 10) * 20)
                
                annotation = page.add_text_annot(
                    fallback_point,
                    f'Найдена неправильная кавычка: "{quote_char}"'
                )
                annotation.set_info(
                    title="❌ Неправильные кавычки",
                    content=f'Обнаружен символ "{quote_char}" (U+{ord(quote_char):04X}). Используйте «ёлочки».'
                )
                annotation.set_colors(stroke=[1, 0, 0])
                annotation.update()
                
                logger.warning(f"⚠️ Резервное размещение для '{quote_char}' (U+{ord(quote_char):04X})")
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении аннотации для '{quote_char}': {str(e)}")
            return False
    
    def process_pdf(self, input_path, output_dir="/tmp"):
        """Обработка PDF файла"""
        try:
            # Открываем PDF
            doc = fitz.open(input_path)
            violations = []
            total_violations = 0
            
            logger.info(f"📄 Начинаем анализ PDF: {os.path.basename(input_path)}")
            logger.info(f"📊 Количество страниц: {len(doc)}")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                logger.info(f"\n{'='*60}")
                logger.info(f"📖 АНАЛИЗ СТРАНИЦЫ {page_num + 1}")
                logger.info(f"{'='*60}")
                
                # Извлекаем текст разными методами
                text_methods = self._extract_text_multiple_methods(page)
                
                # Анализируем и выбираем лучший метод
                best_method, page_text = self._analyze_text_methods(text_methods, page_num + 1)
                
                if not page_text or isinstance(page_text, str) and len(page_text.strip()) == 0:
                    logger.warning(f"⚠️ Страница {page_num + 1}: текст не извлечен")
                    continue
                
                # Ищем неправильные кавычки
                matches = list(self.wrong_quotes_pattern.finditer(page_text))
                logger.info(f"🔎 Найдено совпадений: {len(matches)}")
                
                # Обрабатываем найденные совпадения
                for match in matches:
                    quote_char = match.group()
                    
                    violation = {
                        'page': page_num + 1,
                        'position': match.start(),
                        'quote': quote_char,
                        'unicode': f"U+{ord(quote_char):04X}",
                        'context': self._get_context(page_text, match.start()),
                        'method': best_method
                    }
                    violations.append(violation)
                    total_violations += 1
                    
                    logger.info(f"🚨 НАРУШЕНИЕ #{total_violations}: '{quote_char}' (U+{ord(quote_char):04X}) на странице {page_num + 1}")
                    logger.info(f"   Контекст: ...{violation['context']}...")
                    
                    # Добавляем аннотацию
                    success = self._add_annotation(page, quote_char, page_text, match.start())
                    if success:
                        logger.info(f"✅ Аннотация добавлена успешно")
                    else:
                        logger.error(f"❌ Не удалось добавить аннотацию")
            
            logger.info(f"\n{'='*60}")
            logger.info(f"📊 ИТОГОВАЯ СТАТИСТИКА")
            logger.info(f"{'='*60}")
            logger.info(f"Всего нарушений найдено: {total_violations}")
            
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
            
            logger.info(f"💾 Файл сохранен: {output_path}")
            
            # Формируем отчеты
            user_message = self._generate_user_report(total_violations)
            admin_logs = self._generate_admin_logs(violations, input_path, output_path)
            
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
            method_stats = {}
            
            for violation in violations:
                quote = violation['quote']
                unicode_code = violation['unicode']
                method = violation.get('method', 'unknown')
                
                key = f"'{quote}' ({unicode_code})"
                quote_stats[key] = quote_stats.get(key, 0) + 1
                method_stats[method] = method_stats.get(method, 0) + 1
            
            logs.append("  Статистика по типам кавычек:")
            for quote_type, count in quote_stats.items():
                logs.append(f"    • {quote_type}: {count} раз")
            
            logs.append("  Методы извлечения текста:")
            for method, count in method_stats.items():
                logs.append(f"    • {method}: {count} нарушений")
            
            # Показываем первые 5 примеров
            logs.append(f"[{timestamp}] Примеры:")
            for i, violation in enumerate(violations[:5], 1):
                logs.append(f"  {i}. Стр.{violation['page']}: {violation['quote']} → ...{violation['context']}...")
        else:
            logs.append(f"[{timestamp}] Нарушений не найдено")
        
        return "\n".join(logs)

# Создаем экземпляр анализатора
analyzer = PDFQuoteAnalyzer()
