import re
import logging
from datetime import datetime
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def plural_ru(n, forms):
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20:
        return forms[2]
    if 1 < n1 < 5:
        return forms[1]
    if n1 == 1:
        return forms[0]
    return forms[2]

class PDFQuoteAnalyzer:
    
    def __init__(self):
        # Все неправильные кавычки — ASCII и типографские
        BAD_QUOTES = "\"'“”‘’„‚‹›"
        self.bad_quotes_str = BAD_QUOTES
        self.wrong_quotes_pattern = re.compile(f"[{re.escape(BAD_QUOTES)}]")
        
        logger.info("🔍 Инициализация анализатора кавычек")
        logger.info("📋 Неправильные кавычки для поиска:")
        for char in BAD_QUOTES:
            logger.info(f"   '{char}' (U+{ord(char):04X})")
    
    def _analyze_text_methods(self, methods, page_num):
        logger.info(f"🔍 СРАВНЕНИЕ МЕТОДОВ ИЗВЛЕЧЕНИЯ ТЕКСТА (стр. {page_num}):")
        
        best_method = None
        max_quotes = 0
        
        for method_name, text in methods.items():
            if isinstance(text, str):
                quote_count = 0
                found_quotes = []
                
                # Используем простую проверку принадлежности
                for char in text:
                    if char in self.bad_quotes_str:
                        quote_count += 1
                        found_quotes.append(f"'{char}'(U+{ord(char):04X})")
                
                logger.info(f"📖 {method_name}: {len(text)} символов, {quote_count} неправильных кавычек")
                if found_quotes:
                    # Показываем уникальные кавычки
                    unique_quotes = list(set(found_quotes))
                    logger.info(f"   Найдены: {', '.join(unique_quotes[:10])}")
                
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
        """Контекст вокруг найденной кавычки"""
        start = max(0, position - context_length)
        end = min(len(text), position + context_length)
        return text[start:end].strip()
    
    def analyze_document(self, pdf_document, pdf_handler):
        """
        Анализирует PDF документ на наличие неправильных кавычек
        
        Args:
            pdf_document: Объект PDF документа (fitz.Document)
            pdf_handler: Экземпляр PDFHandler для работы с PDF
            
        Returns:
            dict: Результаты анализа
        """
        try:
            violations = []
            total_violations = 0
            total_annotations = 0
            
            logger.info(f"📄 Начинаем анализ PDF документа")
            logger.info(f"📊 Количество страниц: {len(pdf_document)}")
            
            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                
                logger.info(f"\n{'='*60}")
                logger.info(f"📖 АНАЛИЗ СТРАНИЦЫ {page_num + 1}")
                logger.info(f"{'='*60}")
                
                # Получаем текст разными методами
                text_methods = pdf_handler.get_page_text_methods(page)
                best_method, page_text = self._analyze_text_methods(text_methods, page_num + 1)
                
                if not page_text or isinstance(page_text, str) and len(page_text.strip()) == 0:
                    logger.warning(f"⚠️ Страница {page_num + 1}: текст не извлечен")
                    continue
                
                # Детальный анализ кавычек в тексте
                logger.info("🔍 Детальный анализ кавычек в тексте:")
                all_quotes_in_text = []
                for i, char in enumerate(page_text):
                    if char in self.bad_quotes_str:
                        context = page_text[max(0, i-10):i+10].replace('\n', ' ')
                        all_quotes_in_text.append(f"'{char}'(U+{ord(char):04X}) в позиции {i}: ...{context}...")
                
                if all_quotes_in_text:
                    logger.info(f"   Найдено кавычек в тексте: {len(all_quotes_in_text)}")
                    for quote_info in all_quotes_in_text[:5]:  # Показываем первые 5
                        logger.info(f"   • {quote_info}")
                else:
                    logger.info("   Кавычек в тексте не найдено")
                
                # Применяем regex поиск
                logger.info(f"🔍 Применяем regex поиск...")
                matches = list(self.wrong_quotes_pattern.finditer(page_text))
                logger.info(f"🔎 Regex нашел совпадений: {len(matches)}")
                
                # Группируем совпадения по символам
                matches_by_char = {}
                for match in matches:
                    quote_char = match.group()
                    if quote_char not in matches_by_char:
                        matches_by_char[quote_char] = []
                    matches_by_char[quote_char].append(match)
                
                # Обрабатываем каждый тип кавычек отдельно
                for quote_char, char_matches in matches_by_char.items():
                    logger.info(f"🎯 Обрабатываем символ '{quote_char}' (U+{ord(quote_char):04X}): {len(char_matches)} вхождений")
                    
                    # Добавляем аннотации для всех вхождений этого символа
                    success = pdf_handler.add_annotation_to_page(page, quote_char, page_text, char_matches[0].start())
                    
                    if success:
                        total_annotations += 1
                        logger.info(f"✅ Аннотации для '{quote_char}' добавлены успешно")
                    else:
                        logger.error(f"❌ Не удалось добавить аннотации для '{quote_char}'")
                    
                    # Записываем все нарушения для отчета
                    for match in char_matches:
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
                        
                        logger.info(f"🚨 НАРУШЕНИЕ #{total_violations}: '{quote_char}' (U+{ord(quote_char):04X}) на стр. {page_num + 1}")
                        logger.info(f"   Контекст: ...{violation['context']}...")
            
            logger.info(f"\n{'='*60}")
            logger.info(f" ИТОГОВАЯ СТАТИСТИКА")
            logger.info(f"{'='*60}")
            logger.info(f"Всего нарушений найдено: {total_violations}")
            logger.info(f"Всего аннотаций добавлено: {total_annotations}")
            
            return {
                'status': 'success',
                'violations': violations,
                'violations_count': total_violations,
                'annotations_count': total_annotations
            }
            
        except Exception as e:
            logger.error(f" Ошибка при анализе документа: {str(e)}")
            return {
                'status': 'error',
                'error_message': str(e),
                'violations_count': 0,
                'annotations_count': 0
            }
    
    def _generate_short_user_report(self, violations):
        count = len(violations)
        if count == 0:
            return "✅Проверка кавычек"
        pages = sorted(set([v['page'] for v in violations]))
        word = plural_ru(count, ('нарушение', 'нарушения', 'нарушений'))
        if count == 1:
            return f"⚠️Проверка кавычек: обнаружено 1 {word} кавычек (на странице {pages[0]})."
        else:
            return f"⚠️Проверка кавычек: обнаружено {count} {word} кавычек на страницах: {', '.join(map(str, pages))}."
    
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
            
            logs.append(f"[{timestamp}] Примеры:")
            for i, violation in enumerate(violations[:5], 1):
                logs.append(f"  {i}. Стр.{violation['page']}: {violation['quote']} → ...{violation['context']}...")
        else:
            logs.append(f"[{timestamp}] Нарушений не найдено")
        
        return "\n".join(logs)
    
    def process_pdf_file(self, input_path, output_dir="/tmp", basename=None):
        from .pdf_handler import PDFHandler
        
        try:
            with PDFHandler() as pdf_handler:
                # Открываем PDF
                pdf_document = pdf_handler.open_pdf(input_path)
                
                # Анализируем документ
                analysis_result = self.analyze_document(pdf_document, pdf_handler)
                
                if analysis_result['status'] != 'success':
                    return {
                        'status': 'error',
                        'error_message': analysis_result['error_message'],
                        'user_message': f"Ошибка при анализе файла: {analysis_result['error_message']}",
                        'admin_logs': f"ERROR: {analysis_result['error_message']}"
                    }
                
                # Сохраняем файл с аннотациями
                output_path = pdf_handler.save_pdf(output_dir, basename=basename)
                
                # Генерируем отчеты
                violations_count = analysis_result['violations_count']
                user_message = self._generate_short_user_report(violations_count)
                admin_logs = self._generate_admin_logs(
                    analysis_result['violations'], 
                    input_path, 
                    output_path
                )
                
                return {
                    'status': 'success',
                    'output_path': output_path,
                    'user_message': user_message,
                    'admin_logs': admin_logs,
                    'violations_count': violations_count
                }
                
        except Exception as e:
            logger.error(f"Ошибка при обработке PDF файла: {str(e)}")
            return {
                'status': 'error',
                'error_message': str(e),
                'user_message': f"Ошибка при обработке файла: {str(e)}",
                'admin_logs': f"ERROR: {str(e)}"
            }

# Создаем экземпляр анализатора
analyzer = PDFQuoteAnalyzer()
