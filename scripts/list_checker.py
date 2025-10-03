# scripts/list_checker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict
from const import *  # ожидаются: EN_DASH, NBSP, LEFT_MARGIN_PT, RIGHT_MARGIN_PT, PARAGRAPH_INDENT_PT,
                     # INDENT_STEP_PT, INDENT_TOL_PT, ALIGN_TOL_PT, ALIGN_FRACTION_OK,
                     # LINE_SPACING_TARGET, LINE_SPACING_TOL, MAX_GAP_BEFORE_AFTER_FACTOR,
                     # RE_START_SIMPLE, RE_START_TIGHT, DEBUG_DIAGNOSTICS, CM_TO_PT, MARKER_MAX_W_PT, MARKER_MAX_H_PT

# --- локальные расширения символов-маркеров ---
MINUS = "\u2212"  # математический минус − (U+2212)
# Базовый набор буллитов + en/em-dash + дефис + минус
BULLET_CHARS = "•·●▪■" + EN_DASH + "—-" + MINUS

def mm_to_pt(mm: float) -> float: return mm * 2.8346456693
def pt_to_mm(pt: float) -> float: return pt / 2.8346456693

@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]
    x0_text: float
    x0_text_src: str
    head_text: str
    head_kind: str
    number_kind: str
    tight_sep_ok: bool
    marker_x0: Optional[float] = None
    marker_x1: Optional[float] = None
    is_bold: bool = False  # вся видимая строка жирная?

@dataclass
class Item:
    page_index0: int
    line: Line
    level: int
    kind: str            # "bulleted" | "numbered"
    marker_text: str     # сам маркер (символ или префикс)
    number_kind: str     # "digits" | "rusalpha" | "roman" | ""

@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect

# --- заголовок раздела источников, ниже которого списки не проверяем ---
RE_REFS_HEAD = re.compile(
    r"^\s*(?:СПИСОК\s+ИСПОЛЬЗОВАННЫХ\s+ИСТОЧНИКОВ|СПИСОК\s+ИСТОЧНИКОВ|ЛИТЕРАТУРА|ИСТОЧНИКИ|БИБЛИОГРАФИЧЕСКИЙ\s+СПИСОК)\s*\.?\s*$",
    re.IGNORECASE
)

# --- эвристики жирности ---
BOLD_HINTS = ("bold", "semibold", "demi", "black", "heavy", "extrabold", "ultrabold")
NON_BOLD_HINTS = ("regular", "book", "light", "thin")

def _fontname_is_bold(name: str) -> bool:
    n = (name or "").lower()
    if any(h in n for h in NON_BOLD_HINTS):
        return False
    return any(h in n for h in BOLD_HINTS)

def _span_is_bold(sp: Dict) -> bool:
    return _fontname_is_bold(sp.get("font", ""))

def _line_all_bold(spans: List[Dict]) -> bool:
    has_visible = False
    for sp in spans or []:
        t = sp.get("text", "")
        if t and t.strip():
            has_visible = True
            if not _span_is_bold(sp):
                return False
    return has_visible

def _median(vals: List[float]) -> float:
    if not vals: return 0.0
    s = sorted(vals); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2.0

def _y_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    return max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))

def _rect_area_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    ix0 = max(a.x0, b.x0); iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1); iy1 = min(a.y1, b.y1)
    if ix1 > ix0 and iy1 > iy0:
        return (ix1-ix0)*(iy1-iy0)
    return 0.0

# ---- Сбор текста + RAW (char-level) ----
def _collect_text_lines_with_raw(page: fitz.Page) -> List[Line]:
    vis_lines: List[Line] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys, texts, spans, sizes, fonts = [], [], [], [], [], []
            for sp in ln.get("spans", []):
                x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                t = sp.get("text") or ""
                if t.strip():
                    xs += [x0,x1]; ys += [y0,y1]
                    sizes.append(float(sp.get("size",0))); fonts.append(sp.get("font",""))
                    spans.append(sp); texts.append(t)
            if not xs:
                continue
            text = "".join(texts).strip()
            if not text:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""

            vis_lines.append(Line(
                text=text, bbox=rect, size=size, font=font, spans=spans,
                x0_text=rect.x0, x0_text_src="", head_text="", head_kind="",
                number_kind="", tight_sep_ok=False, is_bold=False
            ))

    if not vis_lines:
        return vis_lines

    raw = page.get_text("rawdict")
    raw_lines = []
    for b in raw.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys = [], []
            for sp in ln.get("spans", []):
                bb = sp.get("bbox", [0,0,0,0])
                xs += [bb[0], bb[2]]
                ys += [bb[1], bb[3]]
            if xs:
                raw_lines.append({"bbox": [min(xs), min(ys), max(xs), max(ys)],
                                  "spans": ln.get("spans", [])})

    # Сопоставление для извлечения позиционирования после маркера
    for L in vis_lines:
        # жирность строки
        L.is_bold = _line_all_bold(L.spans)

        best = None
        best_overlap = 0.0
        for rl in raw_lines:
            rb = fitz.Rect(*map(float, rl["bbox"]))
            ov = _rect_area_overlap(L.bbox, rb)
            if ov > best_overlap:
                best = rl; best_overlap = ov

        head_text, head_kind, number_kind, tight_sep_ok = "", "", "", False
        x0_text = L.bbox.x0; x0_text_src = "line_bbox"

        # пробуем регулярку по префиксу
        m = RE_START_SIMPLE.match(L.text) or RE_START_TIGHT.match(L.text)
        if m:
            head_text = m.group(0).lstrip()
            if head_text.startswith(EN_DASH) or head_text[:1] in BULLET_CHARS:
                head_kind = "bulleted"; number_kind = ""
            elif head_text and head_text[0].isdigit():
                head_kind = "numbered"; number_kind = "digits"
            elif head_text and head_text[0] in ALLOWED_LETTERS:
                head_kind = "numbered"; number_kind = "rusalpha"
            elif re.match(r"[IVXLC]", head_text[:1] or ""):
                head_kind = "numbered"; number_kind = "roman"

            marker_x0 = None
            marker_x1 = None
            if best and best.get("spans"):
                sp0 = best["spans"][0]
                chars = sp0.get("chars", [])
                i = 0
                for c in head_text:
                    if i < len(chars) and chars[i].get("c") == c:
                        i += 1
                    else:
                        break
                if i > 0 and chars:
                    first_bb = chars[0].get("bbox", [L.bbox.x0, L.bbox.y0, L.bbox.x1, L.bbox.y1])
                    last_bb  = chars[i-1].get("bbox", first_bb)
                    marker_x0 = float(first_bb[0])
                    marker_x1 = float(last_bb[2])

                while i < len(chars) and chars[i].get("c") in (" ", NBSP, "\t"):
                    i += 1
                if i < len(chars):
                    bb = chars[i].get("bbox", [L.bbox.x0, L.bbox.y0, L.bbox.x1, L.bbox.y1])
                    x0_text = float(bb[0]); x0_text_src = "after_marker_char"
                    if i-1 >= 0:
                        prev_bb = chars[i-1].get("bbox", bb)
                        tight_sep_ok = (x0_text - float(prev_bb[2])) >= 0.2 * (L.size or 10.0)
                else:
                    if len(L.spans) >= 2:
                        x0_text = float(L.spans[1].get("bbox", [L.bbox.x0])[0]); x0_text_src = "next_span"
                    else:
                        x0_text = L.bbox.x0; x0_text_src = "tight_regex_fallback"

            else:
                if len(L.spans) >= 2:
                    x0_text = float(L.spans[1].get("bbox", [L.bbox.x0])[0]); x0_text_src = "next_span"
                else:
                    x0_text = L.bbox.x0; x0_text_src = "tight_regex_fallback"

            L.marker_x0 = marker_x0
            L.marker_x1 = marker_x1

        else:
            # маркер-символ в первом span
            if L.spans:
                s0 = L.spans[0].get("text", "")
                if s0:
                    ch0 = s0[0]
                    if ch0 in BULLET_CHARS:
                        head_text = ch0; head_kind = "bulleted"; number_kind = ""
                        if best and best.get("spans"):
                            sp0 = best["spans"][0]; chars = sp0.get("chars", [])
                            if len(chars) >= 1:
                                first_bb = chars[0].get("bbox", [L.bbox.x0, L.bbox.y0, L.bbox.x1, L.bbox.y1])
                                L.marker_x0 = float(first_bb[0])
                                L.marker_x1 = float(first_bb[2])
                            if len(chars) >= 2:
                                j = 1
                                while j < len(chars) and chars[j].get("c") in (" ", NBSP, "\t"):
                                    j += 1
                                if j < len(chars):
                                    bb = chars[j].get("bbox", [L.bbox.x0, L.bbox.y0, L.bbox.x1, L.bbox.y1])
                                    x0_text = float(bb[0]); x0_text_src = "after_marker_char"
                                    prev_bb = chars[j-1].get("bbox", bb)
                                    tight_sep_ok = (x0_text - float(prev_bb[2])) >= 0.4 * (L.size or 10.0)

        L.x0_text = x0_text
        L.x0_text_src = x0_text_src
        L.head_text = head_text
        L.head_kind = head_kind
        L.number_kind = number_kind
        L.tight_sep_ok = tight_sep_ok

    # горизонтальный снап, чтобы упорядочивание было стабильнее
    y_snap = mm_to_pt(0.3)
    vis_lines.sort(key=lambda L: (round(L.bbox.y0 / y_snap)*y_snap, L.bbox.x0))
    return vis_lines

# ---- Векторные маркеры (fallback) ----
def _collect_vector_markers(page: fitz.Page) -> List[fitz.Rect]:
    markers: List[fitz.Rect] = []
    try:
        drawings = page.get_drawings(extended=True)
    except TypeError:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        rect = d.get("rect") or d.get("bbox")
        if not rect:
            continue
        r = fitz.Rect(*map(float, rect))
        if r.width <= MARKER_MAX_W_PT and r.height <= MARKER_MAX_H_PT:
            ar = r.width / max(1e-3, r.height)
            if 0.6 <= ar <= 1.6:
                markers.append(r)
    return markers

# ---- Склейка «строка-маркер + строка-текст» в PDF, где маркер отдельной строкой ----
_SIMPLE_BULLET_ONLY = re.compile(
    r"^\s*(?:[" + re.escape(BULLET_CHARS) + r"]|" + re.escape(EN_DASH) + r")\s*$"
)

def _merge_bullet_lines(lines: List[Line]) -> List[Line]:
    if not lines:
        return lines
    out: List[Line] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        t = cur.text.strip()
        if _SIMPLE_BULLET_ONLY.match(t) and (i + 1 < len(lines)):
            nxt = lines[i + 1]
            if _y_overlap(cur.bbox, nxt.bbox) >= 0.25 * max(1.0, min(cur.bbox.height, nxt.bbox.height)):
                if nxt.bbox.x0 >= cur.bbox.x0 - 2.0:
                    merged_text = (t + " " + nxt.text.lstrip()).strip()
                    mx0 = min(cur.bbox.x0, nxt.bbox.x0)
                    my0 = min(cur.bbox.y0, nxt.bbox.y0)
                    mx1 = max(cur.bbox.x1, nxt.bbox.x1)
                    my1 = max(cur.bbox.y1, nxt.bbox.y1)
                    out.append(Line(
                        text=merged_text,
                        bbox=fitz.Rect(mx0,my0,mx1,my1),
                        size=_median([cur.size, nxt.size]) or nxt.size or 12.0,
                        font=nxt.font or cur.font,
                        spans=cur.spans + nxt.spans,
                        x0_text=nxt.x0_text,
                        x0_text_src=nxt.x0_text_src or "next_span",
                        head_text=cur.head_text or EN_DASH,
                        head_kind="bulleted",
                        number_kind="",
                        tight_sep_ok=True,
                        marker_x0=cur.marker_x0,
                        marker_x1=cur.marker_x1,
                        is_bold=(cur.is_bold and nxt.is_bold)
                    ))
                    i += 2
                    continue
        out.append(cur)
        i += 1
    return out

def _marker_is_bold(line: Line) -> bool:
    if not line.spans:
        return False
    sp0 = line.spans[0]
    if not _span_is_bold(sp0):
        return False
    t0 = sp0.get("text", "") or ""
    if not t0:
        return False
    if RE_START_SIMPLE.match(t0) or RE_START_TIGHT.match(t0):
        return True
    if t0 and t0[0] in BULLET_CHARS:
        return True
    return False

# ---- Классификация линии -> Item ----
def _classify_simple(line: Line, work_left: float, vector_markers: List[fitz.Rect]) -> Optional[Item]:
    # ЖИРНЫЕ пункты НЕ детектим
    if line.is_bold:
        return None
    if _marker_is_bold(line):
        return None

    t = line.text
    x0 = line.x0_text if line.x0_text_src else line.bbox.x0
    base = work_left + PARAGRAPH_INDENT_PT
    delta = x0 - base
    level = max(0, int((delta + INDENT_TOL_PT) // INDENT_STEP_PT))

    # уже выделенный head_kind/number_kind
    if line.head_kind:
        if line.head_kind == "bulleted":
            mt = (line.head_text or "").strip()
            if mt.startswith(EN_DASH):
                mt = EN_DASH
            elif mt and mt[0] in BULLET_CHARS:
                mt = mt[0]
            else:
                mt = EN_DASH
            return Item(-1, line, level, "bulleted", mt, "")
        elif line.head_kind == "numbered":
            return Item(-1, line, level, "numbered", (line.head_text or "").strip(), line.number_kind or "")

    # регулярки
    m = RE_START_SIMPLE.match(t) or RE_START_TIGHT.match(t)
    if m:
        head = m.group(0).lstrip()
        if head.startswith(EN_DASH) or (head[:1] in BULLET_CHARS):
            # распознаём как маркер (в т.ч. "-" и "−"), потом на валидации наругаем
            mt = head[:1] if head[:1] in BULLET_CHARS else EN_DASH
            return Item(-1, line, level, "bulleted", mt, "")
        elif head[:1].isdigit():
            return Item(-1, line, level, "numbered", head.strip(), "digits")
        elif head[:1] in ALLOWED_LETTERS:
            return Item(-1, line, level, "numbered", head.strip(), "rusalpha")
        elif re.match(r"[IVXLC]", head[:1] or ""):
            return Item(-1, line, level, "numbered", head.strip(), "roman")

    # первый символ — явный буллит
    if t and t[:1] in BULLET_CHARS:
        return Item(-1, line, level, "bulleted", t[:1], "")

    # векторный маркер слева от текста
    best = None; best_dx = None
    for r in vector_markers:
        if _y_overlap(r, line.bbox) / max(1.0, min(r.height, line.bbox.height)) < 0.4:
            continue
        if r.x1 > x0 + 1.1*INDENT_STEP_PT:
            continue
        if r.x1 <= x0:
            dx = x0 - r.x1
            if (best is None) or (dx < best_dx):
                best, best_dx = r, dx
    if best is not None:
        line.marker_x0 = float(best.x0)
        line.marker_x1 = float(best.x1)
        return Item(-1, line, level, "bulleted", "[vector]", "")
    return None

# ---- Сбор многострочных пунктов ----
def _gather_multiline_items(lines: List[Line], work_left: float, vector_markers: List[fitz.Rect]) -> List[Item]:
    items: List[Item] = []
    current: Optional[Item] = None
    last_y1: Optional[float] = None

    TAIL_DY_FACTOR = 3.0

    for ln in lines:
        # ЖИРНЫЕ строки обнуляют текущий пункт и пропускаются
        if ln.is_bold or _marker_is_bold(ln):
            current = None
            last_y1 = None
            continue

        head = _classify_simple(ln, work_left, vector_markers)

        if head:
            # очистим ведущий маркер в тексте пункта (оставим «голый» текст)
            clean = RE_START_TIGHT.sub("", head.line.text, count=1).lstrip()
            head.line = Line(
                text=clean,
                bbox=head.line.bbox,
                size=head.line.size, font=head.line.font, spans=head.line.spans,
                x0_text=(head.line.x0_text if head.line.x0_text_src else head.line.bbox.x0),
                x0_text_src=head.line.x0_text_src or "line_bbox",
                head_text=head.line.head_text, head_kind=head.line.head_kind,
                number_kind=head.line.number_kind, tight_sep_ok=head.line.tight_sep_ok,
                marker_x0=head.line.marker_x0, marker_x1=head.line.marker_x1,
                is_bold=head.line.is_bold
            )
            items.append(head)
            current = items[-1]
            last_y1 = current.line.bbox.y1
            continue

        if current is not None:
            fs = current.line.size or 12.0
            if last_y1 is None or (ln.bbox.y0 - last_y1) <= TAIL_DY_FACTOR * fs:
                current.line.text = (current.line.text.rstrip() + " " + ln.text.lstrip()).strip()
                cb = current.line.bbox
                current.line.bbox = fitz.Rect(
                    min(cb.x0, ln.bbox.x0), min(cb.y0, ln.bbox.y0),
                    max(cb.x1, ln.bbox.x1), max(cb.y1, ln.bbox.y1)
                )
                current.line.spans += ln.spans
                last_y1 = current.line.bbox.y1

    return items

# ---- Кластеризация ----
def _cluster_items(items: List[Item], admin: List[str]) -> List[FoundList]:
    if not items:
        return []
    items = sorted(items, key=lambda it: (it.line.bbox.y0, it.line.bbox.x0))
    out: List[FoundList] = []
    cur: List[Item] = []

    def flush():
        if len(cur) >= 2:
            xs0 = [it.line.bbox.x0 for it in cur]
            ys0 = [it.line.bbox.y0 for it in cur]
            xs1 = [it.line.bbox.x1 for it in cur]
            ys1 = [it.line.bbox.y1 for it in cur]
            out.append(FoundList(cur[0].page_index0, cur.copy(), fitz.Rect(min(xs0),min(ys0),max(xs1),max(ys1))))
        elif len(cur) == 1:
            # одиночный явный список (важно для продолжения на следующей странице)
            it = cur[0]
            out.append(FoundList(it.page_index0, [it], it.line.bbox))
        cur.clear()

    MAX_DY_FACTOR = 2.0
    LEFT_TOL = INDENT_TOL_PT + 0.6 * INDENT_STEP_PT

    if DEBUG_DIAGNOSTICS:
        admin.append(f"[Dbg] Clustering pre-check over {len(items)} items:")

    for it in items:
        if not cur:
            cur = [it]; continue
        prev = cur[-1]
        dy = it.line.bbox.y0 - prev.line.bbox.y1
        fs = _median([it.line.size, prev.line.size]) or 12.0
        step_ok = (-0.2 * fs) <= dy <= (MAX_DY_FACTOR * fs)

        x0_it  = it.line.x0_text if it.line.x0_text_src else it.line.bbox.x0
        x0_prv = prev.line.x0_text if prev.line.x0_text_src else prev.line.bbox.x0
        left_diff = abs(x0_it - x0_prv)
        level_change = abs(it.level - prev.level)
        same_kind = (it.kind == prev.kind) and (it.number_kind == prev.number_kind)
        left_ok = (left_diff <= LEFT_TOL) or (level_change in (0, 1))
        compatible = same_kind or (level_change in (0, 1))

        if DEBUG_DIAGNOSTICS:
            admin.append(
                f"    pair y={prev.line.bbox.y1:.2f}->y0={it.line.bbox.y0:.2f}  "
                f"dy={dy:.2f} step_ok={step_ok} | "
                f"x0={x0_prv:.2f}->{x0_it:.2f} diff={left_diff:.2f} left_ok={left_ok} | "
                f"kind={prev.kind}:{prev.number_kind}/{it.kind}:{it.number_kind} same={same_kind} compat={compatible}"
            )

        if step_ok and left_ok and compatible:
            cur.append(it)
        else:
            flush()
            cur = [it]
    flush()
    return out

# ---- Нормализация уровней ----
def _normalize_levels_in_list(fl: FoundList) -> None:
    if not fl.items:
        return

    def _x0(it: Item) -> float:
        return it.line.x0_text if it.line.x0_text_src else it.line.bbox.x0

    fl.items[0].level = 0

    for i in range(1, len(fl.items)):
        prev, cur = fl.items[i - 1], fl.items[i]
        x_prev, x_cur = _x0(prev), _x0(cur)
        dx = x_cur - x_prev

        same_kind = (cur.kind == prev.kind) and (cur.number_kind == prev.number_kind)

        if not same_kind:
            if dx >= -INDENT_TOL_PT:
                lvl = prev.level + 1
            else:
                lvl = max(0, prev.level - 1)
        else:
            if dx > (INDENT_STEP_PT * 0.5):
                lvl = prev.level + 1
            elif dx < -(INDENT_STEP_PT * 0.5):
                lvl = max(0, prev.level - 1)
            else:
                lvl = prev.level

        if lvl > prev.level + 1:
            lvl = prev.level + 1
        elif lvl < max(0, prev.level - 1):
            lvl = max(0, prev.level - 1)

        cur.level = lvl

# ---- Межстрочник ----
def _first_text_span_size(ln: Line) -> float:
    if ln.spans:
        for sp in ln.spans:
            t = sp.get("text", "")
            if t and t[0] not in (EN_DASH,):
                try:
                    v = float(sp.get("size", 0.0))
                    if v > 0:
                        return v
                except Exception:
                    pass
    return ln.size or 12.0

def _line_spacing_check(lines: List[Line]):
    if len(lines) < 2:
        return True, None
    y_refs = []
    for ln in lines:
        yb = None
        if ln.spans:
            yb = ln.spans[0].get("origin", [None, None])[1]
        y_refs.append(float(yb) if yb is not None else float(ln.bbox.y0))
    dys = [y_refs[i] - y_refs[i-1] for i in range(1, len(y_refs))]
    hs  = [_first_text_span_size(ln) for ln in lines]
    r = _median(dys) / max(1e-3, _median(hs))
    lo, hi = LINE_SPACING_TARGET - LINE_SPACING_TOL, LINE_SPACING_TARGET + LINE_SPACING_TOL
    return (lo-1e-3 <= r <= hi+1e-3), r

# ---- Исключения пересекающихся регионов (иллюстрации и т.п.) ----
def _apply_excludes_to_lines(lines: List[Line], excludes: List[fitz.Rect]) -> List[Line]:
    if not excludes:
        return lines
    kept = []
    for ln in lines:
        drop = False
        for bb in excludes:
            area = _rect_area_overlap(ln.bbox, bb)
            if area / max(1.0, ln.bbox.get_area()) >= 0.30:
                drop = True; break
        if not drop:
            kept.append(ln)
    return kept

def _apply_excludes_to_markers(markers: List[fitz.Rect], excludes: List[fitz.Rect]) -> List[fitz.Rect]:
    if not excludes or not markers:
        return markers
    kept = []
    for r in markers:
        drop = False
        for bb in excludes:
            area = _rect_area_overlap(r, bb)
            if area / max(1.0, r.get_area()) >= 0.30:
                drop = True; break
        if not drop:
            kept.append(r)
    return kept

def _space_visual_width_estimate(line: Line) -> float:
    fs = line.size or 12.0
    w = None
    if line.spans:
        sp0 = line.spans[0]
        chars = sp0.get("chars", [])
        for ch in chars:
            if ch.get("c") == " ":
                bb = ch.get("bbox", None)
                if bb:
                    try:
                        w = float(bb[2]) - float(bb[0])
                        break
                    except Exception:
                        pass
    if w is None:
        w = 0.33 * fs
    return max(3.0, min(10.0, w))

def _gap_after_marker(line: Line) -> Optional[float]:
    if line.marker_x1 is None:
        return None
    x_text = line.x0_text if line.x0_text_src else line.bbox.x0
    return float(x_text) - float(line.marker_x1)

# ---- Поиск "среза" по «Списку источников» ----
def _find_refs_cutoff(pdf_document: fitz.Document, start_page: int = 1) -> Optional[Tuple[int, float]]:
    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue
        d = page.get_text("dict")
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                texts, xs, ys = [], [], []
                for sp in ln.get("spans", []):
                    t = sp.get("text") or ""
                    if t.strip():
                        texts.append(t)
                        x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                        xs += [x0,x1]; ys += [y0,y1]
                if not texts:
                    continue
                line_text = "".join(texts).strip()
                if RE_REFS_HEAD.match(line_text):
                    return (pidx, float(min(ys)))
    return None

# ---- Вспомогательное: продолжение списков через разрыв страницы ----
def _clean_item_text_for_end(item: Item) -> str:
    """Текст пункта без ведущего маркера, для проверки финальной точки."""
    t = RE_START_TIGHT.sub("", item.line.text, count=1).strip()
    return t

def _append_item_to_foundlist(fl: FoundList, it: Item):
    """Добавить пункт к уже найденному списку и расширить bbox."""
    fl.items.append(it)
    cb = fl.bbox; lb = it.line.bbox
    fl.bbox = fitz.Rect(min(cb.x0, lb.x0), min(cb.y0, lb.y0), max(cb.x1, lb.x1), max(cb.y1, lb.y1))

# ---- Основная проверка ----
def check_lists(
    pdf_document: fitz.Document,
    *,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    annotate_pdf: bool = True,
    start_page: int = 1,
) -> Dict[str, object]:
    admin: List[str] = []
    list_bboxes_by_page: Dict[int, List[Tuple[float,float,float,float]]] = defaultdict(list)
    error_pages = set()
    n_lists = 0

    # начало «Списка источников»
    cutoff = _find_refs_cutoff(pdf_document, start_page=start_page)
    if DEBUG_DIAGNOSTICS:
        admin.append(f"[Dbg] Refs cutoff: {cutoff!r}")

    # висячий список, который будет продолжен на следующей странице
    pending: Optional[FoundList] = None

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        # после «Списка источников» — пропускаем
        if cutoff and pidx > cutoff[0]:
            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][p{page_num}] skipped (after refs cutoff)")
            continue

        rect = page.bound()
        work_left  = rect.x0 + LEFT_MARGIN_PT
        work_right = rect.x1 - RIGHT_MARGIN_PT

        lines = _collect_text_lines_with_raw(page)
        lines = _merge_bullet_lines(lines)

        # на странице с заголовком «Список источников» берём только строки выше заголовка
        if cutoff and pidx == cutoff[0]:
            cut_y0 = cutoff[1]
            lines = [ln for ln in lines if ln.bbox.y0 < cut_y0]

        excludes = []
        if exclude_bboxes_by_page and page_num in exclude_bboxes_by_page:
            excludes = [fitz.Rect(*b) for b in exclude_bboxes_by_page.get(page_num, [])]
            lines = _apply_excludes_to_lines(lines, excludes)

        vector_markers = _collect_vector_markers(page)
        if excludes:
            vector_markers = _apply_excludes_to_markers(vector_markers, excludes)
        if cutoff and pidx == cutoff[0]:
            cut_y0 = cutoff[1]
            vector_markers = [r for r in vector_markers if r.y0 < cut_y0]

        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Lines after collect+merge: {len(lines)}")
            admin.append(f"[Dbg][p{page_num}] Vector markers: {len(vector_markers)}")

        candidates: List[Item] = _gather_multiline_items(lines, work_left, vector_markers)
        for it in candidates:
            it.page_index0 = pidx

        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Items after classify+multiline: {len(candidates)}")

        # Кластеризация кандидатов в списки
        found = _cluster_items(candidates, admin)

        # Если есть "висячий" список с предыдущей страницы — продолжим его первым списком текущей страницы
        if pending and found:
            # Склеиваем pending + found[0]
            first = found.pop(0)
            for it in first.items:
                _append_item_to_foundlist(pending, it)
            # Проверяем завершающую точку в последнем пункте
            if pending.items:
                last_txt = _clean_item_text_for_end(pending.items[-1])
                if last_txt.endswith("."):
                    # список закрыт — считаем, нормализуем уровни и отдаём в общую обработку как один из found
                    _normalize_levels_in_list(pending)
                    found.insert(0, pending)
                    pending = None
                else:
                    # всё ещё висит — оставляем pending, а found продолжаем как обычно
                    pass

        # Если pending был, но на этой странице нет ни одного списка — оставляем pending висеть дальше

        # Обработка/валидация найденных (и, возможно, склеенного pending) списков
        for fl in list(found):
            _normalize_levels_in_list(fl)
            n_lists += 1
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []
            top_kind = None
            top_number_kind = None
            for it in fl.items:
                if it.level == 0 and top_kind is None:
                    top_kind = it.kind; top_number_kind = it.number_kind

                # Вид маркера верхнего уровня
                if it.level == 0:
                    if it.kind == "bulleted" and it.marker_text not in (EN_DASH, "[vector]"):
                        issues.append(f"Ур.1: маркированный список должен использовать только «{EN_DASH}».")
                    if it.kind == "numbered" and it.number_kind not in ("digits","rusalpha","roman"):
                        issues.append("Ур.1: нумерованный список должен быть вида «1)», «а)» или «I)».")
                else:
                    if top_kind and it.kind == top_kind:
                        issues.append(f"Ур.{it.level+1}: вид маркера/номера должен отличаться от уровня 1.")
                    if top_number_kind and it.number_kind and it.number_kind == top_number_kind:
                        issues.append(f"Ур.{it.level+1}: тип нумерации должен отличаться от уровня 1.")

            # Контекст до/после списка
            if fl.items:
                all_lines_on_page = lines
                head = fl.items[0].line
                head_y0 = head.bbox.y0
                prev = max((ln for ln in all_lines_on_page if ln.bbox.y1 <= head_y0), key=lambda L: L.bbox.y1, default=None)
                if prev:
                    fs = max(10.0, head.size or 12.0)
                    gap = head_y0 - prev.bbox.y1
                    if gap > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                        issues.append("Перед списком не должно быть пустой строки (интервал до = 0 pt).")
                    if not prev.text.rstrip().endswith(":"):
                        issues.append("Перед списком должно быть предложение, оканчивающееся двоеточием.")
                else:
                    issues.append("Не найдено предложение с двоеточием непосредственно перед списком.")

                tail_y1 = fl.items[-1].line.bbox.y1
                nxt = min((ln for ln in all_lines_on_page if ln.bbox.y0 >= tail_y1), key=lambda L: L.bbox.y0, default=None)
                if nxt:
                    fs2 = max(10.0, fl.items[-1].line.size or 12.0)
                    gap2 = nxt.bbox.y0 - tail_y1
                    if gap2 > MAX_GAP_BEFORE_AFTER_FACTOR * fs2:
                        issues.append("После списка не должно быть пустой строки (интервал после = 0 pt).")

            # Межстрочный интервал внутри списка
            block_lines = [it.line for it in fl.items]
            ok_raw, ratio_raw = _line_spacing_check(block_lines)
            if ratio_raw is not None:
                # корректировка под возможные смещения
                ratio_adj = max(0.0, ratio_raw - 0.25)
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                ok_ls = (lo - 1e-3) <= ratio_adj <= (hi + 1e-3)
                if not ok_ls:
                    issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio_adj:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # Отступы и зазор после маркера (жирные мы уже отфильтровали на входе => здесь всё обычное)
            for it in fl.items:
                marker_x0 = it.line.marker_x0
                if marker_x0 is None:
                    marker_x0 = it.line.x0_text if it.line.x0_text_src else it.line.bbox.x0
                dx_marker = float(marker_x0) - work_left
                required_indent_pt = PARAGRAPH_INDENT_PT + it.level * INDENT_STEP_PT

                if abs(dx_marker - required_indent_pt) > (INDENT_TOL_PT + 3.0):
                    issues.append(
                        f"Каждый пункт списка уровня {it.level+1} "
                        f"должен иметь отступ {required_indent_pt/CM_TO_PT:.2f} см (меряем до маркера)."
                    )

                gap = _gap_after_marker(it.line)
                if gap is not None:
                    space_w = _space_visual_width_estimate(it.line)
                    if gap > space_w * 1.15:
                        issues.append("После маркера слишком большой зазор: не больше одного пробела.")

            # Пунктуация пунктов
            pure_texts = [RE_START_TIGHT.sub("", it.line.text, count=1).strip() for it in fl.items]
            for i, txt in enumerate(pure_texts):
                last = (i == len(pure_texts)-1)
                words = [w for w in re.sub(r"[^\w\sА-Яа-яЁё-]", "", txt).split() if re.search(r"[A-Za-zА-Яа-яЁё]", w)]
                is_short = 1 <= len(words) <= 2
                if not last:
                    if is_short and not txt.endswith(","):
                        issues.append("Короткий пункт (1–2 слова) должен оканчиваться запятой, кроме последнего (точка).")
                    if not is_short and not txt.endswith(";"):
                        issues.append("Пункт должен оканчиваться точкой с запятой, кроме последнего (точка).")
                else:
                    if not txt.endswith("."):
                        issues.append("Последний пункт списка должен оканчиваться точкой.")
                if not last and re.search(r"\.\s+[А-ЯЁA-Z]", txt):
                    issues.append("Внутри пункта не допускаются новые предложения.")

            # Аннотации/сигналы
            if issues:
                error_pages.add(page_num)
                if annotate_pdf:
                    try:
                        ann = page.add_text_annot(fitz.Point(fl.bbox.x0, fl.bbox.y0),
                            "Список: нарушения\n" + "\n".join(f"• {m}" for m in issues))
                        ann.set_info(title="Сервис нормоконтроля", content="\n".join(issues))
                        ann.update()
                    except Exception:
                        pass
            else:
                if annotate_pdf:
                    try:
                        ann = page.add_text_annot(fitz.Point(fl.bbox.x0, fl.bbox.y0),
                            f"Список корректен ({len(fl.items)} п.)")
                        ann.set_info(title="Сервис нормоконтроля", content="Список корректен")
                        ann.update()
                    except Exception:
                        pass

        # Если на странице остались списки и последний из них НЕ заканчивается точкой — вешаем pending
        if not pending and found:
            last_fl = found[-1]
            if last_fl.items:
                last_txt = _clean_item_text_for_end(last_fl.items[-1])
                if not last_txt.endswith("."):
                    # переносим его в pending (и НЕ считаем сейчас, он будет учтён когда закроется точкой)
                    pending = last_fl
                    # выкидываем его из подсчитанных (мы уже увеличили n_lists и добавили bbox выше, скорректируем)
                    # Корректно: отменим учёт последнего — уменьшим n_lists и уберём bbox
                    n_lists -= 1
                    if list_bboxes_by_page.get(page_num):
                        try:
                            list_bboxes_by_page[page_num].pop()
                            if not list_bboxes_by_page[page_num]:
                                list_bboxes_by_page.pop(page_num, None)
                        except Exception:
                            pass
                    # Также надо убрать аннотации, но это сложно постфактум — оставим как есть.

    # Если документ закончился, а pending не закрыт точкой — считаем его завер­шившимся на последнем пункте этой же страницы
    # (но добавим предупреждение)
    if pending and pending.items:
        # финализируем
        _normalize_levels_in_list(pending)
        page_num = pending.page_index0 + 1
        if page_num >= start_page:
            n_lists += 1
            list_bboxes_by_page[page_num].append((pending.bbox.x0, pending.bbox.y0, pending.bbox.x1, pending.bbox.y1))
            error_pages.add(page_num)

    user_summary = ("⚠️Проверка списков: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
                    ) if error_pages else "✅Проверка списков"
    admin_details = (f"[Lists] Найдено списков: {n_lists}"
                     + ("\n" + "\n".join(admin) if admin else ""))

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
    }
