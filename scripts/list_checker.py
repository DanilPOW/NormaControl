# scripts/list_checker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict
from const import *  # см. список ожидаемых констант ниже

# Ожидается, что в const.py определено:
# EN_DASH, NBSP,
# LEFT_MARGIN_PT, RIGHT_MARGIN_PT, PARAGRAPH_INDENT_PT,
# INDENT_STEP_PT, INDENT_TOL_PT,
# LINE_SPACING_TARGET, LINE_SPACING_TOL, MAX_GAP_BEFORE_AFTER_FACTOR,
# RE_START_SIMPLE, RE_START_TIGHT, DEBUG_DIAGNOSTICS, CM_TO_PT,
# MARKER_MAX_W_PT, MARKER_MAX_H_PT, ALLOWED_LETTERS

def mm_to_pt(mm: float) -> float: return mm * 2.8346456693
def pt_to_mm(pt: float) -> float: return pt / 2.8346456693

# --- Набор буллитов и тире (расширенный) ---
MINUS = "\u2212"  # − (U+2212)
BULLET_CHARS = "•·●▪■"
DASH_CHARS = EN_DASH + "—-" + MINUS  # короткое тире + длинное тире + дефис + минус

# --- Заголовок раздела источников (срез документа) ---
RE_REFS_HEAD = re.compile(
    r"^\s*(?:СПИСОК\s+ИСПОЛЬЗОВАННЫХ\s+ИСТОЧНИКОВ|СПИСОК\s+ИСТОЧНИКОВ|ЛИТЕРАТУРА|ИСТОЧНИКИ|БИБЛИОГРАФИЧЕСКИЙ\s+СПИСОК)\s*\.?\s*$",
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


@dataclass
class Item:
    page_index0: int
    line: Line
    level: int              # 0 = верхний уровень (требуется EN_DASH), 1+ = остальные
    kind: str               # "bulleted" | "numbered"
    marker_text: str        # исходный маркер (символ/токен)
    number_kind: str        # "digits" | "rusalpha" | "roman" | ""


@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect


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


# --- Сбор видимых строк + жирность ---
def _collect_text_lines_with_raw(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
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
                x0_text=rect.x0, x0_text_src="line_bbox", is_bold=_line_all_bold(spans)
            ))
    snap = mm_to_pt(0.3)
    out.sort(key=lambda L: (round(L.bbox.y0/snap)*snap, L.bbox.x0))
    return out


# --- Регэкспы нумерации ---
RE_NUM_DIGITS  = re.compile(r"^\s*(\d+)(?:[.)])\s+")
RE_NUM_ALPHA   = re.compile(r"^\s*([А-Яа-яЁёA-Za-z])(?:[.)])\s+")
RE_NUM_ROMAN   = re.compile(r"^\s*([IVXLC]+)(?:[.)])\s+")

def _classify_marker(line: Line, admin: Optional[List[str]] = None) -> Optional[Tuple[str,str,str]]:
    """
    Возвращает (kind, number_kind, marker_text) либо None.
    kind: "bulleted"|"numbered"
    number_kind: "digits"|"rusalpha"|"roman"|""
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

    # 2) Нумерация
    m = RE_NUM_DIGITS.match(t)
    if m:
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] digits '{t[:10]}' -> '{m.group(1)})'")
        return ("numbered", "digits", m.group(1)+")")

    m = RE_NUM_ALPHA.match(t)
    if m:
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] alpha '{t[:10]}' -> '{m.group(1)})'")
        return ("numbered", "rusalpha", m.group(1)+")")

    m = RE_NUM_ROMAN.match(t)
    if m:
        if DEBUG_DIAGNOSTICS and admin is not None:
            admin.append(f"[Dbg][classify] roman '{t[:10]}' -> '{m.group(1)})'")
        return ("numbered", "roman", m.group(1)+")")

    m2 = RE_START_SIMPLE.match(t) or RE_START_TIGHT.match(t)
    if m2:
        head = m2.group(0).lstrip()
        if head and (head[0] in BULLET_CHARS or head[0] in DASH_CHARS):
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] bulleted-tight '{head[:10]}' -> '{head[0]}'")
            return ("bulleted", "", head[0])
        if head[:1].isdigit():
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] digits-tight '{head[:10]}'")
            return ("numbered", "digits", head.strip())
        if head[:1] in ALLOWED_LETTERS:
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] alpha-tight '{head[:10]}'")
            return ("numbered", "rusalpha", head.strip())
        if re.match(r"[IVXLC]", head[:1] or ""):
            if DEBUG_DIAGNOSTICS and admin is not None:
                admin.append(f"[Dbg][classify] roman-tight '{head[:10]}'")
            return ("numbered", "roman", head.strip())

    if DEBUG_DIAGNOSTICS and admin is not None:
        admin.append(f"[Dbg][classify] no-marker '{t[:40]}'")
    return None


def _strip_marker_text(kind: str, marker_text: str, txt: str) -> str:
    """
    Удаляет маркер из начала строки и возвращает «чистый» текст пункта.
    """
    s = txt.lstrip()
    if kind == "bulleted":
        return s[1:].lstrip() if s else ""
    m = re.match(r"^\s*\S+\s+(.*)$", s)  # numbered
    if m:
        return m.group(1).strip()
    return s


# --- Сбор пунктов с многострочкой и логами ---
def _gather_items(lines: List[Line], admin: List[str], stats: Dict[str, int]) -> List[Item]:
    items: List[Item] = []
    cur: Optional[Item] = None
    last_y1: Optional[float] = None

    for ln in lines:
        # краткое описание строки для логов
        if DEBUG_DIAGNOSTICS:
            msg = f"[Dbg][gather] y0={ln.bbox.y0:.2f} '{ln.text[:40].replace(NBSP,' ')}'"

        # жирные — пропускаем полностью
        if ln.is_bold:
            stats["bold_skipped"] += 1
            if DEBUG_DIAGNOSTICS:
                admin.append(msg + " -> skip: bold line")
            cur = None
            last_y1 = None
            continue

        mark = _classify_marker(ln, admin if DEBUG_DIAGNOSTICS else None)
        if mark:
            stats["marker_hits"] += 1
            kind, number_kind, marker_text = mark

            # уровень 0 только если маркер — короткое тире EN_DASH
            level = 0 if (kind == "bulleted" and marker_text == EN_DASH) else 1
            clean = _strip_marker_text(kind, marker_text, ln.text)

            if DEBUG_DIAGNOSTICS:
                admin.append(msg + f" -> marker: kind={kind} num={number_kind} mark='{marker_text}' level={level}")

            it = Item(
                page_index0=-1,
                line=Line(
                    text=clean, bbox=ln.bbox, size=ln.size, font=ln.font, spans=ln.spans,
                    x0_text=ln.x0_text, x0_text_src=ln.x0_text_src, is_bold=ln.is_bold
                ),
                level=level, kind=kind, marker_text=marker_text, number_kind=number_kind
            )
            items.append(it)
            cur = it
            last_y1 = ln.bbox.y1
            continue

        stats["marker_none"] += 1
        if DEBUG_DIAGNOSTICS:
            admin.append(msg + " -> no marker")

        # многострочный хвост?
        if cur is not None:
            fs = _first_text_span_size(cur.line)
            dy = _y_dist(cur.line.bbox, ln.bbox)
            if dy <= 2.5 * fs:
                stats["multiline_attached"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][gather] multiline attach: dy={dy:.2f} fs={fs:.2f} -> '{ln.text[:40]}'")
                # склеиваем текст и bbox
                cur.line.text = (cur.line.text.rstrip() + " " + ln.text.lstrip()).strip()
                cb = cur.line.bbox
                cur.line.bbox = fitz.Rect(
                    min(cb.x0, ln.bbox.x0), min(cb.y0, ln.bbox.y0),
                    max(cb.x1, ln.bbox.x1), max(cb.y1, ln.bbox.y1)
                )
                last_y1 = cur.line.bbox.y1
                continue
            else:
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][gather] multiline FAIL: dy={dy:.2f} fs={fs:.2f} (too far)")

        # сброс текущего пункта
        cur = None
        last_y1 = None

    return items


def _normalize_bbox(fl: FoundList) -> None:
    xs0 = [it.line.bbox.x0 for it in fl.items]
    ys0 = [it.line.bbox.y0 for it in fl.items]
    xs1 = [it.line.bbox.x1 for it in fl.items]
    ys1 = [it.line.bbox.y1 for it in fl.items]
    fl.bbox = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1))


def _find_refs_cutoff(pdf_document: fitz.Document, start_page: int = 1) -> Optional[Tuple[int, float]]:
    """
    Возвращает (page_index0, y0) первой строки заголовка «Список источников».
    Всё ниже этой строки (на странице заголовка) и все последующие страницы — не проверяются.
    """
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

    # сводная статистика
    stats = dict(
        bold_skipped=0,
        marker_hits=0,
        marker_none=0,
        multiline_attached=0,
        list_breaks=0,
        pending_started=0,
        pending_attached=0,
        pending_finalized=0,
    )

    cutoff = _find_refs_cutoff(pdf_document, start_page=start_page)
    if DEBUG_DIAGNOSTICS:
        admin.append(f"[Dbg] Refs cutoff: {cutoff!r}")

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

        lines = _collect_text_lines_with_raw(page)

        # на странице с заголовком источников — режем низ
        if cutoff and pidx == cutoff[0]:
            cut_y0 = cutoff[1]
            lines = [ln for ln in lines if ln.bbox.y0 < cut_y0]

        # исключаем заданные области
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

        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Lines after collect+merge: {len(lines)}")

        # кандидаты пунктов
        candidates = _gather_items(lines, admin, stats)
        for it in candidates:
            it.page_index0 = pidx

        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Items after classify+multiline: {len(candidates)}")

        # --- Простая линейная группировка на странице ---
        found_on_page: List[FoundList] = []
        cur_list: Optional[FoundList] = None
        prev_item: Optional[Item] = None

        for it in candidates:
            if cur_list is None:
                cur_list = FoundList(page_index0=pidx, items=[it], bbox=it.line.bbox)
                prev_item = it
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] list:start @y={it.line.bbox.y0:.2f} '{it.line.text[:40]}'")
                continue

            dy = _y_dist(prev_item.line.bbox, it.line.bbox)
            fs = _median([_first_text_span_size(prev_item.line), _first_text_span_size(it.line)])

            if dy <= 2.0 * fs:
                cur_list.items.append(it)
                _normalize_bbox(cur_list)
                prev_item = it
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] list:append dy={dy:.2f} fs={fs:.2f} '{it.line.text[:40]}'")
            else:
                stats["list_breaks"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] list:break dy={dy:.2f} fs={fs:.2f} -> NEW '{it.line.text[:40]}'")
                found_on_page.append(cur_list)
                cur_list = FoundList(page_index0=pidx, items=[it], bbox=it.line.bbox)
                prev_item = it
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] list:start @y={it.line.bbox.y0:.2f} '{it.line.text[:40]}'")

        if cur_list and cur_list.items:
            found_on_page.append(cur_list)

        # --- Склейка с pending (перенос между страницами) ---
        def _ends_with_dot(fl: FoundList) -> bool:
            if not fl.items:
                return True
            last_txt = (fl.items[-1].line.text or "").rstrip()
            return last_txt.endswith(".")

        if pending:
            # приклеиваем «первый» список текущей страницы, если он есть
            if found_on_page:
                first = found_on_page.pop(0)
                for it in first.items:
                    pending.items.append(it)
                _normalize_bbox(pending)
                stats["pending_attached"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] pending:attach {len(first.items)} items (carry-over)")

        finalized: List[FoundList] = []

        for i, fl in enumerate(found_on_page):
            if pending:
                # продолжаем перенос — всё, что осталось на странице, прилепляем к pending
                for it in fl.items:
                    pending.items.append(it)
                _normalize_bbox(pending)
                stats["pending_attached"] += 1
                if DEBUG_DIAGNOSTICS:
                    admin.append(f"[Dbg][page {page_num}] pending:attach {len(fl.items)} items (cont)")
            else:
                # новый самостоятельный список: если оканчивается на точку — сразу финализируем
                if _ends_with_dot(fl):
                    finalized.append(fl)
                else:
                    pending = fl
                    stats["pending_started"] += 1
                    if DEBUG_DIAGNOSTICS:
                        admin.append(f"[Dbg][page {page_num}] pending:start (ends-without-dot) items={len(fl.items)}")

        # граница страницы: по текущей упрощённой логике
        # «берём последний маркер как последний пункт» → закрываем pending на границе страницы
        if pending:
            finalized.append(pending)
            stats["pending_finalized"] += 1
            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][page {page_num}] pending:finalize-at-pagebreak items={len(pending.items)}")
            pending = None

        # считаем списком только цепочки с >=2 пунктами
        finalized = [fl for fl in finalized if len(fl.items) >= 2]

        # --- Валидации и подсчёт ---
        for fl in finalized:
            n_lists += 1
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []

            # Маркер верхнего уровня: только EN_DASH
            for it in fl.items:
                if it.level == 0 and not (it.kind == "bulleted" and it.marker_text == EN_DASH):
                    issues.append(f"Ур.1: маркированный список должен использовать только «{EN_DASH}» (найдено «{it.marker_text}»).")

            # Межстрочный интервал
            ok_raw, ratio_raw = _line_spacing_check([it.line for it in fl.items])
            if ratio_raw is not None:
                ratio_adj = max(0.0, ratio_raw - 0.25)
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                if not (lo - 1e-3 <= ratio_adj <= hi + 1e-3):
                    issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio_adj:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # Контекст до/после (минимум)
            head = fl.items[0].line
            prev = max((ln for ln in lines if ln.bbox.y1 <= head.bbox.y0), key=lambda L: L.bbox.y1, default=None)
            if prev:
                fs = max(10.0, head.size or 12.0)
                gap = head.bbox.y0 - prev.bbox.y1
                if gap > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                    issues.append("Перед списком не должно быть пустой строки (интервал до = 0 pt).")
                if not prev.text.rstrip().endswith(":"):
                    issues.append("Перед списком должно быть предложение, оканчивающееся двоеточием.")
            else:
                issues.append("Не найдено предложение с двоеточием непосредственно перед списком.")

            tail = fl.items[-1].line
            nxt = min((ln for ln in lines if ln.bbox.y0 >= tail.bbox.y1), key=lambda L: L.bbox.y0, default=None)
            if nxt:
                fs2 = max(10.0, tail.size or 12.0)
                gap2 = nxt.bbox.y0 - tail.bbox.y1
                if gap2 > MAX_GAP_BEFORE_AFTER_FACTOR * fs2:
                    issues.append("После списка не должно быть пустой строки (интервал после = 0 pt).")

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

    # если документ закончился, а pending висел — закрываем (без потери)
    if pending and pending.items:
        page_num = pending.page_index0 + 1
        if page_num >= start_page:
            n_lists += 1
            list_bboxes_by_page[page_num].append((pending.bbox.x0, pending.bbox.y0, pending.bbox.x1, pending.bbox.y1))
            error_pages.add(page_num)

    # сводка
    user_summary = ("⚠️Проверка списков: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
                    ) if error_pages else "✅Проверка списков"

    admin_header = f"[Lists] Найдено списков: {n_lists}\n[Dbg] Stats: {stats}"
    admin_details = admin_header + (("\n" + "\n".join(admin)) if admin else "")

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
    }
