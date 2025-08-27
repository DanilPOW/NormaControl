# scripts/table_caption_checker.py
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
import re
import fitz  # PyMuPDF

# --- Константы макета / утилиты ---
MM_TO_PT = 2.8346456693  # 1 мм = 2.8346 pt
LEFT_MARGIN_PT = 3 * 28.35  # для режима anchor_mode="workarea"

# Нормативные элементы
# Оставим набор для нормализации, но в правилах ниже допускаем ТОЛЬКО короткое тире '–'
DASH_CHARS = "-–—"                 # дефис, en dash, em dash
CAPTION_PREFIX = "Таблица"

# Регулярка формата: Таблица <№> – <Название>
# Разрешаем ТОЛЬКО en dash (короткое тире, U+2013) и ровно по одному пробелу вокруг него.
CAPTION_NUMBER_RE = re.compile(
    rf"^Таблица\s+"
    rf"(?P<prefix>[A-Za-zА-Яа-я])?\.?\s*"           # необязательная буква для приложений: А.1
    rf"(?P<number>\d+(?:\.\d+)*)"                   # номер/подномер: 1, 1.1, 2.3.4
    rf"\s–\s"                                       # строго « пробел – пробел »
    rf"(?P<title>.+?)\s*$"
)

# Явно запрещаем сокращения "таб", "таб." и др. в начале подписи
BAD_PREFIX_RE = re.compile(r"^\s*таб(?:\.|\b)", re.IGNORECASE)


# ================== ДАТАКЛАССЫ ==================

@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class CaptionDetected:
    text: str                    # нормализованный текст (дефисы/тире сведены)
    raw_text: str                # сырой текст как в PDF (для точной проверки символов)
    bbox: BBox
    lines: List[Dict]            # сырые строки: {'text','bbox','font','size','color'}
    number_str: Optional[str]    # '3' | '1.2' | 'А.1' и т.п.
    title: Optional[str]
    font: str
    size: float
    rgb: Tuple[int, int, int]
    no_indent_ok: bool
    line_spacing_ok: bool
    starts_with_Tablitsa: bool
    ends_with_dot: bool
    gap_to_table_pt: float
    lines_between_caption_and_table: List[Dict]


@dataclass
class CaptionValidation:
    ok: bool
    issues: List[str]


# ================== ВСПОМОГАТЕЛЬНЫЕ ==================

def _normalize_dash(s: str) -> str:
    """Единообразим все типы тире к обычному дефису для стабильного сравнения/логов."""
    return s.replace("–", "-").replace("—", "-")


def _normalize_color_to_rgb255(c) -> Tuple[int, int, int]:
    """Нормализуем PyMuPDF color/fill к (R,G,B) в 0..255."""
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
    """
    Возвращает строки с bbox и стилем: [{'text','bbox','font','size','color'}, ...],
    отсортированные сверху-вниз (y0 растёт вниз).
    """
    out: List[Dict] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            xs, ys = [], []
            line_text = []
            sizes, fonts, colors = [], [], []
            for sp in line.get("spans", []):
                x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                xs += [x0, x1]; ys += [y0, y1]
                t = (sp.get("text") or "")
                if t:
                    line_text.append(t)
                sizes.append(float(sp.get("size", 0)))
                fonts.append(sp.get("font", ""))
                colors.append(sp.get("color", sp.get("fill", 0)))
            if not xs:
                continue
            text = "".join(line_text).strip()
            if not text:
                continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            color = colors[0] if colors else 0
            out.append({"text": text, "bbox": rect, "font": font, "size": size, "color": color})
    out.sort(key=lambda L: (L["bbox"].y0, L["bbox"].x0))
    return out


def _near_caption_lines_above_table(lines: List[Dict], tbl_rect: BBox, max_band_pt: float) -> List[Dict]:
    """
    Возвращает строки в «окне поиска» над таблицей: y1 ∈ [tbl_top - band, tbl_top).
    Сортировка снизу-вверх (ближайшая к таблице сначала).
    """
    top = tbl_rect.y0
    band_top = max(0, top - max_band_pt)
    out = [L for L in lines if (band_top <= L["bbox"].y1 <= top - 0.5)]
    out.sort(key=lambda L: -L["bbox"].y1)
    return out


def _is_no_par_indent_left(line: Dict, anchor_x: float, tol_px: float = 2.0) -> bool:
    """«Без абзацного отступа»: левая грань строки ≈ якорю (левое поле или левая граница таблицы)."""
    return abs(line["bbox"].x0 - anchor_x) <= tol_px


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


def is_times_new_roman_name(font_name: str) -> bool:
    """Учитываем популярные варианты имён Times New Roman."""
    if not font_name:
        return False
    base = font_name.split('+', 1)[-1]
    f = base.replace(" ", "").lower()
    return ("timesnewroman" in f) or ("times-roman" in f) or ("timesnewromanps" in f) or (f == "tnr") or ("times" in f)


# ================== ПУБЛИЧНЫЕ АПИ ==================

def find_table_caption(page: fitz.Page, tbl_rect: BBox,
                       search_band_mm: float = 25.0,
                       anchor_mode: str = "workarea",
                       tol_px: float = 2.0) -> Optional[CaptionDetected]:
    """
    Поиск подписи над таблицей. Возвращает CaptionDetected или None.
    anchor_mode:
        - "workarea": без отступа относительно левого поля страницы (LEFT_MARGIN_PT)
        - "table":    без отступа относительно левой границы таблицы (tbl_rect.x0)
    """
    max_band_pt = search_band_mm * MM_TO_PT
    lines = _collect_text_lines(page)
    candidates = _near_caption_lines_above_table(lines, tbl_rect, max_band_pt)

    anchor_x = LEFT_MARGIN_PT if anchor_mode == "workarea" else tbl_rect.x0

    # Найдём первую снизу строку, начинающуюся с "Таблица"
    head_idx = None
    for i, L in enumerate(candidates):
        if L["text"].strip().startswith(CAPTION_PREFIX):
            head_idx = i
            break
    if head_idx is None:
        return None

    # Многострочность: собираем блок подряд идущих строк над головной
    cap_lines = [candidates[head_idx]]
    i = head_idx + 1
    while i < len(candidates):
        up = candidates[i]
        same_left = abs(up["bbox"].x0 - cap_lines[-1]["bbox"].x0) <= 2.0
        avg_sz = (up["size"] + cap_lines[-1]["size"]) / 2.0
        close_y = (cap_lines[-1]["bbox"].y0 - up["bbox"].y1) <= (1.6 * avg_sz + 1.0)
        if same_left and close_y:
            cap_lines.append(up)
            i += 1
        else:
            break

    # Без абзацного отступа (по первой строке)
    no_indent_ok = _is_no_par_indent_left(cap_lines[0], anchor_x, tol_px=tol_px)

    # Полный текст
    raw_text = " ".join(L["text"] for L in cap_lines).strip()                    # сырой (как в PDF)
    cap_text = " ".join(_normalize_dash(L["text"]) for L in cap_lines).strip()   # нормализованный (для логов)

    # Парсим номер и заголовок по raw_text (важен реальный символ разделителя)
    m = CAPTION_NUMBER_RE.match(raw_text)
    if m:
        prefix = m.group("prefix") or ""
        number_str = ((prefix + ".") if prefix else "") + m.group("number")
        title = (m.group("title") or "").strip()
    else:
        number_str = None
        title = None

    # Стиль (по первой строке)
    first = cap_lines[0]
    font = first["font"]
    size = float(first["size"])
    rgb = _normalize_color_to_rgb255(first["color"])

    # Геометрия подписи
    xs, ys = [], []
    for L in cap_lines:
        xs += [L["bbox"].x0, L["bbox"].x1]
        ys += [L["bbox"].y0, L["bbox"].y1]
    cap_bbox = BBox(min(xs), min(ys), max(xs), max(ys))

    # Зазор до таблицы и лишние строки
    gap_to_table_pt = max(0.0, tbl_rect.y0 - cap_bbox.y1)

    cap_ids = {id(L) for L in cap_lines}
    lines_between: List[Dict] = []
    for L in lines:
        if id(L) in cap_ids:
            continue
        if (cap_bbox.y1 + 0.5) <= L["bbox"].y1 <= (tbl_rect.y0 - 0.5):
            lines_between.append(L)

    return CaptionDetected(
        text=cap_text,
        raw_text=raw_text,
        bbox=cap_bbox,
        lines=cap_lines,
        number_str=number_str,
        title=title,
        font=font,
        size=size,
        rgb=rgb,
        no_indent_ok=no_indent_ok,
        line_spacing_ok=_line_spacing_ok(cap_lines),
        starts_with_Tablitsa=raw_text.startswith(CAPTION_PREFIX),
        ends_with_dot=raw_text.endswith("."),
        gap_to_table_pt=gap_to_table_pt,
        lines_between_caption_and_table=lines_between,
    )


def validate_table_caption(cap: CaptionDetected, expected_num_str: str,
                           anchor_mode: str = "workarea",
                           max_pt: float = 14.0,
                           must_black: bool = True,
                           must_tnr: bool = True,
                           require_dash: bool = True,
                           max_gap_em: float = 0.6,
                           max_gap_pt: Optional[float] = None) -> CaptionValidation:
    """
    Проверка соответствия подписи требованиям.
    expected_num_str: ожидаемый номер таблицы (например '3', '1.2', 'А.1').
    anchor_mode: для формулировки предупреждения про «без абзацного отступа».
    max_gap_em: допустимый зазор между подписью и таблицей в "em" (от размера шрифта подписи).
    max_gap_pt: вместо em можно задать фикс. порог в pt (если None — используется em).
    """
    issues: List[str] = []

    # 0) Нельзя "таб", "таб."
    if BAD_PREFIX_RE.match(cap.raw_text):
        issues.append("Подпись оформлена неверно: нельзя использовать «таб», «таб.» — должно быть «Таблица»")

    # 1) Ключевые признаки формата
    if not cap.starts_with_Tablitsa:
        issues.append("Подпись должна начинаться со слова «Таблица» с прописной буквы")

    if cap.number_str is None or cap.title is None:
        issues.append("Подпись не соответствует формату «Таблица N – Наименование»")

    if cap.ends_with_dot:
        issues.append("В конце подписи не должно быть точки")

    # 2) «Без абзацного отступа»
    if not cap.no_indent_ok:
        anchor = "левого поля страницы" if anchor_mode == "workarea" else "левой границы таблицы"
        issues.append(f"Подпись должна быть без абзацного отступа относительно {anchor}")

    # 3) Межстрочный интервал (для многострочных)
    if len(cap.lines) >= 2 and not cap.line_spacing_ok:
        issues.append("Если наименование таблицы многострочное, межстрочный интервал должен быть одинарным")

    # 4) Шрифт/цвет/размер
    if must_tnr and not is_times_new_roman_name(cap.font):
        issues.append(f"Шрифт подписи не Times New Roman: {cap.font}")
    if cap.size > max_pt + 0.1:
        issues.append(f"Размер шрифта подписи {cap.size:.1f}pt > {max_pt:.0f}pt")
    if must_black and not is_black_rgb(cap.rgb, tol=6):
        issues.append(f"Цвет подписи не чёрный: RGB{cap.rgb}")

    # 5) Сверка номера
    if cap.number_str is not None:
        got = cap.number_str.replace(" ", "")
        exp = expected_num_str.replace(" ", "")
        if got != exp:
            issues.append(f"Неверный номер таблицы: в подписи «{got}», ожидается «{exp}»")

    # 6) Зазор между подписью и таблицей / «пустая строка»
    allowed_gap = max_gap_pt if max_gap_pt is not None else (max_gap_em * max(1.0, cap.size))
    if cap.gap_to_table_pt > allowed_gap + 0.1:
        issues.append(
            f"Между подписью и таблицей слишком большой зазор ({cap.gap_to_table_pt:.1f} pt); "
            f"не должно быть пустой строки"
        )
    if cap.lines_between_caption_and_table:
        issues.append("Между подписью и таблицей не должно быть других строк")

    # 7) Проверка типа разделителя и пробелов вокруг (строго ' – '):
    sep_m = re.match(
        r"^Таблица\s+(?:[A-Za-zА-Яа-я]\.\s*)?(\d+(?:\.\d+)*)\s*(?P<sep>[-–—])\s*(?P<title>.+?)\s*$",
        cap.raw_text
    )
    if sep_m:
        sep_char = sep_m.group("sep")
        exact_en_ok = bool(re.match(
            r"^Таблица\s+(?:[A-Za-zА-Яа-я]\.\s*)?\d+(?:\.\d+)*\s–\s",
            cap.raw_text
        ))
        if sep_char != "–":
            issues.append("Неверный разделитель: должно быть короткое тире ‘–’ (en dash), а не ‘—’ или ‘-’.")
        if not exact_en_ok:
            issues.append("Разделитель ‘–’ должен иметь ровно один пробел по обеим сторонам: « – ».")

    # 8) Первая буква названия — прописная
    if cap.title:
        t = cap.title.lstrip(' «"„‚(\'')  # пропускаем начальные кавычки/пробелы
        if t:
            ch = t[0]
            if ch.isalpha() and ch == ch.lower() and ch != ch.upper():  # кириллица/латиница
                issues.append("Название таблицы должно начинаться с прописной буквы")

    return CaptionValidation(ok=(len(issues) == 0), issues=issues)
