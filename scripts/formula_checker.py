# scripts/formula_checker
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz
from collections import defaultdict
from const import *

# --- настройки ---
MATH_FONTS = {"Cambria Math"}
TOL_Y = 3.0
TOL_CENTER = 6.0
MIN_LINE_GAP = 12.0
TOL_RIGHT = 6.0

NUM_TAIL_RE = re.compile(r"\((\d+)\)\s*$")  # номер в КОНЦЕ строки

@dataclass
class FormulaLine:
    page_index0: int
    core_bbox: fitz.Rect            # только формула (без номера)
    bbox: fitz.Rect                 # формула + номер (если найден)
    number: Optional[int]
    number_bbox: Optional[fitz.Rect]

# ---------- утилиты ----------
def _rect_union(rects: List[fitz.Rect]) -> Optional[fitz.Rect]:
    if not rects: return None
    r = fitz.Rect(rects[0])
    for x in rects[1:]: r |= x
    return r

def _center_ok(bb: fitz.Rect, left: float, right: float, tol=TOL_CENTER) -> bool:
    return abs(((bb.x0 + bb.x1)/2) - ((left + right)/2)) <= tol

def _inside_work(bb: fitz.Rect, page_bound: fitz.Rect) -> bool:
    return (
        bb.x0 >= page_bound.x0 + LEFT_MARGIN_PT - .5 and
        bb.x1 <= page_bound.x1 - RIGHT_MARGIN_PT + .5 and
        bb.y0 >= page_bound.y0 + TOP_MARGIN_PT - .5 and
        bb.y1 <= page_bound.y1 - BOTTOM_MARGIN_PT + .5
    )

def _is_math_char(c: str) -> bool:
    if not c: return False
    u = ord(c)
    return (0x0370 <= u <= 0x03FF) or (0x2200 <= u <= 0x22FF)

def _collect_lines_raw(page: fitz.Page) -> List[dict]:
    out: List[dict] = []
    d = page.get_text("rawdict")
    for b in d.get("blocks", []):
        if b.get("type") != 0: continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            xs, ys = [], []
            for sp in spans:
                bb = sp.get("bbox", [0,0,0,0])
                xs += [bb[0], bb[2]]; ys += [bb[1], bb[3]]
            if xs:
                rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                out.append({
                    "spans": spans,
                    "bbox": rect,
                    "y_mid": (rect.y0 + rect.y1)/2
                })
    return out

def _line_core_bbox_and_ratio(line: dict) -> Tuple[Optional[fitz.Rect], float]:
    char_rects: List[fitz.Rect] = []
    total = math_total = 0
    for sp in line["spans"]:
        font = str(sp.get("font",""))
        for ch in sp.get("chars", []) or []:
            c = ch.get("c","")
            if not c: continue
            total += 1
            if font in MATH_FONTS or _is_math_char(c):
                bb = ch.get("bbox", None)
                if bb:
                    char_rects.append(fitz.Rect(*map(float, bb)))
                math_total += 1
    if total == 0: return (None, 0.0)
    return (_rect_union(char_rects), math_total / total)

def _find_trailing_number_bbox(line: dict) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    """
    Ищем (n) в КОНЦЕ той же линии посимвольно, чтобы получить точный bbox.
    """
    # соберём все chars в порядке возрастания x
    chars = []
    for sp in line["spans"]:
        for ch in sp.get("chars", []) or []:
            c = ch.get("c",""); bb = ch.get("bbox", None)
            if not c or not bb: continue
            chars.append((c, fitz.Rect(*map(float, bb))))
    if not chars: return (None, None)
    chars.sort(key=lambda t: (t[1].y0, t[1].x0))

    # восстановим текст линии и сопоставим индексы → ббоксы
    text = "".join(c for c,_ in chars)
    m = NUM_TAIL_RE.search(text)
    if not m: return (None, None)

    n = int(m.group(1))
    start, end = m.span()  # диапазон в тексте
    # защитимся от несоответствий длины
    if not (0 <= start < end <= len(chars)): return (None, None)
    num_rects = [chars[i][1] for i in range(start, end)]
    num_bb = _rect_union(num_rects)
    return (n, num_bb)

def _nearest_text_line_gaps(page: fitz.Page, formula_bb: fitz.Rect, ignore_rects: List[fitz.Rect]) -> Tuple[Optional[float], Optional[float], float]:
    lines: List[Tuple[fitz.Rect, float]] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0: continue
        for ln in b.get("lines", []):
            xs, ys, sizes = [], [], []
            for sp in ln.get("spans", []):
                bb = sp.get("bbox", [0,0,0,0])
                xs += [bb[0], bb[2]]; ys += [bb[1], bb[3]]
                sz = float(sp.get("size",0)) or 0.0
                if sz>0: sizes.append(sz)
            if not xs: continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            if any(rect.intersects(ir) for ir in ignore_rects):  # игнорим номер
                continue
            avg = (sum(sizes)/len(sizes)) if sizes else 12.0
            lines.append((rect, avg))
    if not lines: return (None, None, 12.0)

    above = [x for x in lines if x[0].y1 <= formula_bb.y0]
    below = [x for x in lines if x[0].y0 >= formula_bb.y1]
    gap_above = (formula_bb.y0 - max(above, key=lambda x: x[0].y1)[0].y1) if above else None
    gap_below = (min(below, key=lambda x: x[0].y0)[0].y0 - formula_bb.y1) if below else None
    sizes_all = [sz for _,sz in lines if sz>0]
    ref = sorted(sizes_all)[len(sizes_all)//2] if sizes_all else 12.0
    return (gap_above, gap_below, ref)

# ---------- сборка формул ----------
def _gather_formula_rows_on_page(page: fitz.Page, pidx: int) -> List[FormulaLine]:
    lines = _collect_lines_raw(page)
    out: List[FormulaLine] = []
    for ln in lines:
        core_bb, ratio = _line_core_bbox_and_ratio(ln)
        if not core_bb or ratio < 0.6:
            continue
        num, num_bb = _find_trailing_number_bbox(ln)
        row_bb = _rect_union([core_bb, num_bb] if num_bb else [core_bb])
        out.append(FormulaLine(
            page_index0=pidx,
            core_bbox=core_bb,
            bbox=row_bb,
            number=num,
            number_bbox=num_bb
        ))
    return out

def _collect_all_refs(doc: fitz.Document, start_page: int) -> Dict[int, List[Tuple[int, fitz.Rect]]]:
    refs: Dict[int, List[Tuple[int, fitz.Rect]]] = defaultdict(list)
    for pidx in range(doc.page_count):
        pg = pidx + 1
        if pg < start_page: continue
        page = doc[pidx]
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0: continue
            for ln in b.get("lines", []):
                txt = "".join(sp.get("text","") or "" for sp in ln.get("spans", [])).strip()
                m = NUM_TAIL_RE.search(txt)
                if not m: continue
                try: n = int(m.group(1))
                except: continue
                xs, ys = [], []
                for sp in ln.get("spans", []):
                    bb = sp.get("bbox", [0,0,0,0])
                    xs += [bb[0], bb[2]]; ys += [bb[1], bb[3]]
                rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                refs[pg].append((n, rect))
    return refs

# ---------- публичная функция ----------
def check_formulas(
    pdf_document: fitz.Document,
    *, annotate_pdf: bool = True,
    start_page: int = 2,
) -> Dict[str, object]:
    admin: List[str] = []
    formula_bboxes_by_page: Dict[int, List[Tuple[float,float,float,float]]] = defaultdict(list)
    error_pages = set()
    n_formulas = 0

    all_refs = _collect_all_refs(pdf_document, start_page=start_page)

    for pidx, page in enumerate(pdf_document):
        pg = pidx + 1
        if pg < start_page: continue

        page_bound = page.bound()
        work_left  = page_bound.x0 + LEFT_MARGIN_PT
        work_right = page_bound.x1 - RIGHT_MARGIN_PT

        formulas = _gather_formula_rows_on_page(page, pidx)
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Formulas][p{pg}] candidates={len(formulas)}")

        for f in formulas:
            n_formulas += 1
            formula_bboxes_by_page[pg].append((f.bbox.x0, f.bbox.y0, f.bbox.x1, f.bbox.y1))

            issues: List[str] = []

            # свободные строки (считаем от объединённого bbox; номер игнорим как «следующую строку»)
            ig = [f.number_bbox] if f.number_bbox else []
            gap_above, gap_below, ref = _nearest_text_line_gaps(page, f.bbox, ig)
            need = max(MIN_LINE_GAP, ref * 1.0)
            if gap_above is None or gap_above < need:
                issues.append(f"Перед формулой должна быть как минимум одна свободная строка (~≥{need:.1f} pt).")
            if gap_below is None or gap_below < need:
                issues.append(f"После формулы должна быть как минимум одна свободная строка (~≥{need:.1f} pt).")

            # ЦЕНТР: только формула (без номера)
            if not _center_ok(f.core_bbox, work_left, work_right):
                issues.append("Формула должна быть выровнена по центру строки.")

            # номер справа
            if f.number is None:
                issues.append("Формула должна иметь порядковый номер в круглых скобках справа, например (1).")
            else:
                if f.number_bbox and abs(work_right - f.number_bbox.x1) > TOL_RIGHT:
                    issues.append("Номер формулы должен располагаться у правого края рабочей области строки.")
                # ссылки (6.8.4)
                refs = [(p,bb) for p,items in all_refs.items() for (n,bb) in items if n==f.number]
                # исключим совпадение с самой строкой
                refs = [(p,bb) for (p,bb) in refs if not (p==pg and bb.intersects(f.bbox))]
                if not refs:
                    issues.append(f"Нет ссылок в тексте вида «({f.number})» на эту формулу (6.8.4).")

            if not _inside_work(f.bbox, page_bound):
                issues.append("Формула/номер выходят за пределы рабочих полей страницы.")

            # аннотации и лог
            if issues:
                error_pages.add(pg)
                if annotate_pdf:
                    try:
                        page.add_text_annot(
                            fitz.Point(f.bbox.x0, max(f.bbox.y0 - 8, page_bound.y0 + TOP_MARGIN_PT)),
                            "Формула: нарушения\n" + "\n".join("• " + i for i in issues)
                        )
                    except: pass
                admin.append(f"[p{pg}] Формула: проблемы:")
                admin.extend("  - " + i for i in issues)
            else:
                if annotate_pdf:
                    try:
                        page.add_text_annot(
                            fitz.Point(f.bbox.x0, max(f.bbox.y0 - 8, page_bound.y0 + TOP_MARGIN_PT)),
                            f"Формула корректна{f' (№{f.number})' if f.number else ''}"
                        )
                    except: pass
                admin.append(f"[p{pg}] Формула корректна{f' (№{f.number})' if f.number else ''}.")

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
