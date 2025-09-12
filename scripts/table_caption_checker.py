# table_caption_checker.py
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
CONTINUATION_PREFIX = "Продолжение таблицы"

# Регулярка формата: Таблица <№> – <Название>
# Разрешаем ТОЛЬКО en dash (короткое тире, U+2013) и ровно по одному пробелу вокруг него.
CAPTION_NUMBER_RE = re.compile(
    rf"^Таблица\s+"
    rf"(?P<prefix>[A-Za-zА-Яа-я])?\.?\s*"           # необязательная буква для приложений: А.1
    rf"(?P<number>\d+(?:\.\d+)*)"                   # номер/подномер: 1, 1.1, 2.3.4
    rf"\s–\s"                                       # строго « пробел – пробел »
    rf"(?P<title>.+?)\s*$"
)

# Подпись продолжения: «Продолжение таблицы N»
CONT_NUMBER_RE = re.compile(
    r"^Продолжение\s+таблицы\s+"
    r"(?P<prefix>[A-Za-zА-Яа-я])?\.?\s*"
    r"(?P<number>\d+(?:\.\d+)*)\s*$",
    re.IGNORECASE
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


# === Данные об упоминании «таблица N» в тексте ===
@dataclass
class TableMention:
    page_index: int                 # 0-based
    text: str                       # исходная строка с попаданием
    bbox: BBox                      # bbox строки
    is_lowercase_word: bool         # «таблица» в упоминании именно строчными
    match_span: Tuple[int, int]     # (start, end) совпадения внутри text

@dataclass
class MentionValidation:
    ok: bool
    issues: List[str]
    where: Optional[str]            # кратко: 'same_page_above', 'prev_page', 'same_page_below', None
    found: Optional[TableMention]


# === Подпись «Продолжение таблицы N»
@dataclass
class ContCaptionDetected:
    raw_text: str
    bbox: BBox
    lines: List[Dict]
    number_str: Optional[str]
    font: str
    size: float
    rgb: Tuple[int, int, int]
    no_indent_ok: bool
    gap_to_table_pt: float
    lines_between_caption_and_table: List[Dict]


# ================== ВСПОМОГАТЕЛЬНЫЕ ==================

def _mention_regex(expected_num_str: str) -> re.Pattern:
    """
    Ищем слово 'таблица' (полное слово) + пробелы + ожидаемый номер.
    Разрешаем префикс вида 'А.' в expected_num_str (если он есть).
    Чувствительность к регистру — case-insensitive, но отдельно проверим строчные.
    """
    exp = re.escape(expected_num_str)
    return re.compile(rf"\bтаблица\s+{exp}\b", re.IGNORECASE)

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
        y_upper_limit: Optional[float] = None,
    ) -> Optional[TableMention]:
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
            is_lower = (word == "таблица")
            return TableMention(
                page_index=page_index0,
                text=L["text"],
                bbox=BBox(L["bbox"].x0, L["bbox"].y0, L["bbox"].x1, L["bbox"].y1),
                is_lowercase_word=is_lower,
                match_span=(s, e),
            )
    return None

def validate_table_mention_placement(
        expected_num_str: str,
        tbl_rect: BBox,
        current_page: fitz.Page,
        current_page_index0: int,
        caption: Optional[CaptionDetected],
        prev_page: Optional[fitz.Page] = None,
        prev_page_index0: Optional[int] = None,
        max_dist_pt_same_page: float = 3000.0
    ) -> MentionValidation:
    issues: List[str] = []
    found: Optional[TableMention] = None
    where: Optional[str] = None

    exclude_ids = set(id(L) for L in (caption.lines if caption else []))
    if caption is not None:
        cur_first = _find_first_mention_on_page(
            current_page,
            current_page_index0,
            expected_num_str,
            exclude_line_ids=exclude_ids,
            y_upper_limit=caption.bbox.y0,
        )
    else:
        cur_first = _find_first_mention_on_page(
            current_page,
            current_page_index0,
            expected_num_str,
            exclude_line_ids=None,
            y_upper_limit=tbl_rect.y0,
        )

    if cur_first:
        found = cur_first
        where = "same_page_above"
        if not cur_first.is_lowercase_word:
            issues.append("В упоминании должно быть «таблица» строчными буквами.")
        return MentionValidation(ok=(len(issues) == 0), issues=issues, where=where, found=found)

    if prev_page is not None and prev_page_index0 is not None:
        prev_first = _find_first_mention_on_page(
            prev_page, prev_page_index0, expected_num_str, exclude_line_ids=None, y_upper_limit=None
        )
        if prev_first:
            found = prev_first
            where = "prev_page"
            if not prev_first.is_lowercase_word:
                issues.append("В упоминании должно быть «таблица» строчными буквами.")
            return MentionValidation(ok=(len(issues) == 0), issues=issues, where=where, found=found)

    issues.append("Не найдено корректное упоминание «таблица N» выше подписи/таблицы или на предыдущей странице.")
    return MentionValidation(ok=False, issues=issues, where=None, found=None)

def _normalize_dash(s: str) -> str:
    return s.replace("–", "-").replace("—", "-")

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

def _horiz_overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    left  = max(a.x0, b.x0)
    right = min(a.x1, b.x1)
    inter = max(0.0, right - left)
    denom = max(1.0, min(a.x1 - a.x0, b.x1 - b.x0))
    return inter / denom

def _has_foreign_line_between(all_lines: List[Dict], low: Dict, up: Dict,
                              overlap_min: float = 0.35) -> bool:
    y_top = up["bbox"].y1 + 0.5
    y_bot = low["bbox"].y0 - 0.5
    if y_bot <= y_top:
        return False
    band = fitz.Rect(min(low["bbox"].x0, up["bbox"].x0), up["bbox"].y0,
                     max(low["bbox"].x1, up["bbox"].x1), low["bbox"].y1)
    for L in all_lines:
        if L is low or L is up:
            continue
        y1 = L["bbox"].y1
        if y_top <= y1 <= y_bot:
            if _horiz_overlap_ratio(L["bbox"], band) >= overlap_min:
                return True
    return False

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

def _near_caption_lines_above_table(lines: List[Dict], tbl_rect: BBox, max_band_pt: float) -> List[Dict]:
    top = tbl_rect.y0
    band_top = max(0, top - max_band_pt)
    EPS = 0.1
    out = [L for L in lines if (band_top <= L["bbox"].y0 <= top - EPS)]
    out.sort(key=lambda L: -L["bbox"].y0)
    return out

def _is_no_par_indent_left(line: Dict, anchor_x: float, tol_px: float = 2.0) -> bool:
    return abs(line["bbox"].x0 - anchor_x) <= tol_px

def _line_spacing_ok(lines: List[Dict], tol_ratio=(0.85, 1.30)) -> bool:
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

    cap_lines = [candidates[head_idx]]
    head = cap_lines[0]

    # --- базовые метрики и допуски ---
    avg_pt = float(head["size"])
    EPS_PT = 0.8
    LINE_FACTOR_MAX = 1.40
    BLANK_GAP_FACTOR = 1.75
    MAX_LEFT_SHIFT_PT = max(6.0, 0.5 * avg_pt)
    HANG_TOL = max(4.0, 0.6 * avg_pt)
    OVERLAP_MIN = 0.25

    head_left = head["bbox"].x0

    def _title_start_x_for_line(line: Dict) -> Optional[float]:
        m = re.match(r"^(Таблица\s+(?:[A-Za-zА-Яа-я]\.\s*)?\d+(?:\.\d+)*)\s–\s", line["text"])
        if not m:
            return None
        cut = len(m.group(0))
        passed = 0
        for sp in line.get("spans", []):
            t = sp["text"]
            n = len(t)
            if n == 0:
                continue
            if passed + n < cut:
                passed += n
                continue
            inside = cut - passed
            frac = 0.0 if n == 0 else max(0.0, min(1.0, inside / n))
            return sp["bbox"].x0 + frac * (sp["bbox"].x1 - sp["bbox"].x0)
        return line["bbox"].x1

    title_x = _title_start_x_for_line(head)

    cap_hrect = fitz.Rect(head["bbox"])

    j = head_idx - 1
    while j >= 0:
        cand = candidates[j]

        if not _style_similar(head, cand):
            break

        dy = cap_lines[-1]["bbox"].y0 - cand["bbox"].y1
        avg_sz = (cand["size"] + cap_lines[-1]["size"]) / 2.0
        spacing_ok = dy <= (LINE_FACTOR_MAX * avg_sz + EPS_PT)

        if dy > (BLANK_GAP_FACTOR * avg_sz + EPS_PT):
            break

        same_left = abs(cand["bbox"].x0 - head_left) <= MAX_LEFT_SHIFT_PT
        hang_left = (title_x is not None) and (abs(cand["bbox"].x0 - title_x) <= HANG_TOL)
        overlap_ok = _horiz_overlap_ratio(cand["bbox"], cap_hrect) >= OVERLAP_MIN

        foreign = _has_foreign_line_between(candidates, cap_lines[-1], cand, overlap_min=0.30)
        if foreign:
            break

        if spacing_ok and (same_left or hang_left or overlap_ok):
            cap_lines.append(cand)
            cap_hrect = cap_hrect | cand["bbox"]
            j -= 1
        else:
            break

    cap_lines.sort(key=lambda L: (L["bbox"].y0, L["bbox"].x0))

    no_indent_ok = _is_no_par_indent_left(cap_lines[0], anchor_x, tol_px=tol_px)

    raw_text = " ".join(L["text"] for L in cap_lines).strip()
    cap_text = " ".join(_normalize_dash(L["text"]) for L in cap_lines).strip()

    m = CAPTION_NUMBER_RE.match(raw_text)
    if m:
        prefix = m.group("prefix") or ""
        number_str = ((prefix + ".") if prefix else "") + m.group("number")
        title = (m.group("title") or "").strip()
    else:
        number_str = None
        title = None

    first = cap_lines[0]
    font = first["font"]
    size = float(first["size"])
    rgb = _normalize_color_to_rgb255(first["color"])

    xs, ys = [], []
    for L in cap_lines:
        xs += [L["bbox"].x0, L["bbox"].x1]
        ys += [L["bbox"].y0, L["bbox"].y1]
    cap_bbox = BBox(min(xs), min(ys), max(xs), max(ys))

    gap_to_table_pt = max(0.0, tbl_rect.y0 - cap_bbox.y1)

    cap_ids = {id(L) for L in cap_lines}
    lines_between: List[Dict] = []
    for L in lines:
        if id(L) in cap_ids:
            continue
        if (cap_bbox.y1 + 0.5) <= L["bbox"].y1 <= (tbl_rect.y0 - 0.5):
            lines_between.append(L)

    band_rect = fitz.Rect(cap_bbox.x0, cap_bbox.y0, cap_bbox.x1, tbl_rect.y0)
    filtered_between = []
    for L in lines_between:
        if _horiz_overlap_ratio(L["bbox"], band_rect) < 0.60:
            continue
        if not _style_similar(first, L):
            filtered_between.append(L)
    lines_between = filtered_between

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
        starts_with_Tablitsa=raw_text.lstrip().startswith(CAPTION_PREFIX),
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
    if must_tnr and not _is_times_family(cap.font):
        issues.append(f"Шрифт подписи не Times New Roman (семейство): {cap.font}")
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
    allowed_gap = max(allowed_gap, 8.0)
    if cap.gap_to_table_pt > allowed_gap + 0.1:
        issues.append(
            f"Между подписью и таблицей слишком большой зазор ({cap.gap_to_table_pt:.1f} pt); "
            f"не должно быть пустой строки"
        )
    if cap.lines_between_caption_and_table:
        issues.append("Между подписью и таблицей не должно быть других строк")

    # 7) Разделитель « – »
    if require_dash:
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
                issues.append("Неверный разделитель: должно быть короткое тире ‘–’, а не ‘—’ или ‘-’.")
            if not exact_en_ok:
                issues.append("Разделитель ‘–’ должен иметь ровно один пробел по обеим сторонам: « – ».")

    # 8) Первая буква названия — прописная
    if cap.title:
        t = cap.title.lstrip(' «"„‚(\'')
        if t:
            ch = t[0]
            if ch.isalpha() and ch == ch.lower() and ch != ch.upper():
                issues.append("Название таблицы должно начинаться с прописной буквы")

    return CaptionValidation(ok=(len(issues) == 0), issues=issues)


# ====== Поддержка «Продолжение таблицы N» ======

def find_table_continuation_caption(page: fitz.Page, tbl_rect: BBox,
                                    search_band_mm: float = 25.0,
                                    anchor_mode: str = "workarea",
                                    tol_px: float = 2.0) -> Optional[ContCaptionDetected]:
    """
    Ищем строку «Продолжение таблицы N» над таблицей в ограниченной полосе поиска.
    Возвращаем ContCaptionDetected или None.
    """
    max_band_pt = search_band_mm * MM_TO_PT
    lines = _collect_text_lines(page)
    candidates = _near_caption_lines_above_table(lines, tbl_rect, max_band_pt)
    anchor_x = LEFT_MARGIN_PT if anchor_mode == "workarea" else tbl_rect.x0

    head_idx = None
    for i, L in enumerate(candidates):
        if L["text"].strip().lower().startswith(CONTINUATION_PREFIX.lower()):
            head_idx = i
            break
    if head_idx is None:
        return None

    first = candidates[head_idx]

    xs, ys = [first["bbox"].x0, first["bbox"].x1], [first["bbox"].y0, first["bbox"].y1]
    cap_bbox = BBox(min(xs), min(ys), max(xs), max(ys))

    no_indent_ok = _is_no_par_indent_left(first, anchor_x, tol_px=tol_px)
    raw_text = first["text"].strip()

    m = CONT_NUMBER_RE.match(raw_text)
    if m:
        prefix = m.group("prefix") or ""
        number_str = ((prefix + ".") if prefix else "") + m.group("number")
    else:
        number_str = None

    font = first["font"]
    size = float(first["size"])
    rgb = _normalize_color_to_rgb255(first["color"])

    gap_to_table_pt = max(0.0, tbl_rect.y0 - cap_bbox.y1)

    lines_between: List[Dict] = []
    band_rect = fitz.Rect(cap_bbox.x0, cap_bbox.y0, cap_bbox.x1, tbl_rect.y0)
    for L in lines:
        if L is first:
            continue
        if (cap_bbox.y1 + 0.5) <= L["bbox"].y1 <= (tbl_rect.y0 - 0.5):
            if _horiz_overlap_ratio(L["bbox"], band_rect) >= 0.60:
                lines_between.append(L)

    return ContCaptionDetected(
        raw_text=raw_text,
        bbox=cap_bbox,
        lines=[first],
        number_str=number_str,
        font=font,
        size=size,
        rgb=rgb,
        no_indent_ok=no_indent_ok,
        gap_to_table_pt=gap_to_table_pt,
        lines_between_caption_and_table=lines_between,
    )


def validate_table_continuation_caption(cont: ContCaptionDetected,
                                        expected_num_str: Optional[str],
                                        anchor_mode: str = "workarea",
                                        max_pt: float = 14.0,
                                        must_black: bool = True,
                                        must_tnr: bool = True,
                                        max_gap_pt: float = 14.0) -> CaptionValidation:
    """
    Проверяем подпись продолжения. Требования:
    - Текст начинается с «Продолжение таблицы», обязательно указан номер N.
    - Без абзацного отступа (от левого поля или левого края таблицы — по anchor_mode).
    - Шрифт Times New Roman, чёрный, не более 14 pt.
    - Между подписью продолжения и таблицей зазор не более одной строки (<= max_gap_pt; по умолчанию 14 pt).
    - Между подписью продолжения и таблицей не должно быть других строк.
    - Если expected_num_str задан — номер должен совпадать.
    """
    issues: List[str] = []

    if not cont.raw_text.lower().startswith(CONTINUATION_PREFIX.lower()):
        issues.append("Подпись продолжения должна начинаться со слов «Продолжение таблицы».")
    if cont.number_str is None:
        issues.append("В подписи продолжения должен быть указан номер таблицы (N).")
    if cont.raw_text.endswith("."):
        issues.append("В конце подписи продолжения не должно быть точки.")

    if not cont.no_indent_ok:
        anchor = "левого поля страницы" if anchor_mode == "workarea" else "левой границы таблицы"
        issues.append(f"Подпись продолжения должна быть без абзацного отступа относительно {anchor}.")

    if must_tnr and not _is_times_family(cont.font):
        issues.append(f"Шрифт подписи продолжения не Times New Roman (семейство): {cont.font}")
    if cont.size > max_pt + 0.1:
        issues.append(f"Размер шрифта подписи продолжения {cont.size:.1f}pt > {max_pt:.0f}pt")
    if must_black and not is_black_rgb(cont.rgb, tol=6):
        issues.append(f"Цвет подписи продолжения не чёрный: RGB{cont.rgb}")

    if expected_num_str is not None and cont.number_str is not None:
        got = cont.number_str.replace(" ", "")
        exp = expected_num_str.replace(" ", "")
        if got != exp:
            issues.append(f"Номер в подписи продолжения «{got}» должен совпадать с номером основной части «{exp}».")

    if cont.gap_to_table_pt > max_gap_pt + 0.1:
        issues.append(f"Между подписью продолжения и таблицей слишком большой зазор ({cont.gap_to_table_pt:.1f} pt); допускается не более {max_gap_pt:.0f} pt.")
    if cont.lines_between_caption_and_table:
        issues.append("Между подписью продолжения и таблицей не должно быть других строк.")

    return CaptionValidation(ok=(len(issues) == 0), issues=issues)
