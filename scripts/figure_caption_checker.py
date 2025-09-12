# scripts/figure_caption_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
import re
import fitz  # PyMuPDF

# ======= Константы/единицы =======
MM_TO_PT = 2.8346456693
CM_TO_PT = 28.35

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# ======= Регулярки/формат =======
CAPTION_PREFIX = "Рисунок"

BAD_PREFIX_RE = re.compile(
    r"^\s*(рис\.?|рис-|рисунок\.?|картинка|изображение|фото|figure|fig\.?)\b",
    re.IGNORECASE
)

# строго: Рисунок N – Название   (en dash и ровно по одному пробелу)
CAPTION_NUMBER_RE = re.compile(
    r"^Рисунок\s+(?P<number>\d+(?:\.\d+)*)\s–\s(?P<title>.+?)\s*$"
)

# Упоминание: «рисунок N» — слово строчными, N из ожидаемого
def _mention_regex(expected_num_str: str) -> re.Pattern:
    exp = re.escape(expected_num_str)
    return re.compile(rf"\bрисунок\s+{exp}\b", re.IGNORECASE)

# ======= Датаклассы =======

@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def width(self) -> float:
        return self.x1 - self.x0

    def height(self) -> float:
        return self.y1 - self.y0

@dataclass
class CaptionDetected:
    text: str             # нормализованный текст (тире приведены)
    raw_text: str         # сырой текст как в PDF
    bbox: BBox
    lines: List[Dict]     # сырые строки: {'text','bbox','font','size','color'}
    number_str: Optional[str]
    title: Optional[str]
    font: str
    size: float
    rgb: Tuple[int, int, int]
    centered_ok: bool
    no_first_line_indent: bool
    ends_with_dot: bool
    gap_from_figure_pt: float
    gap_to_next_line_pt: Optional[float]  # дистанция до следующей строки после подписи
    line_spacing_ok: bool

@dataclass
class CaptionValidation:
    ok: bool
    issues: List[str]

@dataclass
class FigureMention:
    page_index: int
    text: str
    bbox: BBox
    is_lowercase_word: bool
    match_span: Tuple[int, int]

@dataclass
class MentionValidation:
    ok: bool
    issues: List[str]
    where: Optional[str]       # 'same_page_above' | 'prev_page' | None
    found: Optional[FigureMention]

# ======= Вспомогательные =======

def _line_spacing_ok(lines: List[Dict], tol_ratio=(0.85, 1.30)) -> bool:
    """
    Проверка «одинарного межстрочного интервала».
    Берём средний dy между соседними строками и делим на средний size.
    """
    if len(lines) < 2:
        return True
    dy = []
    sizes = []
    ls = sorted(lines, key=lambda L: L["bbox"].y0)
    for i in range(1, len(ls)):
        dy.append(ls[i]["bbox"].y0 - ls[i-1]["bbox"].y0)
    for L in ls:
        sizes.append(L["size"])
    if not dy or not sizes:
        return True
    mean_dy = sum(dy)/len(dy)
    mean_sz = max(1.0, sum(sizes)/len(sizes))
    ratio = mean_dy / mean_sz
    lo, hi = tol_ratio
    return (lo <= ratio <= hi)

def _normalize_dash(s: str) -> str:
    # визуально не меняем пользователю, но для логов можем сводить — здесь оставим как есть
    return s

def _normalize_color_to_rgb255(c) -> Tuple[int, int, int]:
    def clamp255(x):
        try:
            return max(0, min(255, int(round(x))))
        except Exception:
            return 0
    if isinstance(c, str) and c.startswith("#") and len(c) == 7:
        r = int(c[1:3], 16); g = int(c[3:5], 16); b = int(c[5:7], 16)
        return (r, g, b)
    if isinstance(c, (list, tuple)) and len(c) == 3:
        if max(c) <= 1.0:
            return (clamp255(c[0]*255), clamp255(c[1]*255), clamp255(c[2]*255))
        return (clamp255(c[0]), clamp255(c[1]), clamp255(c[2]))
    if isinstance(c, (int, float)):
        v = clamp255(c*255 if c <= 1.0 else c)
        return (v, v, v)
    return (0, 0, 0)

def is_black_rgb(rgb: Tuple[int, int, int], tol: int = 6) -> bool:
    r, g, b = rgb
    return (r <= tol and g <= tol and b <= tol)

def _collect_text_lines(page: fitz.Page) -> List[Dict]:
    out: List[Dict] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            xs, ys = [], []
            line_text = []
            sizes, fonts, colors = [], [], []
            spans_out = []
            for sp in line.get("spans", []):
                x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                xs += [x0, x1]; ys += [y0, y1]
                t = (sp.get("text") or "")
                if t:
                    line_text.append(t)
                sizes.append(float(sp.get("size", 0)))
                fonts.append(sp.get("font", ""))
                colors.append(sp.get("color", sp.get("fill", 0)))
                spans_out.append({
                    "text": t,
                    "bbox": fitz.Rect(x0, y0, x1, y1),
                    "size": float(sp.get("size", 0)),
                    "font": sp.get("font", ""),
                    "color": sp.get("color", sp.get("fill", 0)),
                })
            if not xs:
                continue
            text = "".join(line_text).strip()
            if not text:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            color = colors[0] if colors else 0
            out.append({"text": text, "bbox": rect, "font": font, "size": size, "color": color, "spans": spans_out})
    out.sort(key=lambda L: (L["bbox"].y0, L["bbox"].x0))
    return out

def _horiz_centered_ok(inner_left: float, inner_right: float, rect: fitz.Rect, tol_pt: float = 2.0) -> bool:
    inner_cx = (inner_left + inner_right) / 2.0
    rect_cx  = (rect.x0 + rect.x1) / 2.0
    return abs(inner_cx - rect_cx) <= tol_pt

def _has_first_line_indent(cap_lines: List[Dict], indent_tol_pt: float = 6.0) -> bool:
    """
    Эвристика «красной строки»: если первая строка заметно больше смещена вправо,
    чем последующие (для многострочных подписей).
    """
    if len(cap_lines) < 2:
        return False
    x0s = [float(L["bbox"].x0) for L in cap_lines]
    x0_first = x0s[0]
    x0_rest_min = min(x0s[1:])
    return (x0_first - x0_rest_min) >= indent_tol_pt

def _lines_between(page: fitz.Page, upper: fitz.Rect, lower: fitz.Rect) -> List[Dict]:
    """Строки между двумя прямоугольниками по Y (upper над lower)."""
    lines = _collect_text_lines(page)
    y_top = upper.y1 + 0.5
    y_bot = lower.y0 - 0.5
    out = []
    for L in lines:
        if y_top <= L["bbox"].y1 <= y_bot:
            out.append(L)
    return out

# ======= Поиск подписи под рисунком =======

def find_figure_caption(
    page: fitz.Page,
    fig_rect: BBox,
    *,
    search_band_mm: float = 25.0,
    require_prefix: str = CAPTION_PREFIX,
) -> Optional[CaptionDetected]:
    """
    Ищем подпись СРАЗУ ПОД рисунком в вертикальной «полосе» search_band_mm.
    Подпись должна начинаться с «Рисунок».
    """
    max_band_pt = search_band_mm * MM_TO_PT
    lines = _collect_text_lines(page)

    top = fig_rect.y1
    band_bottom = top + max_band_pt

    # кандидаты — строки внизу от рисунка до band_bottom
    candidates = [L for L in lines if (top + 0.1) <= L["bbox"].y0 <= (band_bottom + 0.1)]
    candidates.sort(key=lambda L: (L["bbox"].y0, L["bbox"].x0))

    # Находим первую строку, начинающуюся с «Рисунок»
    head_idx = None
    for i, L in enumerate(candidates):
        if L["text"].lstrip().startswith(require_prefix):
            head_idx = i
            break
    if head_idx is None:
        return None

    # Склейка многострочной подписи (вниз по документу)
    cap_lines = [candidates[head_idx]]
    # продолжаем ниже, пока расстояния и стиль адекватны (упростим: пока рядом по Y)
    i = head_idx + 1
    while i < len(candidates):
        prev = cap_lines[-1]["bbox"]
        cur  = candidates[i]["bbox"]
        dy = cur.y0 - prev.y1
        if dy > (1.75 * max(1.0, cap_lines[-1]["size"])) + 0.8:  # «пустая строка» — завершаем
            break
        # горизонтальная связность: хэуристика
        if cur.x1 < prev.x0 - 4 or cur.x0 > prev.x1 + 4:
            break
        cap_lines.append(candidates[i])
        i += 1

    # геометрия блока подписи
    xs, ys = [], []
    for L in cap_lines:
        xs += [L["bbox"].x0, L["bbox"].x1]
        ys += [L["bbox"].y0, L["bbox"].y1]
    cap_bbox = fitz.Rect(min(xs), min(ys), max(xs), max(ys))

    # рабочее поле страницы
    work_left  = page.rect.x0 + LEFT_MARGIN_PT
    work_right = page.rect.x1 - RIGHT_MARGIN_PT

    # центрирование по рабочему полю
    centered_ok = _horiz_centered_ok(work_left, work_right, cap_bbox, tol_pt=2.0)

    # «красная строка» (эвристика по многострочной подписи)
    first_line_indent = _has_first_line_indent(cap_lines, indent_tol_pt=6.0)

    # строки между рисунком и подписью (не допускаются)
    between = _lines_between(page, fitz.Rect(fig_rect.x0, fig_rect.y0, fig_rect.x1, fig_rect.y1), cap_bbox)
    # зазор до подписи
    gap_to_cap_pt = cap_bbox.y0 - fig_rect.y1

    # следующая строка ПОСЛЕ подписи — чтобы проверить пустую строку после
    all_lines = _collect_text_lines(page)
    after = [L for L in all_lines if L["bbox"].y0 >= (cap_bbox.y1 + 0.1)]
    after.sort(key=lambda L: (L["bbox"].y0, L["bbox"].x0))
    gap_after_pt = (after[0]["bbox"].y0 - cap_bbox.y1) if after else None

    # текст
    raw_text = " ".join(L["text"] for L in cap_lines).strip()
    norm_text = _normalize_dash(raw_text)

    m = CAPTION_NUMBER_RE.match(raw_text)
    if m:
        number_str = m.group("number")
        title = (m.group("title") or "").strip()
    else:
        number_str = None
        title = None

    first = cap_lines[0]
    font = first["font"]
    size = float(first["size"])
    rgb = _normalize_color_to_rgb255(first["color"])
    
    line_spacing_ok = _line_spacing_ok(cap_lines)

    return CaptionDetected(
        text=norm_text,
        raw_text=raw_text,
        bbox=BBox(cap_bbox.x0, cap_bbox.y0, cap_bbox.x1, cap_bbox.y1),
        lines=cap_lines,
        number_str=number_str,
        title=title,
        font=font,
        size=size,
        rgb=rgb,
        centered_ok=centered_ok,
        no_first_line_indent=(not first_line_indent),
        ends_with_dot=raw_text.endswith("."),
        gap_from_figure_pt=gap_to_cap_pt,
        gap_to_next_line_pt=gap_after_pt,
        line_spacing_ok=line_spacing_ok,
    )

# ======= Валидация подписи =======

def _is_times_family(font_name: str) -> bool:
    if not font_name:
        return False
    f = font_name.split("+", 1)[-1].lower().replace(" ", "")
    return ("timesnewroman" in f) or ("times-roman" in f) or ("timesnewromanps" in f) or (f in {"tnr","timesroman","times"})

def validate_figure_caption(
    cap: CaptionDetected,
    expected_num_str: str,
    *,
    max_pt: float = 14.0,
    must_black: bool = True,
    must_tnr: bool = True,
    require_dash: bool = True,
    check_line_spacing: bool = True,
) -> CaptionValidation:
    issues: List[str] = []

    # 0) Запрещённые префиксы
    if BAD_PREFIX_RE.match(cap.raw_text) and not cap.raw_text.startswith(CAPTION_PREFIX):
        issues.append("Подпись неверно оформлена: должно быть «Рисунок», а не «рис.», «картинка», «фото» и т.п.")

    # 1) Формат «Рисунок N – Наименование»
    m = CAPTION_NUMBER_RE.match(cap.raw_text)
    if not m:
        issues.append("Подпись не соответствует формату «Рисунок N – Наименование» (короткое тире «–», пробелы по обе стороны).")
    else:
        # разделитель — строго en dash с 1 пробелом с обеих сторон
        if require_dash and not re.match(r"^Рисунок\s+\d+(?:\.\d+)*\s–\s", cap.raw_text):
            issues.append("Неверный разделитель: должно быть короткое тире «–» с одним пробелом по обе стороны: « – ».")

        # номер
        got = (m.group("number") or "").replace(" ", "")
        exp = expected_num_str.replace(" ", "")
        if got != exp:
            issues.append(f"Неверный номер рисунка в подписи: «{got}», ожидается «{exp}».")

        # наименование — с прописной буквы
        title = (m.group("title") or "").lstrip(' «"„‚(\'')
        if title and title[0].isalpha() and title[0] == title[0].lower() and title[0] != title[0].upper():
            issues.append("Наименование рисунка должно начинаться с прописной буквы.")

    # 2) Центрирование
    if not cap.centered_ok:
        issues.append("Подпись рисунка должна быть выровнена по центру относительно рабочей области страницы.")

    # 3) «Красная строка»
    if not cap.no_first_line_indent:
        issues.append("В подписи не допускается абзацный отступ («красная строка»).")

    # 4) Точка в конце
    if cap.ends_with_dot:
        issues.append("В конце подписи не должно быть точки.")

    # 5) Шрифт/цвет/размер
    if must_tnr and not _is_times_family(cap.font):
        issues.append(f"Шрифт подписи не Times New Roman (обнаружен: {cap.font}).")
    if cap.size > max_pt + 0.1:
        issues.append(f"Размер шрифта подписи {cap.size:.1f}pt превышает 14pt.")
    if must_black and not is_black_rgb(cap.rgb, tol=6):
        issues.append(f"Цвет подписи не чёрный: RGB{cap.rgb}.")

    # 6) Зазор между рисунком и подписью — не должно быть «пустой строки»
    # используем порог как в таблицах: > ~0.6em считаем лишней пустой строкой
    allowed_gap_pt = 1.5 * max(12.0, min(14.0, cap.size))
    if cap.gap_from_figure_pt > allowed_gap_pt + 0.1:
        issues.append("Между рисунком и подписью не должно быть пустой строки (слишком большой зазор).")

    # 7) Пустая строка после подписи — должна быть
    if cap.gap_to_next_line_pt is not None:
        need_after_pt = 1.5 * max(12.0, min(14.0, cap.size))  # как межстрочник 1.5 с кеглем 12–14
        if cap.gap_to_next_line_pt + 1e-6 < need_after_pt:
            issues.append("После подписи должна быть пустая строка (межстрочный интервал не выдержан).")

    if check_line_spacing and len(cap.lines) >= 2 and not cap.line_spacing_ok:
        issues.append("Если наименование рисунка многострочное, межстрочный интервал должен быть одинарным")

    return CaptionValidation(ok=(len(issues) == 0), issues=issues)

# ======= Поиск упоминания «рисунок N» =======

def _collect_text_lines_with_page(page: fitz.Page, page_index0: int) -> List[Dict]:
    out = _collect_text_lines(page)
    for L in out:
        L["__page_idx0"] = page_index0
    return out

def _find_first_mention_on_page(
    page: fitz.Page,
    page_index0: int,
    expected_num_str: str,
    *,
    exclude_line_ids: Optional[set] = None,
    y_upper_limit: Optional[float] = None,   # искать только выше этой Y
) -> Optional[FigureMention]:
    rx = _mention_regex(expected_num_str)
    for L in _collect_text_lines_with_page(page, page_index0):
        if exclude_line_ids and id(L) in exclude_line_ids:
            continue
        if y_upper_limit is not None and not (L["bbox"].y1 <= y_upper_limit + 0.1):
            continue
        m = rx.search(L["text"])
        if m:
            s, e = m.span()
            frag = L["text"][s:e]
            word = frag.split()[0] if frag else ""
            is_lower = (word == "рисунок")
            return FigureMention(
                page_index=page_index0,
                text=L["text"],
                bbox=BBox(L["bbox"].x0, L["bbox"].y0, L["bbox"].x1, L["bbox"].y1),
                is_lowercase_word=is_lower,
                match_span=(s, e),
            )
    return None

def validate_figure_mention_placement(
    expected_num_str: str,
    fig_rect: BBox,
    current_page: fitz.Page,
    current_page_index0: int,
    caption: Optional[CaptionDetected],
    prev_page: Optional[fitz.Page] = None,
    prev_page_index0: Optional[int] = None,
) -> MentionValidation:
    issues: List[str] = []
    found: Optional[FigureMention] = None
    where: Optional[str] = None

    exclude_ids = set(id(L) for L in (caption.lines if caption else []))
    if caption is not None:
        cur_first = _find_first_mention_on_page(
            current_page, current_page_index0, expected_num_str,
            exclude_line_ids=exclude_ids,
            y_upper_limit=caption.bbox.y0,  # строго выше подписи
        )
    else:
        # подписи нет — ищем выше самого рисунка
        cur_first = _find_first_mention_on_page(
            current_page, current_page_index0, expected_num_str,
            exclude_line_ids=None,
            y_upper_limit=fig_rect.y0,
        )

    if cur_first:
        found = cur_first
        where = "same_page_above"
        if not cur_first.is_lowercase_word:
            issues.append("В упоминании должно быть «рисунок» строчными буквами.")
        return MentionValidation(ok=(len(issues) == 0), issues=issues, where=where, found=found)

    # пробуем предыдущую страницу
    if prev_page is not None and prev_page_index0 is not None:
        prev_first = _find_first_mention_on_page(
            prev_page, prev_page_index0, expected_num_str,
            exclude_line_ids=None, y_upper_limit=None
        )
        if prev_first:
            found = prev_first
            where = "prev_page"
            if not prev_first.is_lowercase_word:
                issues.append("В упоминании должно быть «рисунок» строчными буквами.")
            return MentionValidation(ok=(len(issues) == 0), issues=issues, where=where, found=found)

    issues.append("Не найдено корректное упоминание «рисунок N» выше подписи/рисунка или на предыдущей странице.")
    return MentionValidation(ok=False, issues=issues, where=None, found=None)

# ======= Главная функция =======

def _add_text_annot_silent(page: fitz.Page, point_xy: Tuple[float, float], msg: str):
    try:
        ann = page.add_text_annot(fitz.Point(*point_xy), msg)
        ann.set_info(title="Сервис нормоконтроля", content=msg)
        ann.update()
    except Exception:
        pass

def check_figure_captions(
    pdf_document,
    figures: List[Dict],   # элементы с ключами: 'page' (1-based), 'bbox'=(x0,y0,x1,y1), 'fig_index'=int
    *,
    search_band_mm: float = 25.0,
) -> Dict[str, str]:
    """
    Возвращает:
      {
        "user_summary": "...",
        "admin_details": "...",
      }
    """
    admin_lines: List[str] = []
    error_pages = set()

    for fig in figures:
        page_num = int(fig["page"])
        x0, y0, x1, y1 = fig["bbox"]
        fig_rect = BBox(float(x0), float(y0), float(x1), float(y1))
        expected_number_str = str(int(fig["fig_index"]))

        page = pdf_document[page_num - 1]

        # Ищем подпись
        cap = find_figure_caption(page, fig_rect, search_band_mm=search_band_mm)

        caption_notes: List[str] = []
        figure_notes: List[str]  = []

        # Проверка «подпись и рисунок на одной странице»
        # (если cap найден на другом page — у нас cap=None; проверим только при cap!=None,
        # а сама геометрия подразумевает ту же страницу)
        if cap is None:
            admin_lines.append(f"[FigureCaption][Стр. {page_num}][Рис. {expected_number_str}] Подпись не найдена")
            figure_notes.append("Нет подрисуночной подписи.")
            error_pages.add(page_num)
        else:
            # ============ [ДОБАВЬ ЭТО: диагностические числа по зазорам] ============
            # Формулы те же, что в validate_figure_caption()
            gap_to_cap_pt = cap.gap_from_figure_pt
            allowed_gap_pt = max(8.0, 0.6 * max(1.0, cap.size))  # «не должно быть пустой строки»
            gap_to_cap_mm = gap_to_cap_pt / MM_TO_PT
            allowed_gap_mm = allowed_gap_pt / MM_TO_PT

            gap_after_pt = cap.gap_to_next_line_pt
            need_after_pt = 1.5 * max(12.0, min(14.0, cap.size))  # пустая строка после подписи (межстрочник 1.5)
            if cap is not None and len(cap.lines) >= 2:
                admin_lines.append(
                    f"[FigureCaption][Стр. {page_num}][Рис. {expected_number_str}] "
                    f"Межстрочный интервал: {'✅' if cap.line_spacing_ok else '❌'}"
                )
            if gap_after_pt is not None:
                gap_after_mm = gap_after_pt / MM_TO_PT
                need_after_mm = need_after_pt / MM_TO_PT
                admin_lines.append(
                    f"[FigureCaption][Стр. {page_num}][Рис. {expected_number_str}] "
                    f"gap_to_caption={gap_to_cap_mm:.1f} мм (≤ {allowed_gap_mm:.1f} мм); "
                    f"gap_after={gap_after_mm:.1f} мм (≥ {need_after_mm:.1f} мм)"
                )
            else:
                admin_lines.append(
                    f"[FigureCaption][Стр. {page_num}][Рис. {expected_number_str}] "
                    f"gap_to_caption={gap_to_cap_mm:.1f} мм (≤ {allowed_gap_mm:.1f} мм); "
                    f"gap_after=— (следующая строка не найдена)"
                )
            # =======================================================================

            # Валидация подписи
            val = validate_figure_caption(
                cap,
                expected_num_str=expected_number_str,
                max_pt=14.0,
                must_black=True,
                must_tnr=True,
                require_dash=True,
                check_line_spacing=True,
            )
            if val.ok:
                admin_lines.append(f"[FigureCaption][Стр. {page_num}][Рис. {expected_number_str}] ✅ «{cap.raw_text}»")
            else:
                admin_lines.append(
                    f"[FigureCaption][Стр. {page_num}][Рис. {expected_number_str}] Ошибки:\n  - " + "\n  - ".join(val.issues)
                )
                caption_notes.append("Подпись рисунка:")
                caption_notes += [f"• {e}" for e in val.issues[:10]]
                error_pages.add(page_num)

        # Проверка упоминания «рисунок N»
        try:
            prev_pg = pdf_document[page_num - 2] if page_num - 2 >= 0 else None
        except Exception:
            prev_pg = None

        mention_val = validate_figure_mention_placement(
            expected_num_str=expected_number_str,
            fig_rect=fig_rect,
            current_page=page,
            current_page_index0=page_num - 1,
            caption=cap,
            prev_page=prev_pg,
            prev_page_index0=(page_num - 2 if prev_pg is not None else None),
        )

        if mention_val.found:
            if mention_val.ok:
                where_map = {"same_page_above": "на той же странице выше", "prev_page": "на предыдущей странице"}
                admin_lines.append(
                    f"[FigureMention][Стр. {page_num}][Рис. {expected_number_str}] Найдено упоминание «рисунок {expected_number_str}» ({where_map.get(mention_val.where, 'найдено')})"
                )
            else:
                admin_lines.append(
                    f"[FigureMention][Стр. {page_num}][Рис. {expected_number_str}] Упоминание оформлено неверно: " +
                    ("; ".join(mention_val.issues) if mention_val.issues else "см. аннотацию")
                )
                msg = [f"Упоминание «рисунок {expected_number_str}»: оформление неверно"]
                msg += [f"• {e}" for e in mention_val.issues]
                if cap is not None:
                    caption_notes += msg
                else:
                    figure_notes += msg
                error_pages.add(page_num)
        else:
            admin_lines.append(
                f"[FigureMention][Стр. {page_num}][Рис. {expected_number_str}] Упоминание не найдено (ожидалось «рисунок {expected_number_str}»)"
            )
            msg = [f"Для данного рисунка должно быть упоминание «рисунок {expected_number_str}» в тексте до рисунка "
                   f"(на этой странице выше или на предыдущей странице)."]
            if cap is not None:
                caption_notes += msg
            else:
                figure_notes += msg
            error_pages.add(page_num)

        # Аннотации в самом конце: на подпись (если есть) и/или у рисунка
        if caption_notes and cap is not None and any(s.strip() for s in caption_notes):
            _add_text_annot_silent(page, (cap.bbox.x0, cap.bbox.y0), "\n".join(caption_notes))
        if figure_notes and any(s.strip() for s in figure_notes):
            _add_text_annot_silent(page, (fig_rect.x0, fig_rect.y0), "\n".join(figure_notes))

    # Итоги
    if error_pages:
        user_summary = "⚠️Проверка рисунков/подписей: нарушения на стр " + ", ".join(map(str, sorted(error_pages)))
    else:
        user_summary = "✅Подписи и упоминания рисунков — без нарушений"

    admin_details = "\n".join(admin_lines) if admin_lines else "Замечаний по подрисуночным подписям нет."

    return {
        "user_summary": user_summary,
        "admin_details": admin_details
    }
