# scripts/pdf_table_checker.py
import camelot
import time
import pdfplumber
import fitz  # PyMuPDF
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

LEFT_MARGIN_PT = 3 * 28.35
RIGHT_MARGIN_PT = 1.5 * 28.35
TOP_MARGIN_PT = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT = 2

# ==========================
# ВСПОМОГАТЕЛЬНЫЕ СТРУКТУРЫ
# ==========================

@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def center(self) -> Tuple[float, float]:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def width(self) -> float:
        return self.x1 - self.x0

    def height(self) -> float:
        return self.y1 - self.y0

def bbox_area(b: BBox) -> float:
    return max(0.0, b.width()) * max(0.0, b.height())

def bbox_inset(b: BBox, pad: float) -> BBox:
    return BBox(b.x0 + pad, b.y0 + pad, b.x1 - pad, b.y1 - pad)

def bbox_intersection(a: BBox, b: BBox) -> Tuple[float, Optional[BBox]]:
    x0 = max(a.x0, b.x0); y0 = max(a.y0, b.y0)
    x1 = min(a.x1, b.x1); y1 = min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0, None
    inter = BBox(x0, y0, x1, y1)
    return bbox_area(inter), inter

def bbox_iou(a: BBox, b: BBox) -> float:
    inter_area, _ = bbox_intersection(a, b)
    if inter_area <= 0:
        return 0.0
    return inter_area / (bbox_area(a) + bbox_area(b) - inter_area)

def touches_cell_border(b: BBox, cell: BBox, tol: float = 1.2) -> bool:
    """Элемент касается границы ячейки (рамка/штрих)."""
    left   = abs(b.x0 - cell.x0) <= tol
    right  = abs(b.x1 - cell.x1) <= tol
    top    = abs(b.y0 - cell.y0) <= tol
    bottom = abs(b.y1 - cell.y1) <= tol
    return left or right or top or bottom

@dataclass
class Alignment:
    horizontal: str  # "left"|"center"|"right"|"mixed"
    vertical: str    # "top"|"middle"|"bottom"|"mixed"
    gaps: Dict[str, float]


@dataclass
class CellContent:
    text_spans: List[Dict] = field(default_factory=list)   # [{"text":..., "bbox":BBox}, ...]
    images: List[BBox] = field(default_factory=list)       # bbox картинок
    vectors: List[BBox] = field(default_factory=list)      # bbox векторных объектов
    text_bbox: Optional[BBox] = None
    image_bbox: Optional[BBox] = None
    vector_bbox: Optional[BBox] = None
    alignment_text: Optional[Alignment] = None
    alignment_image: Optional[Alignment] = None
    alignment_vector: Optional[Alignment] = None
    is_formula_like: bool = False                          # эвристика «формульной» ячейки


# ==========================
# УТИЛИТЫ
# ==========================

def bbox_union(b1: BBox, b2: BBox) -> BBox:
    return BBox(
        x0=min(b1.x0, b2.x0),
        y0=min(b1.y0, b2.y0),
        x1=max(b1.x1, b2.x1),
        y1=max(b1.y1, b2.y1),
    )

def fitz_rect_to_bbox(r: fitz.Rect) -> BBox:
    return BBox(r.x0, r.y0, r.x1, r.y1)

def rect_reasonably_inside(elem: BBox, container: BBox, min_cover_ratio: float) -> bool:
    """
    Считаем, что элемент принадлежит контейнеру, если пересечение покрывает
    >= min_cover_ratio площади самого элемента (не по центру!).
    """
    inter, _ = bbox_intersection(elem, container)
    ea = bbox_area(elem)
    return ea > 0 and (inter / ea) >= min_cover_ratio

def camelot_to_fitz_bbox(cell, page_height: float) -> BBox:
    """
    Camelot/pdfminer: origin bottom-left (y↑)
    PyMuPDF:          origin top-left    (y↓)
    """
    x0_c, y0_c, x1_c, y1_c = cell.x1, cell.y1, cell.x2, cell.y2
    return BBox(
        x0=float(x0_c),
        y0=float(page_height - y1_c),  # верх
        x1=float(x1_c),
        y1=float(page_height - y0_c),  # низ
    )

def camelot_table_bbox_to_fitz(x0, y0, x1, y1, page_height: float) -> BBox:
    return BBox(float(x0), float(page_height - y1), float(x1), float(page_height - y0))

def classify_alignment(cell: BBox, content: BBox, tol_px: float = 2.0, padding: float = 0.0) -> Alignment:
    # Учитываем «пэддинг» — отступы внутри ячейки
    cx0 = cell.x0 + padding
    cy0 = cell.y0 + padding
    cx1 = cell.x1 - padding
    cy1 = cell.y1 - padding

    tx0, ty0, tx1, ty1 = content.x0, content.y0, content.x1, content.y1

    left_gap   = max(0.0, tx0 - cx0)
    right_gap  = max(0.0, cx1 - tx1)
    top_gap    = max(0.0, ty0 - cy0)
    bottom_gap = max(0.0, cy1 - ty1)

    # Горизонталь
    cell_mid_x = (cx0 + cx1) / 2
    text_mid_x = (tx0 + tx1) / 2
    center_gap = abs(cell_mid_x - text_mid_x)

    if center_gap <= tol_px:
        h = "center"
    elif abs(left_gap) <= tol_px and right_gap > tol_px:
        h = "left"
    elif abs(right_gap) <= tol_px and left_gap > tol_px:
        h = "right"
    else:
        h = "left" if left_gap < right_gap else "right"

    # Вертикаль
    cell_mid_y = (cy0 + cy1) / 2
    text_mid_y = (ty0 + ty1) / 2
    middle_gap = abs(cell_mid_y - text_mid_y)

    if middle_gap <= tol_px:
        v = "middle"
    elif abs(top_gap) <= tol_px and bottom_gap > tol_px:
        v = "top"
    elif abs(bottom_gap) <= tol_px and top_gap > tol_px:
        v = "bottom"
    else:
        v = "top" if top_gap < bottom_gap else "bottom"

    return Alignment(
        horizontal=h,
        vertical=v,
        gaps={
            "left": left_gap, "right": right_gap,
            "top": top_gap, "bottom": bottom_gap,
            "center_gap": center_gap, "middle_gap": middle_gap
        }
    )

def looks_like_formula(text: str) -> bool:
    """
    Простая эвристика: много матсимволов, греческие буквы,
    бинарные операторы, дроби, индексы и т.д.
    """
    if not text:
        return False
    t = text.strip()

    math_tokens = set("=±≈≡∼∑∏∫√∞≤≥≠×·⋅°′″⊥∥∠ΔΩλμνπσφψθαβγδεζηκξρω")
    ops = set("+-=*/^_()[]{}<>|")
    frac_markers = ["/", "frac", "√", "∑", "∫"]

    score = 0
    score += sum(ch in math_tokens for ch in t)
    score += sum(ch in ops for ch in t)
    score += sum(m in t for m in frac_markers)

    if "_" in t or "^" in t:
        score += 2
    digits = sum(ch.isdigit() for ch in t)
    if digits >= 3:
        score += 1

    return score >= 4  # настроечный порог


# ==========================
# ИЗВЛЕЧЕНИЕ СОДЕРЖИМОГО ЯЧЕЙКИ (fitz)
# ==========================

def extract_cell_content(page: fitz.Page, cell_rect: BBox,
                         tol_px: float = 2.0, padding: float = 1.5) -> CellContent:
    cc = CellContent()

    # Внутренности ячейки (не трогаем границы)
    inner = bbox_inset(cell_rect, padding)

    # Порогики
    MIN_TEXT_COVER = 0.50   # ≥50% спана лежит в ячейке
    MIN_IMG_COVER  = 0.70   # ≥70% картинки лежит в ячейке
    MIN_VEC_COVER  = 0.70   # ≥70% вектора лежит в ячейке
    MIN_AREA_PT2   = 8.0 * 8.0  # минимальная площадь содержимого
    # относительный порог для «крошечного» текста в картинковой ячейке
    TINY_TEXT_VS_IMG = 0.15

    text_dict = page.get_text("dict")

    # -------- ТЕКСТ --------
    span_boxes: List[BBox] = []
    span_texts: List[str] = []
    for b in text_dict.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                sx0, sy0, sx1, sy1 = span.get("bbox", (0, 0, 0, 0))
                sb = BBox(sx0, sy0, sx1, sy1)
                if bbox_area(sb) < MIN_AREA_PT2:
                    continue
                if rect_reasonably_inside(sb, inner, MIN_TEXT_COVER):
                    t = (span.get("text") or "").strip()
                    if t:  # игнор пустых
                        span_boxes.append(sb)
                        span_texts.append(t)

    if span_boxes:
        tb = None
        for box in span_boxes:
            tb = box if tb is None else bbox_union(tb, box)
        cc.text_spans = [{"text": t, "bbox": b} for t, b in zip(span_texts, span_boxes)]
        cc.text_bbox = tb
        cc.alignment_text = classify_alignment(cell_rect, tb, tol_px=tol_px, padding=padding)
        cc.is_formula_like = looks_like_formula(" ".join(span_texts))

    # -------- КАРТИНКИ --------
    img_boxes: List[BBox] = []
    for b in text_dict.get("blocks", []):
        if b.get("type") == 1 and "bbox" in b:
            ib = fitz_rect_to_bbox(fitz.Rect(b["bbox"]))
            if bbox_area(ib) < MIN_AREA_PT2:
                continue
            if rect_reasonably_inside(ib, inner, MIN_IMG_COVER):
                img_boxes.append(ib)

    if img_boxes:
        ibb = None
        for ib in img_boxes:
            ibb = ib if ibb is None else bbox_union(ibb, ib)
        cc.images = img_boxes
        cc.image_bbox = ibb
        cc.alignment_image = classify_alignment(cell_rect, ibb, tol_px=tol_px, padding=padding)

    # -------- ВЕКТОРЫ (НЕ РАМКИ) --------
    vec_boxes: List[BBox] = []
    try:
        drawings = page.get_drawings()
        for d in drawings:
            xs, ys = [], []
            for it in d.get("items", []):
                op, pts = it[0], it[1]
                if op == "re":
                    x0, y0, x1, y1 = pts
                    xs.extend([x0, x1]); ys.extend([y0, y1])
                else:
                    for k in range(0, len(pts), 2):
                        xs.append(pts[k]); ys.append(pts[k+1])
            if xs and ys:
                vb = BBox(min(xs), min(ys), max(xs), max(ys))
                if bbox_area(vb) < MIN_AREA_PT2:
                    continue
                # внутри и не касается границ ячейки — иначе это рамка/решётка
                if rect_reasonably_inside(vb, inner, MIN_VEC_COVER) and not touches_cell_border(vb, cell_rect, tol=padding+0.5):
                    vec_boxes.append(vb)
    except Exception:
        pass

    if vec_boxes:
        vbb = None
        for vb in vec_boxes:
            vbb = vb if vbb is None else bbox_union(vbb, vb)
        cc.vectors = vec_boxes
        cc.vector_bbox = vbb
        cc.alignment_vector = classify_alignment(cell_rect, vbb, tol_px=tol_px, padding=padding)

    # -------- Конфликтология (избавляемся от ложных T/V в «картинковых» ячейках) --------
    cell_area = bbox_area(inner)

    if cc.image_bbox:
        img_area = bbox_area(cc.image_bbox)
        txt_area = bbox_area(cc.text_bbox) if cc.text_bbox else 0.0
        # если картинка «доминирует», а текст крошечный — гасим текст
        if img_area > 0 and (txt_area / img_area) <= TINY_TEXT_VS_IMG:
            cc.text_spans.clear()
            cc.text_bbox = None
            cc.alignment_text = None
            cc.is_formula_like = False  # формулы в чисто картинковой ячейке обычно нет

    # если вектора есть, но их площадь мизерная относительно ячейки — считаем шумом
    if cc.vector_bbox and (bbox_area(cc.vector_bbox) / max(1.0, cell_area) < 0.02):
        cc.vectors.clear()
        cc.vector_bbox = None
        cc.alignment_vector = None

    return cc


# ==========================
# ФОРМАТИРОВАНИЕ ЛОГА ПО ЯЧЕЙКЕ
# ==========================

def _abbr_align(a: Optional[object]) -> Optional[str]:
    """
    Вернуть краткую метку выравнивания вида H/V => L|C|R / T|M|B.
    Поддерживает и dataclass Alignment, и dict {"horizontal":..., "vertical":...}.
    """
    if not a:
        return None

    # вытащить h/v из объекта или словаря
    if isinstance(a, dict):
        h = a.get("horizontal")
        v = a.get("vertical")
    else:
        h = getattr(a, "horizontal", None)
        v = getattr(a, "vertical", None)

    if not h or not v:
        return None

    h_abbr = {"left": "L", "center": "C", "right": "R"}.get(h, "?")
    v_abbr = {"top": "T", "middle": "M", "bottom": "B"}.get(v, "?")
    return f"{h_abbr}/{v_abbr}"

def _cell_brief(cell_info: Dict, r: int, c: int) -> str:
    """
    Краткий отчёт по ячейке:
    [r,c] T=C/M I=L/T V=R/B {F}
    Отсутствующий тип не выводим.
    """
    parts = [f"[{r},{c}]"]

    t = _abbr_align(cell_info.get("alignment_text"))
    i = _abbr_align(cell_info.get("alignment_image"))
    v = _abbr_align(cell_info.get("alignment_vector"))

    if t: parts.append(f"T={t}")
    if i: parts.append(f"I={i}")
    if v: parts.append(f"V={v}")
    if cell_info.get("is_formula_like"):
        parts.append("{F}")

    if len(parts) == 1:
        parts.append("—")

    return " ".join(parts)


# ==========================
# ОСНОВНАЯ ФУНКЦИЯ
# ==========================

def check_tables(pdf_path, pdf_document, start_page=2, tol_px=2.0, cell_padding=1.5):
    """
    Возвращает:
      - user_summary
      - admin_details: теперь включает сводку по каждой таблице и по всем её ячейкам  # <<< NEW
      - table_bboxes_by_page
      - cell_analysis_by_page
    """
    admin_lines = []
    error_pages = set()
    table_bboxes_by_page = {}
    cell_analysis_by_page = {}

    total_pages = len(pdf_document)

    # ---- Этап 1: эвристика страниц с таблицами (pdfplumber)
    t0 = time.perf_counter()
    plumber_table_pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                if page_idx < start_page:
                    continue
                hlines = [l for l in page.lines if abs(l['y0'] - l['y1']) < 1]
                vlines = [l for l in page.lines if abs(l['x0'] - l['x1']) < 1]
                rects = page.rects
                curves = page.curves
                total_graphics = len(hlines) + len(vlines) + len(rects) + len(curves)
                if total_graphics >= 10:
                    plumber_table_pages.append(page_idx)
                    admin_lines.append(
                        f"[pdfplumber][Стр. {page_idx}] Графических объектов: {total_graphics} "
                        f"(гор.:{len(hlines)}, верт.:{len(vlines)}, прям.:{len(rects)}, крив.:{len(curves)})"
                    )
    except Exception as e:
        admin_lines.append(f"[pdfplumber] Ошибка: {e}")
    admin_lines.append(f"[pdfplumber] Найдено {len(plumber_table_pages)} страниц с таблицами за {time.perf_counter()-t0:.2f} сек.")

    # ---- Этап 2: Camelot — извлечение таблиц, проверка полей/центровки
    t1 = time.perf_counter()
    camelot_tables_count = 0
    valid_pages = []
    if plumber_table_pages:
        valid_pages = [p for p in plumber_table_pages if 1 <= p <= total_pages]
        if valid_pages:
            try:
                tables = camelot.read_pdf(
                    pdf_path,
                    flavor="lattice",
                    pages=",".join(map(str, valid_pages)),
                    line_scale=40,       # склеивает мелкие внутренние линии
                    shift_text=["l", "t"],
                    strip_text="\n",
                )
                camelot_tables_count = len(tables)
                for t in tables:
                    page_num = int(t.page)
                    page = pdf_document[page_num - 1]
                    page_width, page_height = page.rect.width, page.rect.height
    
                    # bbox таблицы: miner -> fitz
                    x0_m, y0_m, x1_m, y1_m = t._bbox
                    tbl_rect = camelot_table_bbox_to_fitz(x0_m, y0_m, x1_m, y1_m, page_height)
    
                    # копим bbox (сохраним сразу в fitz-координатах; при желании добавь и miner)
                    table_bboxes_by_page.setdefault(page_num, []).append(
                        (float(tbl_rect.x0), float(tbl_rect.y0), float(tbl_rect.x1), float(tbl_rect.y1))
                    )
    
                    # проверки
                    errors = []
                    if (tbl_rect.x0 < LEFT_MARGIN_PT - TOLERANCE_PT or
                        tbl_rect.x1 > page_width - RIGHT_MARGIN_PT + TOLERANCE_PT or
                        tbl_rect.y0 < TOP_MARGIN_PT - TOLERANCE_PT or
                        tbl_rect.y1 > page_height - BOTTOM_MARGIN_PT + TOLERANCE_PT):
                        errors.append("Таблица выходит за пределы полей")
    
                    work_w = page_width - LEFT_MARGIN_PT - RIGHT_MARGIN_PT
                    work_cx = LEFT_MARGIN_PT + work_w / 2
                    tbl_cx = (tbl_rect.x0 + tbl_rect.x1) / 2
                    if abs(tbl_cx - work_cx) > 2:
                        errors.append("Таблица не по центру относительно полей")
    
                    # лог
                    if errors:
                        admin_lines.append(
                            f"[Camelot][Стр. {page_num}] bbox={t._bbox} | "
                            f"fitz={tbl_rect.x0:.1f},{tbl_rect.y0:.1f},{tbl_rect.x1:.1f},{tbl_rect.y1:.1f} | " +
                            "; ".join(errors)
                        )
                        error_pages.add(page_num)
                    else:
                        admin_lines.append(
                            f"[Camelot][Стр. {page_num}] bbox={t._bbox} | "
                            f"fitz={tbl_rect.x0:.1f},{tbl_rect.y0:.1f},{tbl_rect.x1:.1f},{tbl_rect.y1:.1f} | ✅Таблица корректно расположена"
                        )
            except Exception as e:
                admin_lines.append(f"[Camelot] Ошибка: {e}")
    admin_lines.append(f"[Camelot] Обработано {camelot_tables_count} таблиц за {time.perf_counter()-t1:.2f} сек.")

    # ---- Этап 3: Анализ содержимого ячеек и выравнивания (PyMuPDF)
    t2 = time.perf_counter()
    if camelot_tables_count:
        try:
            # повторно читаем те же страницы (быстро), чтобы пройтись по ячейкам и залогировать
            tables = camelot.read_pdf(
                pdf_path,
                flavor="lattice",
                pages=",".join(map(str, valid_pages)),
                line_scale=40,
                shift_text=["l", "t"],
                strip_text="\n",
            )

            per_page_table_counter: Dict[int, int] = {}  # для нумерации таблиц на странице

            for t in tables:
                page_num = int(t.page)
                per_page_table_counter[page_num] = per_page_table_counter.get(page_num, 0) + 1
                tbl_idx = per_page_table_counter[page_num]  # 1..N на странице

                page = pdf_document[page_num - 1]
                page_height = page.rect.height

                rows = len(t.cells)
                cols = len(t.cells[0]) if rows and len(t.cells[0]) else 0
                if rows == 0 or cols == 0:
                    admin_lines.append(f"[Cells][Стр. {page_num}][Табл. {tbl_idx}] Пустая таблица — пропуск")
                    continue
                page_tables = cell_analysis_by_page.setdefault(page_num, [])

                table_report = {
                    "shape": (rows, cols),
                    "cells": []
                }

                # <<< NEW: заголовок отчёта по таблице >>>
                admin_lines.append(f"[Cells][Стр. {page_num}][Табл. {tbl_idx}] Размер: {rows}×{cols}")

                for r in range(rows):
                    row_cells = []
                    row_briefs = []  # <<< NEW: аккумулируем строку отчёта
                    for c in range(cols):
                        cell = t.cells[r][c]
                        cell_rect = camelot_to_fitz_bbox(cell, page_height)

                        content = extract_cell_content(
                            page, cell_rect, tol_px=tol_px, padding=cell_padding
                        )

                        cell_info = {
                            "cell_bbox": (cell_rect.x0, cell_rect.y0, cell_rect.x1, cell_rect.y1),
                            "has_text": bool(content.text_bbox),
                            "has_images": bool(content.image_bbox),
                            "has_vectors": bool(content.vector_bbox),
                            "is_formula_like": content.is_formula_like,
                            "alignment_text": (
                                None if not content.alignment_text else {
                                    "horizontal": content.alignment_text.horizontal,
                                    "vertical": content.alignment_text.vertical,
                                    "gaps": content.alignment_text.gaps
                                }
                            ),
                            "alignment_image": (
                                None if not content.alignment_image else {
                                    "horizontal": content.alignment_image.horizontal,
                                    "vertical": content.alignment_image.vertical,
                                    "gaps": content.alignment_image.gaps
                                }
                            ),
                            "alignment_vector": (
                                None if not content.alignment_vector else {
                                    "horizontal": content.alignment_vector.horizontal,
                                    "vertical": content.alignment_vector.vertical,
                                    "gaps": content.alignment_vector.gaps
                                }
                            ),
                        }
                        row_cells.append(cell_info)

                        # <<< NEW: краткая метка ячейки в лог >>>
                        row_briefs.append(_cell_brief(cell_info, r, c))

                    table_report["cells"].append(row_cells)
                    # <<< NEW: строка с ячейками >>>
                    admin_lines.append("  " + " | ".join(row_briefs))

                page_tables.append(table_report)

            admin_lines.append(f"[Cells] Анализ содержимого ячеек выполнен за {time.perf_counter()-t2:.2f} сек.")
        except Exception as e:
            admin_lines.append(f"[Cells] Ошибка анализа содержимого ячеек: {e}")
    else:
        admin_lines.append("[Cells] Таблиц не обнаружено — анализ ячеек пропущен.")

    # ---- Итог
    user_summary = (f"⚠️ Проверка таблиц: нарушения на страницах {', '.join(map(str, sorted(error_pages)))}"
                    if error_pages else "✅ Проверка таблиц: нарушений не найдено")

    return {
        "user_summary": user_summary,
        "admin_details": "\n".join(admin_lines),
        "table_bboxes_by_page": table_bboxes_by_page,
        "cell_analysis_by_page": cell_analysis_by_page,
    }
