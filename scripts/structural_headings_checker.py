# -*- coding: utf-8 -*-
"""
Проверка заголовков структурных элементов (ГОСТ 7.32-2017, укороченная логика из ТЗ).

Ищем по строкам каждой страницы строгие заголовки:
  ОБЯЗАТЕЛЬНЫЕ И В ПОРЯДКЕ:
    1) "содержание"
    2) "введение"
    3) "заключение"
    4) "список использованных источников"
    5) "приложения"

Правила:
- Сопоставление ведём на НОРМАЛИЗОВАННОЙ строке (нижний регистр, схлопнутые пробелы,
  удалены завершающие '.' или ';'). В строке не должно быть ничего лишнего.
- Заголовок ДОЛЖЕН быть ПЕРВОЙ строкой страницы — иначе ошибка:
  "Заголовки структурных элементов должны располагаться на новой странице".
- По найденному (ОРИГИНАЛЬНОМУ) тексту проверяем:
  • все буквы заглавные,
  • выравнивание по центру относительно рабочих полей (левое 3 см, правое 1.5 см),
  • нет точки/точки с запятой в конце.

Скрипт ставит аннотации в PDF и возвращает user_summary / admin_details.
"""

from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF

# --- Геометрия страницы / поля ---
MM_TO_PT = 2.8346456693
CM_TO_PT = 28.35

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT   # 3 см
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT   # 1.5 см
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# --- Толерансы/пороговые значения для «центра» ---
CENTER_TOL_PT = 6.0        # допустимая разница «воздуха» слева/справа от рабочей области
EDGE_TOL_PT   = 4.0        # допустимое «касание» рабочих полей

# --- Обязательная последовательность ---
REQUIRED_ORDER = [
    "содержание",
    "введение",
    "заключение",
    "список использованных источников",
    "приложения",
]

# --- Утилиты -----------------------------------------------------------------

@dataclass
class TextLine:
    page_index0: int
    text: str
    bbox: fitz.Rect
    spans: List[dict]

def _collect_text_lines(page: fitz.Page) -> List[TextLine]:
    lines: List[TextLine] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys, texts, spans = [], [], [], []
            for sp in ln.get("spans", []):
                t = sp.get("text") or ""
                if t.strip():
                    x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                    xs += [x0, x1]; ys += [y0, y1]
                    texts.append(t); spans.append(sp)
            if not xs:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            text = "".join(texts).strip()
            if text:
                lines.append(TextLine(page.number, text, rect, spans))
    # порядок чтения: сверху-вниз, затем слева-направо
    lines.sort(key=lambda L: (L.bbox.y0, L.bbox.x0))
    return lines

_norm_trailing_re = re.compile(r"[.;]+\s*$")

def _normalize_for_match(s: str) -> str:
    # 1) trim
    s = (s or "").strip()
    # 2) схлопнуть множественные пробелы
    s = re.sub(r"\s+", " ", s)
    # 3) убрать завершающие '.' / ';'
    s = _norm_trailing_re.sub("", s)
    # 4) нижний регистр
    return s.lower()

def _is_all_caps_letters(original: str) -> bool:
    """
    Проверяем, что все буквенные символы — заглавные.
    Цифры/пробелы/знаки препинания не мешают.
    Учитываем кириллицу/латиницу.
    """
    has_letter = False
    for ch in original:
        if ch.isalpha():
            has_letter = True
            # isupper() корректно работает для кириллицы в Python
            if not ch.isupper():
                return False
    return has_letter  # хотя бы одна буква должна быть

def _is_centered_in_workarea(line: TextLine, page: fitz.Page) -> Tuple[bool, Dict[str, float]]:
    """
    Центрирование относительно рабочих полей:
      work_left = page.rect.x0 + LEFT_MARGIN_PT
      work_right = page.rect.x1 - RIGHT_MARGIN_PT

    Условия:
      - x0 >= work_left - EDGE_TOL_PT, x1 <= work_right + EDGE_TOL_PT
      - |(x0 - work_left) - (work_right - x1)| <= CENTER_TOL_PT
    """
    work_left  = page.rect.x0 + LEFT_MARGIN_PT
    work_right = page.rect.x1 - RIGHT_MARGIN_PT
    x0, x1 = line.bbox.x0, line.bbox.x1

    left_air  = max(0.0, x0 - work_left)
    right_air = max(0.0, work_right - x1)
    span_ok = (x0 >= work_left - EDGE_TOL_PT) and (x1 <= work_right + EDGE_TOL_PT)
    balance_ok = abs(left_air - right_air) <= CENTER_TOL_PT

    ok = span_ok and balance_ok
    return ok, {
        "work_left": work_left, "work_right": work_right,
        "x0": x0, "x1": x1, "left_air": left_air, "right_air": right_air,
        "diff_air": abs(left_air - right_air)
    }

def _add_annot(page: fitz.Page, where: Tuple[float, float], msg: str):
    try:
        ann = page.add_text_annot(fitz.Point(*where), msg)
        ann.set_info(title="Сервис нормоконтроля", content=msg)
        ann.update()
    except Exception:
        pass

# --- Главная проверка --------------------------------------------------------

def check_structural_headings(pdf_document: fitz.Document) -> Dict[str, str]:
    """
    Возвращает:
      {
        "user_summary": "...",
        "admin_details": "..."
      }
    Ставит аннотации в PDF.
    """
    admin: List[str] = []
    issues_count = 0
    found_sequence: List[Tuple[str, int, TextLine]] = []  # (normalized_name, page_num, line)

    # Собираем все кандидаты + проверяем «первая строка страницы»
    for pidx in range(len(pdf_document)):
        page = pdf_document[pidx]
        page_num = pidx + 1
        lines = _collect_text_lines(page)
        if not lines:
            continue

        # Первая значимая строка страницы
        first_line = lines[0]

        for i, ln in enumerate(lines):
            norm = _normalize_for_match(ln.text)
            if norm in REQUIRED_ORDER:
                # Строгая проверка «ничего лишнего» — нормализованная строка ДОЛЖНА совпасть
                # (это уже так: мы сравниваем именно norm с эталоном)
                is_first_on_page = (i == 0)
                if not is_first_on_page:
                    issues_count += 1
                    msg = ("[StructHeadings][Стр. {pg}] «{orig}» — Заголовки структурных элементов должны "
                           "располагаться на новой странице (должен быть первой строкой).").format(
                        pg=page_num, orig=ln.text
                    )
                    admin.append(msg)
                    _add_annot(page, (ln.bbox.x0, ln.bbox.y0),
                               "Заголовки структурных элементов должны располагаться на новой странице")
                found_sequence.append((norm, page_num, ln))

    # Проверка полноты и порядка
    # Собираем только список нормализованных имён в порядке появления
    found_names_in_order = [nm for (nm, _, _) in found_sequence]

    # 1) Полнота
    missing = [nm for nm in REQUIRED_ORDER if nm not in found_names_in_order]
    if missing:
        issues_count += len(missing)
        admin.append("[StructHeadings] Отсутствуют обязательные заголовки: " + ", ".join(missing))

    # 2) Порядок (если что-то нашли)
    if found_names_in_order:
        # Фильтруем только те, что входят в REQUIRED_ORDER
        filtered = [nm for nm in found_names_in_order if nm in REQUIRED_ORDER]
        expected = REQUIRED_ORDER
        # Строим «индексы» найденных
        idx_seq = [expected.index(nm) for nm in filtered]
        # Должно быть строго возрастающей подпоследовательностью без инверсий
        inversions = [(i, idx_seq[i-1], idx_seq[i]) for i in range(1, len(idx_seq)) if idx_seq[i] < idx_seq[i-1]]
        if inversions:
            issues_count += 1
            admin.append("[StructHeadings] Нарушен порядок расположения обязательных заголовков.")
            # Для наглядности добавим пары, где был сбой
            admin.append("  Индексы (ожидается возрастающая последовательность 0..4): " + ", ".join(map(str, idx_seq)))

    # Детальные проверки по каждому найденному заголовку (по исходному тексту)
    for (norm, page_num, ln) in found_sequence:
        page = pdf_document[page_num - 1]

        # 1) Нет точки/«;» в конце (в ОРИГИНАЛЕ)
        trailing_punct_ok = not bool(_norm_trailing_re.search(ln.text))
        if not trailing_punct_ok:
            issues_count += 1
            admin.append(f"[StructHeadings][Стр. {page_num}] «{ln.text}» — недопустим символ в конце ('.' или ';').")
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Уберите точку/точку с запятой в конце заголовка")

        # 2) Все буквы — заглавные (в ОРИГИНАЛЕ)
        all_caps_ok = _is_all_caps_letters(ln.text)
        if not all_caps_ok:
            issues_count += 1
            admin.append(f"[StructHeadings][Стр. {page_num}] «{ln.text}» — все буквы в названии должны быть заглавными.")
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Все буквы в названии должны быть заглавными")

        # 3) Центрирование по рабочей области (лев. 3 см, прав. 1.5 см)
        centered_ok, metr = _is_centered_in_workarea(ln, page)
        if not centered_ok:
            issues_count += 1
            admin.append(
                (f"[StructHeadings][Стр. {page_num}] «{ln.text}» — заголовок не по центру рабочих полей. "
                 f"left_air={metr['left_air']:.1f} pt, right_air={metr['right_air']:.1f} pt, |Δ|={metr['diff_air']:.1f} pt "
                 f"(допуск ±{CENTER_TOL_PT:.1f} pt).")
            )
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Заголовок должен быть выровнен по центру рабочих полей")

    # Резюме для пользователя / админ-логи
    if issues_count == 0 and found_sequence and not missing:
        user_summary = "✅Проверка заголовков структурных элементов: нарушений не обнаружено"
    else:
        # Если вообще не нашли ни одного — сообщим об этом явно
        if not found_sequence:
            admin.append("[StructHeadings] Не найдено ни одного обязательного заголовка из списка.")
        user_summary = f"⚠️Проверка заголовков структурных элементов: найдено нарушений — {issues_count}"

    admin_head = "[StructHeadings] Итог: нарушений={}".format(issues_count)
    admin_details = admin_head + ("\n" + "\n".join(admin) if admin else "\n[StructHeadings] Нарушений не обнаружено")
    return {"user_summary": user_summary, "admin_details": admin_details}

