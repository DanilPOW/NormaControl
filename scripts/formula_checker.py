# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Iterable
import re
import fitz  # PyMuPDF
from collections import defaultdict
from const import * 

MATH_FONTS = {"Cambria Math"}  # при необходимости можно расширить
NUMBER_RE = re.compile(r"\((\d+)\)")

@dataclass
class Span:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    color: int

@dataclass
class FormulaLine:
    page_index0: int
    spans: List[Span]          # все спаны, входящие в формульную строку
    bbox: fitz.Rect            # объединённый bbox
    math_ratio: float          # доля math-спанов
    number: Optional[int]      # номер формулы, если найден (n)
    number_bbox: Optional[fitz.Rect]
    text_joined: str           # слепленный текст

def _rect_union(rects: Iterable[fitz.Rect]) -> Optional[fitz.Rect]:
    rects = list(rects)
    if not rects:
        return None
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return r

def _collect_text_spans(page: fitz.Page) -> List[Span]:
    out: List[Span] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                t = (sp.get("text") or "")
                if not t.strip():
                    continue
                bb = sp.get("bbox", [0,0,0,0])
                out.append(
                    Span(
                        text=t,
                        bbox=fitz.Rect(*map(float, bb)),
                        size=float(sp.get("size", 0.0)) or 0.0,
                        font=str(sp.get("font","")),
                        color=int(sp.get("color", 0)),
                    )
                )
    return out

#группировка частей формул по близости
def _cluster_spans_into_lines(spans: List[Span], y_tol_pt: float = 3.0) -> List[List[Span]]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s.bbox.y0, s.bbox.x0))
    lines: List[List[Span]] = []
    cur: List[Span] = [spans[0]]
    for sp in spans[1:]:
        last = cur[-1]
        # если верх текущего близок к верху предыдущего — та же строка
        if abs(sp.bbox.y0 - last.bbox.y0) <= y_tol_pt:
            cur.append(sp)
        else:
            lines.append(cur)
            cur = [sp]
    lines.append(cur)
    # упорядочим внутри строки по X
    for ln in lines:
        ln.sort(key=lambda s: s.bbox.x0)
    return lines

def _is_math_line(spans: List[Span]) -> Tuple[bool, float]:
    if not spans:
        return (False, 0.0)
    math_cnt = sum(1 for s in spans if s.font in MATH_FONTS)
    ratio = math_cnt / max(1, len(spans))
    return (ratio >= 0.6, ratio)  # порог можно подправить: 0.6 — в пользу формул

def _extract_number_on_line(spans: List[Span]) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    """
    Ищем (n) в правой части строки. Если несколько — берём правый.
    """
    found: List[Tuple[int, fitz.Rect]] = []
    for sp in spans:
        for m in NUMBER_RE.finditer(sp.text):
            try:
                num = int(m.group(1))
            except Exception:
                continue
            # приблизительный bbox подстроки — используем bbox всего спана
            # (точный посимвольный bbox можно получить через "rawdict", но чаще спана достаточно)
            found.append((num, sp.bbox))
    if not found:
        return (None, None)
    # берём самый правый
    found.sort(key=lambda t: t[1].x1, reverse=True)
    return found[0][0], found[0][1]

def _joined_text(spans: List[Span]) -> str:
    return "".join(s.text for s in spans).strip()

def _non_number_bbox(spans: List[Span]) -> fitz.Rect:
    """
    Объединённый bbox без правого спана с номером (если он есть).
    """
    num_idx = None
    candidates = []
    for i, sp in enumerate(spans):
        if NUMBER_RE.search(sp.text):
            candidates.append((i, sp.bbox.x1))
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        num_idx = candidates[0][0]

    kept = [sp.bbox for i, sp in enumerate(spans) if i != num_idx]
    if not kept:
        return _rect_union([spans[0].bbox])  # fallback
    return _rect_union(kept)

def _nearest_text_line_gaps(page: fitz.Page, formula_bb: fitz.Rect) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Оцениваем свободные промежутки сверху/снизу (между ближайшими текстовыми линиями и формулой).
    """
    # собираем «линии» как блоки из dict
    lines: List[Tuple[fitz.Rect, float]] = []  # (bbox, avg_size)
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys, sizes = [], [], []
            for sp in ln.get("spans", []):
                bb = sp.get("bbox", [0,0,0,0])
                xs += [bb[0], bb[2]]; ys += [bb[1], bb[3]]
                try:
                    sizes.append(float(sp.get("size", 0.0)) or 0.0)
                except Exception:
                    pass
            if xs:
                rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                if rect.get_area() <= 0:
                    continue
                sz = (sum(sizes) / len(sizes)) if sizes else 12.0
                lines.append((rect, sz))

    if not lines:
        return (None, None, None)

    # отделим те, что выше/ниже формулы и достаточно близко по горизонтали
    above = [ (bb, sz) for (bb, sz) in lines if bb.y1 <= formula_bb.y0 ]
    below = [ (bb, sz) for (bb, sz) in lines if bb.y0 >= formula_bb.y1 ]

    gap_above = None
    if above:
        nearest_top = max(above, key=lambda x: x[0].y1)
        gap_above = formula_bb.y0 - nearest_top[0].y1

    gap_below = None
    if below:
        nearest_bottom = min(below, key=lambda x: x[0].y0)
        gap_below = nearest_bottom[0].y0 - formula_bb.y1

    # референсный размер шрифта берём по медиане всех строк
    sizes_all = [sz for (_, sz) in lines if sz > 0]
    ref_size = sorted(sizes_all)[len(sizes_all)//2] if sizes_all else 12.0
    return (gap_above, gap_below, ref_size)

def _center_ok(bbox: fitz.Rect, work_left: float, work_right: float, tol_pt: float = 8.0) -> bool:
    mid = (work_left + work_right) / 2.0
    return abs(((bbox.x0 + bbox.x1) / 2.0) - mid) <= tol_pt

def _inside_work_area(bb: fitz.Rect, page_bound: fitz.Rect) -> bool:
    return (
        bb.x0 >= page_bound.x0 + LEFT_MARGIN_PT - 0.5 and
        bb.x1 <= page_bound.x1 - RIGHT_MARGIN_PT + 0.5 and
        bb.y0 >= page_bound.y0 + TOP_MARGIN_PT - 0.5 and
        bb.y1 <= page_bound.y1 - BOTTOM_MARGIN_PT + 0.5
    )

def _gather_formula_lines_on_page(page: fitz.Page, pidx: int) -> List[FormulaLine]:
    spans = _collect_text_spans(page)
    if not spans:
        return []
    # первый фильтр: оставляем только те строки, где есть math-спаны
    lines = _cluster_spans_into_lines(spans, y_tol_pt=3.0)
    out: List[FormulaLine] = []
    for ln in lines:
        is_math, ratio = _is_math_line(ln)
        if not is_math:
            continue
        # объединённый bbox строки
        bb = _rect_union(s.bbox for s in ln)
        if not bb:
            continue
        # номер
        num, num_bb = _extract_number_on_line(ln)
        out.append(
            FormulaLine(
                page_index0=pidx,
                spans=ln,
                bbox=bb,
                math_ratio=ratio,
                number=num,
                number_bbox=num_bb,
                text_joined=_joined_text(ln),
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
                for sp in ln.get("spans", []):
                    t = sp.get("text") or ""
                    for m in NUMBER_RE.finditer(t):
                        try:
                            n = int(m.group(1))
                        except Exception:
                            continue
                        bb = fitz.Rect(*map(float, sp.get("bbox", [0,0,0,0])))
                        refs[page_num].append((n, bb))
    return refs

def check_formulas(
    pdf_document: fitz.Document,
    *,
    annotate_pdf: bool = True,
    start_page: int = 2,
) -> Dict[str, object]:
    admin: List[str] = []
    formula_bboxes_by_page: Dict[int, List[Tuple[float,float,float,float]]] = defaultdict(list)
    error_pages = set()
    n_formulas = 0

    # для проверки ссылок соберём все упоминания (n) в тексте
    all_refs = _collect_all_refs(pdf_document, start_page=start_page)

    # пройдёмся по страницам >= start_page
    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        page_bound = page.bound()
        work_left  = page_bound.x0 + LEFT_MARGIN_PT
        work_right = page_bound.x1 - RIGHT_MARGIN_PT

        formulas = _gather_formula_lines_on_page(page, pidx)
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Formulas][p{page_num}] candidates={len(formulas)}")

        for f in formulas:
            n_formulas += 1
            formula_bboxes_by_page[page_num].append((f.bbox.x0, f.bbox.y0, f.bbox.x1, f.bbox.y1))

            issues = []

            # 6.8.1 отдельная строка + по одной пустой строке сверху/снизу (минимум)
            gap_above, gap_below, ref_fs = _nearest_text_line_gaps(page, f.bbox)
            # оценка: не менее 1.0*ref_fs (можно скорректировать 1.1)
            need_gap = max(10.0, (ref_fs or 12.0) * 1.0)
            if gap_above is None or gap_above < need_gap:
                issues.append(f"Перед формулой должна быть как минимум одна свободная строка (~≥{need_gap:.1f} pt).")
            if gap_below is None or gap_below < need_gap:
                issues.append(f"После формулы должна быть как минимум одна свободная строка (~≥{need_gap:.1f} pt).")

            # 6.8.3 центрирование формулы (без номера)
            core_bb = _non_number_bbox(f.spans)
            if not _center_ok(core_bb, work_left, work_right, tol_pt=8.0):
                issues.append("Формула должна быть выровнена по центру строки.")

            # 6.8.3 номер в круглых скобках справа
            if f.number is None:
                issues.append("Формула должна иметь порядковый номер в круглых скобках справа, например (1).")
            else:
                if f.number_bbox:
                    dx_right = (work_right - f.number_bbox.x1)
                    if abs(dx_right) > 6.0:
                        issues.append("Номер формулы должен располагаться в крайнем правом положении строки.")
                # проверка ссылок 6.8.4 (в тексте встречается '(n)')
                # считаем валидной ссылкой любое упоминание (n) хотя бы 1 раз вне самой строки формулы
                refs_for_n = []
                for pg, items in all_refs.items():
                    for (n, bb) in items:
                        if n == f.number:
                            # исключим совпадение с той же строкой (по значительной вертикальной перекрыше)
                            if pg == page_num and max(0.0, min(bb.y1, f.bbox.y1) - max(bb.y0, f.bbox.y0)) > 0.3 * max(1.0, f.bbox.height):
                                continue
                            refs_for_n.append((pg, bb))
                if not refs_for_n:
                    issues.append(f"Нет ссылок в тексте вида «({f.number})» на эту формулу (6.8.4).")

            # 6.8.1/поля: не выходить за рабочую область
            if not _inside_work_area(f.bbox, page_bound):
                issues.append("Формула/номер выходят за пределы рабочих полей страницы.")

            # аннотирование
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

