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

# --- Толерансы для «центра» ---
CENTER_TOL_PT = 6.0        # допустимая разница «воздуха» слева/справа
EDGE_TOL_PT   = 4.0        # допускаем лёгкое касание рабочих полей

# --- Обязательная последовательность и опциональные заголовки ---
REQUIRED_ORDER_CORE = [
    "содержание",
    "введение",
    "заключение",
    "список использованных источников",
]
OPTIONAL_SINGLE = "приложение"  # может встречаться 0..N раз, но только ПОСЛЕ списка источников

ALL_ALLOWED = REQUIRED_ORDER_CORE + [OPTIONAL_SINGLE]

# --- Вспомогательные структуры и функции -------------------------------------

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
    lines.sort(key=lambda L: (L.bbox.y0, L.bbox.x0))
    return lines

_norm_trailing_re = re.compile(r"[.;]+\s*$")

def _normalize_for_match(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = _norm_trailing_re.sub("", s)  # убрать завершающие '.' или ';'
    return s.lower()

def _is_all_caps_letters(original: str) -> bool:
    has_letter = False
    for ch in original:
        if ch.isalpha():
            has_letter = True
            if not ch.isupper():
                return False
    return has_letter

def _is_centered_in_workarea(bbox: fitz.Rect, page: fitz.Page) -> Tuple[bool, Dict[str, float]]:
    work_left  = page.rect.x0 + LEFT_MARGIN_PT
    work_right = page.rect.x1 - RIGHT_MARGIN_PT
    x0, x1 = bbox.x0, bbox.x1

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
      {"user_summary": str, "admin_details": str}
    Ставит аннотации в PDF на места нарушений.
    """
    admin_lines: List[str] = []
    issues_count = 0

    found_sequence: List[Tuple[str, int, TextLine]] = []  # (normalized_name, page_num, line)
    pages_with_any_text: List[int] = []

    # --- Поиск всех заголовков и проверка «первая строка страницы» ---
    for pidx in range(len(pdf_document)):
        page = pdf_document[pidx]
        page_num = pidx + 1
        lines = _collect_text_lines(page)
        if not lines:
            continue
        pages_with_any_text.append(page_num)

        for i, ln in enumerate(lines):
            norm = _normalize_for_match(ln.text)
            if norm in ALL_ALLOWED:
                # Строгое совпадение и отсутствие лишнего уже обеспечено нормализацией
                is_first_on_page = (i == 0)
                if not is_first_on_page:
                    issues_count += 1
                    admin_lines.append(
                        f"[StructHeadings][Стр. {page_num}] «{ln.text}» — Заголовки структурных элементов должны "
                        f"располагаться на новой странице (должен быть первой строкой)."
                    )
                    _add_annot(page, (ln.bbox.x0, ln.bbox.y0),
                               "Заголовки структурных элементов должны располагаться на новой странице")
                found_sequence.append((norm, page_num, ln))

    # --- Полнота: требуем присутствие только core (приложение — опционально) ---
    found_names_in_order = [nm for (nm, _, _) in found_sequence]
    missing_core = [nm for nm in REQUIRED_ORDER_CORE if nm not in found_names_in_order]
    if missing_core:
        issues_count += len(missing_core)
        admin_lines.append("[StructHeadings] Отсутствуют обязательные заголовки: " + ", ".join(missing_core))

    # --- Порядок: core строго по заданной последовательности ---
    # Вытащим из найденного только core, в порядке появления
    found_core = [(nm, p, ln) for (nm, p, ln) in found_sequence if nm in REQUIRED_ORDER_CORE]
    if found_core:
        expected = REQUIRED_ORDER_CORE
        idx_seq = [expected.index(nm) for (nm, _, _) in found_core]
        inversions = [(i, idx_seq[i-1], idx_seq[i]) for i in range(1, len(idx_seq)) if idx_seq[i] < idx_seq[i-1]]
        if inversions:
            issues_count += 1
            admin_lines.append("[StructHeadings] Нарушен порядок обязательных заголовков.")
            admin_lines.append("  Индексы (ожидается строго возрастающая последовательность 0..3): " +
                               ", ".join(map(str, idx_seq)))

    # --- Порядок: все «приложение» должны идти ПОСЛЕ «список использованных источников» ---
    found_appendices = [(nm, p, ln) for (nm, p, ln) in found_sequence if nm == OPTIONAL_SINGLE]
    last_core_index_in_all = -1
    # индекс последнего в REQUIRED_ORDER_CORE среди ВСЕХ найденных
    filtered_all = [nm for (nm, _, _) in found_sequence if nm in REQUIRED_ORDER_CORE]
    if filtered_all:
        last_core_name = filtered_all[-1]
        last_core_index_in_all = REQUIRED_ORDER_CORE.index(last_core_name)

    # Чтобы быть строгими: требуем, чтобы ПОСЛЕДНИЙ core в документе был именно "список использованных источников"
    if filtered_all:
        if filtered_all[-1] != "список использованных источников":
            # Если приложения есть, это точно ошибка порядка; если приложений нет — всё равно сообщим, т.к. core не завершён корректным пунктом
            issues_count += 1
            admin_lines.append("[StructHeadings] Последним обязательным заголовком должен быть «Список использованных источников».")

    if found_appendices:
        # проверяем, что до первого «приложение» действительно встретился "список использованных источников"
        if "список использованных источников" not in found_names_in_order:
            issues_count += 1
            admin_lines.append("[StructHeadings] «Приложение» обнаружено, но в документе отсутствует «Список использованных источников».")
        else:
            # позиция последнего обязательного заголовка в общей последовательности
            last_core_pos_in_full = max(i for i, nm in enumerate(found_names_in_order) if nm in REQUIRED_ORDER_CORE)
            # позиция первого приложения
            first_app_pos = min(i for i, nm in enumerate(found_names_in_order) if nm == OPTIONAL_SINGLE)
            if first_app_pos <= last_core_pos_in_full:
                issues_count += 1
                admin_lines.append("[StructHeadings] Все «Приложение» должны идти после «Список использованных источников».")

    # --- Детальные проверки по каждому найденному заголовку (по ОРИГИНАЛУ) ---
    for (norm, page_num, ln) in found_sequence:
        page = pdf_document[page_num - 1]

        # 1) Нет точки/«;» в конце
        trailing_ok = not bool(_norm_trailing_re.search(ln.text))
        if not trailing_ok:
            issues_count += 1
            admin_lines.append(f"[StructHeadings][Стр. {page_num}] «{ln.text}» — недопустим символ в конце ('.' или ';').")
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Уберите точку/точку с запятой в конце заголовка")

        # 2) Все буквы — заглавные
        all_caps_ok = _is_all_caps_letters(ln.text)
        if not all_caps_ok:
            issues_count += 1
            admin_lines.append(f"[StructHeadings][Стр. {page_num}] «{ln.text}» — все буквы в названии должны быть заглавными.")
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Все буквы в названии должны быть заглавными")

        # 3) Центрирование по рабочей области
        centered_ok, metr = _is_centered_in_workarea(ln.bbox, page)
        if not centered_ok:
            issues_count += 1
            admin_lines.append(
                (f"[StructHeadings][Стр. {page_num}] «{ln.text}» — заголовок не по центру рабочих полей. "
                 f"left_air={metr['left_air']:.1f} pt, right_air={metr['right_air']:.1f} pt, |Δ|={metr['diff_air']:.1f} pt "
                 f"(допуск ±{CENTER_TOL_PT:.1f} pt).")
            )
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Заголовок должен быть выровнен по центру рабочих полей")

    # --- Резюме ---
    if issues_count == 0 and found_sequence and not missing_core:
        user_summary = "✅Проверка заголовков структурных элементов: нарушений не обнаружено"
    else:
        if not found_sequence:
            admin_lines.append("[StructHeadings] Не найдено ни одного заголовка из перечня (включая опциональные).")
        user_summary = f"⚠️Проверка заголовков структурных элементов: найдено нарушений — {issues_count}"

    admin_head = f"[StructHeadings] Итог: нарушений={issues_count}"
    admin_details = admin_head + ("\n" + "\n".join(admin_lines) if admin_lines else "\n[StructHeadings] Нарушений не обнаружено")
    return {"user_summary": user_summary, "admin_details": admin_details}
