# scripts/body_text_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple, Dict, Optional
import fitz  # PyMuPDF

# ====== настройки / константы ======
DOC_MARGIN_LEFT_CM, DOC_MARGIN_RIGHT_CM, DOC_MARGIN_TOP_CM, DOC_MARGIN_BOTTOM_CM = 3.0, 1.5, 2.0, 2.0
INDENT_MIN_CM = 1.25  # используется для эвристики начала абзаца
SIZE_MIN_PT, SIZE_MAX_PT, SIZE_EPS_PT = 12.0, 14.0, 0.5
LS_R_1_MIN, LS_R_1_MAX, LS_R_15_MIN, LS_R_15_MAX = 1.00, 1.20, 1.20, 1.75
X0_SAME_COL_EPS, INTERSECT_EPS, Y_MERGE_EPS, SPACER_MAX_WIDTH_PT = 40.0, 0.5, 0.8, 6.0

# Префикс для собственных аннотаций и стартовая страница
FILTER_OWN_ANNOTS_PREFIX, DEFAULT_START_PAGE = "Сервис нормоконтроля: ошибка основного текста", 3

TIMES_FALLBACKS = ("timesnewroman", "times new roman", "times-roman", "timesroman", "timesnewromanps", "times")

# ВАЖНО: всё, что набрано Cambria Math, полностью исключаем из проверки основного текста
MATH_FONTS = {"cambria math"}

# --- требование по «красной строке» ---
INDENT_REQ_CM = 1.25                       # должно быть ровно 1.25 см
INDENT_TOL_CM = 0.10                        # допуск ±0.10 см (можешь скорректировать при желании)

# ====== утилиты ======
def mm_to_pt(mm: float) -> float: return mm * 2.8346456693
def cm_to_pt(cm: float) -> float: return mm_to_pt(cm * 10)
LEFT_MARGIN_PT, RIGHT_MARGIN_PT = cm_to_pt(DOC_MARGIN_LEFT_CM), cm_to_pt(DOC_MARGIN_RIGHT_CM)
TOP_MARGIN_PT, BOTTOM_MARGIN_PT = cm_to_pt(DOC_MARGIN_TOP_CM), cm_to_pt(DOC_MARGIN_BOTTOM_CM)
INDENT_MIN_PT = cm_to_pt(INDENT_MIN_CM)
INDENT_REQ_PT = cm_to_pt(INDENT_REQ_CM)
INDENT_TOL_PT = cm_to_pt(INDENT_TOL_CM)

def _fmt(x: float, n: int = 2) -> str:
    try:
        return f"{float(x):.{n}f}"
    except:
        return str(x)

def _intersects_any(r: fitz.Rect, rects: List[fitz.Rect]) -> bool:
    for ex in rects:
        if (r & ex).get_area() > INTERSECT_EPS:
            return True
    return False

def _is_times_font(fontname: str) -> bool:
    if not fontname:
        return False
    name = fontname.lower().split("+", 1)[-1]
    return any(k in name for k in TIMES_FALLBACKS)

def _span_is_bold(sp: dict) -> bool:
    try:
        if int(sp.get("flags", 0)) & 2:
            return True
    except:
        pass
    nm = (sp.get("font") or "").lower()
    return any(k in nm for k in ("bold", "bd", "black", "semibold", "demi", "demibold"))

def _line_all_bold(spans: list) -> bool:
    return bool(spans) and all(_span_is_bold(s) for s in spans)

def _dom_span(spans: List[dict]) -> Tuple[str, float]:
    if not spans:
        return "", 0.0
    best = max(spans, key=lambda s: (s.get("bbox", [0, 0, 0, 0])[2] - s.get("bbox", [0, 0, 0, 0])[0]))
    return best.get("font", ""), float(best.get("size", 0.0))

def _size_ok(size: float) -> bool:
    return (SIZE_MIN_PT - SIZE_EPS_PT) <= size <= (SIZE_MAX_PT + SIZE_EPS_PT)

def _pin_point(spans: list) -> Tuple[float, float]:
    if not spans:
        return 0.0, 0.0
    x0, y0, x1, y1 = map(float, spans[0].get("bbox", [0, 0, 0, 0]))
    return x0 + 2.0, y0 + (y1 - y0) * 0.45

def _annot(page: fitz.Page, x: float, y: float, msg: str):
    try:
        t = f"{FILTER_OWN_ANNOTS_PREFIX}: {msg}"
        a = page.add_text_annot(fitz.Point(x, y), t)
        a.set_info(title=FILTER_OWN_ANNOTS_PREFIX, content=t)
        a.update()
    except:
        pass

def _normalize_excl(mp: Optional[Dict[int, List[Tuple[float, float, float, float]]]], total_pages: int) -> Dict[int, List[fitz.Rect]]:
    out: Dict[int, List[fitz.Rect]] = {}
    if not mp:
        return out
    zero_based = (0 in mp) or (max(mp.keys()) >= total_pages)
    for k, boxes in mp.items():
        p0 = k if zero_based else k - 1
        if 0 <= p0 < total_pages:
            out.setdefault(p0, []).extend(fitz.Rect(b) for b in boxes)
    return out

def _line_x0x1(spans: List[dict]) -> Tuple[float, float]:
    xs0 = [float(s.get("bbox", [0, 0, 0, 0])[0]) for s in spans]
    xs1 = [float(s.get("bbox", [0, 0, 0, 0])[2]) for s in spans]
    return (min(xs0), max(xs1)) if xs0 and xs1 else (0.0, 0.0)

def _spans_text(spans: List[dict]) -> str:
    return "".join(s.get("text", "") for s in spans)

def _merge_lines(raw: List[Tuple[fitz.Rect, List[dict], str, bool]]):
    """
    На вход: кортежи (bbox, spans, txt, is_sp)
    На выход: список визуальных строк (bbox, spans, txt_merged, is_sp_merged)
    """
    merged = []
    cur = None
    for bbox, spans, txt, is_sp in raw:
        y0 = float(bbox.y0)
        if (cur is None) or (abs(y0 - cur[0].y0) > Y_MERGE_EPS):
            if cur:
                merged.append(cur)
            cur = [fitz.Rect(bbox), list(spans), txt, is_sp]
        else:
            cur[0] = fitz.Rect(min(cur[0].x0, bbox.x0), min(cur[0].y0, bbox.y0), max(cur[0].x1, bbox.x1), max(cur[0].y1, bbox.y1))
            cur[1].extend(spans)
            cur[2] = (cur[2] + " " + txt).strip()
            cur[3] = cur[3] and is_sp
    if cur:
        merged.append(cur)
    # spacer признак уточняем: пустая строка → spacer
    return [(b, s, (t or "").strip(), (sp and (t or "").strip() == "")) for b, s, t, sp in merged]

def _ls_ok(si: float, sj: float, top2top: float) -> bool:
    ref = (si + sj) / 2.0 if (si and sj) else max(si, sj, 0.0)
    if ref <= 0:
        return True
    r = top2top / ref
    return (LS_R_1_MIN <= r <= LS_R_1_MAX) or (LS_R_15_MIN < r <= LS_R_15_MAX)

def _first_words(text: str, n: int = 3) -> str:
    ws = [w for w in text.strip().split() if w]
    return " ".join(ws[:n]) if ws else ""

def _last_words(text: str, n: int = 3) -> str:
    ws = [w for w in text.strip().split() if w]
    return " ".join(ws[-n:]) if ws else ""

# ====== мат. строки / Cambria Math ======
def _spans_fonts_share(spans: List[dict]) -> Dict[str, float]:
    """Оцениваем вклад шрифтов как сумму ширин спанов (bbox.x1-x0)."""
    share: Dict[str, float] = {}
    total = 0.0
    for s in spans:
        x0, y0, x1, y1 = map(float, s.get("bbox", [0, 0, 0, 0]))
        w = max(0.0, x1 - x0)
        f = (s.get("font") or "").lower().split("+", 1)[-1]
        share[f] = share.get(f, 0.0) + w
        total += w
    if total <= 0:
        return share
    # нормируем в 0..1
    for k in list(share.keys()):
        share[k] = share[k] / total
    return share

def _is_math_line(spans: List[dict]) -> bool:
    """Строка считается математической, если:
       - доминирующий шрифт — Cambria Math, ИЛИ
       - суммарная доля Cambria Math ≥ 0.6."""
    if not spans:
        return False
    dom_font, _ = _dom_span(spans)
    dom_font_l = dom_font.lower().split("+", 1)[-1]
    if dom_font_l in MATH_FONTS:
        return True
    shares = _spans_fonts_share(spans)
    cm_share = sum(v for k, v in shares.items() if k in MATH_FONTS)
    return cm_share >= 0.60

# ====== абзац ======
class Para:
    def __init__(self, page_num: int):
        self.page_num = page_num
        # держим кортежи (bbox, spans, text)
        self.lines: List[Tuple[fitz.Rect, List[dict], str]] = []
        self.all_bold = True
        self.x0_first = 0.0
        self.y0_first = 0.0

    def add(self, bbox: fitz.Rect, spans: List[dict], text: str):
        if not self.lines:
            self.x0_first = _line_x0x1(spans)[0]
            self.y0_first = bbox.y0
        self.lines.append((bbox, spans, text))
        self.all_bold = self.all_bold and _line_all_bold(spans)

    def dom(self) -> Tuple[str, float]:
        return _dom_span(self.lines[0][1]) if self.lines else ("", 0.0)

    def spacing_issues(self) -> List[str]:
        issues = []
        for i in range(len(self.lines) - 1):
            bi, si, _ = self.lines[i]
            bj, sj, _ = self.lines[i + 1]
            if _line_all_bold(si) or _line_all_bold(sj):
                continue
            same_col = (abs(bi.x0 - bj.x0) < X0_SAME_COL_EPS) or (bj.x0 < bi.x1)
            if not same_col:
                continue
            _, siz = _dom_span(si)
            _, sjz = _dom_span(sj)
            if not _ls_ok(siz, sjz, bj.y0 - bi.y0):
                issues.append("межстрочник не 1.5")
        return issues

    def redline_issue(self, page_left_text: float) -> Optional[str]:
        """Проверка красной строки: первая строка абзаца относительно левого края текста страницы."""
        if not self.lines:
            return None
        # вычисляем отступ
        indent_pt = max(0.0, self.x0_first - page_left_text)
        if abs(indent_pt - INDENT_REQ_PT) > INDENT_TOL_PT:
            return "Красная строка должна быть 1.25"
        return None

    # ------ удобные геттеры для лога ------
    def head_tail(self, n_words: int = 3) -> Tuple[str, str]:
        if not self.lines:
            return "", ""
        head_txt = ""
        for _, _, t in self.lines:
            if t.strip():
                head_txt = t
                break
        tail_txt = ""
        for _, _, t in reversed(self.lines):
            if t.strip():
                tail_txt = t
                break
        return _first_words(head_txt, n_words), _last_words(tail_txt, n_words)

    def avg_size(self) -> float:
        if not self.lines:
            return 0.0
        sizes = []
        for _, sp, _ in self.lines:
            _, sz = _dom_span(sp)
            if sz:
                sizes.append(sz)
        return (sum(sizes) / len(sizes)) if sizes else 0.0

    def dominant_font(self) -> str:
        fnt, _ = self.dom()
        return fnt or ""

# ====== диагностическая распечатка всех строк (не включает Cambria Math) ======
def _lines_diagnostics(page_num: int, vis_lines, left: float, right: float) -> List[str]:
    out = [f"[LINES DEBUG] Стр. {page_num} — всего строк: {len(vis_lines)}"]
    for idx, (bb, sp, txt, is_sp) in enumerate(vis_lines):
        x0, x1 = _line_x0x1(sp)
        font, size = _dom_span(sp)
        distL = x0 - left
        distR = right - x1
        width = bb.x1 - bb.x0
        height = bb.y1 - bb.y0
        bold = _line_all_bold(sp)
        t2t = ratio = "-"
        if idx + 1 < len(vis_lines):
            nbb, nsp, _, _ = vis_lines[idx + 1]
            top2top = nbb.y0 - bb.y0
            _, s0 = _dom_span(sp)
            _, s1 = _dom_span(nsp)
            ref = (s0 + s1) / 2.0 if (s0 and s1) else max(s0, s1, 0.0)
            r = (top2top / ref) if ref > 0 else 0.0
            t2t, ratio = _fmt(top2top), _fmt(r, 3)
        tshort = (txt or "").replace("\n", " ")[:72]
        out.append(
            f"  #{idx:03d} y0={_fmt(bb.y0)} x0={_fmt(x0)} x1={_fmt(x1)} "
            f"distL={_fmt(distL)} distR={_fmt(distR)} w={_fmt(width)} h={_fmt(height)} "
            f"font='{font}' sz={_fmt(size)} bold={int(bold)} spacer={int(is_sp)} "
            f"t2t={t2t} ratio={ratio} | {tshort}"
        )
    return out

# ====== основная функция ======
def check_body_text(
    doc: fitz.Document, *,
    table_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    table_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    figure_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    start_page: int = DEFAULT_START_PAGE, annotate_pdf: bool = True
) -> dict:
    total_pages = len(doc)
    combo: Dict[int, List[Tuple[float, float, float, float]]] = {}
    for mp in (exclude_bboxes_by_page, table_bboxes_by_page, table_caption_bboxes_by_page, figure_caption_bboxes_by_page):
        if mp:
            for k, v in mp.items():
                combo.setdefault(k, []).extend(v)
    excluded = _normalize_excl(combo, total_pages)

    admin_lines: List[str] = []
    page_stats: List[Tuple[int, int, int]] = []
    error_pages: List[int] = []
    total_paras = 0
    total_issues = 0
    lines_debug: List[str] = []
    start0 = max(0, start_page - 1)

    for pno in range(start0, total_pages):
        page = doc[pno]
        mb = page.mediabox
        ex = excluded.get(pno, [])
        # Границы по полям для вертикальной обрезки
        top, bottom = mb.y0 + TOP_MARGIN_PT, mb.y1 - BOTTOM_MARGIN_PT

        # 1) собрать «сырые строки»
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except:
            blocks = []
        raw = []
        for b in blocks:
            for l in b.get("lines", []):
                bb = fitz.Rect(l.get("bbox", [0, 0, 0, 0]))
                if _intersects_any(bb, ex) or bb.y0 < top or bb.y1 > bottom:
                    continue
                spans = l.get("spans", [])
                if not spans:
                    continue
                head = ("".join(s.get("text", "") for s in spans)[:48]).strip()
                if head.startswith(FILTER_OWN_ANNOTS_PREFIX):
                    continue

                # --- ПОЛНОЕ ИСКЛЮЧЕНИЕ CAMBRIA MATH НА УРОВНЕ СТРОКИ ---
                if _is_math_line(spans):
                    continue

                txt = _spans_text(spans)
                x0, x1 = _line_x0x1(spans)
                is_sp = (txt.strip() == "") or (x1 - x0 <= SPACER_MAX_WIDTH_PT)
                raw.append((bb, spans, txt, is_sp))

        # 2) визуальные строки
        raw.sort(key=lambda it: (it[0].y0, it[0].x0))
        vis = _merge_lines(raw)

        # 2.1) динамические границы текста (без Cambria Math — уже исключили)
        page_left_text = None
        page_right_text = None
        for bb, sp, txt, is_sp in vis:
            if is_sp:
                continue
            if not (txt or "").strip():
                continue
            x0, x1 = _line_x0x1(spans=sp)
            if page_left_text is None or x0 < page_left_text:
                page_left_text = x0
            if page_right_text is None or x1 > page_right_text:
                page_right_text = x1

        left_fb, right_fb = (mb.x0 + LEFT_MARGIN_PT), (mb.x1 - RIGHT_MARGIN_PT)
        left = page_left_text if page_left_text is not None else left_fb
        right = page_right_text if page_right_text is not None else right_fb

        # Диагностика строк страницы
        lines_debug.extend(_lines_diagnostics(pno + 1, vis, left, right))

        # 3) построить абзацы (отступ относительно вычисленного left)
        paras: List[Para] = []
        last_sp = False
        i = 0
        while i < len(vis):
            bb, sp, txt, is_sp = vis[i]
            if is_sp:
                last_sp = True
                i += 1
                continue
            x0, _ = _line_x0x1(sp)
            start_new = (not paras) or last_sp or ((x0 - left) >= INDENT_MIN_PT)
            if start_new:
                paras.append(Para(pno + 1))
            paras[-1].add(bb, sp, txt)
            last_sp = False
            i += 1

        # 4) проверки
        page_issues_for_count = 0

        # ЕЩЁ РАЗ фильтруем абзацы, где доминирующий шрифт Cambria Math
        filtered_paras: List[Para] = []
        for pa in paras:
            if not pa.lines:
                continue
            fnt, _ = pa.dom()
            if fnt.lower().split("+", 1)[-1] in MATH_FONTS:
                continue
            filtered_paras.append(pa)

        total_paras += len(filtered_paras)

        for pa in filtered_paras:
            if pa.all_bold:
                continue

            para_has_issue = False

            # --- Проверка красной строки ---
            red_issue_msg = pa.redline_issue(left)
            if red_issue_msg:
                para_has_issue = True
                error_pages.append(pa.page_num)
                if annotate_pdf:
                    _, sp_first, _ = pa.lines[0]
                    x, y = _pin_point(sp_first)
                    # ВАЖНО: точная формулировка по требованию
                    _annot(page, x, y, "Красная строка должна быть 1.25")
                # лог
                head, tail = pa.head_tail(3)
                admin_lines.append(
                    "[Стр. {p}] Абзац y≈{y}: красная строка ≠ 1.25 см | «{h} … {t}»"
                    .format(p=pa.page_num, y=_fmt(pa.y0_first), h=head, t=tail)
                )

            # --- Остальные проверки ---
            errs: List[str] = []
            fnt, sz_dom = pa.dom()

            if not _is_times_font(fnt):
                errs.append("шрифт не Times New Roman")
            if not _size_ok(sz_dom):
                errs.append("кегль вне 12–14 pt")
            errs += pa.spacing_issues()

            if errs:
                para_has_issue = True
                error_pages.append(pa.page_num)
                if annotate_pdf:
                    _, sp_first, _ = pa.lines[0]
                    x, y = _pin_point(sp_first)
                    _annot(page, x, y, " | ".join(sorted(set(errs))))
                head, tail = pa.head_tail(3)
                avg_sz = pa.avg_size()
                dom_font = pa.dominant_font()
                admin_lines.append(
                    "[Стр. {p}] Абзац y≈{y}: {errs} | строк={n} | шрифт='{f}' | кегль: dom={sd} avg={sa} | «{h} … {t}»"
                    .format(
                        p=pa.page_num, y=_fmt(pa.y0_first), errs=" | ".join(sorted(set(errs))),
                        n=len(pa.lines), f=dom_font, sd=_fmt(sz_dom), sa=_fmt(avg_sz),
                        h=head, t=tail
                    )
                )

            if para_has_issue:
                page_issues_for_count += 1
                total_issues += 1

        page_stats.append((pno + 1, len(filtered_paras), page_issues_for_count))

    per_page = [f"Стр. {n}: проверено абзацев {c}, нарушений {i}" for n, c, i in page_stats]
    counts = (
        f"Проверено абзацев основного текста: {total_paras}\n"
        f"Всего нарушений: {total_issues}\n" + ("\n".join(per_page) if per_page else "Страниц с текстом не найдено.")
    )
    admin_details = "[BodyText]\n" + counts + ("\n\n" + ("\n".join(admin_lines)) if admin_lines else "\n\nНарушений в основном тексте не найдено.")
    if lines_debug:
        admin_details += "\n\n" + "\n".join(lines_debug)

    user_summary = ("✅Проверка основного текста: нарушений не обнаружено"
                    if total_issues == 0
                    else f"⚠️Проверка основного текста: нарушения на страницах {', '.join(map(str, sorted(set(error_pages)))) if error_pages else '—'}")
    return {"user_summary": user_summary, "admin_details": admin_details}
