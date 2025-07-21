import fitz  # PyMuPDF
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class PDFHandler:
    """Класс для работы с PDF файлами - открытие, чтение, сохранение"""
    
    def __init__(self):
        self.document = None
        self.input_path = None
    
    def open_pdf(self, file_path):
        """Открывает PDF файл и возвращает объект документа"""
        try:
            self.input_path = file_path
            self.document = fitz.open(file_path)
            logger.info(f"📄 PDF файл открыт: {os.path.basename(file_path)}")
            logger.info(f"📊 Количество страниц: {len(self.document)}")
            return self.document
        except Exception as e:
            logger.error(f"❌ Ошибка при открытии PDF файла: {str(e)}")
            raise Exception(f"Не удалось открыть PDF файл: {str(e)}")
    
    def get_page_text_methods(self, page):
        """Извлечение текста разными методами для сравнения"""
        methods = {}
        
        try:
            methods['standard'] = page.get_text()
        except Exception as e:
            methods['standard'] = f"ERROR: {str(e)}"
        
        try:
            methods['text_format'] = page.get_text("text")
        except Exception as e:
            methods['text_format'] = f"ERROR: {str(e)}"
        
        try:
            blocks = page.get_text("blocks")
            methods['blocks'] = "\n".join([block[4] for block in blocks if len(block) > 4])
        except Exception as e:
            methods['blocks'] = f"ERROR: {str(e)}"
        
        try:
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
        
        return methods
    
    def find_char_position_in_spans(self, page, target_char, global_position):
        """Находит точную позицию символа в span'ах для более точного позиционирования"""
        try:
            text_dict = page.get_text("dict")
            char_counter = 0
            
            for block in text_dict.get("blocks", []):
                if "lines" not in block:
                    continue
                
                for line in block["lines"]:
                    for span in line["spans"]:
                        span_text = span.get("text", "")
                        
                        for i, char in enumerate(span_text):
                            if char_counter == global_position and char == target_char:
                                # Вычисляем приблизительную позицию символа в span'е
                                bbox = span["bbox"]
                                char_width = (bbox[2] - bbox[0]) / len(span_text) if len(span_text) > 0 else 10
                                
                                char_rect = fitz.Rect(
                                    bbox[0] + i * char_width,
                                    bbox[1],
                                    bbox[0] + (i + 1) * char_width,
                                    bbox[3]
                                )
                                
                                return char_rect
                            
                            char_counter += 1
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Ошибка при поиске позиции в span'ах: {str(e)}")
            return None
    
    def add_annotation_to_page(self, page, quote_char, page_text, char_position):
        """Добавление аннотации на страницу PDF"""
        annotations_added = 0
        
        try:
            # Метод 1: Используем search_for для поиска всех вхождений
            quote_instances = page.search_for(quote_char)
            
            if quote_instances:
                logger.info(f"🔍 Найдено {len(quote_instances)} вхождений '{quote_char}' через search_for")
                
                # Аннотируем ВСЕ найденные вхождения
                for i, rect in enumerate(quote_instances):
                    try:
                        full_annotation_text = (
                            'Вы используете не те кавычки. Замените их на «ёлочки» для соответствия стандартам.\n'
                            f'Найден символ: "{quote_char}" (U+{ord(quote_char):04X})'
                        )
                        annotation = page.add_text_annot(
                            rect.tl,
                            full_annotation_text
                        )
                        annotation.set_info(
                            title="❌ Неправильные кавычки",
                            content=full_annotation_text
                        )
                        annotation.set_colors(stroke=[1, 0, 0])
                        annotation.update()
                        
                        annotations_added += 1
                        logger.info(f"✅ Аннотация #{i+1} добавлена для '{quote_char}' в позиции {rect}")
                        
                    except Exception as e:
                        logger.error(f"❌ Ошибка при добавлении аннотации #{i+1}: {str(e)}")
            
            # Метод 2: Точное позиционирование через span'ы
            if not quote_instances or len(quote_instances) < page_text.count(quote_char):
                logger.info(f"🔧 Используем точное позиционирование через span'ы")
                
                positions = [i for i, char in enumerate(page_text) if char == quote_char]
                logger.info(f"📍 Найдено {len(positions)} позиций '{quote_char}' в тексте: {positions[:10]}...")
                
                for pos in positions:
                    char_rect = self.find_char_position_in_spans(page, quote_char, pos)
                    
                    if char_rect:
                        try:
                            full_annotation_text = (
                                'Вы используете не те кавычки. Замените их на «ёлочки» для соответствия стандартам.\n'
                                f'Найден символ: "{quote_char}" (U+{ord(quote_char):04X}) в позиции {pos}'
                            )
                            annotation = page.add_text_annot(
                                char_rect.tl,
                                full_annotation_text
                            )
                            annotation.set_info(
                                title="❌ Неправильные кавычки (точное позиционирование)",
                                content=full_annotation_text
                            )
                            annotation.set_colors(stroke=[1, 0, 0])
                            annotation.update()
                            
                            annotations_added += 1
                            logger.info(f"✅ Точная аннотация добавлена для '{quote_char}' в позиции {pos}")
                            
                        except Exception as e:
                            logger.error(f"❌ Ошибка при точном позиционировании для позиции {pos}: {str(e)}")
            
            # Метод 3: Fallback размещение
            if annotations_added == 0:
                logger.warning(f"⚠️ Используем fallback размещение для '{quote_char}'")
                
                page_rect = page.rect
                fallback_point = fitz.Point(
                    page_rect.width - 100,
                    50 + (char_position % 10) * 20
                )
                
                full_annotation_text = (
                    'Вы используете не те кавычки. Замените их на «ёлочки» для соответствия стандартам.\n'
                    f'Обнаружен символ "{quote_char}" (U+{ord(quote_char):04X}) в позиции {char_position}'
                )
                annotation = page.add_text_annot(
                    fallback_point,
                    full_annotation_text
                )
                annotation.set_info(
                    title="❌ Неправильные кавычки (fallback)",
                    content=full_annotation_text
                )
                annotation.set_colors(stroke=[1, 0, 0])
                annotation.update()
                
                annotations_added += 1
                logger.info(f"✅ Fallback аннотация добавлена для '{quote_char}'")
            
            logger.info(f"📊 Всего добавлено аннотаций для '{quote_char}': {annotations_added}")
            return annotations_added > 0
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при добавлении аннотации для '{quote_char}': {str(e)}")
            return False
    
    def save_pdf(self, output_dir="/tmp"):
        """Сохраняет PDF файл с аннотациями"""
        if not self.document or not self.input_path:
            raise Exception("Документ не открыт или путь не указан")
        
        try:
            # Генерируем имя выходного файла
            original_name = os.path.splitext(os.path.basename(self.input_path))[0]
            current_time = datetime.now()
            date_str = current_time.strftime("%d.%m.%Y")
            time_str = current_time.strftime("%H:%M")
            
            output_filename = f"{original_name}_Проверено_{date_str}_в_{time_str}.pdf"
            output_path = os.path.join(output_dir, output_filename)
            
            self.document.save(output_path)
            logger.info(f"💾 Файл сохранен: {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении PDF: {str(e)}")
            raise Exception(f"Не удалось сохранить PDF файл: {str(e)}")
    
    def close(self):
        """Закрывает документ"""
        if self.document:
            self.document.close()
            self.document = None
            self.input_path = None
            logger.info("📄 PDF документ закрыт")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
