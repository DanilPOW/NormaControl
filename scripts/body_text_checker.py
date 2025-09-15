# scripts/body_text_checker.py
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF

# --- Геометрия страницы / поля ---
MM_TO_PT = 2.8346456693
CM_TO_PT = 28.35

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# --- Нормы основного текста ---
FIRST_LINE_INDENT_PT = 1.25 * CM_TO_PT         # 1.25 см
FIRST_LINE_INDENT_TOL_PT = 4.0                 # допуск ~1.4 мм
LINE_SPACING_TOL = (1.45, 1.62)                # "1.5 строки" с допуском
FONT_MIN_PT = 12.0
FONT_MAX_PT = 14.0

# --- Критерии «это основной абзац» (обновлено) ---
CLOSE_TO_EDGE_TOL_PT = 8.0                     # «рядом с полем»
MIN_BODY_FILL_RATIO = 0.60                     # медианная ширина строк ≥ 60% рабочей области
MIN_LEFT_COHESION_FRAC = 0.70                  # доля строк, совпадающих с локальным левым краем
MAX_MED_RIGHT_AIR_PT = 24.0                    # допустимый медианный правый «воздух» для ragged-right (~8 мм)

# --- эвристики исключений по тексту ---
CAPTION_PREFIXES = ("рисунок", "таблица", "продолжение таблицы", "примечание", "схема")
HEADING_WORDS = ("раздел", "глава", "введение", "заключение", "список литературы", "содержание", "приложение")
LIST_BULLETS = ("•", "–", "-", "—", "∙", "·", "●")

# --- лимиты логов ---
MAX_REASONS_PER_PAR = 6
MAX_FONT_ISSUES_PER_PAR = 6

@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]

@dataclass
class Paragraph:
    page_index0: int
    lines: List[Line]

# ---------- Утилиты ----------
def _median(values: List[float]) -> float:
    if not values: return 0.0
    vals = sorted(values)
    n = len(vals)
    m = n // 2
    return vals[m] if n % 2 else (vals[m-1] + vals[m]) / 2.0

def _font_base_name(font_name: str) -> str:
    if not font_name:
        return ""
    return font_name.split("+", 1)[-1]

def _is_times_family(font_name: str) -> bool:
    f = _font_base_name(font_name).replace(" ", "").lower()
    return ("timesnewroman" in f) or ("times-roman" in f) or ("timesnewromanps" in f) or (f in {"tnr","timesroman","times"})

def _has_bold(font_name: str) -> bool:
    f = _font_base_name(font_name).lower()
    return any(k in f for k in ("bold","black","heavy","demibold","semibold"))

def _has_italic(font_name: str) -> bool:
    f = _font_base_name(font_name).lower()
    return ("italic" in f) or ("oblique" in f)

# ---------- Эвристики исключений ----------
def _looks_like_formula(text: str) -> bool:
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
    if "_" in t or "^" in t: score += 2
    if sum(ch.isdigit() for ch in t) >= 3: score += 1
    return score >= 4

def _is_caption_like(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(t.startswith(pref + " ") for pref in CAPTION_PREFIXES):
        return True
    if re.match(r"^(таблица|рисунок)\s+[A-Za-zА-Яа-я]?\s*\d", t):
        return True
    return False

def _is_heading_like(text: str, avg_size: float) -> bool:
    t = (text or "").strip()
    low = t.lower()
    if any(low.startswith(h) for h in HEADING_WORDS):
        return True
    letters = "".join(ch for ch in t if ch.isalpha())
    if letters and letters.upper() == letters and len(letters) >= 3:
        return True
    if avg_size > (FONT_MAX_PT + 1.0):
        return True
    return False

def _is_list_like(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if any(t.startswith(b + " ") for b in LIST_BULLETS):
        return True
    if re.match(r"^\(?\d+(\.\d+)*[\.\)]\s+", t):
        return True
    return False

# ---------- Геометрия / линии ----------
def _collect_text_lines(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            xs, ys, spans, texts, sizes, fonts = [], [], [], [], [], []
            for sp in line.get("spans", []):
                x0, y0, x1, y1 = sp.get("bbox", (0,0,0,0))
                t = (sp.get("text") or "")
                if t.strip():
                    xs += [x0, x1]; ys += [y0, y1]
                    sizes.append(float(sp.get("size", 0)))
                    fonts.append(sp.get("font", ""))
                    spans.append(sp); texts.append(t)
            if not xs:
                continue
            text = "".join(texts).strip()
            if not text:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            out.append(Line(text=text, bbox=rect, size=size, font=font, spans=spans))
    out.sort(key=lambda L: (L.bbox.y0, L.bbox.x0))
    return out

# --- Пересечение с исключаемыми bbox: IoU + пороги по осям ---
def _rect_iou(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = fitz.Rect(max(a.x0,b.x0), max(a.y0,b.y0), min(a.x1,b.x1), min(a.y1,b.y1))
    if inter.is_empty: return 0.0
    inter_area = max(0.0, inter.x1 - inter.x0) * max(0.0, inter.y1 - inter.y0)
    if inter_area <= 0: return 0.0
    area_a = max(1.0, (a.x1 - a.x0) * (a.y1 - a.y0))
    area_b = max(1.0, (b.x1 - b.x0) * (b.y1 - b.y0))
    return inter_area / (area_a + area_b - inter_area)

def _line_intersects_any(
    b: fitz.Rect,
    boxes: List[Tuple[float,float,float,float]],
    *,
    min_cover_ratio: float = 0.30,
    min_iou: float = 0.18,
    min_vert_cover: float = 0.45,
    min_horiz_cover: float = 0.25,
) -> bool:
    if not boxes: return False
    bw = max(1.0, b.x1 - b.x0)
    bh = max(1.0, b.y1 - b.y0)
    b_area = bw * bh
    for (x0,y0,x1,y1) in boxes:
        bb = fitz.Rect(x0,y0,x1,y1)
        inter = fitz.Rect(max(b.x0,x0), max(b.y0,y0), min(b.x1,x1), min(b.y1,y1))
        if inter.is_empty: 
            continue
        inter_w = max(0.0, inter.x1 - inter.x0)
        inter_h = max(0.0, inter.y1 - inter.y0)
        inter_area = inter_w * inter_h
        cover_ratio = inter_area / b_area
        vert_cover = inter_h / bh
        horiz_cover = inter_w / bw
        iou = _rect_iou(b, bb)
        if (cover_ratio >= min_cover_ratio) or (iou >= min_iou) or (vert_cover >= min_vert_cover and horiz_cover >= min_horiz_cover):
            return True
    return False

# --- Группировка в абзацы + постсклейка ---
def _group_lines_into_paragraphs(lines: List[Line], y_gap_break_ratio=1.75) -> List[Paragraph]:
    rough: List[List[Line]] = []
    cur: List[Line] = []
    for ln in lines:
        if not cur:
            cur = [ln]; continue
        prev = cur[-1]
        dy = ln.bbox.y0 - prev.bbox.y0
        avg_sz = max(1.0, (ln.size + prev.size) / 2.0)
        if dy > y_gap_break_ratio * avg_sz:
            rough.append(cur); cur = [ln]
        else:
            cur.append(ln)
    if cur: rough.append(cur)

    # постсклейка блоков с близким левым краем и малым вертикальным зазором
    merged: List[List[Line]] = []
    i = 0
    while i < len(rough):
        block = rough[i]
        j = i + 1
        while j < len(rough):
            a_last = block[-1]
            b_first = rough[j][0]
            dy = b_first.bbox.y0 - a_last.bbox.y0
            avg_sz = max(1.0, (a_last.size + b_first.size) / 2.0)
            a_left = _median([ln.bbox.x0 for ln in block])
            b_left = _median([ln.bbox.x0 for ln in rough[j]])
            left_ok = abs(a_left - b_left) <= 6.0 or abs((block[0].bbox.x0) - b_left) <= (FIRST_LINE_INDENT_PT + FIRST_LINE_INDENT_TOL_PT + 6.0)
            if dy <= 1.4 * avg_sz and left_ok:
                block = block + rough[j]
                j += 1
            else:
                break
        merged.append(block)
        i = j

    return [Paragraph(page_index0=-1, lines=p) for p in merged]

def _measure_line_spacing_ratio(lines: List[Line]) -> Optional[float]:
    if len(lines) < 2: return None
    d, s = [], []
    for i in range(1, len(lines)):
        d.append(lines[i].bbox.y0 - lines[i-1].bbox.y0)
        s.append((lines[i].size + lines[i-1].size) / 2.0)
    if not d or not s: return None
    mean_d = sum(d) / len(d)
    mean_s = max(1.0, sum(s) / len(s))
    return mean_d / mean_s

def _detect_justify(lines: List[Line], work_left: float, work_right: float, tol_pt=4.0) -> str:
    if not lines or len(lines) == 1: return "unknown"
    x1s = [ln.bbox.x1 for ln in lines[:-1]]
    spread = max(x1s) - min(x1s) if x1s else 0.0
    right_air = [max(0.0, work_right - x1) for x1 in x1s]
    if spread <= tol_pt and (sum(ra <= CLOSE_TO_EDGE_TOL_PT for ra in right_air) >= max(1, int(0.7*len(x1s)))):
        return "justify"
    x0s = [ln.bbox.x0 for ln in lines[:-1]]
    left_spread = max(x0s) - min(x0s) if x0s else 0.0
    if left_spread <= tol_pt and spread <= 2.5 * tol_pt:
        return "center/right"
    return "left"

def _add_text_annot_silent(page: fitz.Page, point_xy: Tuple[float, float], msg: str):
    try:
        ann = page.add_text_annot(fitz.Point(*point_xy), msg)
        ann.set_info(title="Сервис нормоконтроля", content=msg)
        ann.update()
    except Exception:
        pass

# ---------- «Это основной абзац» + причины отказа ----------
def _is_body_paragraph_with_reasons(par: Paragraph, page: fitz.Page) -> Tuple[bool, List[str], Dict[str, float]]:
    """
    Возвращает: (is_body, reasons[], metrics{})
    reasons — почему НЕ прошёл (пусто, если прошёл).
    """
    reasons: List[str] = []
    lines = par.lines
    metrics: Dict[str, float] = {}

    if len(lines) < 2:
        reasons.append("Мало строк в абзаце (<2)")
        return False, reasons, metrics

    work_left  = page.rect.x0 + LEFT_MARGIN_PT
    work_right = page.rect.x1 - RIGHT_MARGIN_PT
    work_w = max(1.0, work_right - work_left)

    # --- ширина по медиане строк (кроме последней) ---
    core = lines[:-1] if len(lines) > 1 else lines
    widths = [(ln.bbox.x1 - ln.bbox.x0) for ln in (core or lines)]
    med_w = _median(widths) if widths else 0.0
    fill_ratio = med_w / work_w
    metrics["med_line_width"] = med_w
    metrics["work_width"] = work_w
    metrics["fill_ratio"] = fill_ratio

    if fill_ratio < MIN_BODY_FILL_RATIO:
        reasons.append(f"Слишком узкий абзац: {fill_ratio*100:.0f}% ширины (нужно ≥ {int(MIN_BODY_FILL_RATIO*100)}%)")

    # --- Локальный левый край абзаца (медиана x0 по строкам 2..N) ---
    body_left = _median([ln.bbox.x0 for ln in lines[1:]]) if len(lines) >= 2 else lines[0].bbox.x0
    metrics["body_left"] = body_left
    metrics["work_left"] = work_left
    metrics["body_left_offset"] = abs(body_left - work_left)

    # --- «Сцепление» строк с локальным левым краем (а не с полем страницы!) ---
    left_cohesion = [abs(ln.bbox.x0 - body_left) <= 6.0 for ln in core]
    left_cohesion_ratio = sum(left_cohesion) / max(1, len(core))
    metrics["left_cohesion_ratio"] = left_cohesion_ratio
    if left_cohesion_ratio < MIN_LEFT_COHESION_FRAC:
        reasons.append(f"Левые края строк внутри абзаца «гуляют»: {left_cohesion_ratio*100:.0f}% совпадений (нужно ≥ {int(MIN_LEFT_COHESION_FRAC*100)}%)")

    # --- Правый край: justify ИЛИ умеренный «воздух» ---
    right_air = [max(0.0, work_right - ln.bbox.x1) for ln in core]
    med_right_air = _median(right_air) if right_air else 999.0
    metrics["med_right_air"] = med_right_air
    align = _detect_justify(lines, work_left, work_right, tol_pt=4.0)
    metrics["align_guess"] = {"left":0,"center/right":1,"justify":2}.get(align, -1)

    if not (align == "justify" or med_right_air <= MAX_MED_RIGHT_AIR_PT):
        reasons.append(
            f"Правый край не justify и слишком «лохматый»: медианный отступ {med_right_air:.1f} pt (> {MAX_MED_RIGHT_AIR_PT:.0f} pt)"
        )

    is_body = (len(reasons) == 0)
    return is_body, reasons[:MAX_REASONS_PER_PAR], metrics

# ---------- Главная функция ----------
def check_body_text(
    pdf_document: fitz.Document,
    * ,
    table_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    figure_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    table_caption_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    start_page: int = 1,
) -> Dict[str, str]:
    """
    Проверяет только основной текст. Исключает:
      - всё, что попадает в bbox таблиц / подписей;
      - подписи/заголовки/списки/формулы по текстовым эвристикам.
    Возвращает user_summary, admin_details и ставит аннотации в PDF.
    """
    admin_lines: List[str] = []
    error_pages = set()
    total_paras = 0
    scanned_paras = 0
    bad_paras = 0

    rejected_details: List[str] = []

    for page_idx0 in range(len(pdf_document)):
        page_num = page_idx0 + 1
        if page_num < start_page:
            continue
        page = pdf_document[page_idx0]

        # собрать исключаемые bbox этой страницы
        ex_boxes: List[Tuple[float,float,float,float]] = []
        for src in (table_bboxes_by_page, figure_caption_bboxes_by_page, table_caption_bboxes_by_page, exclude_bboxes_by_page):
            if src and page_num in src:
                ex_boxes.extend(src.get(page_num, []))

        # собрать строки и отфильтровать
        all_lines = _collect_text_lines(page)
        filtered_lines = []
        for ln in all_lines:
            if _line_intersects_any(ln.bbox, ex_boxes,
                                    min_cover_ratio=0.30,
                                    min_iou=0.18,
                                    min_vert_cover=0.45,
                                    min_horiz_cover=0.25):
                continue
            if _is_caption_like(ln.text):     continue
            if _is_list_like(ln.text):        continue
            if _looks_like_formula(ln.text):  continue
            if _is_heading_like(ln.text, ln.size): continue
            filtered_lines.append(ln)
        if not filtered_lines:
            continue

        # разбить на абзацы
        paras = _group_lines_into_paragraphs(filtered_lines, y_gap_break_ratio=1.75)
        for p in paras:
            p.page_index0 = page_idx0

        total_paras += len(paras)

        # классификация + причины отказа
        body_paras: List[Paragraph] = []
        for par in paras:
            is_body, reasons, metrics = _is_body_paragraph_with_reasons(par, page)
            if is_body:
                body_paras.append(par)
            else:
                sample = par.lines[0].text
                sample = (sample[:120] + "…") if len(sample) > 120 else sample
                par_bbox = fitz.Rect(
                    min(ln.bbox.x0 for ln in par.lines),
                    min(ln.bbox.y0 for ln in par.lines),
                    max(ln.bbox.x1 for ln in par.lines),
                    max(ln.bbox.y1 for ln in par.lines),
                )
                msg = (
                    f"[BodyText-Reject][Стр. {page_idx0+1}] Абзац отвергнут как «основной»: «{sample}»\n"
                    + ("  - " + "\n  - ".join(reasons) if reasons else "  - Нет явных причин (проверьте эвристику)")
                    + "\n  [метрики] fill={:.0f}%, left_cohesion={:.0f}%, medRightAir={:.1f} pt, bodyLeftOffset={:.1f} pt"
                ).format(
                    metrics.get("fill_ratio", 0.0) * 100.0,
                    metrics.get("left_cohesion_ratio", 0.0) * 100.0,
                    metrics.get("med_right_air", -1.0),
                    metrics.get("body_left_offset", -1.0),
                )
                rejected_details.append(msg)
                _add_text_annot_silent(page, (par_bbox.x0, par_bbox.y0),
                                       "Отказ как «основной»:\n" + "\n".join(f"• {r}" for r in reasons))

        scanned_paras += len(body_paras)

        # проверки по каждому подходящему абзацу
        for par in body_paras:
            lines = par.lines
            par_bbox = fitz.Rect(
                min(ln.bbox.x0 for ln in lines),
                min(ln.bbox.y0 for ln in lines),
                max(ln.bbox.x1 for ln in lines),
                max(ln.bbox.y1 for ln in lines),
            )

            # --- шрифт/начертание/кегль ---
            font_issues = []
            for ln in lines:
                for sp in ln.spans:
                    f = sp.get("font", "")
                    s = float(sp.get("size", 0.0))
                    if not _is_times_family(f):
                        font_issues.append(f"Не Times New Roman: {f}")
                    if s < (FONT_MIN_PT - 0.1) or s > (FONT_MAX_PT + 0.1):
                        font_issues.append(f"Размер {s:.1f} pt вне диапазона 12–14 pt")
                    if _has_bold(f):   font_issues.append("Жирное начертание недопустимо")
                    if _has_italic(f): font_issues.append("Курсив недопустим")
                    if bool(sp.get("underline", False)):
                        font_issues.append("Подчёркнутый текст недопустим")

            # --- межстрочный интервал ---
            spacing_issue = None
            ratio = _measure_line_spacing_ratio(lines)
            if ratio is not None:
                lo, hi = LINE_SPACING_TOL
                if not (lo <= ratio <= hi):
                    spacing_issue = f"Межстрочный интервал не 1.5 (получено {ratio:.2f})"

            # --- красная строка ---
            indent_issue = None
            if len(lines) >= 2:
                first_x0 = lines[0].bbox.x0
                body_left_candidates = [ln.bbox.x0 for ln in lines[1:]]
                body_left = _median(body_left_candidates) if body_left_candidates else first_x0
                indent = first_x0 - body_left
                if abs(indent - FIRST_LINE_INDENT_PT) > FIRST_LINE_INDENT_TOL_PT:
                    if indent < (FIRST_LINE_INDENT_PT - FIRST_LINE_INDENT_TOL_PT):
                        indent_issue = f"Нет/маленькая красная строка: {indent:.1f} pt (нужно ~{FIRST_LINE_INDENT_PT:.1f} pt)"
                    else:
                        indent_issue = f"Слишком большая красная строка: {indent:.1f} pt (нужно ~{FIRST_LINE_INDENT_PT:.1f} pt)"

            # --- выравнивание ---
            work_left  = page.rect.x0 + LEFT_MARGIN_PT
            work_right = page.rect.x1 - RIGHT_MARGIN_PT
            align_issue = None
            align = _detect_justify(lines, work_left, work_right, tol_pt=4.0)
            if align != "justify":
                align_issue = "Неверное выравнивание: требуется по ширине"

            # собрать нарушения
            issues = []
            if font_issues:
                seen, uniq = set(), []
                for msg in font_issues:
                    if msg not in seen:
                        seen.add(msg); uniq.append(msg)
                issues += uniq[:MAX_FONT_ISSUES_PER_PAR]
            if spacing_issue: issues.append(spacing_issue)
            if indent_issue:  issues.append(indent_issue)
            if align_issue:   issues.append(align_issue)

            if issues:
                bad_paras += 1
                error_pages.add(par.page_index0 + 1)
                sample = (lines[0].text[:120] + "…") if len(lines[0].text) > 120 else lines[0].text
                admin_lines.append(
                    f"[BodyText][Стр. {par.page_index0+1}] Абзац: «{sample}»\n  - " + "\n  - ".join(issues)
                )
                _add_text_annot_silent(
                    pdf_document[par.page_index0],
                    (par_bbox.x0, par_bbox.y0),
                    "Основной текст:\n" + "\n".join(f"• {m}" for m in issues)
                )

    # --- отчёты ---
    if bad_paras:
        user_summary = f"⚠️Проверка основного текста: нарушений в {bad_paras} из {scanned_paras} абз."
    else:
        user_summary = "✅Проверка основного текста: нарушений не обнаружено"

    admin_head = f"[BodyText] Абзацев: всего={total_paras}, проверено как основной текст={scanned_paras}, нарушений={bad_paras}"
    admin_body = ("\n" + "\n".join(admin_lines) if admin_lines else "\n[BodyText] Нарушений не обнаружено")

    if rejected_details:
        admin_diag = "\n\n[BodyText-Diagnostics] Причины отказа абзацев от прохождения фильтра:\n" + "\n".join(rejected_details)
    else:
        admin_diag = "\n\n[BodyText-Diagnostics] Все абзацы прошли фильтр «основного»"

    admin_details = admin_head + admin_body + admin_diag
    return {"user_summary": user_summary, "admin_details": admin_details}
