# scripts/list_checker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict
import logging

from const import *  # см. ожидания ниже

"""
Ожидается, что в const.py определено:
EN_DASH, NBSP,
LEFT_MARGIN_PT, RIGHT_MARGIN_PT, PARAGRAPH_INDENT_PT,
INDENT_STEP_PT, INDENT_TOL_PT,
LINE_SPACING_TARGET, LINE_SPACING_TOL, MAX_GAP_BEFORE_AFTER_FACTOR,
RE_START_SIMPLE, RE_START_TIGHT, DEBUG_DIAGNOSTICS, CM_TO_PT,
MARKER_MAX_W_PT, MARKER_MAX_H_PT, ALLOWED_LETTERS
"""

# -------------------- ЛОГИ --------------------
logger = logging.getLogger("list_checker")
if not logger.handlers:
    _h = logging.StreamHandler()
    _fmt = logging.Formatter("[%(levelname)s] %(message)s")
    _h.setFormatter(_fmt)
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

def mm_to_pt(mm: float) -> float: return mm * 2.8346456693
def pt_to_mm(pt: float) -> float: return pt / 2.8346456693

# --- Набор буллитов и тире ---
MINUS = "\u2212"  # − (U+2212)
BULLET_CHARS = "•·●▪■"
DASH_CHARS = EN_DASH + "—-" + MINUS  # короткое тире + длинное тире + дефис + минус

# --- Заголовок «Список источников» ---
RE_REFS_HEAD = re.compile(
    r"^\s*(?:СПИСОК\s+ИСПОЛЬЗОВАННЫХ\s+ИСТОЧНИКОВ|СПИСОК\s+ИСТОЧНИКОВ|ЛИТЕРАТУРА|ИСТОЧНИКИ|БИБЛИОГРАФИЧЕСКИЙ\s+СПИСОК|СПИСОК\s+ИСПОЛЬЗ\.\s+ИСТОЧНИКОВ)\s*\.?\s*$",
    re.IGNORECASE
)

# --- Эвристики жирности ---
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

# --- Допустимый «возврат влево» для хвоста пункта ---
LEFT_BACK_TOL_PT = 1.3 * CM_TO_PT  # 1.3 см

@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]
    x0_text: float
    x0_text_src: str
    is_bold: bool = False
    y_bucket: float = 0.0  # для склейки по базовой линии

@dataclass
class Item:
    page_index0: int
    line: Line
    level: int              # вычисляется по отступу относительно base_x0
    kind: str               # "bulleted" | "numbered"
    marker_text: str        # исходный маркер (символ/токен)
    number_kind: str        # "digits" | "alpha" | "roman" | ""

@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect
    # динамические атрибуты (добавляются в коде):
    # _base_x0: float
    # _base_kind: str ("bulleted"/"numbered")
    # _base_marker: str
    # _base_number_kind: str

# --- Утилиты ---
def _rect_area_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    ix0 = max(a.x0, b.x0); iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1); iy1 = min(a.y1, b.y1)
    if ix1 > ix0 and iy1 > iy0:
        return (ix1-ix0)*(iy1-iy0)
    return 0.0

def _median(vals: List[float]) -> float:
    if not vals: return 0.0
    s = sorted(vals); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2.0

def _y_dist(a: fitz.Rect, b: fitz.Rect) -> float:
    return b.y0 - a.y1

def _first_text_span_size(ln: Line) -> float:
    if ln.spans:
        for sp in ln.spans:
            t = sp.get("text", "")
            if t and t.strip():
                try:
                    v = float(sp.get("size", 0.0))
                    if v > 0:
                        return v
                except Exception:
                    pass
    return ln.size or 12.0

# --- Терминатор пункта ---
_TERM_RE = re.compile(r'([\\"»”\)\]]*\s*)[.!?](\s*)$')

def _ends_with_terminator(fl: FoundList) -> bool:
    if not fl.items:
        return True
    tail = (fl.items[-1].line.text or "").rstrip()
    return bool(_TERM_RE.search(tail))

# --- Инденты / уровни ---
def _indent_level(x0: float, base_x0: float) -> int:
    dx = max(0.0, x0 - base_x0)
    step = max(1.0, INDENT_STEP_PT)
    return int(round(dx / step))

# --- Сбор «сырых» линий ---
def _collect_text_lines_with_raw(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    try:
        d = page.get_text("dict")
    except Exception as e:
        logger.exception("Не удалось извлечь текст со страницы %s: %s", page.number+1, e)
        return out

    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            try:
                xs, ys, texts, spans, sizes, fonts = [], [], [], [], [], []
                for sp in ln.get("spans", []):
                    t = sp.get("text") or ""
                    if not t.strip():
                        continue
                    x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                    xs += [x0,x1]; ys += [y0,y1]
                    sizes.append(float(sp.get("size",0)))
                    fonts.append(sp.get("font",""))
                    spans.append(sp); texts.append(t)
                if not xs:
                    continue
                text = "".join(texts).strip()
                if not text:
                    continue
                rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                size = sum(sizes)/len(sizes) if sizes else 0.0
                font = fonts[0] if fonts else ""
                out.append(Line(
                    text=text, bbox=rect, size=size, font=font, spans=spans,
                    x0_text=rect.x0, x0_text_src="line_bbox",
                    is_bold=_line_all_bold(spans),
                ))
            except Exception as e:
                logger.exception("Ошибка разбора строки на стр. %s: %s", page.number+1, e)
    # нормализация и сортировка
    snap = mm_to_pt(0.3)
    for L in out:
        L.y_bucket = round(L.bbox.y0 / snap) * snap
    out.sort(key=lambda L: (L.y_bucket, L.bbox.x0))
    return out

# --- Склейка линий на одной базовой линии (baseline) ---
def _merge_same_baseline(lines: List[Line], admin: List[str], stats: Dict[str,int]) -> List[Line]:
    if not lines:
      return []

    merged: List[Line] = []
    cur_group: List[Line] = [lines[0]]

    def flush(group: List[Line]):
        if not group:
            return
        if len(group) == 1:
            merged.append(group[0]); return
        group.sort(key=lambda l: l.bbox.x0)
        text = " ".join(l.text.strip() for l in group if l.text.strip())
        spans = []
        for l in group: spans.extend(l.spans)
        bbox = fitz.Rect(min(l.bbox.x0 for l in group),
                         min(l.bbox.y0 for l in group),
                         max(l.bbox.x1 for l in group),
                         max(l.bbox.y1 for l in group))
        base = group[0]
        merged.append(Line(
            text=text, bbox=bbox, size=base.size, font=base.font,
            spans=spans, x0_text=bbox.x0, x0_text_src="merged_baseline",
            is_bold=all(l.is_bold for l in group), y_bucket=base.y_bucket
        ))
        stats["baseline_merged_groups"] = stats.get("baseline_merged_groups", 0) + 1
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][baseline] merged {len(group)} parts -> '{text[:40]}'")

    for i in range(1, len(lines)):
        if abs(lines[i].y_bucket - lines[i-1].y_bucket) <= 1e-3:
            cur_group.append(lines[i])
        else:
            flush(cur_group)
            cur_group = [lines[i]]
    flush(cur_group)
    return merged

# --- Склейка «одинокого» маркера с текстом на том же базлайне ---
def _glue_lonely_bullets(lines: List[Line], admin: List[str], stats: Dict[str,int]) -> List[Line]:
    if not lines:
        return []
    out: List[Line] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.text.strip()
        is_single_marker = (len(stripped) == 1 and (stripped in BULLET_CHARS or stripped in DASH_CHARS))
        if is_single_marker:
            j = i + 1
            glued = False
            while j < len(lines) and abs(lines[j].y_bucket - ln.y_bucket) <= 1e-3:
                right = lines[j]
                if right.text.strip():
                    new_text = stripped + " " + right.text.lstrip()
                    bbox = fitz.Rect(min(ln.bbox.x0, right.bbox.x0),
                                     min(ln.bbox.y0, right.bbox.y0),
                                     max(ln.bbox.x1, right.bbox.x1),
                                     max(ln.bbox.y1, right.bbox.y1))
                    spans = ln.spans + right.spans
                    out.append(Line(
                        text=new_text, bbox=bbox, size=right.size or ln.size,
                        font=right.font or ln.font, spans=spans,
                        x0_text=bbox.x0, x0_text_src="glued_marker",
                        is_bold=(ln.is_bold and right.is_bold), y_bucket=ln.y_bucket
                    ))
                    if DEBUG_DIAGNOSTICS:
                        admin.append(f"[Dbg][glue] glued marker '{stripped}' + '{right.text[:30]}' -> '{new_text[:40]}'")
                    stats["glued_markers"] = stats.get("glued_markers", 0) + 1
                    glued = True
                    i = j + 1
                    break
                j += 1
            if glued:
                continue
        out.append(ln)
        i += 1
    return out

# --- Регэкспы нумерации (строго со скобкой ) ) ---
RE_NUM_DIGITS  = re.compile(r"^\s*(\d+)\)\s*")
RE_NUM_ALPHA   = re.compile(r"^\s*([A-Za-zА-Яа-яЁё])\)\s*")
RE_NUM_ROMAN   = re.compile(r"^\s*([IVXLCDMivxlcdm]+)\)\s*")

def _classify_marker(line: Line, admin: Optional[List[str]] = None) -> Optional[Tuple[str,str,str]]:
    """
    Возвращает (kind, number_kind, marker_text) либо None.
    kind: "bulleted"|"numbered"
    number_kind: "digits"|"alpha"|"roman"|"
    marker_text: исходный маркер (например "–", "-" или "1)")
    """
    if line.is_bold:
        return None  # жирные строки вообще не рассматриваем

    t = line.text.lstrip()

    # 1) Символьный буллит/тире
    if t and (t[0] in BULLET_CHARS or t[0] in DASH_CHARS):
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] bulleted '{t[:10].replace(NBSP,' ')}' -> '{t[0]}'")
        return ("bulleted", "", t[0])

    # 2) Нумерация — строго со скобкой ")"
    m = RE_NUM_DIGITS.match(t)
    if m:
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] digits-tight '{m.group(1)}')'")
        return ("numbered", "digits", m.group(1)+")")

    m = RE_NUM_ALPHA.match(t)
    if m:
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] alpha-tight '{m.group(1)}')'")
        return ("numbered", "alpha", m.group(1)+")")

    m = RE_NUM_ROMAN.match(t)
    if m:
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] roman-tight '{m.group(1)}')'")
        return ("numbered", "roman", m.group(1).upper()+")")

    # 3) tight-детекторы начала — тоже только с ")"
    m2 = RE_START_SIMPLE.match(t) or RE_START_TIGHT.match(t)
    if m2:
        head = m2.group(0).lstrip()
        if head and (head[0] in BULLET_CHARS or head[0] in DASH_CHARS):
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] bulleted-tight '{head[:10]}' -> '{head[0]}'")
            return ("bulleted", "", head[0])
        if re.match(r"^\d+\)\s*", head):
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] digits-tight '{head[:10]}'")
            return ("numbered", "digits", re.match(r"^\s*(\d+\))", head).group(1))
        if re.match(r"^[A-Za-zА-Яа-яЁё]\)\s*", head):
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] alpha-tight '{head[:10]}'")
            return ("numbered", "alpha", re.match(r"^\s*([A-Za-zА-Яа-яЁё]\))", head).group(1))
        if re.match(r"^[IVXLCDMivxlcdm]+\)\s*", head):
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] roman-tight '{head[:10]}'")
            return ("numbered", "roman", re.match(r"^\s*([IVXLCDMivxlcdm]+\))", head).group(1).upper())

    if DEBUG_DIAGNOSTICS and admin is not None:
        admin.append(f"[Dbg][classify] no-marker '{t[:40]}'")
    return None

def _strip_marker_text(kind: str, marker_text: str, txt: str) -> str:
    """ Удаляет маркер из начала строки и возвращает «чистый» текст пункта. """
    s = txt.lstrip()
    if kind == "bulleted":
        return s[1:].lstrip() if s else ""
    # numbered: убираем "^\s*<marker>\s+"
    patt = r"^\s*%s\s*(.*)$" % re.escape(marker_text)
    m = re.match(patt, s)
    if m:
        return m.group(1).strip()
    return s

# --- Сбор пунктов с многострочкой ---
def _gather_items(lines: List[Line], admin: List[str], stats: Dict[str, int]) -> List[Item]:
    items: List[Item] = []
    cur: Optional[Item] = None

    for ln in lines:
        # жирные — пропускаем
        if ln.is_bold:
            stats["bold_skipped"] += 1
            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][gather] y0={ln.bbox.y0:.2f} '{ln.text[:40].replace(NBSP,' ')}' -> skip: bold line")
            cur = None
            continue

        mark = _classify_marker(ln, admin if DEBUG_DIAGNOSTICS else None)
        if mark:
            stats["marker_hits"] += 1
            kind, number_kind, marker_text = mark
            clean = _strip_marker_text(kind, marker_text, ln.text)

            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][gather] y0={ln.bbox.y0:.2f} '{ln.text[:40]}' -> marker: {kind}/{number_kind} '{marker_text}'")

            it = Item(
                page_index0=-1,
                line=Line(
                    text=clean, bbox=ln.bbox, size=ln.size, font=ln.font, spans=ln.spans,
                    x0_text=ln.x0_text, x0_text_src=ln.x0_text_src, is_bold=ln.is_bold, y_bucket=ln.y_bucket
                ),
                level=0, kind=kind, marker_text=marker_text, number_kind=number_kind
            )
            items.append(it)
            cur = it
            continue

        stats["marker_none"] += 1
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][gather] y0={ln.bbox.y0:.2f} '{ln.text[:40]}' -> no marker")

        # многострочный хвост к текущему пункту?
        if cur is not None:
            fs = _first_text_span_size(cur.line)
            dy = _y_dist(cur.line.bbox, ln.bbox)

            # сколько «ушла» текущая линия ln влево относительно начала пункта (строки с маркером)
            left_shift = max(0.0, cur.line.x0_text - ln.x0_text)

            # разрешаем левый сдвиг хвоста до 1.3 см; немного ослабим допустимый dy
            if dy <= 3.8 * fs and left_shift <= LEFT_BACK_TOL_PT:
                stats["multiline_attached"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(
                        f"[Dbg][gather] multiline attach: dy={dy:.2f} fs={fs:.2f} left_shift={left_shift:.1f}pt"
                    )
                cur.line.text = (cur.line.text.rstrip() + " " + ln.text.lstrip()).strip()
                cb = cur.line.bbox
                cur.line.bbox = fitz.Rect(
                    min(cb.x0, ln.bbox.x0), min(cb.y0, ln.bbox.y0),
                    max(cb.x1, ln.bbox.x1), max(cb.y1, ln.bbox.y1)
                )
                continue

            # мягкая эвристика — частичное горизонтальное перекрытие и чуть больший лимит
            overlap = max(0.0, min(cur.line.bbox.x1, ln.bbox.x1) - max(cur.line.bbox.x0, ln.bbox.x0))
            width   = max(1.0, min(cur.line.bbox.x1, ln.bbox.x1) - min(cur.line.bbox.x0, ln.bbox.x0))
            if dy <= 4.2 * fs and (overlap / width) >= 0.6 and left_shift <= LEFT_BACK_TOL_PT * 1.1:
                stats["multiline_attached"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(
                        f"[Dbg][gather] multiline attach (soft): dy={dy:.2f}, overlap={overlap/width:.2f}, left_shift={left_shift:.1f}pt"
                    )
                cur.line.text = (cur.line.text.rstrip() + " " + ln.text.lstrip()).strip()
                cb = cur.line.bbox
                cur.line.bbox = fitz.Rect(
                    min(cb.x0, ln.bbox.x0), min(cb.y0, ln.bbox.y0),
                    max(cb.x1, ln.bbox.x1), max(cb.y1, ln.bbox.y1)
                )
                continue

            if DEBUG_DIAGNOSTICS:
                admin.append(
                    f"[Dbg][gather] multiline FAIL: dy={dy:.2f} fs={fs:.2f} left_shift={left_shift:.1f}pt"
                )
            cur = None

    return items

def _normalize_bbox(fl: FoundList) -> None:
    xs0 = [it.line.bbox.x0 for it in fl.items]
    ys0 = [it.line.bbox.y0 for it in fl.items]
    xs1 = [it.line.bbox.x1 for it in fl.items]
    ys1 = [it.line.bbox.y1 for it in fl.items]
    fl.bbox = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1))

def _find_refs_cutoff(pdf_document: fitz.Document, start_page: int = 1) -> Optional[Tuple[int, float]]:
    """ Возвращает (page_index0, y0) заголовка «Список источников».
        Теперь учитываем ТОЛЬКО жирную строку и расположение в верхней четверти страницы.
    """
    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue
        try:
            d = page.get_text("dict")
        except Exception:
            continue
        page_h = float(page.rect.height)
        top_threshold = page_h * 0.25  # верхняя четверть
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                texts, xs, ys, spans = [], [], [], []
                for sp in ln.get("spans", []):
                    t = sp.get("text") or ""
                    if t.strip():
                        texts.append(t)
                        x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                        xs += [x0,x1]; ys += [y0,y1]
                        spans.append(sp)
                if not texts:
                    continue
                line_text = "".join(texts).strip()
                y0_line = float(min(ys)) if ys else 0.0
                if RE_REFS_HEAD.match(line_text):
                    # дополнительно требуем жирность и «положение сверху»
                    if _line_all_bold(spans) and y0_line <= top_threshold:
                        return (pidx, y0_line)
    return None

def _line_spacing_check(lines: List[Line]):
    if len(lines) < 2:
        return True, None
    y_refs = []
    for ln in lines:
        yb = None
        if ln.spans:
            origins = [sp.get("origin", [None, None])[1] for sp in ln.spans if sp.get("origin")]
            if origins:
                yb = min(origins)
        y_refs.append(float(yb) if yb is not None else float(ln.bbox.y0))
    dys = [y_refs[i] - y_refs[i-1] for i in range(1, len(y_refs))]
    hs  = [_first_text_span_size(ln) for ln in lines]
    r = _median(dys) / max(1e-3, _median(hs))
    lo, hi = LINE_SPACING_TARGET - LINE_SPACING_TOL, LINE_SPACING_TARGET + LINE_SPACING_TOL
    # допускаем поправку базовой линии, если она заведена в const
    base_off = getattr(__import__('const'), 'LINE_SPACING_BASELINE_OFFSET', 0.25)
    ratio_adj = max(0.0, r - base_off)
    return (lo-1e-3 <= ratio_adj <= hi+1e-3), ratio_adj

# --- Проверки совместимости/переноса ---
def _next_letter(ch: str, alphabet: str) -> Optional[str]:
    if not ch: return None
    i = alphabet.find(ch)
    if i == -1 or i+1 >= len(alphabet): return None
    return alphabet[i+1]

def _roman_to_int(s: str) -> Optional[int]:
    vals = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
    s = (s or "").upper()
    total = 0
    prev = 0
    for c in reversed(s):
        v = vals.get(c)
        if v is None: return None
        if v < prev: total -= v
        else: total += v; prev = v
    return total

def _compatible_for_carry(prev: FoundList, nxt: FoundList) -> bool:
    if not prev.items or not nxt.items:
        return False
    a, b = prev.items[-1], nxt.items[0]

    base = getattr(prev, "_base_x0", a.line.x0_text)
    la = _indent_level(a.line.x0_text, base)
    lb = _indent_level(b.line.x0_text, base)
    if la != lb:  # переносим только одинаковый уровень
        return False

    if a.kind != b.kind:
        return False

    if a.kind == "bulleted":
        return a.marker_text == b.marker_text

    # numbered: форматы должны совпадать
    if a.number_kind != b.number_kind:
        return False

    if a.number_kind == "digits":
        ma = re.match(r"^\s*(\d+)", a.marker_text or "")
        mb = re.match(r"^\s*(\d+)", b.marker_text or "")
        if not (ma and mb): return False
        return int(mb.group(1)) == int(ma.group(1)) + 1

    if a.number_kind == "alpha":
        la_ = (re.match(r"^\s*([A-Za-zА-Яа-яЁё])", a.marker_text or "") or [None,None])[1]
        lb_ = (re.match(r"^\s*([A-Za-zА-Яа-яЁё])", b.marker_text or "") or [None,None])[1]
        if not (la_ and lb_): return False
        alpha = "".join(ALLOWED_LETTERS)
        return _next_letter(la_, alpha) == lb_

    if a.number_kind == "roman":
        ra_m = re.match(r"^\s*([IVXLCDMivxlcdm]+)", a.marker_text or "")
        rb_m = re.match(r"^\s*([IVXLCDMivxlcdm]+)", b.marker_text or "")
        if not (ra_m and rb_m): return False
        ra = _roman_to_int(ra_m.group(1))
        rb = _roman_to_int(rb_m.group(1))
        if ra is None or rb is None: return False
        return rb == ra + 1

    return False

# --- Основная функция ---
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

    stats = dict(
        bold_skipped=0,
        marker_hits=0,
        marker_none=0,
        multiline_attached=0,
        list_breaks=0,
        pending_started=0,
        pending_attached=0,
        pending_finalized=0,
        baseline_merged_groups=0,
        glued_markers=0,
        finalized_on_break=0,
        carry_rejected=0,
        carry_accepted=0,
    )

    try:
        cutoff = _find_refs_cutoff(pdf_document, start_page=start_page)
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg] Refs cutoff: {cutoff!r}")
    except Exception as e:
        logger.exception("Ошибка поиска заголовка списка источников: %s", e)
        cutoff = None

    pending: Optional[FoundList] = None  # переносимый список между страницами

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        # после «Списка источников» — пропускаем
        if cutoff and pidx > cutoff[0]:
            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][p{page_num}] skipped (after refs cutoff)")
            continue

        try:
            # 1) Сбор строк
            lines = _collect_text_lines_with_raw(page)

            # 2) На странице с заголовком источников — отрезаем низ
            if cutoff and pidx == cutoff[0]:
                cut_y0 = cutoff[1]
                lines = [ln for ln in lines if ln.bbox.y0 < cut_y0]

            # 3) Исключаем заданные области
            if exclude_bboxes_by_page and page_num in exclude_bboxes_by_page:
                excludes = [fitz.Rect(*b) for b in exclude_bboxes_by_page.get(page_num, [])]
                if excludes:
                    kept = []
                    for ln in lines:
                        drop = False
                        for bb in excludes:
                            if _rect_area_overlap(ln.bbox, bb) / max(1.0, ln.bbox.get_area()) >= 0.30:
                                drop = True; break
                        if not drop:
                            kept.append(ln)
                    lines = kept

            # 4) Склейки
            lines = _merge_same_baseline(lines, admin, stats)
            lines = _glue_lonely_bullets(lines, admin, stats)

            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][p{page_num}] Lines after collect+merge: {len(lines)}")

            # 5) Кандидаты пунктов
            candidates = _gather_items(lines, admin, stats)
            for it in candidates:
                it.page_index0 = pidx

            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][p{page_num}] Items after classify+multiline: {len(candidates)}")
        except Exception as e:
            logger.exception("Ошибка подготовки кандидатов на странице %s: %s", page_num, e)
            candidates = []

        # --- Группировка на странице (многоуровневая логика) ---
        found_on_page: List[FoundList] = []
        cur_list: Optional[FoundList] = None
        prev_item: Optional[Item] = None

        def start_new_list(it: Item):
            nonlocal cur_list, prev_item
            cur_list = FoundList(page_index0=pidx, items=[it], bbox=it.line.bbox)
            # базовые параметры списка
            cur_list._base_x0 = it.line.x0_text
            it.level = 0
            cur_list._base_kind = it.kind
            cur_list._base_marker = it.marker_text
            cur_list._base_number_kind = it.number_kind
            prev_item = it
            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][page {page_num}] list:start @y={it.line.bbox.y0:.2f} '{it.line.text[:40]}' base_kind={it.kind} base_marker='{it.marker_text}'")

        def same_list_rule(it: Item) -> bool:
            """ Правило принадлежности к текущему списку:
                - если x0 >= base_x0 (вложенные/тот же уровень),
                - и (на базовом уровне) вид маркера совпадает с базовым.
                Вложенные уровни допускают другие маркеры.
            """
            base_x0 = getattr(cur_list, "_base_x0", it.line.x0_text)
            lvl = _indent_level(it.line.x0_text, base_x0)
            it.level = lvl

            if lvl == 0:
                # базовый уровень: требуем совпадение типа маркера
                if it.kind != cur_list._base_kind:
                    return False
                if it.kind == "bulleted":
                    return it.marker_text == cur_list._base_marker
                else:
                    return it.number_kind == cur_list._base_number_kind
            else:
                # вложенные уровни: любые маркеры, лишь бы отступ не меньше базового
                return (it.line.x0_text + INDENT_TOL_PT) >= base_x0

        for it in candidates:
            if cur_list is None:
                start_new_list(it)
                continue

            if same_list_rule(it):
                cur_list.items.append(it)
                _normalize_bbox(cur_list)
                prev_item = it
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] list:append lvl={it.level} '{it.line.text[:40]}'")
            else:
                # Завершаем текущий список ТОЛЬКО если у него уже есть терминатор
                if _ends_with_terminator(cur_list):
                    found_on_page.append(cur_list)
                    if DEBUG_DIAGNOSTICS:
                        admin.append(f"[Dbg][page {page_num}] list:finalize (new incompatible at base lvl)")
                    start_new_list(it)
                else:
                    # нет точки — продолжаем тем же списком (считаем, что идёт длинный блок)
                    # но если новый явно «влево» от базового x0 — это сигнал нового блока
                    if (it.line.x0_text + INDENT_TOL_PT) < cur_list._base_x0:
                        found_on_page.append(cur_list)
                        if DEBUG_DIAGNOSTICS:
                            admin.append(f"[Dbg][page {page_num}] list:finalize (hard left shift)")
                        start_new_list(it)
                    else:
                        # принимаем как вложенный (форсим уровень ≥ 1)
                        it.level = max(1, _indent_level(it.line.x0_text, cur_list._base_x0))
                        cur_list.items.append(it)
                        _normalize_bbox(cur_list)
                        prev_item = it
                        if DEBUG_DIAGNOSTICS:
                            admin.append(f"[Dbg][page {page_num}] list:append-forced lvl={it.level} '{it.line.text[:40]}'")

        if cur_list and cur_list.items:
            found_on_page.append(cur_list)

        # --- Финализация списков на странице / перенос между страницами ---
        def _finalize_list(fl: FoundList):
            nonlocal n_lists
            if len(fl.items) < 2:
                return
            n_lists += 1
            list_bboxes_by_page[fl.page_index0 + 1].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []

            # Единообразие для базового уровня
            base_kind = getattr(fl, "_base_kind", fl.items[0].kind)
            base_marker = getattr(fl, "_base_marker", fl.items[0].marker_text)
            base_number_kind = getattr(fl, "_base_number_kind", fl.items[0].number_kind)
            base_x0 = getattr(fl, "_base_x0", fl.items[0].line.x0_text)

            # Проверка: базовый уровень должен быть однородным по виду маркера
            top_level_items = [it for it in fl.items if _indent_level(it.line.x0_text, base_x0) == 0]
            if base_kind == "bulleted":
                if any(it.marker_text != base_marker for it in top_level_items):
                    issues.append("На базовом уровне списка должен использоваться один и тот же символ маркера.")
            else:
                if any(it.number_kind != base_number_kind for it in top_level_items):
                    issues.append("Нумерация на базовом уровне списка должна быть одного формата (цифры/буквы/римские).")

            # Межстрочный интервал
            ok_raw, ratio_adj = _line_spacing_check([it.line for it in fl.items])
            if ratio_adj is not None:
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                if not ok_raw:
                    issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio_adj:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # Аннотация
            page = pdf_document[fl.page_index0]
            try:
                if issues:
                    error_pages.add(fl.page_index0 + 1)
                    if annotate_pdf:
                        ann = page.add_text_annot(fitz.Point(fl.bbox.x0, fl.bbox.y0),
                            "Список: нарушения\n" + "\n".join(f"• {m}" for m in issues))
                        ann.set_info(title="Сервис нормоконтроля", content="\n".join(issues))
                        ann.update()
                else:
                    if annotate_pdf:
                        ann = page.add_text_annot(fitz.Point(fl.bbox.x0, fl.bbox.y0),
                            f"Список корректен ({len(fl.items)} п.)")
                        ann.set_info(title="Сервис нормоконтроля", content="Список корректен")
                        ann.update()
            except Exception as e:
                logger.warning("Не удалось проставить аннотацию на стр. %s: %s", fl.page_index0+1, e)

        # Список финалится теперь только при терминаторе/несовм. маркере на базовом уровне или в конце документа
        finalized_now: List[FoundList] = []

        # 1) pending + найденные на странице
        if pending and found_on_page:
            first = found_on_page[0]
            if _ends_with_terminator(pending):
                finalized_now.append(pending)
                stats["pending_finalized"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] pending:finalize (has terminator)")
                pending = None
            else:
                if _compatible_for_carry(pending, first):
                    for it in first.items:
                        it.page_index0 = pending.page_index0
                        # уровень относительно base_x0 pend
                        it.level = _indent_level(it.line.x0_text, getattr(pending, "_base_x0", it.line.x0_text))
                        pending.items.append(it)
                    _normalize_bbox(pending)
                    stats["pending_attached"] += 1
                    stats["carry_accepted"] += 1
                    if DEBUG_DIAGNOSTICS:
                        admin.append(f"[Dbg][page {page_num}] pending:attach {len(first.items)} items (carry-over)")
                    found_on_page = found_on_page[1:]
                else:
                    stats["carry_rejected"] += 1
                    finalized_now.append(pending)
                    stats["pending_finalized"] += 1
                    if DEBUG_DIAGNOSTICS:
                        admin.append(f"[Dbg][page {page_num}] pending:finalize (carry rejected)")
                    pending = None

        # 2) обработка оставшихся списков этой страницы
        for fl in found_on_page:
            if pending:
                # этот блок почти не должен выполняться (pending закрывается/присоединяется выше),
                # но на всякий случай объединим
                for it in fl.items:
                    it.page_index0 = pending.page_index0
                    it.level = _indent_level(it.line.x0_text, getattr(pending, "_base_x0", it.line.x0_text))
                    pending.items.append(it)
                _normalize_bbox(pending)
                stats["pending_attached"] += 1
                if _ends_with_terminator(pending):
                    finalized_now.append(pending); stats["pending_finalized"] += 1; pending = None
            else:
                # если нет терминатора — уходим в pending
                if _ends_with_terminator(fl):
                    finalized_now.append(fl)
                else:
                    pending = fl
                    stats["pending_started"] += 1
                    if DEBUG_DIAGNOSTICS:
                        admin.append(f"[Dbg][page {page_num}] pending:start (no terminator) items={len(fl.items)}")

        # 3) Финализируем те, что готовы
        for fl in finalized_now:
            try:
                _finalize_list(fl)
            except Exception as e:
                logger.exception("Ошибка финализации списка на стр. %s: %s", fl.page_index0+1, e)

        # На границе страницы больше НЕ финализируем по «разрыву секции»
        # (строгое правило: только терминатор/несовместимый базовый маркер)

    # Конец документа: если pending был — финализируем, если содержит >= 2 пунктов
    if pending and pending.items:
        stats["pending_finalized"] += 1
        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][doc-end] pending:finalize items={len(pending.items)}")
        try:
            _normalize_bbox(pending)
            if len(pending.items) >= 2:
                list_bboxes_by_page[pending.page_index0 + 1].append((pending.bbox.x0, pending.bbox.y0, pending.bbox.x1, pending.bbox.y1))
                n_lists += 1
        except Exception as e:
            logger.exception("Ошибка финализации pending в конце документа: %s", e)

    # сводка
    user_summary = ("⚠️Проверка списков: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
                    ) if error_pages else "✅Проверка списков"

    admin_header = f"[Lists] Найдено списков: {n_lists}\n[Dbg] Stats: {stats}"
    admin_details = admin_header + (("\n" + "\n".join(admin)) if admin else "")

    logger.info("Готово. Найдено списков: %s. Нарушения на страницах: %s",
                n_lists, ", ".join(map(str, sorted(error_pages))) if error_pages else "—")

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
    }
