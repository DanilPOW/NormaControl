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

# Варианты имён шрифта Times в PDF
TIMES_FALLBACKS = (
    "timesnewroman",
    "times new roman",
    "times-roman",
    "timesroman",
    "timesnewromanps",
    "times",
)

# ---- Утилиты ----
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
    # убрать PDF-префикс вида "ABCDEF+TimesNewRomanPSMT"
    if "+" in name:
        name = name.split("+", 1)[1]
    return any(key in name for key in TIMES_FALLBACKS)

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

def _annotate(page: fitz.Page, rect: fitz.Rect, msg: str):
    annot = page.add_rect_annot(rect)
    annot.set_info(content=f"Сервис нормоконтроля: {msg}")
    annot.set_colors(stroke=(1, 0, 0))
    annot.set_border(width=0.7)
    annot.update()

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
    # если в ключах есть 0 — скорее всего уже 0-based
    zero_based = (0 in keys) or (max(keys) >= total_pages)
    for k, boxes in mp.items():
        p0 = k if zero_based else (k - 1)
        if p0 < 0 or p0 >= total_pages:
            continue
        for b in boxes:
            rect = fitz.Rect(b)
            out.setdefault(p0, []).append(rect)
    return out

# ---- Главная функция ----
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
    Проверяет основной текст: Times New Roman, 12–14 pt, межстрочник ≈1.5×кегль.
    Исключает области других элементов по карте exclude_bboxes_by_page (если передана).
    Возвращает отчёт в стиле других чекеров: {"user_summary": str, "admin_details": str}.
    """
    total_pages = len(doc)

    # Скомбинируем все карты исключений (если что-то пришло по отдельности)
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
    page_stats: List[Tuple[int, int, int]] = []  # (page_num, lines_checked, issues_found)
    total_lines_checked = 0
    total_issues = 0

    # Пройтись по страницам, начиная со start_page (обычно 1)
    start_idx0 = max(0, start_page - 1)

    for pno in range(start_idx0, total_pages):
        page = doc[pno]
        page_num = pno + 1
        page_ex = excluded.get(pno, [])

        # Структурированный текст
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            blocks = []

        # Собираем строки, которые НЕ попадают в исключённые области
        lines: List[Tuple[fitz.Rect, List[dict]]] = []
        for b in blocks:
            for l in b.get("lines", []):
                l_bbox = fitz.Rect(l.get("bbox", [0, 0, 0, 0]))
                if _intersects_any(l_bbox, page_ex):
                    continue
                spans = l.get("spans", [])
                if not spans:
                    continue
                lines.append((l_bbox, spans))

        lines.sort(key=lambda it: (it[0].y0, it[0].x0))
        page_issues = 0
        total_lines_checked += len(lines)

        # Проверки по строкам
        for i, (bbox_i, spans_i) in enumerate(lines):
            font_i, size_i = _dominant_span_props(spans_i)

            # Шрифт
            if not _is_times_font(font_i):
                page_issues += 1; total_issues += 1
                if annotate_pdf:
                    _annotate(
                        page, bbox_i,
                        "Использован не Times New Roman. Как должно быть: основной текст набран гарнитурой Times New Roman."
                    )
                admin_lines.append(
                    f"[Стр. {page_num}] Неверная гарнитура на y≈{bbox_i.y0:.1f} pt: '{font_i}'"
                )

            # Кегль
            if not _check_size_ok(size_i):
                page_issues += 1; total_issues += 1
                if annotate_pdf:
                    _annotate(
                        page, bbox_i,
                        f"Неверный кегль основного текста ({size_i:.1f} pt). Как должно быть: 12–14 pt."
                    )
                admin_lines.append(
                    f"[Стр. {page_num}] Неверный кегль на y≈{bbox_i.y0:.1f} pt: {size_i:.1f} pt (нужно 12–14)"
                )

            # Межстрочник (top-to-top)
            if i + 1 < len(lines):
                bbox_j, spans_j = lines[i + 1]
                # эвристика «та же колонка»: схожий x0 или наложение по X
                same_column = (abs(bbox_i.x0 - bbox_j.x0) < 40) or (bbox_j.x0 < bbox_i.x1)
                if same_column:
                    _, size_j = _dominant_span_props(spans_j)
                    top2top = bbox_j.y0 - bbox_i.y0
                    lo, hi = _expected_top2top(size_i, size_j)
                    if not (lo <= top2top <= hi):
                        page_issues += 1; total_issues += 1
                        if annotate_pdf:
                            gap_rect = fitz.Rect(min(bbox_i.x0, bbox_j.x0),
                                                 bbox_i.y0,
                                                 max(bbox_i.x1, bbox_j.x1),
                                                 bbox_j.y0)
                            _annotate(
                                page,
                                gap_rect,
                                (f"Неверный межстрочный интервал (~{top2top:.1f} pt). "
                                 f"Как должно быть: ≈ 1.5×кегль (диапазон {lo:.1f}–{hi:.1f} pt для текущего кегля).")
                            )
                        admin_lines.append(
                            f"[Стр. {page_num}] Межстрочник вне допуска на y≈{bbox_i.y0:.1f}→{bbox_j.y0:.1f} pt: "
                            f"{top2top:.1f} pt (нужно {lo:.1f}–{hi:.1f})"
                        )

        page_stats.append((page_num, len(lines), page_issues))
        if page_issues > 0:
            error_pages.append(page_num)

    # --- Итоговый отчёт ---
    per_page_lines = [
        f"Стр. {n}: проверено строк {checked}, нарушений {issues}"
        for n, checked, issues in page_stats
    ]
    counts_summary = (
        f"Проверено строк основного текста: {total_lines_checked}\n"
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
