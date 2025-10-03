# scripts/list_checker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict
from const import *  # Ожидаемые константы см. ниже

# Ожидается, что в const.py определено:
# EN_DASH, NBSP,
# LEFT_MARGIN_PT, RIGHT_MARGIN_PT, PARAGRAPH_INDENT_PT,
# INDENT_STEP_PT, INDENT_TOL_PT,
# LINE_SPACING_TARGET, LINE_SPACING_TOL, MAX_GAP_BEFORE_AFTER_FACTOR,
# RE_START_SIMPLE, RE_START_TIGHT, DEBUG_DIAGNOSTICS, CM_TO_PT,
# MARKER_MAX_W_PT, MARKER_MAX_H_PT, ALLOWED_LETTERS

def mm_to_pt(mm: float) -> float: return mm * 2.8346456693
def pt_to_mm(pt: float) -> float: return pt / 2.8346456693

# --- Расширенный набор простых буллитов и тире ---
MINUS = "\u2212"  # − (U+2212)
BULLET_CHARS = "•·●▪■"
DASH_CHARS = EN_DASH + "—-" + MINUS  # короткое тире + длинное тире + дефис + минус

# --- Заголовок раздела источников («срез» документа) ---
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
    # служебное:
    is_bold: bool = False


@dataclass
class Item:
    page_index0: int
    line: Line
    level: int              # 0 = верхний уровень (ожидаем EN_DASH), 1+ = вложенные
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


# --- Сбор видимых строк + маркировка жирности ---
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
    # стабилизируем порядок
    snap = mm_to_pt(0.3)
    out.sort(key=lambda L: (round(L.bbox.y0/snap)*snap, L.bbox.x0))
    return out


# --- Регэкспы для нумерации ---
RE_NUM_DIGITS  = re.compile(r"^\s*(\d+)(?:[.)])\s+")
RE_NUM_ALPHA   = re.compile(r"^\s*([А-Яа-яЁёA-Za-z])(?:[.)])\s+")
RE_NUM_ROMAN   = re.compile(r"^\s*([IVXLC]+)(?:[.)])\s+")

def _classify_marker(line: Line) -> Optional[Tuple[str,str,str]]:
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
        # берём ровно первый символ как маркер
        return ("bulleted", "", t[0])

    # 2) Нумерация
    m = RE_NUM_DIGITS.match(t)
    if m:
        return ("numbered", "digits", m.group(1)+")")

    m = RE_NUM_ALPHA.match(t)
    if m:
        return ("numbered", "rusalpha", m.group(1)+")")

    m = RE_NUM_ROMAN.match(t)
    if m:
        return ("numbered", "roman", m.group(1)+")")

    # 3) Попытка «плотной» записи маркера через RE_START_*
    m2 = RE_START_SIMPLE.match(t) or RE_START_TIGHT.match(t)
    if m2:
        head = m2.group(0).lstrip()
        if head and (head[0] in BULLET_CHARS or head[0] in DASH_CHARS):
            return ("bulleted", "", head[0])
        if head[:1].isdigit():
            return ("numbered", "digits", head.strip())
        if head[:1] in ALLOWED_LETTERS:
            return ("numbered", "rusalpha", head.strip())
        if re.match(r"[IVXLC]", head[:1] or ""):
            return ("numbered", "roman", head.strip())

    return None


def _strip_marker_text(kind: str, marker_text: str, txt: str) -> str:
    """
    Удаляет маркер из начала строки и возвращает «чистый» текст пункта.
    """
    s = txt.lstrip()
    if kind == "bulleted":
        # убираем первый символ (буллит/тире)
        return s[1:].lstrip() if s else ""
    # numbered: отрежем ведущий токен до пробела
    m = re.match(r"^\s*\S+\s+(.*)$", s)
    if m:
        return m.group(1).strip()
    return s


# --- Сбор пунктов (с многострочкой) слева-направо, сверху-вниз ---
def _gather_items(lines: List[Line]) -> List[Item]:
    items: List[Item] = []
    cur: Optional[Item] = None
    last_y1: Optional[float] = None

    for ln in lines:
        # жирные — пропускаем
        if ln.is_bold:
            cur = None
            last_y1 = None
            continue

        mark = _classify_marker(ln)
        if mark:
            kind, number_kind, marker_text = mark

            # Уровень определяем максимально просто:
            #  - если маркер — КОРОТКОЕ тире EN_DASH → уровень 0 (верхний)
            #  - иначе уровень 1 (или глубже; нам не критично точное число)
            level = 0 if (kind == "bulleted" and marker_text == EN_DASH) else 1

            clean = _strip_marker_text(kind, marker_text, ln.text)

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

        # многострочка: если нет маркера, но есть «текущий» пункт — аккуратно присоединяем
        if cur is not None:
            fs = _first_text_span_size(cur.line)
            dy = _y_dist(cur.line.bbox, ln.bbox)
            if dy <= 2.5 * fs:  # простой разумный порог
                cur.line.text = (cur.line.text.rstrip() + " " + ln.text.lstrip()).strip()
                # расширяем bbox
                cb = cur.line.bbox
                cur.line.bbox = fitz.Rect(
                    min(cb.x0, ln.bbox.x0), min(cb.y0, ln.bbox.y0),
                    max(cb.x1, ln.bbox.x1), max(cb.y1, ln.bbox.y1)
                )
                last_y1 = cur.line.bbox.y1
                continue

        # иначе — сбрасываем «текущий» контекст
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
    Всё ниже этой строки (на странице заголовка) и все дальнейшие страницы — не проверяются.
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

    cutoff = _find_refs_cutoff(pdf_document, start_page=start_page)
    if DEBUG_DIAGNOSTICS:
        admin.append(f"[Dbg] Refs cutoff: {cutoff!r}")

    pending: Optional[FoundList] = None  # незакрытый (переносящийся) список

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue

        # после «Списка источников» — ничего не проверяем
        if cutoff and pidx > cutoff[0]:
            if DEBUG_DIAGNOSTICS:
                admin.append(f"[Dbg][p{page_num}] skipped (after refs cutoff)")
            continue

        lines = _collect_text_lines_with_raw(page)

        # отрезаем низ страницы с заголовком «Список источников»
        if cutoff and pidx == cutoff[0]:
            cut_y0 = cutoff[1]
            lines = [ln for ln in lines if ln.bbox.y0 < cut_y0]

        # применим исключения, если есть
        excludes = []
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

        # --- Кандидаты пунктов (с многострочкой) ---
        candidates = _gather_items(lines)
        for it in candidates:
            it.page_index0 = pidx

        if DEBUG_DIAGNOSTICS:
            admin.append(f"[Dbg][p{page_num}] Items after classify+multiline: {len(candidates)}")

        # --- Грубая линейная группировка: подряд идущие пункты формируют список ---
        # Никаких сложных «совместимостей» — просто последовательность item'ов без больших дыр.
        found_on_page: List[FoundList] = []
        cur_list: Optional[FoundList] = None
        prev_item: Optional[Item] = None

        for it in candidates:
            if cur_list is None:
                cur_list = FoundList(page_index0=pidx, items=[it], bbox=it.line.bbox)
                prev_item = it
                continue

            # если пункт идёт «рядом» — добавим
            dy = _y_dist(prev_item.line.bbox, it.line.bbox)
            fs = _median([_first_text_span_size(prev_item.line), _first_text_span_size(it.line)])
            # Разумный порог «подряд»: до двух межстрочных интервалов
            if dy <= 2.0 * fs:
                cur_list.items.append(it)
                _normalize_bbox(cur_list)
                prev_item = it
            else:
                # закрываем текущий список
                found_on_page.append(cur_list)
                cur_list = FoundList(page_index0=pidx, items=[it], bbox=it.line.bbox)
                prev_item = it

        if cur_list and cur_list.items:
            found_on_page.append(cur_list)

        # --- Склейка с pending: если предыдущая страница завершилась без точки ---
        if pending:
            if found_on_page:
                # «приклеиваем» первый список страницы к хвосту
                first = found_on_page.pop(0)
                for it in first.items:
                    pending.items.append(it)
                _normalize_bbox(pending)
                # Не закрываем прямо сейчас: логика «если маркеры продолжают появляться — берём последний маркер как последний пункт»
                # То есть pending живёт, пока внизу страницы был пункт без точки.
            # Если на странице нет пунктов — просто пойдём дальше с pending

        # --- Перенос по правилу точки: если список оканчивается строкой БЕЗ точки — он переносится ---
        finalized: List[FoundList] = []

        def _ends_with_dot(fl: FoundList) -> bool:
            if not fl.items:
                return True
            last_txt = (fl.items[-1].line.text or "").rstrip()
            return last_txt.endswith(".")

        for i, fl in enumerate(found_on_page):
            if pending:
                # продолжаем перенос
                for it in fl.items:
                    pending.items.append(it)
                _normalize_bbox(pending)
                # Если следующий блок существует — перенос продолжается до конца страницы.
                # Закрывать будем после прохода всех блоков.
            else:
                # новый самостоятельный список
                pending = fl if not _ends_with_dot(fl) else None
                if pending is None:
                    finalized.append(fl)

        # По окончании страницы: если pending есть —
        # «если маркеры продолжают появляться — берём последний маркер как последний пункт списка»
        # Интерпретируем так: если на этой странице были пункты,
        # то pending закрываем последним увиденным пунктом (то есть — закрываем всегда на границе страницы).
        if pending:
            # Закрываем переносимую цепочку на границе страницы
            finalized.append(pending)
            pending = None

        # --- Фильтр мусора: списком считаем только те цепочки, где >=2 пунктов ---
        finalized = [fl for fl in finalized if len(fl.items) >= 2]

        # --- Валидации и подсчёт ---
        for fl in finalized:
            n_lists += 1
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []

            # Проверка маркера верхнего уровня: только EN_DASH
            # (Мы не полагаемся на отступы: уровень 0 = маркер EN_DASH, остальное — уровень 1+)
            for it in fl.items:
                if it.level == 0 and not (it.kind == "bulleted" and it.marker_text == EN_DASH):
                    issues.append(f"Ур.1: маркированный список должен использовать только «{EN_DASH}» (найдено «{it.marker_text}»).")

            # Межстрочный интервал в блоке
            ok_raw, ratio_raw = _line_spacing_check([it.line for it in fl.items])
            if ratio_raw is not None:
                ratio_adj = max(0.0, ratio_raw - 0.25)
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                if not (lo - 1e-3 <= ratio_adj <= hi + 1e-3):
                    issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio_adj:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # Контекст до/после списка (минимально)
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

    # На всякий случай: если документ закончился, а pending оставался — закрываем как есть
    if pending and pending.items:
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
