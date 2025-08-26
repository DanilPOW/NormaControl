# scripts/pdf_table_checker.py
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

import camelot
import pdfplumber
import fitz  # PyMuPDF

# Константы макета страницы
LEFT_MARGIN_PT   = 3 * 28.35
RIGHT_MARGIN_PT  = 1.5 * 28.35
TOP_MARGIN_PT    = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT     = 2
MM_TO_PT = 2.8346456693  # 1 мм = 2.8346 pt


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


@dataclass
class Alignment:
    horizontal: str  # "left"|"center"|"right"|"mixed"
    vertical: str    # "top"|"middle"|"bottom"|"mixed"
    gaps: Dict[str, float]


@dataclass
class CellContent:
    text_spans: List[Dict] = field(default_factory=list)   # [{"text":..., "bbox":BBox, "font":..., "size":..., "color_rgb":(r,g,b)}, ...]
    images: List[BBox] = field(default_factory=list)       # bbox картинок
    vectors: List[BBox] = field(default_factory=list)      # bbox векторных объектов
    text_bbox: Optional[BBox] = None
    image_bbox: Optional[BBox] = None
    vector_bbox: Optional[BBox] = None
    alignment_text: Optional[Alignment] = None
    alignment_image: Optional[Alignment] = None
    alignment_vector: Optional[Alignment] = None
    is_formula_like: bool = False                          # эвристика «формульной» ячейки
    font_report: Optional[Dict] = None                     # {"max_size": float, "all_times_new_roman": bool, "all_black": bool, "violations": [...]}


def _unique_sorted(vals, eps=1.0):
    """Кластеризуем координаты с допуском eps (pt), возвращаем отсортированные уникальные."""
    vals = sorted(vals)
    out = []
    for v in vals:
        if not out or abs(v - out[-1]) > eps:
            out.append(v)
    return out


def build_logical_grid(table, page_height, min_frac=0.30, eps=1.0):
    # 1) все границы в miner-координатах
    xs, ys = [], []
    for row in table.cells:
        for cell in row:
            xs.extend([cell.x1, cell.x2])
            ys.extend([cell.y1, cell.y2])

    xs = _unique_sorted(xs, eps=eps)
    ys = _unique_sorted(ys, eps=eps)

    # 2) интервалы (miner: y растёт вверх)
    col_widths = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    row_heights = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]

    # медианы
    import statistics as st
    med_w = st.median(col_widths) if col_widths else 0
    med_h = st.median(row_heights) if row_heights else 0

    # 3) отбрасываем слишком тонкие интервалы
    min_w = med_w * min_frac
    min_h = med_h * min_frac

    keep_x = [xs[0]] if xs else []
    for i, w in enumerate(col_widths):
        if w >= min_w:
            keep_x.append(xs[i + 1])

    keep_y = [ys[0]] if ys else []
    for i, h in enumerate(row_heights):
        if h >= min_h:
            keep_y.append(ys[i + 1])

    # 4) miner -> fitz (y вниз)
    def miner_to_fitz_y(y_m): return float(page_height - y_m)
    keep_y_fitz = [miner_to_fitz_y(y) for y in keep_y]
    keep_y_fitz = keep_y_fitz[::-1]  # сверху вниз

    keep_x_fitz = [float(x) for x in keep_x]

    return keep_x_fitz, keep_y_fitz


def bbox_union(b1: BBox, b2: BBox) -> BBox:
    return BBox(
        x0=min(b1.x0, b2.x0),
        y0=min(b1.y0, b2.y0),
        x1=max(b1.x1, b2.x1),
        y1=max(b1.y1, b2.y1),
    )


def is_times_new_roman_name(font_name: str) -> bool:
    """
    Учитываем популярные варианты имён: TimesNewRomanPSMT, TimesNewRomanPS-BoldMT,
    Times-Roman, TimesNewRoman, TNR и т.п.
    При желании можно ужесточить (исключить Bold/Italic и т.д.).
    """
    if not font_name:
        return False
    base = font_name.split('+', 1)[-1]  # отрезаем префикс сабсета
    f = base.replace(" ", "").lower()
    return ("timesnewroman" in f) or ("times-roman" in f) or ("timesnewromanps" in f) or (f == "tnr") or ("times" in f)


def _normalize_color_to_rgb255(c) -> Tuple[int, int, int]:
    """
    PyMuPDF span['color'] встречается в разных видах:
    - int/float (серый) 0..255 или 0..1
    - tuple/list из 3 чисел (0..1 или 0..255)
    - строка '#RRGGBB'
    Вернём (R,G,B) в диапазоне 0..255.
    """
    def clamp255(x):
        try:
            return max(0, min(255, int(round(x))))
        except Exception:
            return 0

    if isinstance(c, str) and c.startswith("#") and len(c) == 7:
        r = int(c[1:3], 16); g = int(c[3:5], 16); b = int(c[5:7], 16)
        return (r, g, b)

    if isinstance(c, (list, tuple)) and len(c) == 3:
        if all(isinstance(x, (int, float)) for x in c):
            if max(c) <= 1.0:  # 0..1
                return (clamp255(c[0] * 255), clamp255(c[1] * 255), clamp255(c[2] * 255))
            return (clamp255(c[0]), clamp255(c[1]), clamp255(c[2]))

    if isinstance(c, (int, float)):
        if c <= 1.0:
            v = clamp255(c * 255)
        else:
            v = clamp255(c)
        return (v, v, v)

    return (0, 0, 0)


def is_black_rgb(rgb: Tuple[int, int, int], tol: int = 6) -> bool:
    """
    «Чёрный» с небольшим допуском (на случай антиалиасинга / смешения).
    tol=6 означает, что (0..6) считается чёрным.
    """
    r, g, b = rgb
    return r <= tol and g <= tol and b <= tol


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


def fitz_rect_to_bbox(r: fitz.Rect) -> BBox:
    return BBox(r.x0, r.y0, r.x1, r.y1)


def rect_reasonably_inside(elem: BBox, container: BBox, min_cover_ratio: float) -> bool:
    """
    Элемент принадлежит контейнеру, если площадь пересечения покрывает
    >= min_cover_ratio площади самого элемента (а не по центру).
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
    # входные coords из t._bbox — в miner-системе
    return BBox(float(x0), float(page_height - y1), float(x1), float(page_height - y0))


def _lines_in_inner(page: fitz.Page, inner: BBox, min_cover=0.5) -> List[BBox]:
    """Вернуть bbox строк, лежащих в пределах inner не менее чем на min_cover их площади."""
    lines_boxes: List[BBox] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            xs, ys = [], []
            for span in line.get("spans", []):
                x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                sb = BBox(x0, y0, x1, y1)
                inter_area, inter = bbox_intersection(sb, inner)
                if inter_area > 0 and inter_area / max(1.0, bbox_area(sb)) >= min_cover:
                    xs.extend([inter.x0, inter.x1]); ys.extend([inter.y0, inter.y1])
            if xs and ys:
                lines_boxes.append(BBox(min(xs), min(ys), max(xs), max(ys)))
    return lines_boxes


def _decide_halign_strict_center(inner: BBox,
                                 line_boxes: List[BBox],
                                 tol_px: float,
                                 ignore_last_line: bool = True) -> str:
    """
    Центр даём только при:
      - |cell_mid - content_mid| <= tau_sym
      - |Lm - Rm| <= tau_sym
      - min(Lm,Rm) >= tau_air   (есть «воздух», не микро-зазоры)
    Если заполнение ширины высокое (fills) — центр только при L≈R, иначе left/right.
    """
    if not line_boxes:
        return "mixed"

    # 1) игнорируем последнюю (часто короткую) строку — уменьшает ложный центр
    lines = list(line_boxes)
    if ignore_last_line and len(lines) >= 2:
        bottom_idx = max(range(len(lines)), key=lambda i: lines[i].y1)  # PyMuPDF: y вниз
        lines.pop(bottom_idx)
    if not lines:
        lines = line_boxes

    # 2) медианные зазоры по строкам
    import statistics as st
    L = [max(0.0, lb.x0 - inner.x0) for lb in lines]
    R = [max(0.0, inner.x1 - lb.x1) for lb in lines]
    Lm = st.median(L); Rm = st.median(R)

    # 3) центр и ширина контента
    content_x0 = min(lb.x0 for lb in lines)
    content_x1 = max(lb.x1 for lb in lines)
    content_mid = 0.5 * (content_x0 + content_x1)
    cell_mid    = 0.5 * (inner.x0 + inner.x1)
    center_gap  = abs(cell_mid - content_mid)

    inner_w   = max(1.0, inner.width())
    content_w = content_x1 - content_x0
    fills_ratio = content_w / inner_w
    fills = fills_ratio >= 0.965

    # 4) пороги
    tau_sym  = max(0.45 * tol_px, 0.01 * inner_w)
    tau_air  = 1.8  * tol_px
    tau_bias = 0.60 * tol_px

    if (center_gap <= tau_sym) and (abs(Lm - Rm) <= tau_sym) and (min(Lm, Rm) >= tau_air):
        return "center"

    if fills:
        if abs(Lm - Rm) <= tau_sym:
            return "center"
        return "left" if Lm < Rm else "right"

    if Lm + tau_bias < Rm:
        return "left"
    if Rm + tau_bias < Lm:
        return "right"

    return "left"


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

    # Центры
    cell_mid_x = (cx0 + cx1) / 2.0
    cell_mid_y = (cy0 + cy1) / 2.0
    text_mid_x = (tx0 + tx1) / 2.0
    text_mid_y = (ty0 + ty1) / 2.0

    center_gap = abs(cell_mid_x - text_mid_x)
    middle_gap = abs(cell_mid_y - text_mid_y)

    # Базовое решение по осям
    if center_gap <= tol_px:
        h = "center"
    elif left_gap <= right_gap:
        h = "left"
    else:
        h = "right"

    if middle_gap <= tol_px:
        v = "middle"
    elif top_gap <= bottom_gap:
        v = "top"
    else:
        v = "bottom"

    centered_ok = (center_gap <= tol_px) and (middle_gap <= tol_px)

    gaps = {
        "left": left_gap, "right": right_gap,
        "top": top_gap, "bottom": bottom_gap,
        "center_gap": center_gap, "middle_gap": middle_gap,
        "centered_ok": centered_ok,
    }
    if not centered_ok:
        gaps["note"] = "Элемент в ячейке должен быть в центре ячейки"

    inner_w   = (cell.x1 - cell.x0) - 2 * padding
    content_w = content.width()
    if inner_w > 0 and content_w >= 0.94 * inner_w and min(left_gap, right_gap) <= 0.5 * tol_px:
        h = "left" if left_gap <= right_gap else "right"

    return Alignment(horizontal=h, vertical=v, gaps=gaps)


def looks_like_formula(text: str) -> bool:
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


def extract_cell_content(page: fitz.Page, cell_rect: BBox,
                         tol_px: float = 2.0, padding: float = 1.5) -> CellContent:
    cc = CellContent()

    # Внутренности ячейки (не трогаем границы)
    inner = bbox_inset(cell_rect, padding)

    # Пороги
    MIN_TEXT_COVER = 0.50   # ≥50% спана лежит в ячейке
    MIN_IMG_COVER  = 0.70   # ≥70% картинки лежит в ячейке
    MIN_VEC_COVER  = 0.70   # ≥70% вектора лежит в ячейке
    MIN_AREA_PT2   = 8.0 * 8.0  # минимальная площадь содержимого
    TINY_TEXT_VS_IMG = 0.15      # относительный порог для «крошечного» текста в картинковой ячейке

    text_dict = page.get_text("dict")

    # -------- ТЕКСТ --------
    span_boxes: List[BBox] = []
    span_texts: List[str] = []
    span_fonts: List[str] = []
    span_sizes: List[float] = []
    span_colors: List[Tuple[int, int, int]] = []

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
                    if not t:
                        continue
                    fnt  = span.get("font", "")           # PostScript name
                    sz   = float(span.get("size", 0.0))   # pt
                    raw_col = span.get("color", span.get("fill", 0))
                    colv = _normalize_color_to_rgb255(raw_col)

                    span_boxes.append(sb)
                    span_texts.append(t)
                    span_fonts.append(fnt)
                    span_sizes.append(sz)
                    span_colors.append(colv)

    if span_boxes:
        # общий bbox текста
        tb = None
        for box in span_boxes:
            tb = box if tb is None else bbox_union(tb, box)

        cc.text_spans = [{
            "text": t,
            "bbox": b,
            "font": f,
            "size": s,
            "color_rgb": c
        } for t, b, f, s, c in zip(span_texts, span_boxes, span_fonts, span_sizes, span_colors)]
        cc.text_bbox  = tb

        # вертикаль — по общему bbox
        base_align = classify_alignment(cell_rect, tb, tol_px=tol_px, padding=padding)
        line_boxes = _lines_in_inner(page, inner, min_cover=0.5)
        h_align = _decide_halign_strict_center(inner, line_boxes, tol_px=tol_px, ignore_last_line=True)

        # вычислим центры и построчные медианы — для логов:
        import statistics as st
        if line_boxes:
            L = [max(0.0, lb.x0 - inner.x0) for lb in line_boxes]
            R = [max(0.0, inner.x1 - lb.x1) for lb in line_boxes]
            Lm = st.median(L); Rm = st.median(R)
            content_x0 = min(lb.x0 for lb in line_boxes)
            content_x1 = max(lb.x1 for lb in line_boxes)
            content_mid = 0.5 * (content_x0 + content_x1)
            cell_mid    = 0.5 * (inner.x0 + inner.x1)
            center_gap  = abs(cell_mid - content_mid)
            inner_w     = max(1.0, inner.width())
            fills_ratio = (content_x1 - content_x0) / inner_w
        else:
            Lm = Rm = center_gap = fills_ratio = 0.0
            cell_mid = 0.5 * (inner.x0 + inner.x1)
            content_mid = 0.5 * (tb.x0 + tb.x1)  # fallback по общему bbox

        # базовые зазоры по общему bbox (уже есть в base_align.gaps): left/right/top/bottom
        gaps = dict(base_align.gaps)
        gaps.update({
            "cell_mid_x":    cell_mid,
            "text_mid_x":    content_mid,
            "center_gap_x":  center_gap,
            "median_L":      Lm,
            "median_R":      Rm,
            "fills_ratio":   fills_ratio,
        })

        cc.alignment_text = Alignment(
            horizontal=h_align,
            vertical=base_align.vertical,
            gaps=gaps,
        )

        cc.is_formula_like = looks_like_formula(" ".join(span_texts))

        # ---------- ПРОВЕРКИ ШРИФТА ----------
        MAX_PT = 14.0
        font_violations = []
        all_tnr   = True
        all_black = True
        max_size  = 0.0

        for t, f, s, c in zip(span_texts, span_fonts, span_sizes, span_colors):
            max_size = max(max_size, s)
            if not is_times_new_roman_name(f):
                all_tnr = False
                font_violations.append({"type": "font", "msg": f"Не Times New Roman: {f}", "sample": t[:60], "size": s})
            if s > MAX_PT + 0.1:  # небольшой допуск
                font_violations.append({"type": "size", "msg": f"Размер {s:.1f}pt > 14pt", "sample": t[:60], "font": f})
            if not is_black_rgb(c, tol=6):
                all_black = False
                font_violations.append({"type": "color", "msg": f"Цвет RGB{c} не чёрный", "sample": t[:60], "font": f, "size": s})

        cc.font_report = {
            "max_size": max_size,
            "all_times_new_roman": all_tnr,
            "all_black": all_black,
            "violations": font_violations
        }

    else:
        # текста нет
        cc.alignment_text = None
        cc.is_formula_like = False
        cc.font_report = {"max_size": 0.0, "all_times_new_roman": True, "all_black": True, "violations": []}

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
                        xs.append(pts[k]); ys.append(pts[k + 1])
            if xs and ys:
                vb = BBox(min(xs), min(ys), max(xs), max(ys))
                if bbox_area(vb) < MIN_AREA_PT2:
                    continue
                # внутри и не касается границ ячейки — иначе это рамка/решётка
                if rect_reasonably_inside(vb, inner, MIN_VEC_COVER) and not touches_cell_border(vb, cell_rect, tol=padding + 0.5):
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

    # -------- Конфликтология (снижаем ложные срабатывания) --------
    cell_area = bbox_area(inner)

    if cc.image_bbox:
        img_area = bbox_area(cc.image_bbox)
        txt_area = bbox_area(cc.text_bbox) if cc.text_bbox else 0.0
        # если картинка «доминирует», а текст крошечный — гасим текст
        if img_area > 0 and (txt_area / img_area) <= TINY_TEXT_VS_IMG:
            cc.text_spans.clear()
            cc.text_bbox = None
            cc.alignment_text = None
            cc.is_formula_like = False
            # При этом отчёт по шрфту можно оставить как есть — он отражает реальные спаны,
            # но если нужно, можно обнулить:
            # cc.font_report = {"max_size": 0.0, "all_times_new_roman": True, "all_black": True, "violations": []}

    # если вектора есть, но их площадь мизерная относительно ячейки — считаем шумом
    if cc.vector_bbox and (bbox_area(cc.vector_bbox) / max(1.0, cell_area) < 0.02):
        cc.vectors.clear()
        cc.vector_bbox = None
        cc.alignment_vector = None

    return cc


def _abbr_align(a: Optional[object]) -> Optional[str]:
    """
    Вернуть краткую метку выравнивания вида H/V => L|C|R / T|M|B.
    Поддерживает и dataclass Alignment, и dict {"horizontal":..., "vertical":...}.
    """
    if not a:
        return None

    if isinstance(a, dict):
        h = a.get("horizontal")
        v = a.get("vertical")
    else:
        h = getattr(a, "horizontal", None)
        v = getattr(a, "vertical", None)

    if not h or not v:
        return None

    h_abbr = {"left":"L","center":"C","right":"R","mixed":"M","justify":"J"}.get(h, "?")
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


def _add_text_annot_silent(page: fitz.Page, point_xy: Tuple[float, float], msg: str):
    """Тихая точечная текстовая аннотация в стиле скрипта нумерации (без логов)."""
    try:
        ann = page.add_text_annot(fitz.Point(*point_xy), msg)
        ann.set_info(title="Сервис нормоконтроля", content=msg)
        ann.update()
    except Exception:
        pass


def check_tables(pdf_path, pdf_document, start_page=2, tol_mm=2.0, cell_padding=1.5):
    """
    Возвращает:
      - user_summary
      - admin_details: лог по страницам/таблицам и краткие строки по ячейкам
      - table_bboxes_by_page (fitz-координаты)
      - cell_analysis_by_page
    """
    tol_px = float(tol_mm) * MM_TO_PT
    admin_lines = []
    error_pages = set()
    table_bboxes_by_page: Dict[int, List[Tuple[float, float, float, float]]] = {}
    cell_analysis_by_page: Dict[int, List[Dict]] = {}

    total_pages = len(pdf_document)

    # ---- Этап 1: эвристика страниц с таблицами (pdfplumber)
    t0 = time.perf_counter()
    plumber_table_pages: List[int] = []
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
    admin_lines.append(f"[pdfplumber] Найдено {len(plumber_table_pages)} страниц с таблицами за {time.perf_counter() - t0:.2f} сек.")

    # ---- Этап 2: Camelot — извлечение таблиц, проверка полей/центровки и анализ ячеек
    t1 = time.perf_counter()
    camelot_tables_count = 0
    valid_pages: List[int] = []

    if plumber_table_pages:
        valid_pages = [p for p in plumber_table_pages if 1 <= p <= total_pages]
        if valid_pages:
            try:
                tables = camelot.read_pdf(
                    pdf_path,
                    flavor="lattice",
                    pages=",".join(map(str, valid_pages)),
                    line_scale=15,            # мягче склейка, чем 40
                    process_background=False, # не тащим «линии» из фона
                    strip_text="\n",
                )
                camelot_tables_count = len(tables)

                # Счётчик таблиц на каждой странице для красивого лога
                per_page_table_counter: Dict[int, int] = {}

                for t in tables:
                    page_num = int(t.page)
                    per_page_table_counter[page_num] = per_page_table_counter.get(page_num, 0) + 1
                    tbl_idx = per_page_table_counter[page_num]

                    page = pdf_document[page_num - 1]
                    page_width, page_height = page.rect.width, page.rect.height

                    # bbox таблицы: miner -> fitz
                    x0_m, y0_m, x1_m, y1_m = t._bbox
                    tbl_rect = camelot_table_bbox_to_fitz(x0_m, y0_m, x1_m, y1_m, page_height)

                    # копим bbox (в fitz-координаты)
                    table_bboxes_by_page.setdefault(page_num, []).append(
                        (float(tbl_rect.x0), float(tbl_rect.y0), float(tbl_rect.x1), float(tbl_rect.y1))
                    )

                    # проверки на поля/центр
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

                    if errors:
                        admin_lines.append(
                            f"[Camelot][Стр. {page_num}][Табл. {tbl_idx}] bbox(miner)={t._bbox} | "
                            f"fitz={tbl_rect.x0:.1f},{tbl_rect.y0:.1f},{tbl_rect.x1:.1f},{tbl_rect.y1:.1f} | " +
                            "; ".join(errors)
                        )
                        error_pages.add(page_num)

                        # --- Тихая точечная аннотация у верхнего левого угла таблицы ---
                        ann_text = "Ошибки расположения таблицы:\n" + "\n".join(errors)
                        _add_text_annot_silent(page, (tbl_rect.x0, tbl_rect.y0), ann_text)

                    else:
                        admin_lines.append(
                            f"[Camelot][Стр. {page_num}][Табл. {tbl_idx}] bbox(miner)={t._bbox} | "
                            f"fitz={tbl_rect.x0:.1f},{tbl_rect.y0:.1f},{tbl_rect.x1:.1f},{tbl_rect.y1:.1f} | ✅Таблица корректно расположена"
                        )

                    # ---------- логическая сетка (устойчиво к пере-детекту линий)
                    X, Y = build_logical_grid(t, page_height, min_frac=0.30, eps=1.0)
                    rows = max(0, len(Y) - 1)
                    cols = max(0, len(X) - 1)

                    page_tables = cell_analysis_by_page.setdefault(page_num, [])
                    table_report = {"shape": (rows, cols), "cells": []}

                    admin_lines.append(f"[Cells][Стр. {page_num}][Табл. {tbl_idx}] Размер: {rows}×{cols}")

                    # флаг несоблюдения выравнивания в заголовочных ячейках
                    table_has_header_alignment_issue = False
                    header_alignment_errors = []

                    table_briefs = []   # компакт по всем r
                    table_debugs = []   # все Debug по всем r

                    for r in range(rows):
                        row_cells = []
                        row_briefs = []
                        row_debugs = []

                        y_top, y_bot = Y[r], Y[r + 1]
                        for c in range(cols):
                            x_left, x_right = X[c], X[c + 1]
                            cell_rect = BBox(x_left, y_top, x_right, y_bot)

                            content = extract_cell_content(page, cell_rect, tol_px=tol_px, padding=cell_padding)

                            cell_info = {
                                "cell_bbox": (cell_rect.x0, cell_rect.y0, cell_rect.x1, cell_rect.y1),
                                "has_text": bool(content.text_bbox),
                                "has_images": bool(content.image_bbox),
                                "has_vectors": bool(content.vector_bbox),
                                "is_formula_like": content.is_formula_like,
                                "alignment_text": (None if not content.alignment_text else {
                                    "horizontal": content.alignment_text.horizontal,
                                    "vertical": content.alignment_text.vertical,
                                    "gaps": content.alignment_text.gaps
                                }),
                                "alignment_image": (None if not content.alignment_image else {
                                    "horizontal": content.alignment_image.horizontal,
                                    "vertical": content.alignment_image.vertical,
                                    "gaps": content.alignment_image.gaps
                                }),
                                "alignment_vector": (None if not content.alignment_vector else {
                                    "horizontal": content.alignment_vector.horizontal,
                                    "vertical": content.alignment_vector.vertical,
                                    "gaps": content.alignment_vector.gaps
                                }),
                            }

                            # --- FONT REPORT (переносим из content) ---
                            fr = getattr(content, "font_report", None)
                            if fr is None:
                                fr = {"max_size": 0.0, "all_times_new_roman": True, "all_black": True, "violations": []}
                            cell_info["font_report"] = fr

                            # Если есть нарушения — аннотируем левый верх угла ячейки + лог
                            if fr["violations"]:
                                msgs = []
                                for v in fr["violations"][:3]:
                                    msgs.append(f"• {v['msg']}")
                                if len(fr["violations"]) > 3:
                                    msgs.append(f"... и ещё {len(fr['violations']) - 3}")
                                annot_text = "Нарушения шрифта:\n" + "\n".join(msgs)
                                _add_text_annot_silent(page, (cell_rect.x0 + 2, cell_rect.y0 + 2), annot_text)
                                error_pages.add(page_num)

                                row_debugs.append(
                                    f"[Font][{r},{c}] max={fr['max_size']:.1f}pt | TNR={fr['all_times_new_roman']} | BLACK={fr['all_black']} | n={len(fr['violations'])}"
                                )

                            row_cells.append(cell_info)
                            row_briefs.append(_cell_brief(cell_info, r, c))

                            if content.alignment_text:
                                g = content.alignment_text.gaps
                                row_debugs.append(
                                    (f"[Debug][{r},{c}] H={content.alignment_text.horizontal} V={content.alignment_text.vertical} | "
                                     f"cell_mid_x={g.get('cell_mid_x', 0):.2f} text_mid_x={g.get('text_mid_x', 0):.2f} "
                                     f"center_gap_x={g.get('center_gap_x', 0):.2f} ; "
                                     f"cell_mid_y={g.get('cell_mid_y', 0):.2f} text_mid_y={g.get('text_mid_y', 0):.2f} "
                                     f"middle_gap_y={g.get('middle_gap_y', g.get('middle_gap', 0)):.2f} | "
                                     f"Lm={g.get('median_L', 0):.2f} Rm={g.get('median_R', 0):.2f} "
                                     f"fills={g.get('fills_ratio', 0):.3f} | "
                                     f"bbox_gaps L={g.get('left', 0):.2f} R={g.get('right', 0):.2f} "
                                     f"T={g.get('top', 0):.2f} B={g.get('bottom', 0):.2f}")
                                )

                            # Проверки заголовков как раньше...
                            if r == 0 or c == 0:
                                alignment_errors = []
                                if content.alignment_text:
                                    if r == 0 and content.alignment_text.horizontal != "center":
                                        alignment_errors.append(
                                            f"Заголовок столбца [{r},{c}] должен быть по центру (сейчас: {content.alignment_text.horizontal})"
                                        )
                                    if c == 0 and r > 0 and content.alignment_text.horizontal != "left":
                                        alignment_errors.append(
                                            f"Заголовок строки [{r},{c}] должен быть по левому краю (сейчас: {content.alignment_text.horizontal})"
                                        )
                                if content.alignment_image:
                                    if r == 0 and content.alignment_image.horizontal != "center":
                                        alignment_errors.append(f"Изображение в заголовке столбца [{r},{c}] должно быть по центру")
                                    if c == 0 and r > 0 and content.alignment_image.horizontal != "left":
                                        alignment_errors.append(f"Изображение в заголовке строки [{r},{c}] должно быть по левому краю")
                                if alignment_errors:
                                    table_has_header_alignment_issue = True
                                    header_alignment_errors.extend(alignment_errors)

                        # после строки r — копим, но не печатаем
                        table_report["cells"].append(row_cells)
                        table_briefs.append("  " + " | ".join(row_briefs) if row_briefs else "  —")
                        table_debugs.extend(row_debugs)

                    # 1) Вся «сеточка» сразу
                    for line in table_briefs:
                        admin_lines.append(line)

                    # 2) Затем подробные debug-строчки
                    for dbg in table_debugs:
                        admin_lines.append(dbg)

                    page_tables.append(table_report)

                    # Если нашли проблемы с выравниванием заголовков - добавляем аннотацию
                    if table_has_header_alignment_issue:
                        error_text = "Ошибки выравнивания заголовков:\n" + "\n".join(header_alignment_errors[:3])
                        if len(header_alignment_errors) > 3:
                            error_text += f"\n... и ещё {len(header_alignment_errors) - 3} ошибок"
                        _add_text_annot_silent(
                            page,
                            (tbl_rect.x0, tbl_rect.y0),
                            error_text
                        )
                        error_pages.add(page_num)
                        admin_lines.append(f"[Alignment][Стр. {page_num}][Табл. {tbl_idx}] Обнаружены ошибки выравнивания заголовков:")
                        for error in header_alignment_errors:
                            admin_lines.append(f"  {error}")

            except Exception as e:
                admin_lines.append(f"[Camelot] Ошибка: {e}")

    admin_lines.append(f"[Camelot] Обработано {camelot_tables_count} таблиц за {time.perf_counter() - t1:.2f} сек.")

    # ---- Итог
    if error_pages:
        user_summary = "⚠️Проверка таблиц: обнаружены нарушения на стр " + ", ".join(map(str, sorted(error_pages)))
    else:
        user_summary = "✅Проверка таблиц"

    return {
        "user_summary": user_summary,
        "admin_details": "\n".join(admin_lines),
        "table_bboxes_by_page": table_bboxes_by_page,
        "cell_analysis_by_page": cell_analysis_by_page,
    }
