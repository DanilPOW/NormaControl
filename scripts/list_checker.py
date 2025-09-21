# scripts/list_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict

# ===== DEBUG =====
DEBUG_LISTS = True        # включить подробные логи по спискам
DEBUG_PAGES = None        # например: {3} чтобы логировать только 3-ю страницу (1-based)
MAX_SPANS_TO_SHOW = 3     # сколько первых спанов показывать в логах строки
# =================

# --- Единицы и поля ---
MM_TO_PT = 2.834646
CM_TO_PT = 28.35

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

def mm_to_pt(mm: float) -> float: return mm * MM_TO_PT

# --- Параметры списков / допуски ---
INDENT_STEP_CM = 0.75
INDENT_STEP_PT = INDENT_STEP_CM * CM_TO_PT
INDENT_TOL_PT  = 4.0

# Красная строка (первая строка пункта)
FIRST_LINE_INDENT_PT = 1.25 * CM_TO_PT
FIRST_LINE_TOL_PT    = 3.0  # ≈1 мм

LINE_SPACING_TARGET = 1.50
LINE_SPACING_TOL    = 0.06
ALIGN_TOL_PT        = 4.0
ALIGN_FRACTION_OK   = 0.70

# «До/после = 0 pt» (эвристика)
MAX_GAP_BEFORE_AFTER_FACTOR = 1.2  # * fontsize

# --- Маркеры / регексы ---
EN_DASH = "–"  # короткое тире
RUS_LETTERS = "абвгдеёжзийклмнопрстуфхцчшщьыъэюя"
EXCLUDED = set("ёзйочьъы")
ALLOWED_LETTERS = tuple(ch for ch in RUS_LETTERS if ch not in EXCLUDED)
ALLOWED_STR = "".join(ALLOWED_LETTERS)
NBSP = "\u00A0"
SPACE_CLS = rf"[ \t{NBSP}]"

# Голова пункта: «– » | «1) » | «1. » | «а) » | «IV) »
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

# Буллеты (включая \uf0b7 — Word/Symbol)
BULLET_CHARS = "•·●∙◦▪▫■□◆\uf0b7"

# Векторные маркеры (маленькие квадраты/кружки)
MARKER_MAX_W_PT = mm_to_pt(8.0)
MARKER_MAX_H_PT = mm_to_pt(8.0)

# --- Структуры ---
@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]

@dataclass
class Item:
    page_index0: int
    line: Line
    level: int             # кратность 0.75 см (от левого поля)
    kind: str              # "bulleted" | "numbered"
    marker_text: str       # "–", "1)", "а)" или "[vector]"
    number_kind: str       # "digits" | "rusalpha" | "roman" | "" (bulleted)

@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect

# ---------- Утилиты ----------
def _median(vals: List[float]) -> float:
    if not vals: return 0.0
    s = sorted(vals); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2.0

def _y_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    return max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))

def _fmt_rect(r: fitz.Rect) -> str:
    return f"{r.x0:.2f},{r.y0:.2f}–{r.x1:.2f},{r.y1:.2f}"

def _span_head(line: "Line", k: int = MAX_SPANS_TO_SHOW) -> str:
    parts = []
    for i, sp in enumerate(line.spans[:k]):
        t = (sp.get("text") or "").replace("\n"," ")
        if len(t) > 16:
            t = t[:16] + "…"
        bb = sp.get("bbox", [0,0,0,0])
        parts.append(f"[{i}] «{t}» {sp.get('font','?')} {float(sp.get('size',0)):.2f}pt x0={bb[0]:.2f}")
    if len(line.spans) > k:
        parts.append(f"(+{len(line.spans)-k} spans)")
    return " | ".join(parts)

def _marker_kind_from_head(head_text: str) -> str:
    if not head_text:
        return ""
    s = head_text.lstrip()
    if s.startswith(EN_DASH):
        return "bulleted:dash"
    if s and s[0].isdigit():
        return "numbered:digits"
    if s and re.match(r"[IVXLC]", s[0]):
        return "numbered:roman"
    if s and s[0] in ALLOWED_LETTERS:
        return "numbered:rusalpha"
    return "unknown"

def _collect_text_lines(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0: continue
        for ln in b.get("lines", []):
            xs, ys, texts, spans, sizes, fonts = [], [], [], [], [], []
            for sp in ln.get("spans", []):
                x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                t = sp.get("text") or ""
                if t.strip():
                    xs += [x0,x1]; ys += [y0,y1]
                    sizes.append(float(sp.get("size",0))); fonts.append(sp.get("font",""))
                    spans.append(sp); texts.append(t)
            if not xs: continue
            text = "".join(texts).strip()
            if not text: continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            out.append(Line(text=text, bbox=rect, size=size, font=font, spans=spans))
    # стабилизируем сортировку по Y
    y_snap = mm_to_pt(0.3)
    out.sort(key=lambda L: (round(L.bbox.y0 / y_snap)*y_snap, L.bbox.x0))
    return out

def _collect_vector_markers(page: fitz.Page) -> List[fitz.Rect]:
    """Маленькие возможные маркеры из drawings."""
    markers: List[fitz.Rect] = []
    try:
        drawings = page.get_drawings(extended=True)
    except TypeError:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        rect = d.get("rect") or d.get("bbox")
        if not rect: continue
        r = fitz.Rect(*map(float, rect))
        if r.width <= MARKER_MAX_W_PT and r.height <= MARKER_MAX_H_PT:
            ar = r.width / max(1e-3, r.height)
            if 0.6 <= ar <= 1.6:
                markers.append(r)
    return markers

# «строка = один буллет» (в т.ч. \uf0b7)
_SIMPLE_BULLET_ONLY = re.compile(
    r"^\s*(?:[" + BULLET_CHARS + r"]|{}|-|—)\s*$".format(re.escape(EN_DASH))
)

def _merge_bullet_lines(lines: List[Line]) -> List[Line]:
    """
    Склейка пары:
      [строка с буллитом/тире] + [следующая строка с текстом]
    как один пункт. Поддерживает Word/Symbol (\uf0b7).
    """
    if not lines: return lines
    out: List[Line] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        t = cur.text.strip()
        if _SIMPLE_BULLET_ONLY.match(t) and (i + 1 < len(lines)):
            nxt = lines[i + 1]
            dy = nxt.bbox.y0 - cur.bbox.y1
            fs = _median([cur.size, nxt.size]) or 12.0
            # мягкая привязка по Y и X: текст начинается правее «примерно там же»
            if -2.0 <= dy <= 1.6 * fs and nxt.bbox.x0 >= cur.bbox.x0:
                merged_text = (t + " " + nxt.text.lstrip()).strip()
                mx0 = min(cur.bbox.x0, nxt.bbox.x0); my0 = min(cur.bbox.y0, nxt.bbox.y0)
                mx1 = max(cur.bbox.x1, nxt.bbox.x1); my1 = max(cur.bbox.y1, nxt.bbox.y1)
                out.append(Line(merged_text, fitz.Rect(mx0,my0,mx1,my1),
                                fs, nxt.font or cur.font, cur.spans + nxt.spans))
                i += 2
                continue
        out.append(cur)
        i += 1
    return out

def _text_start_x_after_marker(line: Line) -> Optional[float]:
    """
    Если первый спан — маркер ('1)', 'а)', '–'), возвращает X начала первого
    НЕ пробельного спана после маркера (учитывая, что пробел может быть отдельным спаном).
    Иначе None.
    """
    if not line.spans:
        return None
    head_txt = line.spans[0].get("text", "")
    if not RE_START_SIMPLE.match(head_txt):
        return None
    # идём по спанам после первого и ищем реальный текст
    for sp in line.spans[1:]:
        txt = sp.get("text", "")
        if txt and not txt.isspace():
            bb = sp.get("bbox", None)
            if bb:
                return float(bb[0])
    # если не нашли, вернём x1 первого спана как приближение
    bb0 = line.spans[0].get("bbox", None)
    if bb0:
        return float(bb0[2])
    return None

# ---------- Классификация ----------
def _classify_simple(line: Line, work_left: float, vector_markers: List[fitz.Rect]) -> Optional[Item]:
    """
    Определяет, является ли строка началом пункта, и какой это вид/уровень.
    Учитывает кейс «маркер отдельным спаном» (стр. 3) для корректного уровня/отступа.
    """
    t = line.text
    # Базовый x0: если маркер отдельным спаном — берём X реального текста после маркера
    x0 = _text_start_x_after_marker(line)
    if x0 is None:
        x0 = line.bbox.x0

    level = max(0, int(round((x0 - work_left) / INDENT_STEP_PT)))

    # 1) текстовый маркер/номер по префиксу всей строки
    m = RE_START_SIMPLE.match(t)
    if m:
        head = m.group(0).lstrip()
        if head.startswith(EN_DASH):
            return Item(-1, line, level, "bulleted", EN_DASH, "")
        elif head and head[0].isdigit():
            return Item(-1, line, level, "numbered", head.strip(), "digits")
        elif head and head[0] in ALLOWED_LETTERS:
            return Item(-1, line, level, "numbered", head.strip(), "rusalpha")
        elif head and re.match(r"[IVXLC]", head[0]):
            return Item(-1, line, level, "numbered", head.strip(), "roman")

    # 2) буллет-символ в начале строки (включая \uf0b7)
    if t and t[0] in BULLET_CHARS:
        return Item(-1, line, level, "bulleted", t[0], "")

    # 3) векторный маркер слева на той же строке
    best = None; best_dx = None
    for r in vector_markers:
        if _y_overlap(r, line.bbox) / max(1.0, min(r.height, line.bbox.height)) < 0.4:
            continue
        if r.x1 > (line.bbox.x0 + 0.6*INDENT_STEP_PT):
            continue
        if r.x1 <= line.bbox.x0:
            dx = line.bbox.x0 - r.x1
            if (best is None) or (dx < best_dx):
                best, best_dx = r, dx
    if best is not None:
        return Item(-1, line, level, "bulleted", "[vector]", "")

    return None

# ---------- Сборка многострочных пунктов ----------
def _gather_multiline_items(lines: List[Line], work_left: float, vector_markers: List[fitz.Rect]) -> List[Item]:
    items: List[Item] = []
    i = 0
    while i < len(lines):
        head_line = lines[i]
        head = _classify_simple(head_line, work_left, vector_markers)
        if not head:
            i += 1
            continue
        tails = []
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if _classify_simple(ln, work_left, vector_markers):
                break
            # продолжение: тот же «столбец» и разумный шаг по Y
            same_col = abs((_text_start_x_after_marker(ln) or ln.bbox.x0) - (_text_start_x_after_marker(head.line) or head.line.bbox.x0)) <= (INDENT_TOL_PT + 2.0)
            fs = head.line.size or 12.0
            step_ok = (ln.bbox.y0 - (tails[-1].bbox.y0 if tails else head.line.bbox.y0)) <= 2.2 * fs
            if same_col and step_ok:
                tails.append(ln)
                j += 1
            else:
                break
        if tails:
            # склейка текста и bbox
            full_text = " ".join([head.line.text] + [t.text.strip() for t in tails])
            hb = head.line.bbox
            tb = [t.bbox for t in tails]
            bbox = fitz.Rect(
                min([hb.x0] + [b.x0 for b in tb]),
                min([hb.y0] + [b.y0 for b in tb]),
                max([hb.x1] + [b.x1 for b in tb]),
                max([hb.y1] + [b.y1 for b in tb]),
            )
            head = Item(
                head.page_index0,
                Line(full_text, bbox, head.line.size, head.line.font, head.line.spans),
                head.level, head.kind, head.marker_text, head.number_kind
            )
            i = j
        else:
            i += 1
        items.append(head)
    return items

# ---------- Кластеризация в списки ----------
def _cluster_items(items: List[Item]) -> List[FoundList]:
    if not items: return []
    items = sorted(items, key=lambda it: (it.line.bbox.y0, it.line.bbox.x0))
    out: List[FoundList] = []
    cur: List[Item] = []

    def flush():
        if len(cur) >= 2:
            xs0 = [it.line.bbox.x0 for it in cur]
            ys0 = [it.line.bbox.y0 for it in cur]
            xs1 = [it.line.bbox.x1 for it in cur]
            ys1 = [it.line.bbox.y1 for it in cur]
            out.append(FoundList(cur[0].page_index0, cur.copy(),
                                 fitz.Rect(min(xs0),min(ys0),max(xs1),max(ys1))))
        cur.clear()

    MAX_DY_FACTOR = 2.0
    LEFT_TOL = INDENT_TOL_PT + 0.6 * INDENT_STEP_PT  # чуть мягче

    for it in items:
        if not cur:
            cur = [it]; continue
        prev = cur[-1]
        dy = it.line.bbox.y0 - prev.line.bbox.y0
        fs = _median([it.line.size, prev.line.size]) or 12.0
        step_ok = 0.1 <= dy <= (MAX_DY_FACTOR * fs)

        # согласованность левого края/уровня
        left_diff = abs((_text_start_x_after_marker(it.line) or it.line.bbox.x0) -
                        (_text_start_x_after_marker(prev.line) or prev.line.bbox.x0))
        level_change = abs(it.level - prev.level)
        same_kind = (it.kind == prev.kind) and (it.number_kind == prev.number_kind)
        left_ok = (left_diff <= LEFT_TOL) or (level_change in (0, 1))

        if step_ok and left_ok and same_kind:
            cur.append(it)
        else:
            flush()
            cur = [it]
    flush()
    return out

# ---------- Проверки форматирования блока ----------
def _detect_align_justify(lines: List[Line], work_right: float) -> bool:
    if len(lines) < 2: 
        return True
    x1s = [ln.bbox.x1 for ln in lines[:-1]]  # последнюю строку не учитываем
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
    r = _median(dys) / max(1.0, _median(hs))
    lo, hi = LINE_SPACING_TARGET - LINE_SPACING_TOL, LINE_SPACING_TARGET + LINE_SPACING_TOL
    return (lo-1e-3 <= r <= hi+1e-3), r

# ---------- Основная проверка ----------
def check_lists(
    pdf_document: fitz.Document,
    *,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,  # совместимость
    annotate_pdf: bool = True,
    start_page: int = 1,
) -> Dict[str, object]:
    """
    Детекция списков + проверки ГОСТ + аннотации.
    Возвращает: user_summary, admin_details, list_bboxes_by_page, items_diagnostics.
    """
    admin: List[str] = []
    list_bboxes_by_page: Dict[int, List[Tuple[float,float,float,float]]] = defaultdict(list)
    items_diag: List[Dict[str, object]] = []
    error_pages = set()
    n_lists = 0

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        rect = page.bound()
        work_left  = rect.x0 + LEFT_MARGIN_PT
        work_right = rect.x1 - RIGHT_MARGIN_PT

        # 1) строки (+ склейка «буллит-строка → текст-строка»)
        lines = _collect_text_lines(page)
        lines = _merge_bullet_lines(lines)

        if DEBUG_LISTS and (DEBUG_PAGES is None or page_num in DEBUG_PAGES):
            admin.append(f"[Dbg][p{page_num}] Lines after collect+merge: {len(lines)}")
            for idx, ln in enumerate(lines):
                head_match = RE_START_SIMPLE.match(ln.text)
                head = head_match.group(0) if head_match else ""
                x0_text = _text_start_x_after_marker(ln) or ln.bbox.x0
                admin.append(
                    "  [L{idx}] y0={y:.2f} x0_line={x0:.2f} x0_text={x1:.2f}  head=«{h}» ({hk})  text=«{t}»".format(
                        idx=idx, y=ln.bbox.y0, x0=ln.bbox.x0, x1=x0_text,
                        h=head.strip(), hk=_marker_kind_from_head(head or ""),
                        t=(ln.text[:80] + ("…" if len(ln.text) > 80 else ""))
                    )
                )
                admin.append("        spans: " + _span_head(ln))

        # 2) векторные маркеры
        vector_markers = _collect_vector_markers(page)
        if DEBUG_LISTS and (DEBUG_PAGES is None or page_num in DEBUG_PAGES):
            if vector_markers:
                admin.append(f"[Dbg][p{page_num}] Vector markers: {len(vector_markers)}")
                for i, r in enumerate(vector_markers[:30]):
                    admin.append(f"    [vm#{i}] rect={_fmt_rect(r)} wh=({r.width:.2f}×{r.height:.2f})")
            else:
                admin.append(f"[Dbg][p{page_num}] Vector markers: 0")

        # 3) кандидаты-пункты с учётом многострочности
        candidates: List[Item] = _gather_multiline_items(lines, work_left, vector_markers)
        for it in candidates:
            it.page_index0 = pidx

        if DEBUG_LISTS and (DEBUG_PAGES is None or page_num in DEBUG_PAGES):
            admin.append(f"[Dbg][p{page_num}] Items after classify+multiline: {len(candidates)}")
            for it in candidates:
                admin.append(
                    "    [it] y0={y:.2f} level={lvl} kind={k}/{nk} marker={m}  text=«{t}»".format(
                        y=it.line.bbox.y0, lvl=it.level, k=it.kind, nk=it.number_kind,
                        m=it.marker_text, t=(RE_START_SIMPLE.sub('', it.line.text).strip()[:80])
                    )
                )
            # Предтрассировка кластеризации (по парам)
            admin.append(f"[Dbg][p{page_num}] Clustering pre-check over {len(candidates)} items:")
            for a, b in zip(candidates, candidates[1:]):
                dy = b.line.bbox.y0 - a.line.bbox.y0
                fs = _median([a.line.size, b.line.size]) or 12.0
                step_ok = 0.1 <= dy <= (2.0 * fs)
                left_a = (_text_start_x_after_marker(a.line) or a.line.bbox.x0)
                left_b = (_text_start_x_after_marker(b.line) or b.line.bbox.x0)
                left_diff = abs(left_b - left_a)
                level_change = abs(b.level - a.level)
                same_kind = (a.kind == b.kind) and (a.number_kind == b.number_kind)
                left_ok = (left_diff <= (INDENT_TOL_PT + 0.6*INDENT_STEP_PT)) or (level_change in (0,1))
                admin.append(
                    "    pair y={ya:.2f}->{yb:.2f}  dy={dy:.2f} step_ok={so} | x0={xa:.2f}->{xb:.2f} diff={ld:.2f} left_ok={lo} | kind={ka}/{kb} same={sk}".format(
                        ya=a.line.bbox.y0, yb=b.line.bbox.y0, dy=dy, so=bool(step_ok),
                        xa=left_a, xb=left_b, ld=left_diff, lo=bool(left_ok),
                        ka=f'{a.kind}:{a.number_kind}', kb=f'{b.kind}:{b.number_kind}', sk=bool(same_kind)
                    )
                )

        # 4) группировка подряд в списки
        found = _cluster_items(candidates)

        # 5) проверки и аннотации
        for fl in found:
            n_lists += 1
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []
            top_kind = None
            top_number_kind = None

            for it in fl.items:
                if it.level == 0 and top_kind is None:
                    top_kind, top_number_kind = it.kind, it.number_kind

                # ВЕРХНИЙ УРОВЕНЬ: маркеры только «–» (или вектор в особых случаях)
                if it.level == 0:
                    if it.kind == "bulleted" and it.marker_text not in (EN_DASH, "[vector]"):
                        issues.append("Ур.1: маркированный список должен использовать только «–».")
                    if it.kind == "numbered" and it.number_kind not in ("digits","rusalpha","roman"):
                        issues.append("Ур.1: нумерованный список должен быть вида «1)», «а)» или «I)».")
                else:
                    # Уровни >1: вид/тип нумерации должен отличаться от уровня 1
                    if top_kind and it.kind == top_kind:
                        issues.append(f"Ур.{it.level+1}: вид маркера/номера должен отличаться от уровня 1.")
                    if top_number_kind and it.number_kind and it.number_kind == top_number_kind:
                        issues.append(f"Ур.{it.level+1}: тип нумерации должен отличаться от уровня 1.")

                # Один пробел после маркера
                s = it.line.text
                if it.kind == "bulleted" and it.marker_text == EN_DASH:
                    if not RE_ONLY_ONE_SPACE_AFTER_DASH.match(s):
                        issues.append("После «–» должен быть ровно один пробел.")
                if it.kind == "numbered":
                    if not RE_ONLY_ONE_SPACE_AFTER.match(s):
                        issues.append("После номера должен быть ровно один пробел.")

                # Строчная буква после маркера
                tail = RE_START_SIMPLE.sub("", s).lstrip()
                if tail[:1].isalpha() and tail[:1].isupper():
                    issues.append("Пункт списка должен начинаться со строчной буквы.")

                # Красная строка 1.25 см (берём X начала текста после маркера)
                text_x0 = _text_start_x_after_marker(it.line) or it.line.bbox.x0
                fl_indent = text_x0 - work_left
                if abs(fl_indent - FIRST_LINE_INDENT_PT) > FIRST_LINE_TOL_PT:
                    issues.append("Каждый пункт списка должен иметь абзацный отступ 1.25 см.")

            # Перед списком — двоеточие и нет пустой строки
            if fl.items:
                head = fl.items[0].line
                head_y0 = head.bbox.y0
                prev = max((ln for ln in lines if ln.bbox.y1 <= head_y0),
                           key=lambda L: L.bbox.y1, default=None)
                if prev:
                    fs = max(10.0, head.size or 12.0)
                    gap = head_y0 - prev.bbox.y1
                    if gap > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                        issues.append("Перед списком не должно быть пустой строки (интервал до = 0 pt).")
                    if not prev.text.rstrip().endswith(":"):
                        issues.append("Перед списком должно быть предложение, оканчивающееся двоеточием.")
                else:
                    issues.append("Не найдено предложение с двоеточием непосредственно перед списком.")

                # После списка — нет пустой строки
                tail_y1 = fl.items[-1].line.bbox.y1
                nxt = min((ln for ln in lines if ln.bbox.y0 >= tail_y1),
                          key=lambda L: L.bbox.y0, default=None)
                if nxt:
                    fs2 = max(10.0, fl.items[-1].line.size or 12.0)
                    gap2 = nxt.bbox.y0 - tail_y1
                    if gap2 > MAX_GAP_BEFORE_AFTER_FACTOR * fs2:
                        issues.append("После списка не должно быть пустой строки (интервал после = 0 pt).")

            # Выравнивание и межстрочник
            block_lines = [it.line for it in fl.items]
            if not _detect_align_justify(block_lines, work_right):
                issues.append("Список должен быть выровнен по ширине.")
            ok_ls, ratio = _line_spacing_check(block_lines)
            if not ok_ls and ratio is not None:
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # Кратность 0.75 см по уровню
            for it in fl.items:
                dx = (_text_start_x_after_marker(it.line) or it.line.bbox.x0) - work_left
                want = it.level * INDENT_STEP_PT
                if abs(dx - want) > (INDENT_TOL_PT + 6.0):
                    issues.append(f"Отступ слева у пункта ур.{it.level+1} должен быть кратен 0.75 см.")

            # Пунктуация
            pure_texts = [RE_START_SIMPLE.sub("", it.line.text).strip() for it in fl.items]
            for i, txt in enumerate(pure_texts):
                last = (i == len(pure_texts)-1)
                words = [w for w in re.sub(r"[^\w\sА-Яа-яЁё-]", "", txt, flags=re.UNICODE).split()
                         if re.search(r"[A-Za-zА-Яа-яЁё]", w)]
                is_short = 1 <= len(words) <= 2
                if not last:
                    if is_short:
                        if not txt.endswith(","):
                            issues.append("Короткий пункт (1–2 слова) должен оканчиваться запятой, кроме последнего (точка).")
                    else:
                        if not txt.endswith(";"):
                            issues.append("Пункт должен оканчиваться точкой с запятой, кроме последнего (точка).")
                else:
                    if not txt.endswith("."):
                        issues.append("Последний пункт списка должен оканчиваться точкой.")
                # внутри пункта не допускаются новые предложения
                if not last and re.search(r"\.\s+[А-ЯЁA-Z]", txt):
                    issues.append("Внутри пункта не допускаются новые предложения.")

            # Диагностика по пунктам (программно-парсибельная)
            for it in fl.items:
                items_diag.append({
                    "page": page_num,
                    "y0": float(it.line.bbox.y0),
                    "x0_line": float(it.line.bbox.x0),
                    "x0_text": float(_text_start_x_after_marker(it.line) or it.line.bbox.x0),
                    "level": int(it.level),
                    "kind": it.kind,
                    "number_kind": it.number_kind,
                    "marker_text": it.marker_text,
                    "head": (RE_START_SIMPLE.match(it.line.text).group(0) if RE_START_SIMPLE.match(it.line.text) else ""),
                    "text_head": RE_START_SIMPLE.sub("", it.line.text).strip()[:200],
                })

            # Лог и аннотации
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

    # --- Пользовательская сводка ---
    user_summary = ("⚠️Проверка списков: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
                    ) if error_pages else "✅Проверка списков"
    admin_details = (f"[Lists] Найдено списков: {n_lists}"
                     + ("\n" + "\n".join(admin) if admin else ""))

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
        "items_diagnostics": items_diag,
    }
