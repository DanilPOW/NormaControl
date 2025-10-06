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

# Кегль
SIZE_MIN_PT = 12.0
SIZE_MAX_PT = 14.0
SIZE_EPS_PT = 0.5  # допуск кегля ±0.5 pt

# Межстрочник — пороги «квантования»
# r = top-to-top / ref_size
LS_R_1_MIN = 1.00
LS_R_1_MAX = 1.20
LS_R_15_MIN = 1.20
LS_R_15_MAX = 1.75

# Эвристики
X0_SAME_COL_EPS = 40.0           # «та же колонка»
INTERSECT_EPS = 0.5              # площадь пересечения, pt^2
FILTER_OWN_ANNOTS_PREFIX = "Сервис нормоконтроля"
Y_MERGE_EPS = 0.8                # слияние фрагментов одной строки (pt)
SPACER_MAX_WIDTH_PT = 6.0        # максимально «узкая» строка, считаем пустышкой

# Варианты имён шрифта Times в PDF
TIMES_FALLBACKS = (
    "timesnewroman", "times new roman", "times-roman",
    "timesroman", "timesnewromanps", "times",
)

# Старт страницы
DEFAULT_START_PAGE = 3

# ===== LOGGING =====
VERBOSE_DEBUG_DEFAULT = True
MAX_DEBUG_LINES_DEFAULT = 4000
DEBUG_TO_FILE_DEFAULT = None

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
    if "+" in name:
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

def _pin_point_for_line(spans: list) -> Tuple[float, float]:
    """Точка аннотации внутри строки: немного правее начала первого спана."""
    if not spans:
        return 0.0, 0.0
    b = spans[0].get("bbox", [0,0,0,0])
    x0, y0, x1, y1 = map(float, b)
    x = x0 + 2.0
    y = y0 + (y1 - y0) * 0.45
    return x, y

def _add_text_annot_silent(page: fitz.Page, x: float, y: float, msg: str):
    """Минимальная точечная аннотация."""
    try:
        pt = fitz.Point(float(x), float(y))
        text = f"{FILTER_OWN_ANNOTS_PREFIX}: {msg}"
        ann = page.add_text_annot(pt, text)
        ann.set_info(title=FILTER_OWN_ANNOTS_PREFIX, content=text)
        ann.update()
    except Exception:
        pass

def _normalize_page_map(
    mp: Optional[Dict[int, List[Tuple[float, float, float, float]]]],
    total_pages: int
) -> Dict[int, List[fitz.Rect]]:
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

def _merge_visual_lines(
    raw_lines: List[Tuple[fitz.Rect, List[dict], str, bool]]
) -> List[Tuple[fitz.Rect, List[dict], str, bool]]:
    """
    Схлопывает фрагменты с близкими y0 в одну визуальную строку.
    Вход: [(bbox, spans, text, is_spacer)], y-sorted.
    Выход: тот же формат.
    """
    merged: List[Tuple[fitz.Rect, List[dict], str, bool]] = []
    if not raw_lines:
        return merged

    cur_bbox, cur_spans, cur_text, cur_spacer = None, [], "", True
    cur_y = None

    for bbox, spans, text, is_spacer in raw_lines:
        y0 = float(bbox.y0)
        if cur_y is None or abs(y0 - cur_y) > Y_MERGE_EPS:
            # завершить предыдущую
            if cur_bbox is not None:
                merged.append((cur_bbox, cur_spans, cur_text.strip(), cur_spacer and (cur_text.strip() == "")))
            # начать новую
            cur_bbox = fitz.Rect(bbox)
            cur_spans = list(spans)
            cur_text = text
            cur_spacer = is_spacer
            cur_y = y0
        else:
            # объединяем
            cur_bbox = fitz.Rect(
                min(cur_bbox.x0, bbox.x0),
                min(cur_bbox.y0, bbox.y0),
                max(cur_bbox.x1, bbox.x1),
                max(cur_bbox.y1, bbox.y1),
            )
            cur_spans.extend(spans)
            cur_text = (cur_text + " " + text).strip()
            cur_spacer = cur_spacer and is_spacer

    if cur_bbox is not None:
        merged.append((cur_bbox, cur_spans, cur_text.strip(), cur_spacer and (cur_text.strip() == "")))

    return merged

def _ls_bucket_ok(si: float, sj: float, top2top: float) -> bool:
    """Проверка межстрочного интервала по «коробкам» 1.0 и 1.5."""
    ref = (si + sj) / 2.0 if (si and sj) else max(si, sj, 0.0)
    if ref <= 0.0:
        return True  # нечего проверять
    r = top2top / ref
    # 1.0
    if LS_R_1_MIN <= r <= LS_R_1_MAX:
        return True
    # 1.5
    if LS_R_15_MIN < r <= LS_R_15_MAX:
        return True
    return False

# ===================== АБЗАЦНАЯ МОДЕЛЬ =====================

class Para:
    def __init__(self, page_num: int):
        self.page_num = page_num
        self.lines: List[Tuple[fitz.Rect, List[dict]]] = []   # [(bbox, spans), ...]
        self.all_bold: bool = True
        self.x0_first: float = 0.0
        self.y0_first: float = 0.0

    def add_line(self, bbox: fitz.Rect, spans: List[dict]):
        if not self.lines:
            x0, _ = _line_x0_x1(spans)
            self.x0_first = x0
            self.y0_first = bbox.y0
        self.lines.append((bbox, spans))
        self.all_bold = self.all_bold and _line_is_all_bold(spans)

    def dominant_font_size(self) -> Tuple[str, float]:
        if not self.lines:
            return "", 0.0
        f, s = _dominant_span_props(self.lines[0][1])
        return f, s

    def spacing_issues(self) -> List[str]:
        issues = []
        for i in range(len(self.lines) - 1):
            bbox_i, spans_i = self.lines[i]
            bbox_j, spans_j = self.lines[i + 1]
            if _line_is_all_bold(spans_i) or _line_is_all_bold(spans_j):
                continue
            same_col = (abs(bbox_i.x0 - bbox_j.x0) < X0_SAME_COL_EPS) or (bbox_j.x0 < bbox_i.x1)
            if not same_col:
                continue
            _, si = _dominant_span_props(spans_i)
            _, sj = _dominant_span_props(spans_j)
            t2t = bbox_j.y0 - bbox_i.y0
            if not _ls_bucket_ok(si, sj, t2t):
                issues.append("межстрочник не 1.5")
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
    verbose_debug: bool = VERBOSE_DEBUG_DEFAULT,
    max_debug_lines: int = MAX_DEBUG_LINES_DEFAULT,
    debug_to_file: Optional[str] = DEBUG_TO_FILE_DEFAULT
) -> dict:
    """
    Проверяет основной текст абзацами:
      • поля: слева 3 см, справа 1.5 см, сверху/снизу 2 см;
      • гарнитура Times New Roman;
      • кегль 12–14 pt;
      • межстрочник: r=top2top/кегль ∈ [1.00–1.20] (считаем «1») или (1.20–1.75] (считаем «1.5»).
    Делит на абзацы по отступу/пустой строке. Аннотация: одна на абзац, краткая.
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

    # ======= ОБХОД СТРАНИЦ =======
    for pno in range(start_idx0, total_pages):
        page = doc[pno]
        page_num = pno + 1
        page_ex = excluded.get(pno, [])

        mb = page.mediabox
        usable_left  = mb.x0 + LEFT_MARGIN_PT
        usable_right = mb.x1 - RIGHT_MARGIN_PT
        usable_top   = mb.y0 + TOP_MARGIN_PT
        usable_bottom= mb.y1 - BOTTOM_MARGIN_PT

        if verbose_debug:
            debug_lines.append(f"[p{page_num}] usable_left={_fmt(usable_left)}, usable_right={_fmt(usable_right)}, "
                               f"usable_top={_fmt(usable_top)}, usable_bottom={_fmt(usable_bottom)}")

        # 1) Собираем сырые «строки»
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            blocks = []

        raw_lines: List[Tuple[fitz.Rect, List[dict], str, bool]] = []  # (bbox, spans, text, is_spacer)

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

                # Пропустить наши аннотации
                sample = "".join(s.get("text","") for s in spans)[:48].strip()
                if sample.startswith(FILTER_OWN_ANNOTS_PREFIX):
                    continue

                text_full = "".join(s.get("text","") for s in spans)
                x0, x1 = _line_x0_x1(spans)
                is_spacer = (text_full.strip() == "") or (x1 - x0 <= SPACER_MAX_WIDTH_PT)

                raw_lines.append((l_bbox, spans, text_full, is_spacer))

        # 2) Сортируем и схлопываем в «визуальные строки»
        raw_lines.sort(key=lambda it: (it[0].y0, it[0].x0))
        vis_lines = _merge_visual_lines(raw_lines)

        # 3) Отладка по строкам
        if verbose_debug:
            for idx, (bb, sp, text, is_spacer) in enumerate(vis_lines):
                x0, x1 = _line_x0_x1(sp)
                distL = x0 - usable_left
                distR = usable_right - x1
                font, size = _dominant_span_props(sp)
                flag = "[SPACER]" if is_spacer else "text="
                tshort = text[:48].replace("\n"," ")
                debug_lines.append(
                    f"[p{page_num}][line {idx}] y0={_fmt(bb.y0)}, x0={_fmt(x0)}, x1={_fmt(x1)}, "
                    f"distL={_fmt(distL)}, distR={_fmt(distR)}, font='{font}', size={_fmt(size)} {flag}'{tshort}'"
                )
                if len(debug_lines) >= max_debug_lines:
                    debug_lines.append("... (debug log truncated)")
                    break

        # 4) Построение абзацев
        paras: List[Para] = []
        last_was_spacer = False

        i = 0
        while i < len(vis_lines):
            bbox_i, spans_i, text_i, is_spacer_i = vis_lines[i]

            if is_spacer_i:
                last_was_spacer = True
                if verbose_debug:
                    debug_lines.append(f"[p{page_num}][spacer] y0={_fmt(bbox_i.y0)} — paragraph delimiter")
                i += 1
                continue

            x0_i, _ = _line_x0_x1(spans_i)
            is_indent_start = (x0_i - usable_left) >= INDENT_MIN_PT
            start_new_para = (not paras) or last_was_spacer or is_indent_start

            if verbose_debug:
                debug_lines.append(
                    f"[p{page_num}][line→para] y0={_fmt(bbox_i.y0)}, indent={is_indent_start}, "
                    f"last_was_spacer={last_was_spacer}, start_new_para={start_new_para}"
                )

            if start_new_para:
                paras.append(Para(page_num))

            paras[-1].add_line(bbox_i, spans_i)
            last_was_spacer = False

            # конец абзаца: следующая строка — spacer или с отступом
            end_para = False
            if i + 1 < len(vis_lines):
                bbox_next, spans_next, text_next, is_spacer_next = vis_lines[i + 1]
                if is_spacer_next:
                    end_para = True
                    if verbose_debug:
                        debug_lines.append(f"[p{page_num}][para break] reason=next_is_spacer")
                else:
                    x0_next, _ = _line_x0_x1(spans_next)
                    next_indent = (x0_next - usable_left) >= INDENT_MIN_PT
                    if next_indent:
                        end_para = True
                        if verbose_debug:
                            debug_lines.append(f"[p{page_num}][para break] reason=next_indent")

            if end_para:
                i += 1
                continue

            i += 1

        # 5) Проверка абзацев
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
                para_errs.append("шрифт не Times New Roman")

            # Кегль
            if not _check_size_ok(dom_size):
                para_errs.append("кегль вне 12–14 pt")

            # Межстрочник
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
                    msg = " | ".join(sorted(set(para_errs)))
                    px, py = _pin_point_for_line(para.lines[0][1])
                    _add_text_annot_silent(page, px, py, msg)

                admin_lines.append(
                    f"[Стр. {para.page_num}] Абзац на y≈{_fmt(para.y0_first)}: " + " | ".join(sorted(set(para_errs)))
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
        "[BodyText]\n" + counts_summary +
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\n\nНарушений в основном тексте не найдено.")
    )

    if total_issues == 0:
        user_summary = "✅Проверка основного текста: нарушений не обнаружено"
    else:
        err_pages_str = ", ".join(map(str, sorted(set(error_pages)))) if error_pages else "—"
        user_summary = f"⚠️Проверка основного текста: нарушения на страницах {err_pages_str}"

    # Отладочный хвост — в admin_details и/или в файл
    if verbose_debug and debug_lines:
        admin_details += "\n\n[DEBUG TRACE] =====================\n" + "\n".join(debug_lines[:max_debug_lines])
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
