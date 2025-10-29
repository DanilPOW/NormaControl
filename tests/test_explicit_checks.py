import pytest
import fitz
import json
from pathlib import Path
from scripts.image_checker import check_images

class TestPDFFunctionality:
    TEST_FILES_DIR = Path("tests/test_files")
    
    def test_all_functionality(self):
        """Тестирование всей функциональности по файлам-спецификациям"""
        
        # Соответствие файлов и проверяемой функциональности
        functionality_map = {
            'centered': 'Картинка по центру',
            'numbering': 'Есть нумерация на страницах', 
            'margins': 'Правильные поля',
            'fonts': 'Шрифты встроены',
            'colors': 'Цветовая схема',
            'links': 'Рабочие ссылки'
        }
        
        results = {}
        tested_files = 0
        
        # Проверяем каждый файл и определяем его функциональность
        for pdf_path in self.TEST_FILES_DIR.glob("*.pdf"):
            filename = pdf_path.stem.lower()
            tested_files += 1
            
            # Определяем тип проверки по имени файла
            check_type = None
            for key in functionality_map:
                if key in filename:
                    check_type = key
                    break
            
            if not check_type:
                continue  # Пропускаем файлы без четкой спецификации
                
            try:
                doc = fitz.open(pdf_path)
                result = check_images(doc, str(pdf_path))
                doc.close()
                
                # Проверяем успешность теста
                is_success = "✅" in result["user_summary"]
                results[check_type] = {
                    'status': is_success,
                    'file': pdf_path.name,
                    'message': result["user_summary"].split('\n')[0]  # Первая строка результата
                }
                
            except Exception as e:
                results[check_type] = {
                    'status': False,
                    'file': pdf_path.name,
                    'message': f'Ошибка: {str(e)}'
                }
        
        # Сохраняем результаты
        self._save_functionality_report(results, tested_files)
        
        # Проверяем, что все основные функции работают
        critical_checks = ['centered', 'numbering']  # Критичные проверки
        for check in critical_checks:
            if check in results:
                assert results[check]['status'], f"Проверка {functionality_map[check]} не работает"
    
    def _save_functionality_report(self, results, tested_files):
        """Сохранение отчета по функциональности"""
        import datetime
        
        # Формируем итоговый отчет
        final_results = []
        for check_type, result in results.items():
            status_icon = "✅" if result['status'] else "❌"
            final_results.append(f"{status_icon} {self._get_check_name(check_type)}")
        
        output = {
            'timestamp': datetime.datetime.now().isoformat(),
            'tested_files_count': tested_files,
            'functionality_results': final_results,
            'details': results
        }
        
        Path("test_results").mkdir(exist_ok=True)
        with open("test_results/functionality_report.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    
    def _get_check_name(self, check_type):
        """Получение читаемого названия проверки"""
        names = {
            'centered': 'Картинка по центру',
            'numbering': 'Есть нумерация на страницах',
            'margins': 'Правильные поля',
            'fonts': 'Шрифты встроены',
            'colors': 'Цветовая схема',
            'links': 'Рабочие ссылки'
        }
        return names.get(check_type, check_type)
