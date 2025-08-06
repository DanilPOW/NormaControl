import fitz  # PyMuPDF
import pdfplumber
#import camelot
import os

def find_tables_pymupdf(pdf_path):
    """Ищет таблицы по регулярной сетке линий с помощью PyMuPDF."""
    logs = []
    try:
        doc = fitz.open(pdf_path)
        for page_idx, page in enumerate(doc):
            drawings = page.get_drawings()
            hlines = []
            vlines = []
            for d in drawings:
                # Прямые линии без изгиба
                if d['type'] == 'line':
                    x0, y0, x1, y1 = d['rect']
                    dx = abs(x1 - x0)
                    dy = abs(y1 - y0)
                    # Горизонтальная линия — почти нет вертикального смещения
                    if dy < 2:
                        hlines.append((x0, y0, x1, y1))
                    # Вертикальная линия — почти нет горизонтального смещения
                    if dx < 2:
                        vlines.append((x0, y0, x1, y1))
            # Если и тех и других ≥2 — похоже на таблицу
            if len(hlines) >= 2 and len(vlines) >= 2:
                logs.append(f"[PyMuPDF] Стр. {page_idx+1}: Обнаружено {len(hlines)} горизонтальных и {len(vlines)} вертикальных линий (табличная сетка)")
            else:
                logs.append(f"[PyMuPDF] Стр. {page_idx+1}: Табличная сетка не обнаружена")
        doc.close()
    except Exception as e:
        logs.append(f"[PyMuPDF] Ошибка: {str(e)}")
    return "\n".join(logs)

def find_tables_pdfplumber(pdf_path):
    """Ищет таблицы по линиям и структуре текста с помощью pdfplumber."""
    logs = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                hlines = page.lines
                rects = page.rects
                # Анализируем регулярность линий (наличие параллельных осей)
                x_coords = [round(line['x0'],1) for line in hlines if abs(line['x0']-line['x1'])<1]
                y_coords = [round(line['y0'],1) for line in hlines if abs(line['y0']-line['y1'])<1]
                xuniq = set(x_coords)
                yuniq = set(y_coords)
                # Если ≥2 уникальных по каждой оси — похоже на сетку
                if len(xuniq) >= 2 and len(yuniq) >= 2:
                    logs.append(f"[pdfplumber] Стр. {page_idx+1}: Найдена таблица (линии сетки: {len(xuniq)} верт., {len(yuniq)} гориз.)")
                else:
                    # Пробуем искать регулярность в тексте (по координатам слов)
                    words = page.extract_words()
                    xs = [round(float(w['x0']),1) for w in words]
                    ys = [round(float(w['top']),1) for w in words]
                    x_counts = len(set(xs))
                    y_counts = len(set(ys))
                    if x_counts > 4 and y_counts > 4:
                        logs.append(f"[pdfplumber] Стр. {page_idx+1}: Похожая на таблицу регулярность текста (слов в ряд: {x_counts}, строк: {y_counts})")
                    else:
                        logs.append(f"[pdfplumber] Стр. {page_idx+1}: Таблица не найдена")
    except Exception as e:
        logs.append(f"[pdfplumber] Ошибка: {str(e)}")
    return "\n".join(logs)

'''def find_tables_camelot(pdf_path):
    """Ищет таблицы с помощью Camelot."""
    logs = []
    try:
        # По умолчанию Lattice — по линиям, Stream — по тексту
        tables_lattice = camelot.read_pdf(pdf_path, flavor="lattice", strip_text="\n", pages="all")
        tables_stream = camelot.read_pdf(pdf_path, flavor="stream", strip_text="\n", pages="all")
        logs.append(f"[Camelot-Lattice] Всего найдено: {len(tables_lattice)}")
        for t in tables_lattice:
            logs.append(f"[Camelot-Lattice] Стр. {t.page}: таблица (bbox={t._bbox})")
        logs.append(f"[Camelot-Stream] Всего найдено: {len(tables_stream)}")
        for t in tables_stream:
            logs.append(f"[Camelot-Stream] Стр. {t.page}: таблица (bbox={t._bbox})")
    except Exception as e:
        logs.append(f"[Camelot] Ошибка: {str(e)}")
    return "\n".join(logs)'''

def run_all_table_detectors(pdf_path):
    """Выводит сводный лог по всем способам поиска таблиц."""
    logs = []
    logs.append("======= PyMuPDF =======")
    logs.append(find_tables_pymupdf(pdf_path))
    logs.append("\n======= pdfplumber =======")
    logs.append(find_tables_pdfplumber(pdf_path))
    #logs.append("\n======= Camelot =======")
    #logs.append(find_tables_camelot(pdf_path))
    return "\n".join(logs)
