# scripts/body_text_checker.py
# -*- coding: utf-8 -*-
"""
Проверка основного текста (кроме таблиц, подписей, заголовков, списков и формул).

Для каждого логического абзаца проверяем:
 - Шрифт: Times New Roman, 12–14 pt; без жирного/курсива/подчёркивания.
 - Межстрочный интервал: 1.5 строки (допуск).
 - Красная строка: 1.25 см (≈ 35.43 pt) для 2+ строк.
 - Выравнивание: по ширине (эвристика по правому краю, последняя строка игнорируется).

Исключаем из обработки:
 - Любые строки, пересекающиеся с bbox таблиц (table_bboxes_by_page: {page: [(x0,y0,x1,y1), ...]}).
 - Подписи (Рисунок/Таблица/Продолжение таблицы/Примечание/Схема).
 - Заголовки структурных элементов (РАЗДЕЛ/ГЛАВА/ВВЕДЕНИЕ/ЗАКЛЮЧЕНИЕ/СПИСОК ЛИТЕРАТУРЫ/СОДЕРЖАНИЕ и т.п.),
   а также строки с крупным кеглем и/или ВСЕ ПРОПИСНЫЕ (эвристика).
 - Списки (маркеры •, —, -, цифра.) — по паттернам начала строки и характерному отступу.
 - Формулы (эвристика по символам).

Возвращает:
 {
   "user_summary": "...",
   "admin_details": "...",
 }
и в PDF добавляет текстовые аннотации с краткой причиной.
"""

from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF

MM_TO_PT = 2.8346456693
FIRST_LINE_INDENT_PT = 1.25 * 28.346456693  # 1.25 см
FIRST_LINE_INDENT_TOL_PT = 4.0              # допуск ±4 pt
LINE_SPACING_TARGET = 1.50
LINE_SPACING_TOL = (1.45, 1.62)             # допустимый диапазон
FONT_MIN_PT = 12.0
FONT_MAX_PT = 14.0

# --- эвристики исключений ---
CAPTION_PREFIXES = (
    "рисунок", "таблица", "продолжение таблицы", "примечание", "схема"
)
HEADING_WORDS = (
    "раздел", "глава", "введение", "заключение",
    "список литературы", "содержание", "приложение"
)
LIST_BULLETS = ("•", "–", "-", "—", "∙", "·", "●")

@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]  # raw spans from PyMuPDF

@dataclass
class Paragraph:
    page_index0: int
    lines: List[Line]

# ---------- УТИЛИТЫ ----------

def _font_base_name(font_name: str) -> str:
    if not font_name:
        return ""
    return font_name.split("+", 1)[-1]  # убираем сабсет-префикс

def _is_times_family(font_name: str) -> bool:
    f = _font_base_name(font_name).replace(" ", "").lower()
    return (
        "timesnewroman" in f
        or "times-roman" in f
        or "timesnewromanps" in f
        or f in {"tnr", "timesroman", "times"}
    )

def _has_bold(font_name: str) -> bool:
    f = _font_base_name(font_name).lower()
    return any(k in f for k in ("bold", "black", "heavy", "demibold", "semibold"))

def _has_italic(font_name: str) -> bool:
    f = _font_base_name(font_name).lower()
    return ("italic" in f) or ("oblique" in f)

def _looks_like_formula(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    math_tokens = set("=±≈≡∼∑∏∫√∞≤≥≠×·⋅°′″⊥∥∠ΔΩλμνπσφψθαβγδεζηκξρω")
    ops = set("+-=*/^_()[]{}<>|")
    frac_markers = ["/", "frac", "√", "∑", "∫"]
    score = 0
    score += sum(ch in math_tokens for ch in t)
    score += sum(ch in ops for ch in t)
    score += sum(m in t for m in frac_markers)
    if "_" in t or "^" in t:
        score += 2
    digits = sum(ch.isdigit() for ch in t)
    if digits >= 3:
        score += 1
    return score >= 4

def _is_caption_like(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    for pref in CAPTION_PREFIXES:
        if t.startswith(pref + " "):
            return True
    # «Таблица 1 – ...», «Рисунок 2 – ...»
    if re.match(r"^(таблица|рисунок)\s+[A-Za-zА-Яа-я]?\s*\d", t):
        return True
    return False

def _is_heading_like(text: str, avg_size: float) -> bool:
    t = (text or "").strip()
    low = t.lower()
    if any(low.startswith(h) for h in HEADING_WORDS):
        return True
    # «все ПРОПИСНЫЕ» (но игнорируем цифры/пробелы/пунктуацию)
    letters = "".join(ch for ch in t if ch.isalpha())
    if letters and letters.upper() == letters and len(letters) >= 3:
        return True
    # крупный кегль (часто заголовок)
    if avg_size > (FONT_MAX_PT + 1.0):
        return True
    return False

def _is_list_like(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if any(t.startswith(b + " ") for b in LIST_BULLETS):
        return True
    if re.match(r"^\(?\d+(\.\d+)*[\.\)]\s+", t):
        return True
    return False

def _line_intersects_any(b: fitz.Rect, boxes: List[Tuple[float, float, float, float]], min_cover_ratio=0.30) -> bool:
    """Есть ли пересечение линии с любым bbox так, что >=min_cover_ratio площади линии покрыта пересечением."""
    if not boxes:
        return False
    line_area = max(1.0, (b.x1 - b.x0) * (b.y1 - b.y0))
    for (x0, y0, x1, y1) in boxes:
        inter = fitz.Rect(max(b.x0, x0), max(b.y0, y0), min(b.x1, x1), min(b.y1, y1))
        if inter.is_empty:
            continue
        inter_area = (inter.x1 - inter.x0) * (inter.y1 - inter.y0)
        if inter_area / line_area >= min_cover_ratio:
            return True
    return False

def _collect_text_lines(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            xs, ys, spans = [], [], []
            texts, sizes, fonts = [], [], []
            for sp in line.get("spans", []):
                x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                t = (sp.get("text") or "")
                if t.strip():
                    xs += [x0, x1]; ys += [y0, y1]
                    sizes.append(float(sp.get("size", 0)))
                    fonts.append(sp.get("font", ""))
                    spans.append(sp)
                    texts.append(t)
            if not xs:
                continue
            text = "".join(texts).strip()
            if not text:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes) / len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            out.append(Line(text=text, bbox=rect, size=size, font=font, spans=spans))
    # сортируем сверху-вниз, потом слева-направо
    out.sort(key=lambda L: (L.bbox.y0, L.bbox.x0))
    return out

def _group_lines_into_paragraphs(lines: List[Line], y_gap_break_ratio=1.75) -> List[Paragraph]:
    """
    Склейка последовательных строк в абзац.
    Разрыв абзаца: вертикальный зазор > y_gap_break_ratio * средний кегль пары.
    """
    paras: List[Paragraph] = []
    cur: List[Line] = []
    for i, ln in enumerate(lines):
        if not cur:
            cur = [ln]
            continue
        prev = cur[-1]
        dy = ln.bbox.y0 - prev.bbox.y0
        avg_sz = max(1.0, (ln.size + prev.size) / 2.0)
        if dy > y_gap_break_ratio * avg_sz:
            paras.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        paras.append(cur)
    return [Paragraph(page_index0=-1, lines=p) for p in paras]

def _measure_line_spacing_ratio(lines: List[Line]) -> Optional[float]:
    if len(lines) < 2:
        return None
    d = []
    s = []
    for i in range(1, len(lines)):
        d.append(lines[i].bbox.y0 - lines[i-1].bbox.y0)
        s.append((lines[i].size + lines[i-1].size) / 2.0)
    if not d or not s:
        return None
    mean_d = sum(d) / len(d)
    mean_s = max(1.0, sum(s) / len(s))
    return mean_d / mean_s

def _detect_justify(lines: List[Line], tol_pt=4.0) -> str:
    """
    Эвристика «по ширине»: у непоследних строк правые края (x1) практически равны.
    Возвращает 'justify' | 'left' | 'center/right' | 'unknown'
    """
    if not lines:
        return "unknown"
    if len(lines) == 1:
        return "unknown"  # для однострочных вывод по выравниванию не фиксируем

    x1s = [ln.bbox.x1 for ln in lines[:-1]]  # игнорируем последнюю строку
    if not x1s:
        return "unknown"
    spread = max(x1s) - min(x1s)
    if spread <= tol_pt:
        return "justify"

    # центр / право: оценим симметрию левого и правого «воздуха» относительно общей коробки
    x0s = [ln.bbox.x0 for ln in lines[:-1]]
    left_spread = max(x0s) - min(x0s)
    if left_spread <= tol_pt and spread <= 2.5 * tol_pt:
        return "center/right"
    return "left"

def _para_bbox(par: Paragraph) -> fitz.Rect:
    xs, ys = [], []
    for ln in par.lines:
        xs += [ln.bbox.x0, ln.bbox.x1]
        ys += [ln.bbox.y0, ln.bbox.y1]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))

def _add_text_annot_silent(page: fitz.Page, point_xy: Tuple[float, float], msg: str):
    try:
        ann = page.add_text_annot(fitz.Point(*point_xy), msg)
        ann.set_info(title="Сервис нормоконтроля", content=msg)
        ann.update()
    except Exception:
        pass

# ---------- ОСНОВНАЯ ФУНКЦИЯ ----------

def check_body_text(pdf_document: fitz.Document,
                    table_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
                    start_page: int = 1) -> Dict[str, str]:
    """
    Проверяет основной текст документа и аннотирует нарушения.
    table_bboxes_by_page — словарь из pdf_table_checker.check_tables(...).
    """
    admin_lines: List[str] = []
    error_pages = set()
    total_paras = 0
    bad_paras = 0

    for page_idx0 in range(len(pdf_document)):
        page_num = page_idx0 + 1
        if page_num < start_page:
            continue

        page = pdf_document[page_idx0]
        all_lines = _collect_text_lines(page)

        # 1) исключаем пересечение с таблицами
        tbl_boxes = (table_bboxes_by_page or {}).get(page_num, [])
        lines = [
            ln for ln in all_lines
            if not _line_intersects_any(ln.bbox, tbl_boxes, min_cover_ratio=0.30)
        ]

        # 2) предварительный фильтр (подписи, заголовки, списки, формулы)
        filtered: List[Line] = []
        for ln in lines:
            if _is_caption_like(ln.text):
                continue
            if _is_list_like(ln.text):
                continue
            if _looks_like_formula(ln.text):
                continue
            # грубая фильтрация заголовков по одной строке
            if _is_heading_like(ln.text, ln.size):
                continue
            filtered.append(ln)

        if not filtered:
            continue

        paras = _group_lines_into_paragraphs(filtered, y_gap_break_ratio=1.75)
        # допишем page index (для bbox и аннотаций)
        for p in paras:
            p.page_index0 = page_idx0

        # 3) валидации по каждому абзацу
        for par in paras:
            total_paras += 1
            lines = par.lines
            par_bbox = _para_bbox(par)

            # эвристика: если пара состоит из 1 строки — пропускаем красную строку и выравнивание
            single_line = (len(lines) == 1)

            # --- шрифты/начертание/кегль ---
            font_issues = []
            for ln in lines:
                for sp in ln.spans:
                    f = sp.get("font", "")
                    s = float(sp.get("size", 0.0))
                    # шрифт
                    if not _is_times_family(f):
                        font_issues.append(f"Не Times New Roman: {f}")
                    # кегль
                    if s < (FONT_MIN_PT - 0.1) or s > (FONT_MAX_PT + 0.1):
                        font_issues.append(f"Размер {s:.1f} pt вне диапазона 12–14 pt")
                    # начертание
                    if _has_bold(f):
                        font_issues.append("Жирное начертание недопустимо")
                    if _has_italic(f):
                        font_issues.append("Курсив недопустим")
                    if bool(sp.get("underline", False)):
                        font_issues.append("Подчёркнутый текст недопустим")

            # --- межстрочный интервал ---
            spacing_issue = None
            ratio = _measure_line_spacing_ratio(lines)
            if ratio is not None:
                lo, hi = LINE_SPACING_TOL
                if not (lo <= ratio <= hi):
                    spacing_issue = f"Межстрочный интервал не 1.5 (получено {ratio:.2f})"

            # --- красная строка (для 2+ строк) ---
            indent_issue = None
            if not single_line and len(lines) >= 2:
                first_x0 = lines[0].bbox.x0
                body_left = min(ln.bbox.x0 for ln in lines[1:])
                indent = first_x0 - body_left
                if abs(indent - FIRST_LINE_INDENT_PT) > FIRST_LINE_INDENT_TOL_PT:
                    if indent < (FIRST_LINE_INDENT_PT - FIRST_LINE_INDENT_TOL_PT):
                        indent_issue = f"Нет/маленькая красная строка: {indent:.1f} pt (нужно ~{FIRST_LINE_INDENT_PT:.1f} pt)"
                    else:
                        indent_issue = f"Слишком большая красная строка: {indent:.1f} pt (нужно ~{FIRST_LINE_INDENT_PT:.1f} pt)"

            # --- выравнивание (только если 2+ строк) ---
            align_issue = None
            if not single_line and len(lines) >= 2:
                align = _detect_justify(lines, tol_pt=4.0)
                if align != "justify":
                    align_issue = "Неверное выравнивание: требуется по ширине"

            # собрать нарушения для абзаца
            issues = []
            if font_issues:
                # уберём дубли
                seen = set()
                uniq = []
                for msg in font_issues:
                    if msg not in seen:
                        seen.add(msg)
                        uniq.append(msg)
                # ограничим до читабельного количества в аннотации
                issues += uniq[:6]
            if spacing_issue:
                issues.append(spacing_issue)
            if indent_issue:
                issues.append(indent_issue)
            if align_issue:
                issues.append(align_issue)

            if issues:
                bad_paras += 1
                error_pages.add(page_num)
                sample = (lines[0].text[:120] + "…") if len(lines[0].text) > 120 else lines[0].text
                admin_lines.append(
                    f"[BodyText][Стр. {page_num}] Абзац: «{sample}»\n  - " + "\n  - ".join(issues)
                )
                # аннотация в PDF
                _add_text_annot_silent(pdf_document[par.page_index0], (par_bbox.x0, par_bbox.y0),
                                       "Основной текст:\n" + "\n".join(f"• {m}" for m in issues))

    # ----- отчёты -----
    if bad_paras:
        user_summary = f"⚠️Проверка основного текста: нарушений в {bad_paras} из {total_paras} абзацев"
    else:
        user_summary = "✅Проверка основного текста: нарушений не обнаружено"

    admin_details = (
        f"[BodyText] Проверено абзацев: {total_paras}, нарушений: {bad_paras}\n" +
        ("\n".join(admin_lines) if admin_lines else "[BodyText] Нарушений не обнаружено")
    )

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
    }
