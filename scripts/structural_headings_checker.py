from dataclasses import dataclass
from typing import List, Dict, Tuple
import re
import fitz  # PyMuPDF

MM_TO_PT = 2.8346456693
CM_TO_PT = 28.35
LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# Допуски
CENTER_TOL_PT = 6.0
EDGE_TOL_PT   = 4.0
FONT_MIN_PT   = 12.0
FONT_MAX_PT   = 14.0
FONT_TOL_PT   = 0.1

def _size_for_check(x: float) -> float:
    # округляем в ту же точность, что и в сообщениях — до десятых
    return round(float(x), 1)

# Набор обязательных заголовков
REQUIRED_ORDER_CORE = [
    "содержание",
    "введение",
    "заключение",
    "список использованных источников",
]

OPTIONAL_REFS = "реферат"
OPTIONAL_TERMS = "термины и определения"
OPTIONAL_ABBREV = "перечень сокращений и обозначений"

ALL_ALLOWED_CORE = set(REQUIRED_ORDER_CORE)
ALL_ALLOWED_OPTIONALS = {OPTIONAL_REFS, OPTIONAL_TERMS, OPTIONAL_ABBREV}

# Обработка приложений
APP_REGEX = re.compile(r"^приложение(?:\s+[0-9a-zа-я])?$", re.IGNORECASE)

# Объект для удобного хранения параметров строк
@dataclass
class TextLine:
    page_index0: int
    text: str
    bbox: fitz.Rect
    spans: List[dict]

# Бежим по всему документу, текстовые блоки -> строки -> спаны
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

#убрать точки, запятые в конце
_norm_trailing_re = re.compile(r"[.;]+\s*$")

def _normalize_for_match(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = _norm_trailing_re.sub("", s)
    return s.lower()

#проверка на приложение
def _is_appendix_norm(norm: str) -> bool:
    return bool(APP_REGEX.match(norm))

def _is_all_caps_letters(original: str) -> bool:
    has_letter = False
    for ch in original:
        if ch.isalpha():
            has_letter = True
            if not ch.isupper():
                return False
    return has_letter



def _font_base_name(font_name: str) -> str:
    if not font_name:
        return ""
    return font_name.split("+", 1)[-1]

def _is_times_family(font_name: str) -> bool:
    f = _font_base_name(font_name).replace(" ", "").lower()
    return ("timesnewroman" in f) or ("times-roman" in f) or ("timesnewromanps" in f) or (f in {"tnr","timesroman","times"})

def _has_bold(font_name: str) -> bool:
    f = _font_base_name(font_name).lower()
    return any(k in f for k in ("bold","black","heavy","demibold","semibold"))

def _has_italic(font_name: str) -> bool:
    f = _font_base_name(font_name).lower()
    return ("italic" in f) or ("oblique" in f)



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
    admin_lines: List[str] = []
    issues_count = 0

    # found: (norm_name, page_num, line, kind), kind in {"core","appendix","optional"}
    found: List[Tuple[str, int, TextLine, str]] = []

    # сбор кандидатов и проверка "первая текстовая строка страницы"
    for pidx in range(len(pdf_document)):
        page = pdf_document[pidx]
        page_num = pidx + 1
        lines = _collect_text_lines(page)
        if not lines:
            continue

        for i, ln in enumerate(lines):
            norm = _normalize_for_match(ln.text)

            if norm in ALL_ALLOWED_CORE:
                found.append((norm, page_num, ln, "core"))
                lines_above = [l for l in lines[:i] if l.text.strip()]
                if lines_above:
                    issues_count += 1
                    admin_lines.append(
                        f"[StructHeadings][Стр. {page_num}] «{ln.text}» — Заголовки структурных элементов должны располагаться на новой странице (должен быть первой строкой)."
                    )
                    _add_annot(page, (ln.bbox.x0, ln.bbox.y0),
                               "Заголовки структурных элементов должны располагаться на новой странице")
                continue

            if norm in ALL_ALLOWED_OPTIONALS:
                found.append((norm, page_num, ln, "optional"))
                lines_above = [l for l in lines[:i] if l.text.strip()]
                if lines_above:
                    issues_count += 1
                    admin_lines.append(
                        f"[StructHeadings][Стр. {page_num}] «{ln.text}» — заголовки должны располагаться на новой странице (первая строка)."
                    )
                    _add_annot(page, (ln.bbox.x0, ln.bbox.y0),
                               "Заголовок структурного элемента должен быть первой строкой страницы")
                continue

            if _is_appendix_norm(norm):
                found.append(("приложение", page_num, ln, "appendix"))
                lines_above = [l for l in lines[:i] if l.text.strip()]
                if lines_above:
                    issues_count += 1
                    admin_lines.append(
                        f"[StructHeadings][Стр. {page_num}] «{ln.text}» — Заголовки структурных элементов должны располагаться на новой странице (должен быть первой строкой)."
                    )
                    _add_annot(page, (ln.bbox.x0, ln.bbox.y0),
                               "Заголовки структурных элементов должны располагаться на новой странице")
                continue

    # Каких обязательных заголовков нет 
    found_names_in_order = [nm for (nm, _, _, _) in found]
    missing_core = [nm for nm in REQUIRED_ORDER_CORE if nm not in found_names_in_order]
    if missing_core:
        issues_count += len(missing_core)
        msg = "Отсутствуют обязательные заголовки: " + ", ".join(missing_core)
        admin_lines.append("[StructHeadings] " + msg)
        target_page_index = 1 if len(pdf_document) > 1 else 0
        _add_annot(pdf_document[target_page_index], (LEFT_MARGIN_PT, TOP_MARGIN_PT), msg)

    # Порядок  
    found_core = [(nm, p, ln) for (nm, p, ln, k) in found if k == "core"]
    if found_core:
        expected = REQUIRED_ORDER_CORE
        idx_seq = [expected.index(nm) for (nm, _, _) in found_core]
        inversions = [(i, idx_seq[i-1], idx_seq[i]) for i in range(1, len(idx_seq)) if idx_seq[i] < idx_seq[i-1]]
        if inversions:
            issues_count += 1
            admin_lines.append("[StructHeadings] Нарушен порядок обязательных заголовков (ожидается «Содержание → Введение → Заключение → Список использованных источников»).")
            admin_lines.append("  Индексы: " + ", ".join(map(str, idx_seq)))

    # Порядок для опциональных
    pos = {name: i for i, name in enumerate(found_names_in_order)}
    if OPTIONAL_REFS in pos and "содержание" in pos:
        if not (pos[OPTIONAL_REFS] < pos["содержание"]):
            issues_count += 1
            admin_lines.append("[StructHeadings] «Реферат» должен располагаться ДО «Содержание».")
    for opt_name in (OPTIONAL_TERMS, OPTIONAL_ABBREV):
        if opt_name in pos:
            ok = True
            if "содержание" in pos and not (pos[opt_name] > pos["содержание"]):
                ok = False
            if "введение" in pos and not (pos[opt_name] < pos["введение"]):
                ok = False
            if not ok:
                issues_count += 1
                admin_lines.append(f"[StructHeadings] «{opt_name.capitalize()}» должен располагаться ПОСЛЕ «Содержание» и ДО «Введение».")
    found_appendices = [(nm, p, ln) for (nm, p, ln, k) in found if k == "appendix"]
    if found_appendices:
        if "список использованных источников" not in pos:
            issues_count += 1
            admin_lines.append("[StructHeadings] «Приложение» обнаружено, но отсутствует «Список использованных источников».")
        else:
            last_core_pos_in_full = pos["список использованных источников"]
            first_app_pos = min(i for i, nm in enumerate(found_names_in_order) if nm == "приложение")
            if first_app_pos <= last_core_pos_in_full:
                issues_count += 1
                admin_lines.append("[StructHeadings] Все «Приложение» должны идти ПОСЛЕ «Список использованных источников».")

    # Детальные проверки каждого найденного заголовка
    for (nm, page_num, ln, kind) in found:
        page = pdf_document[page_num - 1]

        #нет точки/";" в конце
        trailing_ok = not bool(_norm_trailing_re.search(ln.text))
        if not trailing_ok:
            issues_count += 1
            admin_lines.append(f"[StructHeadings][Стр. {page_num}] «{ln.text}» — недопустим символ в конце ('.' или ';').")
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Уберите точку/точку с запятой в конце заголовка")

        #все буквы заглавные
        all_caps_ok = _is_all_caps_letters(ln.text)
        if not all_caps_ok:
            issues_count += 1
            admin_lines.append(f"[StructHeadings][Стр. {page_num}] «{ln.text}» — все буквы в названии должны быть заглавными.")
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Все буквы в названии должны быть заглавными")

        #центрирование по рабочей области
        centered_ok, metr = _is_centered_in_workarea(ln.bbox, page)
        if not centered_ok:
            issues_count += 1
            admin_lines.append(
                (f"[StructHeadings][Стр. {page_num}] «{ln.text}» — заголовок структурного элемента не по центру рабочих полей. "
                 f"left_air={metr['left_air']:.1f} pt, right_air={metr['right_air']:.1f} pt, |Δ|={metr['diff_air']:.1f} pt "
                 f"(допуск ±{CENTER_TOL_PT:.1f} pt).")
            )
            shift_pt = (metr['left_air'] - metr['right_air']) / 2.0
            shift_mm = shift_pt / MM_TO_PT
            admin_lines.append(
                f"[StructHeadings][Стр. {page_num}] Подсказка: центр смещён на {shift_pt:+.1f} pt (~{shift_mm:+.1f} мм). "
                "Проверьте отступы/табуляцию/контейнер."
            )
            _add_annot(page, (ln.bbox.x0, ln.bbox.y0), "Заголовок структурного элемента должен быть выровнен по центру рабочих полей")

        #шрифтовые требования 
        font_issues = []
        for sp in ln.spans:
            f = sp.get("font", "")
            s_raw = float(sp.get("size", 0.0))
            s = _size_for_check(s_raw)  # округление до десятых
            if not _is_times_family(f):
                font_issues.append(f"Не Times New Roman: {f}")
            if s < (FONT_MIN_PT - FONT_TOL_PT) or s > (FONT_MAX_PT + FONT_TOL_PT):
                font_issues.append(
                    f"Размер {s:.1f} pt вне диапазона {FONT_MIN_PT:.0f}–{FONT_MAX_PT:.0f} pt (допуск ±{FONT_TOL_PT:.1f})"
                )
            if not _has_bold(f):
                font_issues.append("Заголовок должен быть жирным")
            if _has_italic(f):
                font_issues.append("Курсив недопустим")

        if font_issues:
            issues_count += 1
            uniq, seen = [], set()
            for m in font_issues:
                if m not in seen:
                    seen.add(m); uniq.append(m)
            admin_lines.append(
                f"[StructHeadings][Стр. {page_num}] «{ln.text}» — нарушения шрифтовых требований:\n  - " +
                "\n  - ".join(uniq)
            )
            _add_annot(
                page, (ln.bbox.x0, ln.bbox.y0),
                "Заголовок структурного элемента: проверьте шрифт (Times New Roman, 12–14 pt, жирный, без курсива)"
            )

    if issues_count == 0 and ([nm for (nm,_,_,k) in found if k=="core"]) and not missing_core:
        user_summary = "✅Проверка заголовков структурных элементов"
    else:
        if not found:
            admin_lines.append("[StructHeadings] Не найдено ни одного заголовка из перечня (включая опциональные).")
        user_summary = f"⚠️Проверка заголовков структурных элементов: найдено нарушений — {issues_count}"

    admin_head = f"[StructHeadings] Итог: нарушений={issues_count}"
    admin_details = admin_head + ("\n" + "\n".join(admin_lines) if admin_lines else "\n[StructHeadings] Нарушений не обнаружено")
    return {"user_summary": user_summary, "admin_details": admin_details}
