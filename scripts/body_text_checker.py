# scripts/body_text_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple, Dict, Optional
import fitz  # PyMuPDF

# ===================== НАСТРОЙКИ =====================

# Поля документа (см):
DOC_MARGIN_LEFT_CM   = 3.0     # левое поле: 3 см
DOC_MARGIN_RIGHT_CM  = 1.5     # правое поле: 1.5 см
DOC_MARGIN_TOP_CM    = 2.0     # верхнее поле: 2 см
DOC_MARGIN_BOTTOM_CM = 2.0     # нижнее поле: 2 см

# Абзацный отступ (минимум, см от левого поля)
INDENT_MIN_CM = 1.25

# Кегль и межстрочный
SIZE_MIN_PT = 12.0
SIZE_MAX_PT = 14.0
SIZE_EPS_PT = 0.5                # допуск кегля ±0.5 pt
LINE_SPACING_TARGET = 1.5        # целевой коэффициент межстрочного интервала
LINE_SPACING_TOL = 0.30          # допуск ±30% от целевого (в долях кегля)
EMPTY_GAP_SURPLUS = 0.10         # «пустой зазор»: > верхней границы ещё на 10%

# Прочие эвристики
X0_SAME_COL_EPS = 40.0           # та же колонка (допуск по x0)
INTERSECT_EPS = 0.5              # минимальная площадь пересечения, pt^2
FILTER_OWN_ANNOTS_PREFIX = "Сервис нормоконтроля"

# Варианты имён шрифта Times в PDF
TIMES_FALLBACKS = (
    "timesnewroman", "times new roman", "times-roman",
    "timesroman", "timesnewromanps", "times",
)

# Автокалибровка полей (по двум страницам, начиная со start_page)
AUTO_CAL_PAGES = 2
AUTO_CAL_BIN_SIZE = 2.0          # ширина корзины (pt) для «грубой моды»
AUTO_CAL_SAMPLES_PER_PAGE = 400  # максимум строк на страницу для оценки

# Старт проверки (по умолчанию — с 3-й страницы)
DEFAULT_START_PAGE = 3

# ===== LOGGING / DEBUG =====
VERBOSE_DEBUG_DEFAULT = True     # выводить подробный лог?
MAX_DEBUG_LINES_DEFAULT = 4000   # верхний предел строк лога (чтобы не разрастался)
DEBUG_TO_FILE_DEFAULT = None     # путь для сохранения лога (str | None)

# ===================== ВСПОМОГАТЕЛЬНОЕ =====================

def mm_to_pt(mm: float) -> float:
    return mm * 2.8346456693

def cm_to_pt(cm: float) -> float:
    return mm_to_pt(cm * 10.0)

LEFT_MARGIN_PT   = cm_to_pt(DOC_MARGIN_LEFT_CM)
RIGHT_MARGIN_PT  = cm_to_pt(DOC_MARGIN_RIGHT_CM)
TOP_MARGIN_PT    = cm_to_pt(DOC_MARGIN_TOP_CM)
BOTTOM_MARGIN_PT = cm_to_pt(DOC_MARGIN_BOTTOM_CM)
INDENT_MIN_PT    = cm_to_pt(INDENT_MIN_CM)

def _fmt(x: float, n: int = 1) -> str:
    try:
        return f"{float(x):.{n}f}"
    except Exception:
        return str(x)

def _intersects_any(r: fitz.Rect, rects: List[fitz.Rect]) -> bool:
    if not rects:
        return False
    for ex in rects:
        ir = r & ex
        if ir.get_area() > INTERSECT_EPS:
            return True
    return False

def _is_times_font(fontname: str) -> bool:
    if not fontname:
        return False
    name = fontname.lower()
    if "+" in name:  # убрать PDF-префикс "ABCDEF+TimesNewRomanPSMT"
        name = name.split("+", 1)[1]
    return any(key in name for key in TIMES_FALLBACKS)

def _span_is_bold(sp: dict) -> bool:
    try:
        if int(sp.get("flags", 0)) & 2:
            return True
    except Exception:
        pass
    name = (sp.get("font") or "").lower()
    return any(k in name for k in ("bold", "bd", "black", "semibold", "demi", "demibold"))

def _line_is_all_bold(spans: list) -> bool:
    if not spans:
        return False
    return all(_span_is_bold(s) for s in spans)

def _dominant_span_props(spans: List[dict]) -> Tuple[str, float]:
    """(основной_шрифт, основной_кегль) по спану с максимальной шириной."""
    if not spans:
        return "", 0.0
    def _w(s):
        b = s.get("bbox", [0, 0, 0, 0])
        return (b[2] - b[0])
    best = max(spans, key=_w)
    return best.get("font", "") or "", float(best.get("size", 0.0))

def _check_size_ok(size: float) -> bool:
    return (SIZE_MIN_PT - SIZE_EPS_PT) <= size <= (SIZE_MAX_PT + SIZE_EPS_PT)

def _expected_top2top(size_a: float, size_b: float) -> Tuple[float, float]:
    """Допустимый диапазон top-to-top для 1.5×кегля с допуском."""
    ref = (size_a + size_b) / 2.0 if (size_a and size_b) else max(size_a, size_b, 0.0)
    target = LINE_SPACING_TARGET * ref
    return target * (1.0 - LINE_SPACING_TOL), target * (1.0 + LINE_SPACING_TOL)

def _add_text_annot_silent(page: fitz.Page, x: float, y: float, msg: str):
    """Точечная аннотация (pin) без падений."""
    try:
        pt = fitz.Point(float(x), float(y))
        ann = page.add_text_annot(pt, f"{FILTER_OWN_ANNOTS_PREFIX}: {msg}")
        ann.set_info(title=FILTER_OWN_ANNOTS_PREFIX, content=msg)
        ann.update()
    except Exception:
        pass

def _normalize_page_map(
    mp: Optional[Dict[int, List[Tuple[float, float, float, float]]]],
    total_pages: int
) -> Dict[int, List[fitz.Rect]]:
    """
    Приводит карту исключений к ключам-индексам страниц 0-based и fitz.Rect.
    Поддерживает 1-based ключи.
    """
    out: Dict[int, List[fitz.Rect]] = {}
    if not mp:
        return out
    keys = list(mp.keys())
    if not keys:
        return out
    zero_based = (0 in keys) or (max(keys) >= total_pages)
    for k, boxes in mp.items():
        p0 = k if zero_based else (k - 1)
        if p0 < 0 or p0 >= total_pages:
            continue
        for b in boxes:
            rect = fitz.Rect(b)
            out.setdefault(p0, []).append(rect)
    return out

def _line_x0_x1(spans: List[dict]) -> Tuple[float, float]:
    xs0, xs1 = [], []
    for s in spans:
        b = s.get("bbox", [0,0,0,0])
        xs0.append(float(b[0])); xs1.append(float(b[2]))
    return (min(xs0), max(xs1)) if xs0 and xs1 else (0.0, 0.0)

def _dominant_bin(values: List[float], bin_size: float = 2.0) -> Optional[float]:
    """Грубая мода по корзинам фиксированного размера (pt)."""
    if not values:
        return None
    from collections import Counter
    buckets = Counter(int(v // bin_size) for v in values)
    k, _ = buckets.most_common(1)[0]
    lo, hi = k * bin_size, (k + 1) * bin_size
    in_bucket = [v for v in values if lo <= v < hi]
    return sum(in_bucket) / len(in_bucket) if in_bucket else (k + 0.5) * bin_size

def _estimate_margins_two_pages(
    doc: fitz.Document,
    *,
    start_idx0: int,
    pages_to_scan: int = AUTO_CAL_PAGES,
    sample_limit_per_page: int = AUTO_CAL_SAMPLES_PER_PAGE,
) -> Dict[int, Tuple[float, float]]:
    """
    Оценка usable_left/usable_right для первых двух страниц проверки (локальные).
    Возвращает словарь: per_page[pno] = (usable_left, usable_right).
    """
    per_page: Dict[int, Tuple[float, float]] = {}

    for pno in range(start_idx0, min(len(doc), start_idx0 + pages_to_scan)):
        page = doc[pno]
        mb = page.mediabox

        # Базовые поля — по ТЗ (фиксированные)
        base_left  = mb.x0 + LEFT_MARGIN_PT
        base_right = mb.x1 - RIGHT_MARGIN_PT

        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            blocks = []

        dleft, dright = [], []
        count = 0
        for b in blocks:
            for l in b.get("lines", []):
                if count >= sample_limit_per_page:
                    break
                spans = l.get("spans", [])
                if not spans:
                    continue
                # фильтр: не учитываем наши же аннотации
                sample = "".join(s.get("text","") for s in spans)[:48].strip()
                if sample.startswith(FILTER_OWN_ANNOTS_PREFIX):
                    continue

                x0, x1 = _line_x0_x1(spans)
                if x1 <= x0:
                    continue
                dleft.append(max(0.0, x0 - mb.x0))
                dright.append(max(0.0, mb.x1 - x1))
                count += 1

        dom_left = _dominant_bin(dleft, bin_size=AUTO_CAL_BIN_SIZE)
        dom_right = _dominant_bin(dright, bin_size=AUTO_CAL_BIN_SIZE)

        # мягкая корректировка вокруг базовых полей
        if dom_left is not None:
            usable_left = 0.7 * (mb.x0 + dom_left) + 0.3 * base_left
        else:
            usable_left = base_left

        if dom_right is not None:
            usable_right = 0.7 * (mb.x1 - dom_right) + 0.3 * base_right
        else:
            usable_right = base_right

        per_page[pno] = (usable_left, usable_right)

    return per_page

def _pin_point_for_line(spans: list) -> Tuple[float, float]:
    """Точка привязки примечания внутри строки: середина первого спана + небольшой сдвиг."""
    if not spans:
        return 0.0, 0.0
    b = spans[0].get("bbox", [0,0,0,0])
    x0, y0, x1, y1 = map(float, b)
    x = x0 + 2.0                    # +2 pt вправо от начала текста
    y = y0 + (y1 - y0) * 0.45       # ближе к базовой линии
    return x, y

# ===================== АБЗАЦНАЯ МОДЕЛЬ =====================

class Para:
    def __init__(self, page_num: int):
        self.page_num = page_num
        self.lines: List[Tuple[fitz.Rect, List[dict]]] = []   # [(bbox, spans), ...]
        self.fonts: List[str] = []
        self.sizes: List[float] = []
        self.all_bold: bool = True
        self.x0_first: float = 0.0
        self.y0_first: float = 0.0

    def add_line(self, bbox: fitz.Rect, spans: List[dict]):
        if not self.lines:
            x0, _ = _line_x0_x1(spans)
            self.x0_first = x0
            self.y0_first = bbox.y0
        self.lines.append((bbox, spans))
        f, s = _dominant_span_props(spans)
        if f:
            self.fonts.append(f)
        if s:
            self.sizes.append(s)
        self.all_bold = self.all_bold and _line_is_all_bold(spans)

    def dominant_font_size(self) -> Tuple[str, float]:
        if not self.lines:
            return "", 0.0
        f, s = _dominant_span_props(self.lines[0][1])
        return f, s

    def spacing_issues(self) -> List[str]:
        """Проверка межстрочника внутри абзаца (по соседним строкам)."""
        issues = []
        for i in range(len(self.lines)-1):
            (bbox_i, spans_i) = self.lines[i]
            (bbox_j, spans_j) = self.lines[i+1]
            if _line_is_all_bold(spans_i) or _line_is_all_bold(spans_j):
                continue
            same_column = (abs(bbox_i.x0 - bbox_j.x0) < X0_SAME_COL_EPS) or (bbox_j.x0 < bbox_i.x1)
            if not same_column:
                continue
            _, si = _dominant_span_props(spans_i)
            _, sj = _dominant_span_props(spans_j)
            top2top = bbox_j.y0 - bbox_i.y0
            lo, hi = _expected_top2top(si, sj)
            if not (lo <= top2top <= hi):
                issues.append(
                    f"межстрочник {top2top:.1f} pt (допуск {lo:.1f}–{hi:.1f}) на y≈{bbox_i.y0:.1f}→{bbox_j.y0:.1f}"
                )
        return issues

# ===================== ГЛАВНАЯ ФУНКЦИЯ =====================

def check_body_text(
    doc: fitz.Document,
    *,
    table_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    table_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    figure_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    start_page: int = DEFAULT_START_PAGE,
    annotate_pdf: bool = True,
    auto_calibrate: bool = False,   # аккуратно уточнит usable_left/right на первых двух страницах
    verbose_debug: bool = VERBOSE_DEBUG_DEFAULT,        # включить подробный лог
    max_debug_lines: int = MAX_DEBUG_LINES_DEFAULT,     # ограничение длины лога
    debug_to_file: Optional[str] = DEBUG_TO_FILE_DEFAULT # путь для сохранения лога
) -> dict:
    """
    Проверяет основной текст абзацами относительно полей:
      - левое 3 см, правое 1.5 см, верх/низ 2 см (с опцией мягкой автокалибровки);
      - Times New Roman, 12–14 pt;
      - межстрочник ≈1.5×кегля с допуском;
      - абзацы: начало — абзацный отступ или «пустой зазор»; конец — «пустой зазор» или отступ у следующей строки.
    Исключает области других элементов.
    Делает точечные аннотации по одной на абзац с нарушением.
    Возвращает:
      - user_summary (коротко для пользователя),
      - admin_details (итог + ошибки),
      - debug_log (детальная трассировка; можно писать в файл).
    """
    total_pages = len(doc)

    # Скомбинируем карты исключений
    combined: Dict[int, List[Tuple[float, float, float, float]]] = {}
    def _merge_map(mp):
        if not mp:
            return
        for k, v in mp.items():
            combined.setdefault(k, []).extend(v)
    _merge_map(exclude_bboxes_by_page)
    _merge_map(table_bboxes_by_page)
    _merge_map(table_caption_bboxes_by_page)
    _merge_map(figure_caption_bboxes_by_page)
    excluded = _normalize_page_map(combined, total_pages)

    admin_lines: List[str] = []
    debug_lines: List[str] = []
    error_pages: List[int] = []
    page_stats: List[Tuple[int, int, int]] = []  # (page, paras_checked, issues_found)
    total_paras_checked = 0
    total_issues = 0

    start_idx0 = max(0, start_page - 1)

    # Локальная авто-калибровка usable_left/usable_right на первых 2 стр. проверки (мягко)
    per_page_margins: Dict[int, Tuple[float, float]] = {}
    if auto_calibrate:
        per_page_margins = _estimate_margins_two_pages(doc, start_idx0=start_idx0)

    # ======= ОБХОД СТРАНИЦ =======
    for pno in range(start_idx0, total_pages):
        page = doc[pno]
        page_num = pno + 1
        page_ex = excluded.get(pno, [])

        mb = page.mediabox
        # базовые usable поля — из ТЗ
        usable_left  = mb.x0 + LEFT_MARGIN_PT
        usable_right = mb.x1 - RIGHT_MARGIN_PT
        usable_top   = mb.y0 + TOP_MARGIN_PT
        usable_bottom= mb.y1 - BOTTOM_MARGIN_PT

        # при включённой калибровке уточним левый/правый
        if auto_calibrate and pno in per_page_margins:
            pl, pr = per_page_margins[pno]
            # в разумных пределах (не выходить далеко за рамки базовых полей)
            delta = cm_to_pt(0.5)
            usable_left  = max(usable_left - delta, min(pl, usable_left  + delta))
            usable_right = min(usable_right + delta, max(pr, usable_right - delta))

        if verbose_debug:
            debug_lines.append(f"[p{page_num}] usable_left={_fmt(usable_left)}, usable_right={_fmt(usable_right)}, "
                               f"usable_top={_fmt(usable_top)}, usable_bottom={_fmt(usable_bottom)}")

        # Текстовые линии
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            blocks = []

        raw_lines: List[Tuple[fitz.Rect, List[dict]]] = []  # (bbox, spans)
        for b in blocks:
            for l in b.get("lines", []):
                l_bbox = fitz.Rect(l.get("bbox", [0, 0, 0, 0]))
                if _intersects_any(l_bbox, page_ex):
                    continue
                if l_bbox.y0 < usable_top or l_bbox.y1 > usable_bottom:
                    continue
                spans = l.get("spans", [])
                if not spans:
                    continue
                # пропустить наши аннотации при повторной проверке
                sample = "".join(s.get("text","") for s in spans)[:48].strip()
                if sample.startswith(FILTER_OWN_ANNOTS_PREFIX):
                    continue
                raw_lines.append((l_bbox, spans))

        # Отсортируем по Y, затем X
        raw_lines.sort(key=lambda it: (it[0].y0, it[0].x0))

        # Лог — список строк с координатами/отступами
        if verbose_debug:
            for idx, (bb, sp) in enumerate(raw_lines):
                x0, x1 = _line_x0_x1(sp)
                distL = x0 - usable_left
                distR = usable_right - x1
                font, size = _dominant_span_props(sp)
                debug_lines.append(
                    f"[p{page_num}][line {idx}] y0={_fmt(bb.y0)}, x0={_fmt(x0)}, x1={_fmt(x1)}, "
                    f"distL={_fmt(distL)}, distR={_fmt(distR)}, font='{font}', size={_fmt(size)}"
                )
                if len(debug_lines) >= max_debug_lines:
                    debug_lines.append("... (debug log truncated)")
                    break

        # Построение абзацев
        paras: List[Para] = []
        i = 0
        while i < len(raw_lines):
            if verbose_debug and len(debug_lines) >= max_debug_lines:
                break

            bbox_i, spans_i = raw_lines[i]
            x0_i, x1_i = _line_x0_x1(spans_i)

            # «пустой зазор до текущей»
            empty_before = False
            if paras and paras[-1].lines:
                prev_bbox, prev_spans = paras[-1].lines[-1]
                _, s_prev = _dominant_span_props(prev_spans)
                _, s_cur  = _dominant_span_props(spans_i)
                lo, hi = _expected_top2top(s_prev, s_cur)
                top2top = bbox_i.y0 - prev_bbox.y0
                empty_before = top2top > hi * (1.0 + EMPTY_GAP_SURPLUS)
                if verbose_debug:
                    debug_lines.append(
                        f"[p{page_num}][gap check] prev_y0={_fmt(prev_bbox.y0)} -> cur_y0={_fmt(bbox_i.y0)}, "
                        f"t2t={_fmt(top2top)}, hi={_fmt(hi)}, empty_before={empty_before}"
                    )

            is_indent_start = (x0_i - usable_left) >= INDENT_MIN_PT
            is_new_para = (not paras) or empty_before or is_indent_start

            if verbose_debug:
                debug_lines.append(
                    f"[p{page_num}][line→para] y0={_fmt(bbox_i.y0)}, indent={is_indent_start}, "
                    f"empty_before={empty_before}, start_new_para={is_new_para}"
                )

            if is_new_para:
                paras.append(Para(page_num))

            paras[-1].add_line(bbox_i, spans_i)

            # Проверим конец абзаца: пустой зазор ПОСЛЕ или отступ у следующей строки
            empty_after = False
            next_indent = False
            if i + 1 < len(raw_lines):
                bbox_next, spans_next = raw_lines[i + 1]
                _, s_cur = _dominant_span_props(spans_i)
                _, s_next = _dominant_span_props(spans_next)
                lo2, hi2 = _expected_top2top(s_cur, s_next)
                top2top2 = bbox_next.y0 - bbox_i.y0
                empty_after = top2top2 > hi2 * (1.0 + EMPTY_GAP_SURPLUS)
                x0_next, _ = _line_x0_x1(spans_next)
                next_indent = (x0_next - usable_left) >= INDENT_MIN_PT

                if verbose_debug:
                    debug_lines.append(
                        f"[p{page_num}][end check] cur_y0={_fmt(bbox_i.y0)} -> next_y0={_fmt(bbox_next.y0)}, "
                        f"t2t={_fmt(top2top2)}, hi={_fmt(hi2)}, empty_after={empty_after}, next_indent={next_indent}"
                    )

            if empty_after or next_indent:
                if verbose_debug:
                    debug_lines.append(f"[p{page_num}][para break] reason={'empty_after' if empty_after else 'next_indent'}")
                i += 1
                continue

            i += 1

        # ==== Проверка абзацев ====
        page_issues = 0
        total_paras_checked += len(paras)

        for k, para in enumerate(paras, 1):
            if not para.lines:
                continue
            if para.all_bold:
                if verbose_debug:
                    debug_lines.append(f"[p{page_num}][para {k}] y0={_fmt(para.y0_first)} ALL BOLD -> skip")
                continue

            dom_font, dom_size = para.dominant_font_size()
            para_errs: List[str] = []

            # Шрифт
            if not _is_times_font(dom_font):
                para_errs.append(f"Неверная гарнитура: '{dom_font}' (ожидается Times New Roman)")

            # Кегль
            if not _check_size_ok(dom_size):
                para_errs.append(f"Неверный кегль: {dom_size:.1f} pt (нужно 12–14 pt)")

            # Межстрочники внутри абзаца
            spacing_errs = para.spacing_issues()
            para_errs.extend(spacing_errs)

            if verbose_debug:
                debug_lines.append(
                    f"[p{page_num}][para {k}] y0={_fmt(para.y0_first)} lines={len(para.lines)} "
                    f"font='{dom_font}' size={_fmt(dom_size)} issues={len(para_errs)}"
                )

            if para_errs:
                page_issues += 1
                total_issues += 1
                error_pages.append(para.page_num)

                if annotate_pdf:
                    msg = " | ".join(para_errs)
                    if len(msg) > 300:
                        msg = msg[:297] + "…"
                    _, first_spans = para.lines[0]
                    px, py = _pin_point_for_line(first_spans)
                    _add_text_annot_silent(page, px, py, f"Абзац: {msg}")

                admin_lines.append(
                    f"[Стр. {para.page_num}] Абзац на y≈{_fmt(para.y0_first)}: " + " | ".join(para_errs)
                )

        page_stats.append((page_num, len(paras), page_issues))

    # --- Итоговый отчёт ---
    per_page_lines = [
        f"Стр. {n}: проверено абзацев {checked}, нарушений {issues}"
        for n, checked, issues in page_stats
    ]
    counts_summary = (
        f"Проверено абзацев основного текста: {total_paras_checked}\n"
        f"Всего нарушений: {total_issues}\n" +
        ("\n".join(per_page_lines) if per_page_lines else "Страниц с текстом не найдено.")
    )

    admin_details = (
        counts_summary +
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\n\nНарушений в основном тексте не найдено.")
    )

    if total_issues == 0:
        user_summary = "✅Проверка основного текста: нарушений не обнаружено"
    else:
        err_pages_str = ", ".join(map(str, sorted(set(error_pages)))) if error_pages else "—"
        user_summary = f"⚠️Проверка основного текста: нарушения на страницах {err_pages_str}"

    # ВКЛЕИВАЕМ ОТЛАДОЧНЫЙ ЛОГ ПРЯМО В admin_details
    if verbose_debug and debug_lines:
        admin_details += "\n\n[DEBUG TRACE] =====================\n" + "\n".join(debug_lines)
        if debug_to_file:
            try:
                with open(debug_to_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(debug_lines))
            except Exception:
                pass

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
