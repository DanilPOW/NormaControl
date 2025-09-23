# scripts/list_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict

# ==========================
# Константы и настройки
# ==========================
MM_TO_PT = 2.834646
CM_TO_PT = 28.35

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

def mm_to_pt(mm: float) -> float: return mm * MM_TO_PT
def pt_to_mm(pt: float) -> float: return pt / MM_TO_PT

# Уровни и допуски
INDENT_STEP_CM = 0.75
INDENT_STEP_PT = INDENT_STEP_CM * CM_TO_PT
INDENT_TOL_PT  = 4.0

# Абзацный отступ для пунктов (ГОСТ: 1.25 см)
PARAGRAPH_INDENT_CM = 1.25
PARAGRAPH_INDENT_PT = PARAGRAPH_INDENT_CM * CM_TO_PT

LINE_SPACING_TARGET = 1.50
LINE_SPACING_TOL    = 0.06
ALIGN_TOL_PT        = 4.0
ALIGN_FRACTION_OK   = 0.70

# «До/после = 0 pt» (эвристика)
MAX_GAP_BEFORE_AFTER_FACTOR = 1.2  # * fontsize

# Маркеры
EN_DASH = "–"
RUS_LETTERS = "абвгдеёжзийклмнопрстуфхцчшщьыъэюя"
EXCLUDED = set("ёзйочьъы")
ALLOWED_LETTERS = tuple(ch for ch in RUS_LETTERS if ch not in EXCLUDED)
ALLOWED_STR = "".join(ALLOWED_LETTERS)
NBSP = "\u00A0"
SPACE_CLS = rf"[ \t{NBSP}]"

# Головы пункта
RE_START_TIGHT = re.compile(
    rf"^\s*(?:{re.escape(EN_DASH)}|"
    rf"\d+[.)]|"
    rf"[{ALLOWED_STR}][.)]|"
    rf"[IVXLC]+[.)])"
)
RE_START_SIMPLE = re.compile(
    rf"^\s*(?:{re.escape(EN_DASH)}{SPACE_CLS}+|\d+[.)]{SPACE_CLS}+|"
    rf"[{ALLOWED_STR}][.)]{SPACE_CLS}+|[IVXLC]+[.)]{SPACE_CLS}+)"
)
RE_ONLY_ONE_SPACE_AFTER = re.compile(
    rf"^\s*(?:\d+[.)]|[{ALLOWED_STR}][.)]|[IVXLC]+[.)]){SPACE_CLS}(?!{SPACE_CLS})"
)
RE_ONLY_ONE_SPACE_AFTER_DASH = re.compile(
    rf"^\s*{re.escape(EN_DASH)}{SPACE_CLS}(?!{SPACE_CLS})"
)

# Символьные буллиты
BULLET_CHARS = "•·●∙◦▪▫■□◆►▶▸▹➤➣➢➧➜➔➙➛➟"
PSEUDO_BULLET_CHARS = "oO"  # допускаем как буллит только при большом зазоре

# Векторные маркеры (fallback)
MARKER_MAX_W_PT = mm_to_pt(8.0)
MARKER_MAX_H_PT = mm_to_pt(8.0)

# Диагностика
DEBUG_DIAGNOSTICS = True

# ==========================
# Структуры
# ==========================
@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]
    x0_text: float           # левый край контента после маркера
    x0_text_src: str         # источник x0_text
    head_text: str           # распознанная «голова»
    head_kind: str           # "bulleted" | "numbered" | ""
    number_kind: str         # "digits" | "rusalpha" | "roman" | ""
    tight_sep_ok: bool       # визуальная щель «засчитана»

@dataclass
class Item:
    page_index0: int
    line: Line
    level: int
    kind: str              # "bulleted" | "numbered"
    marker_text: str
    number_kind: str       # "digits" | "rusalpha" | "roman" | "" (bulleted)

@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect

# ==========================
# Вспомогательные
# ==========================
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

# ==========================
# Сбор текста + RAW (char-level)
# ==========================
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
                number_kind="", tight_sep_ok=False
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

    # матчим vis_line с raw_line по максимуму перекрытия
    for L in vis_lines:
        best = None
        best_overlap = 0.0
        for rl in raw_lines:
            rb = fitz.Rect(*map(float, rl["bbox"]))
            ov = _rect_area_overlap(L.bbox, rb)
            if ov > best_overlap:
                best = rl; best_overlap = ov

        head_text, head_kind, number_kind, tight_sep_ok = "", "", "", False
        x0_text = L.bbox.x0; x0_text_src = "line_bbox"

        m = RE_START_SIMPLE.match(L.text) or RE_START_TIGHT.match(L.text)
        if m:
            head_text = m.group(0).lstrip()
            if head_text.startswith(EN_DASH):
                head_kind = "bulleted"; number_kind = ""
            elif head_text and head_text[0].isdigit():
                head_kind = "numbered"; number_kind = "digits"
            elif head_text and head_text[0] in ALLOWED_LETTERS:
                head_kind = "numbered"; number_kind = "rusalpha"
            elif re.match(r"[IVXLC]", head_text[:1] or ""):
                head_kind = "numbered"; number_kind = "roman"

            if best and best.get("spans"):
                sp0 = best["spans"][0]
                chars = sp0.get("chars", [])
                i = 0
                for c in head_text:
                    if i < len(chars) and chars[i].get("c") == c:
                        i += 1
                    else:
                        break
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
        else:
            # буллит первым символом
            if L.spans:
                s0 = L.spans[0].get("text", "")
                if s0:
                    ch0 = s0[0]
                    if ch0 in BULLET_CHARS:
                        head_text = ch0; head_kind = "bulleted"; number_kind = ""
                        if best and best.get("spans"):
                            sp0 = best["spans"][0]; chars = sp0.get("chars", [])
                            if len(chars) >= 2:
                                j = 1
                                while j < len(chars) and chars[j].get("c") in (" ", NBSP, "\t"):
                                    j += 1
                                if j < len(chars):
                                    bb = chars[j].get("bbox", [L.bbox.x0, L.bbox.y0, L.bbox.x1, L.bbox.y1])
                                    x0_text = float(bb[0]); x0_text_src = "after_marker_char"
                                    prev_bb = chars[j-1].get("bbox", bb)
                                    tight_sep_ok = (x0_text - float(prev_bb[2])) >= 0.4 * (L.size or 10.0)
                                else:
                                    x0_text = L.bbox.x0; x0_text_src = "line_bbox"
                        else:
                            if len(L.spans) >= 2:
                                x0_text = float(L.spans[1].get("bbox", [L.bbox.x0])[0]); x0_text_src = "next_span"
                    elif ch0 in PSEUDO_BULLET_CHARS:
                        head_text = ch0; head_kind = "bulleted"; number_kind = ""
                        if best and best.get("spans"):
                            sp0 = best["spans"][0]; chars = sp0.get("chars", [])
                            if len(chars) >= 2:
                                j = 1
                                while j < len(chars) and chars[j].get("c") in (" ", NBSP, "\t"):
                                    j += 1
                                if j < len(chars):
                                    bb = chars[j].get("bbox", [L.bbox.x0, L.bbox.y0, L.bbox.x1, L.bbox.y1])
                                    x0_text = float(bb[0]); x0_text_src = "after_marker_char"
                                    prev_bb = chars[j-1].get("bbox", bb)
                                    tight_sep_ok = (x0_text - float(prev_bb[2])) >= 0.8 * (L.size or 10.0)

        L.x0_text = x0_text
        L.x0_text_src = x0_text_src
        L.head_text = head_text
        L.head_kind = head_kind
        L.number_kind = number_kind
        L.tight_sep_ok = tight_sep_ok

    y_snap = mm_to_pt(0.3)
    vis_lines.sort(key=lambda L: (round(L.bbox.y0 / y_snap)*y_snap, L.bbox.x0))
    return vis_lines

# ==========================
# Векторные маркеры (fallback)
# ==========================
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

# ==========================
# Склейка «буллит-строка + текст-строка»
# ==========================
_SIMPLE_BULLET_ONLY = re.compile(
    r"^\s*(?:[" + BULLET_CHARS + r"]|{}|-|—)\s*$".format(re.escape(EN_DASH))
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
                        tight_sep_ok=True
                    ))
                    i += 2
                    continue
        out.append(cur)
        i += 1
    return out

# ==========================
# Классификация линии -> Item
# ==========================
def _classify_simple(line: Line, work_left: float, vector_markers: List[fitz.Rect]) -> Optional[Item]:
    t = line.text
    x0 = line.x0_text if line.x0_text_src else line.bbox.x0
    base = work_left + PARAGRAPH_INDENT_PT
    level = max(0, int(round((x0 - base) / INDENT_STEP_PT)))

    if line.head_kind:
        if line.head_kind == "bulleted":
            return Item(-1, line, level, "bulleted", line.head_text or EN_DASH, "")
        elif line.head_kind == "numbered":
            return Item(-1, line, level, "numbered", line.head_text.strip(), line.number_kind or "")

    m = RE_START_SIMPLE.match(t) or RE_START_TIGHT.match(t)
    if m:
        head = m.group(0).lstrip()
        if head.startswith(EN_DASH):
            return Item(-1, line, level, "bulleted", EN_DASH, "")
        elif head[:1].isdigit():
            return Item(-1, line, level, "numbered", head.strip(), "digits")
        elif head[:1] in ALLOWED_LETTERS:
            return Item(-1, line, level, "numbered", head.strip(), "rusalpha")
        elif re.match(r"[IVXLC]", head[:1] or ""):
            return Item(-1, line, level, "numbered", head.strip(), "roman")

    if t and t[:1] in BULLET_CHARS:
        return Item(-1, line, level, "bulleted", t[:1], "")

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
        return Item(-1, line, level, "bulleted", "[vector]", "")
    return None

# ==========================
# Сбор многострочных пунктов (простой режим)
# ==========================
def _gather_multiline_items(lines: List[Line], work_left: float, vector_markers: List[fitz.Rect]) -> List[Item]:
    """
    Простой режим:
      - Маркерная строка -> новый пункт (голова).
      - Все подряд строки без маркера между двумя головами -> хвост текущего пункта.
      - Следующая маркерная строка -> новый пункт того же списка.
    """
    items: List[Item] = []
    current: Optional[Item] = None
    last_y1: Optional[float] = None

    # максимально допустимый вертикальный разрыв для хвоста относительно
    # предыдущей строки пункта
    TAIL_DY_FACTOR = 3.0

    for ln in lines:
        head = _classify_simple(ln, work_left, vector_markers)

        if head:
            # вычищаем маркер из текста головы
            clean = RE_START_TIGHT.sub("", head.line.text, count=1).lstrip()
            head.line = Line(
                text=clean,
                bbox=head.line.bbox,
                size=head.line.size, font=head.line.font, spans=head.line.spans,
                x0_text=(head.line.x0_text if head.line.x0_text_src else head.line.bbox.x0),
                x0_text_src=head.line.x0_text_src or "line_bbox",
                head_text=head.line.head_text, head_kind=head.line.head_kind,
                number_kind=head.line.number_kind, tight_sep_ok=head.line.tight_sep_ok
            )
            items.append(head)
            current = items[-1]
            last_y1 = current.line.bbox.y1
            continue

        # не голова — возможно хвост последнего пункта
        if current is not None:
            fs = current.line.size or 12.0
            if last_y1 is None or (ln.bbox.y0 - last_y1) <= TAIL_DY_FACTOR * fs:
                # присоединяем
                current.line.text = (current.line.text.rstrip() + " " + ln.text.lstrip()).strip()
                cb = current.line.bbox
                current.line.bbox = fitz.Rect(
                    min(cb.x0, ln.bbox.x0), min(cb.y0, ln.bbox.y0),
                    max(cb.x1, ln.bbox.x1), max(cb.y1, ln.bbox.y1)
                )
                current.line.spans += ln.spans
                last_y1 = current.line.bbox.y1
            else:
                # слишком далеко — игнорируем как хвост в рамках простого режима
                pass

    return items

# ==========================
# Кластеризация
# ==========================
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
        cur.clear()

    MAX_DY_FACTOR = 2.0
    LEFT_TOL = INDENT_TOL_PT + 0.6 * INDENT_STEP_PT

    if DEBUG_DIAGNOSTICS:
        admin.append(f"[Dbg] Clustering pre-check over {len(items)} items:")

    for it in items:
        if not cur:
            cur = [it]; continue
        prev = cur[-1]
        # считаем от НИЗА предыдущего пункта — учитывает многострочные хвосты
        dy = it.line.bbox.y0 - prev.line.bbox.y1
        fs = _median([it.line.size, prev.line.size]) or 12.0
        # допускаем малое перекрытие и разумный зазор
        step_ok = (-0.2 * fs) <= dy <= (MAX_DY_FACTOR * fs)

        x0_it  = it.line.x0_text if it.line.x0_text_src else it.line.bbox.x0
        x0_prv = prev.line.x0_text if prev.line.x0_text_src else prev.line.bbox.x0
        left_diff = abs(x0_it - x0_prv)
        level_change = abs(it.level - prev.level)
        same_kind = (it.kind == prev.kind) and (it.number_kind == prev.number_kind)
        left_ok = (left_diff <= LEFT_TOL) or (level_change in (0,1))

        if DEBUG_DIAGNOSTICS:
            admin.append(
                f"    pair y={prev.line.bbox.y1:.2f}->y0={it.line.bbox.y0:.2f}  "
                f"dy={dy:.2f} step_ok={step_ok} | "
                f"x0={x0_prv:.2f}->{x0_it:.2f} diff={left_diff:.2f} left_ok={left_ok} | "
                f"kind={prev.kind}:{prev.number_kind}/{it.kind}:{it.number_kind} same={same_kind}"
            )

        if step_ok and left_ok and same_kind:
            cur.append(it)
        else:
            flush()
            cur = [it]
    flush()
    return out

# ==========================
# Выравнивание и межстрочник
# ==========================
def _detect_align_justify(lines: List[Line], work_left: float, work_right: float) -> bool:
    if len(lines) < 2:
        return True
    x1s = [ln.bbox.x1 for ln in lines[:-1]]
    if not x1s:
        return True
    spread = max(x1s) - min(x1s)
    right_air = [max(0.0, work_right - x1) for x1 in x1s]
    ok_count = sum(ra <= 8.0 for ra in right_air)
    return (spread <= ALIGN_TOL_PT) and (ok_count >= max(1, int(ALIGN_FRACTION_OK * len(x1s))))

def _line_spacing_check(lines: List[Line]):
    if len(lines) < 2:
        return True, None
    y0s = [ln.bbox.y0 for ln in lines]
    dys = [y0s[i]-y0s[i-1] for i in range(1,len(y0s))]
    hs  = [ln.size or 12.0 for ln in lines]
    r = _median(dys)/_median(hs)
    lo, hi = LINE_SPACING_TARGET - LINE_SPACING_TOL, LINE_SPACING_TARGET + LINE_SPACING_TOL
    return (lo-1e-3 <= r <= hi+1e-3), r

# ==========================
# Применение exclude-зон
# ==========================
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

# ==========================
# Основная проверка
# ==========================
def check_lists(
    pdf_document: fitz.Document,
    *,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    annotate_pdf: bool = True,
    start_page: int = 1,
) -> Dict[str, object]:
    """
    Проверка списков в PDF:
      - детекция пунктов (текстовые/символьные/векторные маркеры, включая случай «маркер и текст в одном спане» — по char-level);
      - сбор многострочных пунктов;
      - группировка в списки;
      - валидации ГОСТ-подобных требований.
    """
    admin: List[str] = []
    list_bboxes_by_page: Dict[int, List[Tuple[float,float,float,float]]] = defaultdict(list)
    error_pages = set()
    n_lists = 0

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        rect = page.bound()
        work_left  = rect.x0 + LEFT_MARGIN_PT
        work_right = rect.x1 - RIGHT_MARGIN_PT

        # 1) строки (RAW-aware) + склейка
        lines = _collect_text_lines_with_raw(page)
        lines = _merge_bullet_lines(lines)

        # Применяем exclude зоны к строкам
        excludes = []
        if exclude_bboxes_by_page and page_num in exclude_bboxes_by_page:
            excludes = [fitz.Rect(*b) for b in exclude_bboxes_by_page.get(page_num, [])]
            lines = _apply_excludes_to_lines(lines, excludes)

        # 2) векторные маркеры (fallback) + exclude
        vector_markers = _collect_vector_markers(page)
        if excludes:
            vector_markers = _apply_excludes_to_markers(vector_markers, excludes)

        # DEBUG: печать содержимого
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Lines after collect+merge: {len(lines)}")
            for idx, L in enumerate(lines):
                head_tag = f"{L.head_text}" if L.head_text else ""
                head_kind = f"{L.head_kind}:{L.number_kind}" if L.head_kind else ""
                admin.append(
                    f"  [L{idx}] y0={L.bbox.y0:.2f} x0_line={L.bbox.x0:.2f} "
                    f"x0_text={(L.x0_text if L.x0_text_src else L.bbox.x0):.2f}  "
                    f"head=«{head_tag}» ({head_kind}) tight_sep_ok={L.tight_sep_ok}  text=«{(L.text[:60] + '…') if len(L.text)>60 else L.text}»"
                )
                if L.spans:
                    parts = []
                    for i, sp in enumerate(L.spans):
                        t = sp.get("text", "")
                        bb = sp.get("bbox", [0,0,0,0])
                        parts.append(f"[{i}] «{(t[:20] + '…') if len(t)>20 else t}» {sp.get('font','?')} {float(sp.get('size',0)):.2f}pt x0={float(bb[0]):.2f}")
                    admin.append("        spans: " + " | ".join(parts))
            admin.append(f"[Dbg][p{page_num}] Vector markers: {len(vector_markers)}")
            for i, r in enumerate(vector_markers):
                admin.append(f"    [vm#{i}] rect={r.x0:.2f},{r.y0:.2f}–{r.x1:.2f},{r.y1:.2f} wh=({r.width:.2f}×{r.height:.2f})")

        # 3) классификация + многострочность
        candidates: List[Item] = _gather_multiline_items(lines, work_left, vector_markers)
        for it in candidates:
            it.page_index0 = pidx

        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Items after classify+multiline: {len(candidates)}")
            for it in candidates:
                admin.append(
                    f"    [it] y0={it.line.bbox.y0:.2f} level={it.level} kind={it.kind}/{it.number_kind or ''} "
                    f"marker={it.marker_text}  text=«{(it.line.text[:80] + '…') if len(it.line.text)>80 else it.line.text}»"
                )

        # 4) группируем
        found = _cluster_items(candidates, admin)

        # 5) проверки и аннотации
        for fl in found:
            n_lists += 1
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []
            top_kind = None
            top_number_kind = None
            for it in fl.items:
                if it.level == 0 and top_kind is None:
                    top_kind = it.kind; top_number_kind = it.number_kind

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

                s = it.line.text
                if it.kind == "bulleted" and it.marker_text == EN_DASH:
                    if not RE_ONLY_ONE_SPACE_AFTER_DASH.match(s) and not it.line.tight_sep_ok:
                        issues.append("После «–» должен быть ровно один пробел.")
                if it.kind == "numbered":
                    if not RE_ONLY_ONE_SPACE_AFTER.match(s) and not it.line.tight_sep_ok:
                        issues.append("После номера должен быть ровно один пробел.")

                tail = RE_START_TIGHT.sub("", s, count=1).lstrip()
                if tail[:1].isalpha() and tail[:1].isupper():
                    issues.append("Пункт списка должен начинаться со строчной буквы.")

            # до/после списка
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

            # выравнивание и межстрочник
            block_lines = [it.line for it in fl.items]
            if not _detect_align_justify(block_lines, work_left, work_right):
                issues.append("Список должен быть выровнен по ширине.")
            ok_ls, ratio = _line_spacing_check(block_lines)
            if not ok_ls and ratio is not None:
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # абзацный отступ 1.25 см (по x0_text)
            for it in fl.items:
                x0_text = it.line.x0_text if it.line.x0_text_src else it.line.bbox.x0
                dx = x0_text - work_left
                if abs(dx - PARAGRAPH_INDENT_PT) > (INDENT_TOL_PT + 3.0):
                    issues.append("Каждый пункт списка должен иметь абзацный отступ 1.25 см.")

            # пунктуация
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

            # лог и аннотации
            admin.append(f"[List][Стр. {page_num}] пунктов={len(fl.items)} | уровни~{sorted(set(i.level for i in fl.items))}")
            if issues:
                error_pages.add(page_num)
                admin.extend("  - " + m for m in issues)
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

    user_summary = ("⚠️Проверка списков: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
                    ) if error_pages else "✅Проверка списков"
    admin_details = (f"[Lists] Найдено списков: {n_lists}"
                     + ("\n" + "\n".join(admin) if admin else ""))

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
    }
