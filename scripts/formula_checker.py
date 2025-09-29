# scripts/formula_checker
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict
from const import *  # ожидаются LEFT/RIGHT/TOP/BOTTOM_MARGIN_PT, DEBUG_DIAGNOSTICS

MATH_FONTS = {"Cambria Math"}        # при необходимости расширяйте
NUMBER_RE = re.compile(r"^\(\d+\)$") # номер формулы целиком в строке
TOL_Y = 3.0                          # pt: принадлежность одной «логической строке»
TOL_CENTER = 6.0                     # pt: допуск по центрированию
MIN_LINE_GAP = 12.0                  # pt: свободная строка сверху/снизу
TOL_RIGHT = 6.0                      # pt: допуск попадания номера в край по правому полю

@dataclass
class FormulaLine:
    page_index0: int
    bbox: fitz.Rect             # объединённый bbox формулы (и номера, если сшит)
    number: Optional[int]       # номер, если найден
    number_bbox: Optional[fitz.Rect]
    text_joined: str            # текст строки формулы (без номера)
    math_ratio: float           # доля math-символов в строке

def _rect_union(rects: List[fitz.Rect]) -> Optional[fitz.Rect]:
    if not rects:
        return None
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return r

def _work_center(page_bound: fitz.Rect) -> float:
    return (page_bound.x0 + LEFT_MARGIN_PT + page_bound.x1 - RIGHT_MARGIN_PT) / 2.0

def _center_ok(bb: fitz.Rect, work_left: float, work_right: float, tol_pt: float = TOL_CENTER) -> bool:
    mid = (work_left + work_right) / 2.0
    return abs(((bb.x0 + bb.x1) / 2.0) - mid) <= tol_pt

def _inside_work_area(bb: fitz.Rect, page_bound: fitz.Rect) -> bool:
    return (
        bb.x0 >= page_bound.x0 + LEFT_MARGIN_PT - 0.5 and
        bb.x1 <= page_bound.x1 - RIGHT_MARGIN_PT + 0.5 and
        bb.y0 >= page_bound.y0 + TOP_MARGIN_PT - 0.5 and
        bb.y1 <= page_bound.y1 - BOTTOM_MARGIN_PT + 0.5
    )

def _is_math_char(c: str) -> bool:
    if not c:
        return False
    code = ord(c)
    # греческий + math operators (быстро и достаточно надежно)
    return (0x0370 <= code <= 0x03FF) or (0x2200 <= code <= 0x22FF)

def _collect_lines_raw(page: fitz.Page) -> List[dict]:
    """
    Берём 'rawdict', чтобы добраться до посимвольных bbox.
    Возвращаем список линий с полями:
      {
        "spans": [...],
        "bbox": fitz.Rect,
        "text": str,
        "y_mid": float
      }
    """
    out: List[dict] = []
    d = page.get_text("rawdict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs: List[float] = []
            ys: List[float] = []
            text_parts: List[str] = []
            spans = ln.get("spans", [])
            for sp in spans:
                bb = sp.get("bbox", [0, 0, 0, 0])
                xs += [bb[0], bb[2]]
                ys += [bb[1], bb[3]]
                text_parts.append(sp.get("text", "") or "")
            if xs:
                rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                if rect.get_area() > 0:
                    out.append({
                        "spans": spans,
                        "bbox": rect,
                        "text": "".join(text_parts).strip(),
                        "y_mid": (rect.y0 + rect.y1) / 2.0,
                    })
    return out

def _line_math_chars_bbox(line: dict) -> Tuple[Optional[fitz.Rect], float, str]:
    """
    Возвращает объединённый bbox ВСЕХ символов формулы из строки (по chars),
    оценку math_ratio и текст без номера.
    """
    char_rects: List[fitz.Rect] = []
    total_chars = 0
    math_chars = 0
    text_no_num = []
    for sp in line["spans"]:
        text = sp.get("text", "") or ""
        font = str(sp.get("font", ""))
        chars = sp.get("chars", []) or []
        # считаем «математический» символ: либо шрифт, либо класс символов
        for ch in chars:
            c = ch.get("c", "")
            if not c:
                continue
            total_chars += 1
            if font in MATH_FONTS or _is_math_char(c):
                bb = ch.get("bbox", None)
                if bb:
                    char_rects.append(fitz.Rect(*map(float, bb)))
                math_chars += 1
        text_no_num.append(text)

    # если символов нет — вернуть None
    if total_chars == 0:
        return (None, 0.0, "")

    ratio = math_chars / max(1, total_chars)
    # вычистим номер из текста (если есть)
    joined = "".join(text_no_num).strip()
    if NUMBER_RE.fullmatch(joined):
        # это чисто номер — формулы тут нет
        joined_wo_num = ""
    else:
        joined_wo_num = NUMBER_RE.sub("", joined).strip()

    return (_rect_union(char_rects), ratio, joined_wo_num)

def _y_close(a: float, b: float, tol: float = TOL_Y) -> bool:
    return abs(a - b) <= tol

def _try_attach_number(lines: List[dict], formula_line: dict, bbox_formula: fitz.Rect) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    """
    Ищем справа на той же логической высоте короткую строку-номер (N) в скобках.
    Берём самую правую подходящую.
    """
    candidates: List[Tuple[int, fitz.Rect]] = []
    for ln in lines:
        if not _y_close(ln["y_mid"], formula_line["y_mid"]):
            continue
        txt = ln["text"]
        if not txt:
            continue
        if not NUMBER_RE.fullmatch(txt):
            continue
        # правее формулы и компактный
        bb = ln["bbox"]
        if bb.x0 + 1.0 >= bbox_formula.x1 and bb.width <= 60.0:
            # парсим число
            try:
                n = int(txt.strip()[1:-1])
                candidates.append((n, bb))
            except Exception:
                pass
    if not candidates:
        return (None, None)
    candidates.sort(key=lambda t: t[1].x1, reverse=True)
    return candidates[0]

def _nearest_text_line_gaps(page: fitz.Page, formula_bb: fitz.Rect, ignore_rects: List[fitz.Rect]) -> Tuple[Optional[float], Optional[float], float]:
    """
    Ищем ближайшие вверх/вниз текстовые линии и считаем вертикальные зазоры.
    ignore_rects — прямоугольники, которые надо игнорировать (например, bbox номера).
    """
    lines: List[Tuple[fitz.Rect, float]] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs: List[float] = []
            ys: List[float] = []
            sizes: List[float] = []
            for sp in ln.get("spans", []):
                bb = sp.get("bbox", [0, 0, 0, 0])
                xs += [bb[0], bb[2]]
                ys += [bb[1], bb[3]]
                sz = float(sp.get("size", 0.0)) or 0.0
                if sz > 0:
                    sizes.append(sz)
            if xs:
                rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                if rect.get_area() <= 0:
                    continue
                # игнорируем «служебные» прямоугольники (напр., номер)
                if any(rect.intersects(ir) and _y_close((rect.y0 + rect.y1)/2.0, (ir.y0 + ir.y1)/2.0) for ir in ignore_rects):
                    continue
                avg = (sum(sizes) / len(sizes)) if sizes else 12.0
                lines.append((rect, avg))

    if not lines:
        return (None, None, 12.0)

    above = [(bb, sz) for (bb, sz) in lines if bb.y1 <= formula_bb.y0]
    below = [(bb, sz) for (bb, sz) in lines if bb.y0 >= formula_bb.y1]

    gap_above = None
    if above:
        nearest_top = max(above, key=lambda x: x[0].y1)
        gap_above = formula_bb.y0 - nearest_top[0].y1

    gap_below = None
    if below:
        nearest_bottom = min(below, key=lambda x: x[0].y0)
        gap_below = nearest_bottom[0].y0 - formula_bb.y1

    sizes_all = [sz for (_, sz) in lines if sz > 0]
    ref_size = sorted(sizes_all)[len(sizes_all)//2] if sizes_all else 12.0
    return (gap_above, gap_below, ref_size)

def _gather_formula_rows_on_page(page: fitz.Page, pidx: int) -> List[FormulaLine]:
    """
    Главный сборщик формул:
      - берём посимвольные bbox math-символов для устойчивости к таблицам/ячейкам;
      - при необходимости сшиваем справа номер (N) на той же логической высоте.
    """
    lines = _collect_lines_raw(page)
    out: List[FormulaLine] = []
    for ln in lines:
        bbox_formula, ratio, text_wo_num = _line_math_chars_bbox(ln)
        if not bbox_formula:
            continue
        if ratio < 0.6:
            # мало «математичности» — пропустим как неформулу
            continue

        # попробуем найти номер справа на той же логической высоте
        num, num_bb = _try_attach_number(lines, ln, bbox_formula)
        bbox_row = _rect_union([bbox_formula, num_bb] if num_bb else [bbox_formula])

        out.append(
            FormulaLine(
                page_index0=pidx,
                bbox=bbox_row,
                number=num,
                number_bbox=num_bb,
                text_joined=text_wo_num,
                math_ratio=ratio,
            )
        )
    return out

def _collect_all_refs(doc: fitz.Document, start_page: int) -> Dict[int, List[Tuple[int, fitz.Rect]]]:
    """
    Собираем все упоминания '(n)' по документу, начиная со start_page.
    Возвращаем: {page_num1: [(n, bbox), ...], ...}
    """
    refs: Dict[int, List[Tuple[int, fitz.Rect]]] = defaultdict(list)
    for pidx in range(doc.page_count):
        page_num = pidx + 1
        if page_num < start_page:
            continue
        page = doc[pidx]
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                txt = "".join(sp.get("text", "") or "" for sp in ln.get("spans", []))
                txt = txt.strip()
                if not NUMBER_RE.fullmatch(txt):
                    continue
                try:
                    n = int(txt[1:-1])
                except Exception:
                    continue
                bb_x: List[float] = []
                bb_y: List[float] = []
                for sp in ln.get("spans", []):
                    bb = sp.get("bbox", [0, 0, 0, 0])
                    bb_x += [bb[0], bb[2]]
                    bb_y += [bb[1], bb[3]]
                if bb_x:
                    refs[page_num].append((n, fitz.Rect(min(bb_x), min(bb_y), max(bb_x), max(bb_y))))
    return refs

# --- публичная функция ---
def check_formulas(
    pdf_document: fitz.Document,
    *,
    annotate_pdf: bool = True,
    start_page: int = 2,
) -> Dict[str, object]:
    admin: List[str] = []
    formula_bboxes_by_page: Dict[int, List[Tuple[float, float, float, float]]] = defaultdict(list)
    error_pages = set()
    n_formulas = 0

    # ссылки на формулы
    all_refs = _collect_all_refs(pdf_document, start_page=start_page)

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        page_bound = page.bound()
        work_left = page_bound.x0 + LEFT_MARGIN_PT
        work_right = page_bound.x1 - RIGHT_MARGIN_PT

        formulas = _gather_formula_rows_on_page(page, pidx)
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Formulas][p{page_num}] candidates={len(formulas)}")

        for f in formulas:
            n_formulas += 1
            formula_bboxes_by_page[page_num].append((f.bbox.x0, f.bbox.y0, f.bbox.x1, f.bbox.y1))

            issues: List[str] = []

            # свободные строки (сверху/снизу) — считаем от ОБЩЕГО bbox (формула + номер)
            ignore_rects: List[fitz.Rect] = [f.number_bbox] if f.number_bbox else []
            gap_above, gap_below, ref_fs = _nearest_text_line_gaps(page, f.bbox, ignore_rects)
            need_gap = max(MIN_LINE_GAP, (ref_fs or 12.0) * 1.0)
            if gap_above is None or gap_above < need_gap:
                issues.append(f"Перед формулой должна быть как минимум одна свободная строка (~≥{need_gap:.1f} pt).")
            if gap_below is None or gap_below < need_gap:
                issues.append(f"После формулы должна быть как минимум одна свободная строка (~≥{need_gap:.1f} pt).")

            # центрирование по рабочей полосе
            if not _center_ok(f.bbox, work_left, work_right, tol_pt=TOL_CENTER):
                issues.append("Формула должна быть выровнена по центру строки.")

            # номер справа
            if f.number is None:
                issues.append("Формула должна иметь порядковый номер в круглых скобках справа, например (1).")
            else:
                if f.number_bbox:
                    dx_right = abs(work_right - f.number_bbox.x1)
                    if dx_right > TOL_RIGHT:
                        issues.append("Номер формулы должен располагаться у правого края рабочей области строки.")
                # наличие ссылок в тексте (6.8.4)
                refs_for_n = []
                for pg, items in all_refs.items():
                    for (n, bb) in items:
                        if n != f.number:
                            continue
                        # исключаем совпадение с той же строкой (существенное вертикальное пересечение)
                        if pg == page_num and max(0.0, min(bb.y1, f.bbox.y1) - max(bb.y0, f.bbox.y0)) > 0.3 * max(1.0, f.bbox.height):
                            continue
                        refs_for_n.append((pg, bb))
                if not refs_for_n:
                    issues.append(f"Нет ссылок в тексте вида «({f.number})» на эту формулу (6.8.4).")

            # рамки рабочих полей
            if not _inside_work_area(f.bbox, page_bound):
                issues.append("Формула/номер выходят за пределы рабочих полей страницы.")

            # аннотации/логи
            if issues:
                error_pages.add(page_num)
                if annotate_pdf:
                    try:
                        page.add_text_annot(
                            fitz.Point(f.bbox.x0, max(f.bbox.y0 - 8, page_bound.y0 + TOP_MARGIN_PT)),
                            "Формула: нарушения\n" + "\n".join("• " + i for i in issues)
                        )
                    except Exception:
                        pass
                admin.append(f"[p{page_num}] Формула: проблемы:")
                admin.extend("  - " + i for i in issues)
            else:
                if annotate_pdf:
                    try:
                        page.add_text_annot(
                            fitz.Point(f.bbox.x0, max(f.bbox.y0 - 8, page_bound.y0 + TOP_MARGIN_PT)),
                            f"Формула корректна{f' (№{f.number})' if f.number else ''}"
                        )
                    except Exception:
                        pass
                admin.append(f"[p{page_num}] Формула корректна{f' (№{f.number})' if f.number else ''}.")

    user_summary = (
        "⚠️Проверка формул: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
    ) if error_pages else "✅Проверка формул"

    admin_details = (f"[Formulas] Найдено формул: {n_formulas}"
                     + ("\n" + "\n".join(admin) if admin else ""))

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "formula_bboxes_by_page": dict(formula_bboxes_by_page),
    }
