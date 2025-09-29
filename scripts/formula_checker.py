# scripts/formula_checker
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz
from collections import defaultdict
from const import *  # ожидаются LEFT/RIGHT/TOP/BOTTOM_MARGIN_PT, DEBUG_DIAGNOSTICS

# --- настройки/эвристики ---
MATH_FONTS = {"Cambria Math"}
TOL_Y = 3.0              # pt: принадлежность одной логической высоте
TOL_CENTER = 6.0         # pt: допуск по центрированию
TOL_RIGHT = 6.0          # pt: допуск к правому краю для номера
MIN_OVERLAP_X = 0.30     # доля перекрытия по X для склейки строк формулы
MAX_VGAP_FACTOR = 1.5    # вертикальный зазор для склейки, в долях ref_font
NUM_MAX_WIDTH = 60.0     # pt: максимальная ширина строки-номера
# Адаптивный порог свободной строки: clamp(ref * SCALE, MIN..MAX)
GAP_MIN_PT = 8.0
GAP_MAX_PT = 11.0
GAP_SCALE = 0.75

# Регэкспы для номера
NUM_TAIL_RE = re.compile(r"\((\d+)\)\s*$")   # номер в КОНЦЕ строки
NUM_ANY_RE  = re.compile(r"\((\d+)\)")       # номер где угодно

@dataclass
class FormulaLineRaw:
    spans: list
    bbox: fitz.Rect
    y_mid: float
    core_bbox: Optional[fitz.Rect]
    math_ratio: float

@dataclass
class FormulaBlock:
    page_index0: int
    core_bbox: fitz.Rect          # bbox формулы без номера (объединённый по строкам)
    bbox: fitz.Rect               # формула + номер (если найден)
    number: Optional[int]
    number_bbox: Optional[fitz.Rect]

# ---------- утилиты ----------
def _rect_union(rects: List[fitz.Rect]) -> Optional[fitz.Rect]:
    if not rects: 
        return None
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return r

def _center_ok(bb: fitz.Rect, left: float, right: float, tol=TOL_CENTER) -> bool:
    return abs(((bb.x0 + bb.x1) / 2) - ((left + right) / 2)) <= tol

def _inside_work(bb: fitz.Rect, page_bound: fitz.Rect) -> bool:
    return (
        bb.x0 >= page_bound.x0 + LEFT_MARGIN_PT - 0.5 and
        bb.x1 <= page_bound.x1 - RIGHT_MARGIN_PT + 0.5 and
        bb.y0 >= page_bound.y0 + TOP_MARGIN_PT - 0.5 and
        bb.y1 <= page_bound.y1 - BOTTOM_MARGIN_PT + 0.5
    )

def _is_math_char(c: str) -> bool:
    if not c: 
        return False
    u = ord(c)
    # греческий + математические операторы
    return (0x0370 <= u <= 0x03FF) or (0x2200 <= u <= 0x22FF)

def _y_close(a: float, b: float, tol: float = TOL_Y) -> bool:
    return abs(a - b) <= tol

def _x_overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    left = max(a.x0, b.x0)
    right = min(a.x1, b.x1)
    inter = max(0.0, right - left)
    width = max(1.0, min(a.width, b.width))
    return inter / width

def _line_text_by_chars(line_spans: list) -> str:
    chars = []
    for sp in line_spans:
        for ch in sp.get("chars", []) or []:
            c = ch.get("c", "")
            if c:
                chars.append(c)
    return "".join(chars)

def _line_core_bbox_and_ratio(line_spans: list) -> Tuple[Optional[fitz.Rect], float]:
    rects: List[fitz.Rect] = []
    total = math_total = 0
    for sp in line_spans:
        font = str(sp.get("font", ""))
        for ch in sp.get("chars", []) or []:
            c = ch.get("c", "")
            if not c:
                continue
            total += 1
            if font in MATH_FONTS or _is_math_char(c):
                bb = ch.get("bbox", None)
                if bb:
                    rects.append(fitz.Rect(*map(float, bb)))
                math_total += 1
    if total == 0:
        return (None, 0.0)
    return (_rect_union(rects), math_total / total)

# ---------- парсинг страниц ----------
def _collect_lines_raw(page: fitz.Page) -> List[FormulaLineRaw]:
    out: List[FormulaLineRaw] = []
    d = page.get_text("rawdict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            xs, ys = [], []
            for sp in spans:
                bb = sp.get("bbox", [0, 0, 0, 0])
                xs += [bb[0], bb[2]]
                ys += [bb[1], bb[3]]
            if not xs:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            core_bb, ratio = _line_core_bbox_and_ratio(spans)
            out.append(FormulaLineRaw(
                spans=spans,
                bbox=rect,
                y_mid=(rect.y0 + rect.y1) / 2,
                core_bbox=core_bb,
                math_ratio=ratio
            ))
    return out

# ---------- поиск номера ----------
def _find_trailing_number_same_line(line_spans: list) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    chars = []
    for sp in line_spans:
        for ch in sp.get("chars", []) or []:
            c = ch.get("c", ""); bb = ch.get("bbox", None)
            if not c or not bb:
                continue
            chars.append((c, fitz.Rect(*map(float, bb))))
    if not chars:
        return (None, None)
    chars.sort(key=lambda t: (t[1].y0, t[1].x0))
    text = "".join(c for c, _ in chars)
    m = NUM_TAIL_RE.search(text)
    if not m:
        return (None, None)
    try:
        n = int(m.group(1))
    except:
        return (None, None)
    start, end = m.span()
    if not (0 <= start < end <= len(chars)):
        return (None, None)
    num_bb = _rect_union([chars[i][1] for i in range(start, end)])
    return (n, num_bb)

def _find_number_right_same_height(all_lines: List[FormulaLineRaw], ref: FormulaLineRaw, core_bb: fitz.Rect) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    candidates: List[Tuple[int, fitz.Rect]] = []
    for ln in all_lines:
        if ln is ref:
            continue
        if not _y_close(ln.y_mid, ref.y_mid):
            continue
        txt = _line_text_by_chars(ln.spans).strip()
        m = NUM_TAIL_RE.fullmatch(txt)
        if not m:
            continue
        bb = ln.bbox
        if bb.x0 >= core_bb.x1 - 0.5 and bb.width <= NUM_MAX_WIDTH:
            try:
                n = int(m.group(1))
                candidates.append((n, bb))
            except:
                pass
    if not candidates:
        return (None, None)
    candidates.sort(key=lambda t: t[1].x1, reverse=True)
    return candidates[0]

def _find_number_on_next_line(all_lines: List[FormulaLineRaw], block_bb: fitz.Rect, ref_font: float, work_right: float) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    """
    Номер на следующей строке, сразу под формулой, у правого края.
    """
    max_vgap = max(8.0, ref_font * 1.2)
    best: Optional[Tuple[int, fitz.Rect, float]] = None  # (n, bb, vertical_gap)
    for ln in all_lines:
        if ln.bbox.y0 < block_bb.y1 - 0.5:
            continue
        vgap = ln.bbox.y0 - block_bb.y1
        if vgap < -0.5 or vgap > max_vgap:
            continue
        txt = _line_text_by_chars(ln.spans).strip()
        m = NUM_TAIL_RE.fullmatch(txt)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except:
            continue
        bb = ln.bbox
        # у правого края
        if abs(work_right - bb.x1) <= max(TOL_RIGHT, 6.0) and bb.width <= NUM_MAX_WIDTH:
            if best is None or vgap < best[2]:
                best = (n, bb, vgap)
    if not best:
        return (None, None)
    return (best[0], best[1])

# ---------- склейка строк в блоки формул ----------
def _group_math_lines_to_blocks(lines: List[FormulaLineRaw], ref_font: float) -> List[Tuple[List[FormulaLineRaw], fitz.Rect]]:
    """
    Склеивает соседние мат.-строки в блок, если:
      - обе «достаточно математические» (ratio >= 0.6);
      - вертикальный зазор <= MAX_VGAP_FACTOR * ref_font;
      - по X есть перекрытие долей не меньше MIN_OVERLAP_X.
    Возвращает список пар: (список_строк_блока, core_bbox_union)
    """
    math_lines = [ln for ln in lines if ln.core_bbox and ln.math_ratio >= 0.6]
    if not math_lines:
        return []
    math_lines.sort(key=lambda ln: ln.bbox.y0)

    blocks: List[List[FormulaLineRaw]] = []
    cur: List[FormulaLineRaw] = [math_lines[0]]
    for ln in math_lines[1:]:
        prev = cur[-1]
        vgap = ln.bbox.y0 - prev.bbox.y1
        xov = _x_overlap_ratio(prev.core_bbox, ln.core_bbox)
        if vgap <= MAX_VGAP_FACTOR * max(10.0, ref_font) and xov >= MIN_OVERLAP_X:
            cur.append(ln)
        else:
            blocks.append(cur)
            cur = [ln]
    blocks.append(cur)

    out: List[Tuple[List[FormulaLineRaw], fitz.Rect]] = []
    for bl in blocks:
        core = _rect_union([ln.core_bbox for ln in bl if ln.core_bbox])
        if core:
            out.append((bl, core))
    return out

# ---------- сборка формульных блоков на странице ----------
def _gather_formula_blocks_on_page(page: fitz.Page, pidx: int, work_right: float) -> Tuple[List[FormulaBlock], float]:
    # ref_font как медиана кеглей на странице
    sizes = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                sz = float(sp.get("size", 0)) or 0.0
                if sz > 0:
                    sizes.append(sz)
    ref_font = sorted(sizes)[len(sizes)//2] if sizes else 12.0

    raw_lines = _collect_lines_raw(page)
    blocks_raw = _group_math_lines_to_blocks(raw_lines, ref_font)

    out: List[FormulaBlock] = []
    for bl_lines, core_bb in blocks_raw:
        # 1) номер в конце последней строки блока
        last_ln = bl_lines[-1]
        num, num_bb = _find_trailing_number_same_line(last_ln.spans)

        # 2) если не найден — номер справа на той же высоте (табличный кейс)
        if num is None:
            for candidate_ln in reversed(bl_lines):
                num, num_bb = _find_number_right_same_height(raw_lines, candidate_ln, core_bb)
                if num is not None:
                    break

        # 3) если не найден — номер на следующей строке у правого края
        if num is None:
            num, num_bb = _find_number_on_next_line(raw_lines, core_bb, ref_font, work_right)

        row_bb = _rect_union([core_bb, num_bb] if num_bb else [core_bb])
        out.append(FormulaBlock(
            page_index0=pidx,
            core_bbox=core_bb,
            bbox=row_bb,
            number=num,
            number_bbox=num_bb
        ))
    return out, ref_font

# ---------- поиск ссылок (n) по всему документу ----------
def _collect_all_refs(doc: fitz.Document, start_page: int) -> Dict[int, List[Tuple[int, fitz.Rect]]]:
    refs: Dict[int, List[Tuple[int, fitz.Rect]]] = defaultdict(list)
    for pidx in range(doc.page_count):
        pg = pidx + 1
        if pg < start_page:
            continue
        page = doc[pidx]
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                txt = "".join(sp.get("text", "") or "" for sp in ln.get("spans", [])).strip()
                for m in NUM_ANY_RE.finditer(txt):
                    try:
                        n = int(m.group(1))
                    except:
                        continue
                    xs, ys = [], []
                    for sp in ln.get("spans", []):
                        bb = sp.get("bbox", [0, 0, 0, 0])
                        xs += [bb[0], bb[2]]
                        ys += [bb[1], bb[3]]
                    if xs:
                        refs[pg].append((n, fitz.Rect(min(xs), min(ys), max(xs), max(ys))))
    return refs

# ---------- публичная функция ----------
def check_formulas(
    pdf_document: fitz.Document,
    *, annotate_pdf: bool = True,
    start_page: int = 2,
) -> Dict[str, object]:
    admin: List[str] = []
    formula_bboxes_by_page: Dict[int, List[Tuple[float, float, float, float]]] = defaultdict(list)
    error_pages = set()
    n_formulas = 0

    all_refs = _collect_all_refs(pdf_document, start_page=start_page)

    for pidx, page in enumerate(pdf_document):
        pg = pidx + 1
        if pg < start_page:
            continue

        page_bound = page.bound()
        work_left = page_bound.x0 + LEFT_MARGIN_PT
        work_right = page_bound.x1 - RIGHT_MARGIN_PT

        blocks, ref_font = _gather_formula_blocks_on_page(page, pidx, work_right)
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Formulas][p{pg}] candidates={len(blocks)}")

        for blk in blocks:
            n_formulas += 1
            formula_bboxes_by_page[pg].append((blk.bbox.x0, blk.bbox.y0, blk.bbox.x1, blk.bbox.y1))
            issues: List[str] = []

            # --- свободные строки сверху/снизу ---
            ig: List[fitz.Rect] = [blk.number_bbox] if blk.number_bbox else []
            gap_above, gap_below, ref = _nearest_text_line_gaps(page, blk.bbox, ig=ig)  # type: ignore

            # порог: clamp(ref*GAP_SCALE, GAP_MIN_PT..GAP_MAX_PT)
            ref_use = ref if ref and ref > 0 else ref_font
            need = max(GAP_MIN_PT, min(GAP_MAX_PT, (ref_use or 12.0) * GAP_SCALE))
            if gap_above is None or gap_above + 1e-3 < need:
                issues.append(f"Перед формулой должна быть как минимум одна свободная строка (~≥{need:.1f} pt).")
            if gap_below is None or gap_below + 1e-3 < need:
                issues.append(f"После формулы должна быть как минимум одна свободная строка (~≥{need:.1f} pt).")

            # --- центрирование (только core, без номера) ---
            if not _center_ok(blk.core_bbox, work_left, work_right):
                issues.append("Формула должна быть выровнена по центру строки.")

            # --- номер справа ---
            if blk.number is None:
                issues.append("Формула должна иметь порядковый номер в круглых скобках справа, например (1).")
            else:
                if blk.number_bbox and abs(work_right - blk.number_bbox.x1) > TOL_RIGHT:
                    issues.append("Номер формулы должен располагаться у правого края рабочей области строки.")
                # ссылки (6.8.4)
                refs = [(p, bb) for p, items in all_refs.items() for (n, bb) in items if n == blk.number]
                # исключаем совпадение с самой строкой/блоком
                refs = [(p, bb) for (p, bb) in refs if not (p == pg and bb.intersects(blk.bbox))]
                if not refs:
                    issues.append(f"Нет ссылок в тексте вида «({blk.number})» на эту формулу (6.8.4).")

            # --- выход за рабочую область ---
            if not _inside_work(blk.bbox, page_bound):
                issues.append("Формула/номер выходят за пределы рабочих полей страницы.")

            # --- аннотации и лог ---
            if issues:
                error_pages.add(pg)
                if annotate_pdf:
                    try:
                        page.add_text_annot(
                            fitz.Point(blk.bbox.x0, max(blk.bbox.y0 - 8, page_bound.y0 + TOP_MARGIN_PT)),
                            "Формула: нарушения\n" + "\n".join("• " + i for i in issues)
                        )
                    except:
                        pass
                admin.append(f"[p{pg}] Формула: проблемы:")
                admin.extend("  - " + i for i in issues)
            else:
                if annotate_pdf:
                    try:
                        page.add_text_annot(
                            fitz.Point(blk.bbox.x0, max(blk.bbox.y0 - 8, page_bound.y0 + TOP_MARGIN_PT)),
                            f"Формула корректна{f' (№{blk.number})' if blk.number else ''}"
                        )
                    except:
                        pass
                admin.append(f"[p{pg}] Формула корректна{f' (№{blk.number})' if blk.number else ''}.")

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

# ---------- вспомогательная функция: поиск ближайших линий (не формула/номер) ----------
def _nearest_text_line_gaps(page: fitz.Page, formula_bb: fitz.Rect, ig: List[fitz.Rect]) -> Tuple[Optional[float], Optional[float], float]:
    """
    Ищем ближайшие вверх/вниз текстовые линии и считаем зазоры.
    ig — прямоугольники, которые игнорируем (например, номер).
    """
    lines: List[Tuple[fitz.Rect, float]] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys, sizes = [], [], []
            for sp in ln.get("spans", []):
                bb = sp.get("bbox", [0, 0, 0, 0])
                xs += [bb[0], bb[2]]
                ys += [bb[1], bb[3]]
                sz = float(sp.get("size", 0)) or 0.0
                if sz > 0:
                    sizes.append(sz)
            if not xs:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            if any(rect.intersects(ir) for ir in ig):
                continue
            avg = (sum(sizes) / len(sizes)) if sizes else 12.0
            lines.append((rect, avg))

    if not lines:
        return (None, None, 12.0)

    above = [x for x in lines if x[0].y1 <= formula_bb.y0]
    below = [x for x in lines if x[0].y0 >= formula_bb.y1]
    gap_above = (formula_bb.y0 - max(above, key=lambda x: x[0].y1)[0].y1) if above else None
    gap_below = (min(below, key=lambda x: x[0].y0)[0].y0 - formula_bb.y1) if below else None
    sizes_all = [sz for _, sz in lines if sz > 0]
    ref = sorted(sizes_all)[len(sizes_all)//2] if sizes_all else 12.0
    return (gap_above, gap_below, ref)
