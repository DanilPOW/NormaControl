# scripts/body_text_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple, Dict, Iterable, Any, Optional
import fitz  # PyMuPDF

# ---- Настройки/допуски ----
SIZE_MIN_PT = 12.0
SIZE_MAX_PT = 14.0
SIZE_EPS_PT = 0.5               # допуск по размеру кегля
LINE_SPACING_TARGET = 1.5       # целевой межстрочный коэффициент
LINE_SPACING_TOL = 0.30         # допуск ±30% от целевого (в долях кегля)
INTERSECT_EPS = 0.5             # минимальная площадь пересечения, pt^2

# Поля/отступы абзацев и эвристики «рваного края»
LEFT_MARGIN_PT  = 72.0          # левое поле страницы (примерно 2.54 см)
RIGHT_MARGIN_PT = 72.0          # правое поле страницы
INDENT_MIN_PT   = 18.0          # минимальный отступ начала абзаца (~0.63 см)
RAGGED_EPS_PT   = 18.0          # насколько «короче» от правого поля считать строку концом абзаца
X0_SAME_COL_EPS = 40.0          # допуск для «той же колонки» (как было)
EMPTY_GAP_FACTOR = 1.2          # множитель к среднему кеглю для определения «пустой строки»

# Варианты имён шрифта Times в PDF
TIMES_FALLBACKS = (
    "timesnewroman",
    "times new roman",
    "times-roman",
    "timesroman",
    "timesnewromanps",
    "times",
)

# ================= УТИЛИТЫ =================
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
    if "+" in name:  # убрать PDF-префикс вида "ABCDEF+TimesNewRomanPSMT"
        name = name.split("+", 1)[1]
    return any(key in name for key in TIMES_FALLBACKS)

def _span_is_bold(sp: dict) -> bool:
    try:
        if int(sp.get("flags", 0)) & 2:  # у PyMuPDF бит 2 часто означает bold
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
    """Вернуть (основной_шрифт, основной_кегль) по спану с максимальной шириной."""
    if not spans:
        return "", 0.0
    def _w(s):
        b = s.get("bbox", [0, 0, 0, 0]); return (b[2] - b[0])
    best = max(spans, key=_w)
    return best.get("font", "") or "", float(best.get("size", 0.0))

def _check_size_ok(size: float) -> bool:
    return (SIZE_MIN_PT - SIZE_EPS_PT) <= size <= (SIZE_MAX_PT + SIZE_EPS_PT)

def _expected_top2top(size_a: float, size_b: float) -> Tuple[float, float]:
    """Вернуть допустимый диапазон top-to-top для 1.5×кегля с допуском."""
    ref = (size_a + size_b) / 2.0 if (size_a and size_b) else max(size_a, size_b, 0.0)
    target = LINE_SPACING_TARGET * ref
    return target * (1.0 - LINE_SPACING_TOL), target * (1.0 + LINE_SPACING_TOL)

def _add_text_annot_silent(page: fitz.Page, x: float, y: float, msg: str):
    """Безопасно добавить точечную аннотацию (pin) с заголовком."""
    try:
        pt = fitz.Point(float(x), float(y))
        ann = page.add_text_annot(pt, f"Сервис нормоконтроля: {msg}")
        ann.set_info(title="Сервис нормоконтроля", content=msg)
        ann.update()
    except Exception:
        pass

def _normalize_page_map(
    mp: Optional[Dict[int, List[Tuple[float, float, float, float]]]],
    total_pages: int
) -> Dict[int, List[fitz.Rect]]:
    """
    Приводит карту исключений к ключам-индексам страниц 0-based и значениям fitz.Rect.
    Поддерживает, если ключи приходят 1-based (обычно так делают чекеры).
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

def _line_text_x0(spans: List[dict]) -> float:
    """Минимальный x0 по спанам — «реальный» старт текста в строке."""
    x0s = []
    for s in spans:
        b = s.get("bbox", [0,0,0,0])
        x0s.append(float(b[0]))
    return min(x0s) if x0s else 0.0

def _line_text_x1(spans: List[dict]) -> float:
    """Максимальный x1 по спанам — «реальный» конец текста в строке."""
    x1s = []
    for s in spans:
        b = s.get("bbox", [0,0,0,0])
        x1s.append(float(b[2]))
    return max(x1s) if x1s else 0.0

# ================= АБЗАЦНАЯ ЛОГИКА =================
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
            self.x0_first = _line_text_x0(spans)
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
        # По первой строке абзаца (часто определяет стиль абзаца)
        f, s = _dominant_span_props(self.lines[0][1])
        return f, s

    def avg_size(self) -> float:
        return sum(self.sizes)/len(self.sizes) if self.sizes else 0.0

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
                issues.append(f"межстрочник {top2top:.1f} pt (допуск {lo:.1f}–{hi:.1f}) на y≈{bbox_i.y0:.1f}→{bbox_j.y0:.1f}")
        return issues

# ================= ГЛАВНАЯ ФУНКЦИЯ =================
def check_body_text(
    doc: fitz.Document,
    *,
    table_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    table_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    figure_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float, float, float, float]]]] = None,
    start_page: int = 1,
    annotate_pdf: bool = True,
) -> dict:
    """
    Проверяет основной текст абзацами:
      - Times New Roman, 12–14 pt,
      - межстрочник ≈1.5×кегля с допуском,
      - объединение строк в абзацы по правилам: начало — строка с отступом; конец — «короткая» строка
        (не доходит до правого поля) или «пустая строка» до/после.
    Исключает области других элементов (если переданы).
    Делает ТОЛЬКО точечные аннотации (без рамок) — по одной на абзац с нарушением.
    """
    total_pages = len(doc)

    # Скомбинируем все карты исключений
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
    error_pages: List[int] = []
    page_stats: List[Tuple[int, int, int]] = []  # (page, paras_checked, issues_found)
    total_paras_checked = 0
    total_issues = 0

    start_idx0 = max(0, start_page - 1)

    for pno in range(start_idx0, total_pages):
        page = doc[pno]
        page_num = pno + 1
        page_ex = excluded.get(pno, [])

        # Геометрия страницы
        mediabox = page.mediabox
        usable_left  = mediabox.x0 + LEFT_MARGIN_PT
        usable_right = mediabox.x1 - RIGHT_MARGIN_PT

        # Забираем структурированный текст, исключаем области
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
                spans = l.get("spans", [])
                if not spans:
                    continue
                raw_lines.append((l_bbox, spans))

        # Отсортируем по Y, затем X
        raw_lines.sort(key=lambda it: (it[0].y0, it[0].x0))

        # Построение абзацев
        paras: List[Para] = []
        i = 0
        while i < len(raw_lines):
            bbox_i, spans_i = raw_lines[i]
            x0_text = _line_text_x0(spans_i)
            x1_text = _line_text_x1(spans_i)

            # эвристика «пустая строка до текущей»
            empty_before = False
            if paras and paras[-1].lines:
                last_bbox_prev, last_spans_prev = paras[-1].lines[-1]
                # оценим средний кегль между строками
                _, s_prev = _dominant_span_props(last_spans_prev)
                _, s_cur  = _dominant_span_props(spans_i)
                s_avg = (s_prev + s_cur)/2 if (s_prev and s_cur) else max(s_prev, s_cur, 0.0)
                gap = bbox_i.y0 - last_bbox_prev.y1
                if s_avg and gap > EMPTY_GAP_FACTOR * s_avg:
                    empty_before = True

            is_indent_start = (x0_text - usable_left) >= INDENT_MIN_PT
            is_new_para = (not paras) or empty_before or is_indent_start

            if is_new_para:
                paras.append(Para(page_num))

            # добавляем текущую строку в последний абзац
            paras[-1].add_line(bbox_i, spans_i)

            # проверим критерий конца абзаца на самой строке
            line_is_short = (usable_right - x1_text) >= RAGGED_EPS_PT

            # также посмотрим «пустую строку ПОСЛЕ»
            empty_after = False
            if i + 1 < len(raw_lines):
                bbox_next, spans_next = raw_lines[i + 1]
                _, s_cur = _dominant_span_props(spans_i)
                _, s_next = _dominant_span_props(spans_next)
                s_avg2 = (s_cur + s_next)/2 if (s_cur and s_next) else max(s_cur, s_next, 0.0)
                gap2 = bbox_next.y0 - bbox_i.y1
                if s_avg2 and gap2 > EMPTY_GAP_FACTOR * s_avg2:
                    empty_after = True

            # если конец абзаца — просто стартуем следующий на следующей итерации
            if line_is_short or empty_after:
                i += 1
                continue

            # иначе возможно это «продолжение абзаца» — следующая строка без отступа
            i += 1

        # ==== Проверка абзацев ====
        page_issues = 0
        total_paras_checked += len(paras)

        for para in paras:
            if not para.lines:
                continue
            # Пропустить полностью полужирные абзацы
            if para.all_bold:
                continue

            # Доминирующий шрифт/кегль (по первой строке абзаца)
            dom_font, dom_size = para.dominant_font_size()

            # Сбор ошибок абзаца
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

            if para_errs:
                page_issues += 1
                total_issues += 1
                error_pages.append(para.page_num)

                # Одна аннотация на абзац — ставим пин в начале абзаца
                if annotate_pdf:
                    msg = " ; ".join(para_errs)
                    # чтобы текст пина не был слишком длинным
                    if len(msg) > 300:
                        msg = msg[:297] + "…"
                    # координата — первая строка абзаца
                    first_bbox, first_spans = para.lines[0]
                    _add_text_annot_silent(
                        page, para.x0_first, para.y0_first,
                        f"Абзац: {msg}"
                    )

                # Админ-лог
                admin_lines.append(
                    f"[Стр. {para.page_num}] Абзац на y≈{para.y0_first:.1f}: " + " | ".join(para_errs)
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

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
