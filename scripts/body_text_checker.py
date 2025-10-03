# scripts/body_text_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Tuple, Iterable, Any
import fitz  # PyMuPDF

# Импортируем все bboxes из других чекеров
from scripts.image_checker import image_bboxes
from scripts.pdf_table_checker import table_bboxes
from scripts.figure_caption_checker import figure_caption_bboxes
from scripts.table_caption_checker import table_caption_bboxes
from scripts.formula_checker import formula_bboxes
from scripts.list_checker import list_bboxes
from scripts.structural_headings_checker import heading_bboxes

# Собираем в один список для исключения
all_excluded_bboxes = (
    image_bboxes
    + table_bboxes
    + figure_caption_bboxes
    + table_caption_bboxes
    + formula_bboxes
    + list_bboxes
    + heading_bboxes
)

# ---- Настройки/допуски ----
SIZE_MIN_PT = 12.0
SIZE_MAX_PT = 14.0
SIZE_EPS_PT = 0.5               # допуск по размеру
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
def _to_rect(obj: Any) -> Tuple[int, fitz.Rect]:
    """Привести внешний bbox к паре (page_index0, fitz.Rect)."""
    if isinstance(obj, dict):
        p = obj.get("page_index", obj.get("page", None))
        b = obj.get("bbox", None)
        if p is None or b is None:
            raise ValueError("Unsupported bbox dict format.")
        rect = fitz.Rect(b) if not isinstance(b, fitz.Rect) else b
        return int(p), rect
    if isinstance(obj, tuple) and len(obj) == 2:
        p, b = obj
        rect = fitz.Rect(b) if not isinstance(b, fitz.Rect) else b
        return int(p), rect
    if hasattr(obj, "page_index") and hasattr(obj, "x0"):
        p = int(getattr(obj, "page_index"))
        rect = fitz.Rect(float(obj.x0), float(obj.y0), float(obj.x1), float(obj.y1))
        return p, rect
    if hasattr(obj, "page_index") and hasattr(obj, "bbox"):
        p = int(getattr(obj, "page_index"))
        b = getattr(obj, "bbox")
        rect = fitz.Rect(b) if not isinstance(b, fitz.Rect) else b
        return p, rect
    raise ValueError("Unsupported bbox object format.")

def _group_excluded_by_page(externals: Iterable[Any]) -> dict:
    by_page = {}
    for e in externals:
        try:
            p, r = _to_rect(e)
        except Exception:
            continue
        by_page.setdefault(p, []).append(r)
    return by_page

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

def _dominant_span_props(spans: List[dict]) -> Tuple[str, float]:
    """Вернуть (основной_шрифт, основной_кегль) по спану с максимальной шириной."""
    if not spans:
        return "", 0.0
    def _w(s): 
        b = s.get("bbox", [0,0,0,0]); return (b[2]-b[0])
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

# ---- Главная функция ----
def check_body_text(doc: fitz.Document) -> dict:
    """
    Проверка основного текста с исключением всех внешних bbox.
    Аннотации добавляются в PDF. Возвращает отчёт в стиле image_checker.
    """
    excluded = _group_excluded_by_page(all_excluded_bboxes)

    admin_lines: List[str] = []
    error_pages: List[int] = []
    page_stats: List[Tuple[int, int, int]] = []  # (page_num, lines_checked, issues_found)
    total_lines_checked = 0
    total_issues = 0

    for pno in range(len(doc)):
        page = doc[pno]
        page_num = pno + 1
        page_ex = excluded.get(pno, [])

        text = page.get_text("dict")  # blocks -> lines -> spans
        blocks = text.get("blocks", [])
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

        # Проверка шрифта/кегля и межстрочника
        for i, (bbox_i, spans_i) in enumerate(lines):
            font_i, size_i = _dominant_span_props(spans_i)

            # Шрифт
            if not _is_times_font(font_i):
                page_issues += 1; total_issues += 1
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

    # --- Итоговый отчёт в стиле image_checker ---
    # Сводка по страницам
    per_page_lines = [
        f"Стр. {n}: проверено строк {checked}, нарушений {issues}"
        for n, checked, issues in page_stats
    ]
    counts_summary = (
        f"Проверено строк основного текста: {total_lines_checked}\n"
        f"Всего нарушений: {total_issues}\n" +
        ("\n".join(per_page_lines) if per_page_lines else "Страниц с текстом не найдено.")
    )

    # Детали
    admin_details = (
        counts_summary +
        ("\n\n" + "\n".join(admin_lines) if admin_lines else "\n\nНарушений в основном тексте не найдено.")
    )

    # Короткая сводка для пользователя
    if total_issues == 0:
        user_summary = "✅Проверка основного текста: нарушений не обнаружено"
    else:
        err_pages_str = ", ".join(map(str, sorted(set(error_pages)))) if error_pages else "—"
        user_summary = f"⚠️Проверка основного текста: нарушения на страницах {err_pages_str}"

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
