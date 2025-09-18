# scripts/list_checker.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re
import fitz  # PyMuPDF
from collections import defaultdict

# --- Единицы и поля ---
MM_TO_PT = 2.834646
CM_TO_PT = 28.35

LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

def mm_to_pt(mm: float) -> float: return mm * MM_TO_PT
def pt_to_mm(pt: float) -> float: return pt / MM_TO_PT

# --- Параметры форматирования списка ---
INDENT_STEP_CM = 0.75
INDENT_STEP_PT = INDENT_STEP_CM * CM_TO_PT     # ≈ 21.26 pt
INDENT_TOL_PT  = 4.0

LINE_SPACING_TARGET = 1.50
LINE_SPACING_TOL    = 0.06
ALIGN_TOL_PT        = 4.0

# --- «До/после = 0 pt» (эвристика) ---
MAX_GAP_BEFORE_AFTER_FACTOR = 1.2  # * fontsize

# --- Текстовые маркеры ---
EN_DASH = "–"  # короткое тире
RUS_LETTERS = "абвгдеёжзийклмнопрстуфхцчшщьыъэюя"
EXCLUDED = set("ёзйочьъы")
ALLOWED_LETTERS = tuple(ch for ch in RUS_LETTERS if ch not in EXCLUDED)

RE_START_SIMPLE = re.compile(
    r"^\s*(?:{}\s|\d+\)\s|[{}]\)\s)".format(
        re.escape(EN_DASH),
        "".join(ALLOWED_LETTERS)
    )
)
RE_ONLY_ONE_SPACE_AFTER = re.compile(
    r"^\s*(?:\d+\)|[{}]\))\s(?!\s)".format("".join(ALLOWED_LETTERS))
)
RE_ONLY_ONE_SPACE_AFTER_DASH = re.compile(r"^\s*{}\s(?!\s)".format(EN_DASH))

# --- Векторные маркеры ---
MARKER_MAX_W_MM = 8.0
MARKER_MAX_H_MM = 8.0
MARKER_MAX_W_PT = mm_to_pt(MARKER_MAX_W_MM)
MARKER_MAX_H_PT = mm_to_pt(MARKER_MAX_H_MM)

# --- Структуры ---
@dataclass
class Line:
    text: str
    bbox: fitz.Rect
    size: float
    font: str
    spans: List[Dict]

@dataclass
class Item:
    page_index0: int
    line: Line
    level: int             # округление до шага 0.75 см от левого поля
    kind: str              # "bulleted" | "numbered"
    marker_text: str       # "–", "1)", "а)" или "[vector]"
    number_kind: str       # "digits" | "rusalpha" | "" (bulleted)

@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect

# ---------- Утилиты ----------
def _median(vals: List[float]) -> float:
    if not vals: return 0.0
    s = sorted(vals); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2.0

def _y_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    return max(0.0, min(a.y1, b.y1) - max(a.y0, b.y0))

def _collect_text_lines(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys, texts, spans, sizes, fonts = [], [], [], [], [], []
            for sp in ln.get("spans", []):
                x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                t = sp.get("text") or ""
                if t.strip():
                    xs += [x0,x1]; ys += [y0,y1]
                    sizes.append(float(sp.get("size",0))); fonts.append(sp.get("font",""))
                    spans.append(sp); texts.append(t)
            if not xs: continue
            text = "".join(texts).strip()
            if not text: continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            out.append(Line(text=text, bbox=rect, size=size, font=font, spans=spans))
    y_snap = mm_to_pt(0.3)
    out.sort(key=lambda L: (round(L.bbox.y0 / y_snap)*y_snap, L.bbox.x0))
    return out

def _collect_vector_markers(page: fitz.Page) -> List[fitz.Rect]:
    markers: List[fitz.Rect] = []
    try:
        drawings = page.get_drawings(extended=True)
    except TypeError:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        rect = d.get("rect") or d.get("bbox")
        if not rect: continue
        r = fitz.Rect(float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
        if r.width <= MARKER_MAX_W_PT and r.height <= MARKER_MAX_H_PT:
            markers.append(r)
    return markers

# очень простая склейка: отдельная «точка/тире» строкой + следующая строка текста
_SIMPLE_BULLET_ONLY = re.compile(r"^\s*(?:[•·●∙◦‣▪▫◆■□●●]|{}|-|—|•|)\s*$".format(re.escape(EN_DASH)))

def _merge_bullet_lines(lines: List[Line]) -> List[Line]:
    if not lines: return lines
    out: List[Line] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        t = cur.text.strip()
        if _SIMPLE_BULLET_ONLY.match(t) and (i + 1 < len(lines)):
            nxt = lines[i + 1]
            # достаточно, чтобы следующий началcя правее и был близко по Y
            dy = nxt.bbox.y0 - cur.bbox.y1
            fs = _median([cur.size, nxt.size] + [sp.get("size",0.0) for sp in (cur.spans+nxt.spans)]) or 12.0
            if -2.0 <= dy <= 1.6 * fs and nxt.bbox.x0 >= cur.bbox.x1 - 2.0:
                merged_text = (t + " " + nxt.text.lstrip()).strip()
                mx0 = min(cur.bbox.x0, nxt.bbox.x0); my0 = min(cur.bbox.y0, nxt.bbox.y0)
                mx1 = max(cur.bbox.x1, nxt.bbox.x1); my1 = max(cur.bbox.y1, nxt.bbox.y1)
                out.append(Line(merged_text, fitz.Rect(mx0,my0,mx1,my1),
                                _median([cur.size, nxt.size]) or nxt.size,
                                nxt.font or cur.font, cur.spans + nxt.spans))
                i += 2
                continue
        out.append(cur)
        i += 1
    return out

def _detect_align_justify(lines: List[Line], work_left: float, work_right: float) -> bool:
    if len(lines) < 2: return True
    x1s = [ln.bbox.x1 for ln in lines[:-1]]
    spread = max(x1s) - min(x1s) if x1s else 0.0
    right_air = [max(0.0, work_right - x1) for x1 in x1s]
    return spread <= ALIGN_TOL_PT and (sum(ra <= 8.0 for ra in right_air) >= max(1, int(0.7*len(x1s))))

def _line_spacing_check(lines: List[Line]):
    if len(lines) < 2: return True, None
    y0s = [ln.bbox.y0 for ln in lines]
    dys = [y0s[i]-y0s[i-1] for i in range(1,len(y0s))]
    hs  = [max(0.1, ln.bbox.y1 - ln.bbox.y0) for ln in lines]
    r = _median(dys)/_median(hs)
    lo, hi = LINE_SPACING_TARGET - LINE_SPACING_TOL, LINE_SPACING_TARGET + LINE_SPACING_TOL
    return (lo-1e-3 <= r <= hi+1e-3), r

# ---------- Простая классификация ----------
def _classify_simple(line: Line, work_left: float, vector_markers: List[fitz.Rect]) -> Optional[Item]:
    t = line.text
    x0 = line.bbox.x0
    level = max(0, int(round((x0 - work_left) / INDENT_STEP_PT)))

    # 1) текстовый маркер/номер (просто по префиксу)
    m = RE_START_SIMPLE.match(t)
    if m:
        head = m.group(0)
        if head.lstrip().startswith(EN_DASH):
            return Item(-1, line, level, "bulleted", EN_DASH, "")
        elif head.lstrip()[0].isdigit():
            return Item(-1, line, level, "numbered", head.strip(), "digits")
        else:
            return Item(-1, line, level, "numbered", head.strip(), "rusalpha")

    # 2) векторный маркер слева на той же строке
    #    критерий: заметное перекрытие по Y и он левее начала текста
    best = None; best_dx = None
    for r in vector_markers:
        if _y_overlap(r, line.bbox) / max(1.0, min(r.height, line.bbox.height)) < 0.4:
            continue
        if r.x1 > x0 + 0.6*INDENT_STEP_PT:  # слишком далеко вправо — уже не маркер
            continue
        if r.x1 <= x0:  # левее начала текста — самое то
            dx = x0 - r.x1
            if (best is None) or (dx < best_dx):
                best, best_dx = r, dx
    if best is not None:
        return Item(-1, line, level, "bulleted", "[vector]", "")

    # 3) нет признаков пункта
    return None

def _cluster_items(items: List[Item]) -> List[FoundList]:
    if not items: return []
    items = sorted(items, key=lambda it: (it.line.bbox.y0, it.line.bbox.x0))
    out: List[FoundList] = []
    cur: List[Item] = []

    def flush():
        if len(cur) >= 2:
            xs0 = [it.line.bbox.x0 for it in cur]
            ys0 = [it.line.bbox.y0 for it in cur]
            xs1 = [it.line.bbox.x1 for it in cur]
            ys1 = [it.line.bbox.y1 for it in cur]
            out.append(FoundList(cur[0].page_index0, cur.copy(), fitz.Rect(min(xs0),min(ys0),max(xs1),max(ys1))))
        cur.clear()

    MAX_DY_FACTOR = 2.2
    LEFT_TOL = INDENT_TOL_PT + INDENT_STEP_PT + 6.0

    for it in items:
        if not cur:
            cur = [it]; continue
        prev = cur[-1]
        dy = it.line.bbox.y0 - prev.line.bbox.y0
        fs = _median([it.line.size, prev.line.size] + [sp.get("size",0.0) for sp in (it.line.spans + prev.line.spans)]) or 12.0
        step_ok = 0.1 <= dy <= (MAX_DY_FACTOR * fs)
        left_ok = abs(it.line.bbox.x0 - prev.line.bbox.x0) <= LEFT_TOL
        if step_ok and left_ok:
            cur.append(it)
        else:
            flush()
            cur = [it]
    flush()
    return out

# ---------- Основная проверка ----------
def check_lists(
    pdf_document: fitz.Document,
    *,
    exclude_bboxes_by_page: Optional[Dict[int, List[Tuple[float,float,float,float]]]] = None,
    annotate_pdf: bool = True,
    start_page: int = 1,
) -> Dict[str, object]:
    admin: List[str] = []
    list_bboxes_by_page: Dict[int, List[Tuple[float,float,float,float]]] = defaultdict(list)
    error_pages = set()
    n_lists = 0

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue
        rect = page.rect
        work_left  = rect.x0 + LEFT_MARGIN_PT
        work_right = rect.x1 - RIGHT_MARGIN_PT

        # 1) строки
        lines = _collect_text_lines(page)
        lines = _merge_bullet_lines(lines)  # простая склейка «маркер отдельно»

        # исключаем зоны (таблицы/подписи/фигуры и т.п.)
        if exclude_bboxes_by_page and page_num in exclude_bboxes_by_page:
            exb = [fitz.Rect(*b) for b in exclude_bboxes_by_page.get(page_num, [])]
            keep = []
            for ln in lines:
                inter_areas = []
                for bb in exb:
                    ix0 = max(ln.bbox.x0, bb.x0); iy0 = max(ln.bbox.y0, bb.y0)
                    ix1 = min(ln.bbox.x1, bb.x1); iy1 = min(ln.bbox.y1, bb.y1)
                    if ix1 > ix0 and iy1 > iy0:
                        inter_areas.append((ix1-ix0)*(iy1-iy0))
                drop = (sum(inter_areas)/max(1.0, ln.bbox.get_area())) >= 0.30
                if not drop: keep.append(ln)
            lines = keep
        else:
            exb = []

        # 2) векторные маркеры (маленькие объекты)
        vector_markers = _collect_vector_markers(page)
        # уберём те, что попали в exclude
        if exb:
            vm_keep = []
            for r in vector_markers:
                skip = False
                for bb in exb:
                    ix0 = max(r.x0, bb.x0); iy0 = max(r.y0, bb.y0)
                    ix1 = min(r.x1, bb.x1); iy1 = min(r.y1, bb.y1)
                    if ix1 > ix0 and iy1 > iy0:
                        area = (ix1-ix0)*(iy1-iy0)
                        if area / max(1.0, r.get_area()) >= 0.30:
                            skip = True; break
                if not skip: vm_keep.append(r)
            vector_markers = vm_keep

        # 3) классифицируем пункты максимально просто
        candidates: List[Item] = []
        for ln in lines:
            it = _classify_simple(ln, work_left, vector_markers)
            if it:
                it.page_index0 = pidx
                candidates.append(it)

        # 4) группируем подряд в списки
        found = _cluster_items(candidates)

        # 5) проверки требований и аннотации
        for fl in found:
            n_lists += 1
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            issues = []
            # a) верхний уровень: маркер только EN DASH, либо цифры/рус.буквы с ')'
            top_kind = None
            top_number_kind = None
            for it in fl.items:
                if it.level == 0 and top_kind is None:
                    top_kind = it.kind; top_number_kind = it.number_kind

                if it.level == 0:
                    if it.kind == "bulleted" and it.marker_text not in (EN_DASH, "[vector]"):
                        issues.append(f"Ур.1: маркированный список должен использовать только «{EN_DASH}».")
                    if it.kind == "numbered" and it.number_kind not in ("digits","rusalpha"):
                        issues.append("Ур.1: нумерованный список должен быть вида «1)» или «а)».")
                else:
                    # уровни >1 должны отличаться по виду от уровня 1
                    if top_kind and it.kind == top_kind:
                        issues.append(f"Ур.{it.level+1}: вид маркера/номера должен отличаться от уровня 1.")
                    if top_number_kind and it.number_kind and it.number_kind == top_number_kind:
                        issues.append(f"Ур.{it.level+1}: тип нумерации должен отличаться от уровня 1.")

                # один пробел после текстового маркера
                s = it.line.text
                if it.kind == "bulleted" and it.marker_text == EN_DASH:
                    if not RE_ONLY_ONE_SPACE_AFTER_DASH.match(s):
                        issues.append("После «–» должен быть ровно один пробел.")
                if it.kind == "numbered":
                    if not RE_ONLY_ONE_SPACE_AFTER.match(s):
                        issues.append("После номера должен быть ровно один пробел.")

                # строчная буква после маркера
                tail = RE_START_SIMPLE.sub("", s).lstrip()
                if tail[:1].isalpha() and tail[:1].isupper():
                    issues.append("Пункт списка должен начинаться со строчной буквы.")

            # перед списком — строка с двоеточием и нет пустой строки
            if fl.items:
                all_lines_on_page = lines  # уже очищенные и склеенные
                head_y0 = fl.items[0].line.bbox.y0
                prev = max((ln for ln in all_lines_on_page if ln.bbox.y1 <= head_y0), key=lambda L: L.bbox.y1, default=None)
                if prev:
                    fs = max(10.0, fl.items[0].line.size or 12.0)
                    gap = head_y0 - prev.bbox.y1
                    if gap > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                        issues.append("Перед списком не должно быть пустой строки (интервал до = 0 pt).")
                    if not prev.text.rstrip().endswith(":"):
                        issues.append("Перед списком должно быть предложение, оканчивающееся двоеточием.")
                else:
                    issues.append("Не найдено предложение с двоеточием непосредственно перед списком.")

                # после списка — нет пустой строки
                tail_y1 = fl.items[-1].line.bbox.y1
                nxt = min((ln for ln in all_lines_on_page if ln.bbox.y0 >= tail_y1), key=lambda L: L.bbox.y0, default=None)
                if nxt:
                    fs = max(10.0, fl.items[-1].line.size or 12.0)
                    gap = nxt.bbox.y0 - tail_y1
                    if gap > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                        issues.append("После списка не должно быть пустой строки (интервал после = 0 pt).")

            # выравнивание и межстрочник по блоку
            block_lines = [it.line for it in fl.items]
            if not _detect_align_justify(block_lines, work_left, work_right):
                issues.append("Список должен быть выровнен по ширине.")
            ok_ls, ratio = _line_spacing_check(block_lines)
            if not ok_ls and ratio is not None:
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # кратность 0.75 см по уровню
            for it in fl.items:
                dx = it.line.bbox.x0 - work_left
                want = it.level * INDENT_STEP_PT
                if abs(dx - want) > (INDENT_TOL_PT + 6.0):
                    issues.append(f"Отступ слева у пункта ур.{it.level+1} должен быть кратен 0.75 см.")

            # пунктуация
            def is_short(text: str) -> bool:
                txt = re.sub(r"[^\w\sА-Яа-яЁё-]", "", text, flags=re.UNICODE)
                words = [w for w in txt.split() if re.search(r"[A-Za-zА-Яа-яЁё]", w)]
                return 1 <= len(words) <= 2

            pure_texts = []
            for it in fl.items:
                s = RE_START_SIMPLE.sub("", it.line.text).strip()
                pure_texts.append(s)

            for i, txt in enumerate(pure_texts):
                last = (i == len(pure_texts)-1)
                if not last:
                    if is_short(txt):
                        if not txt.endswith(","):
                            issues.append("Короткий пункт (1–2 слова) должен оканчиваться запятой, кроме последнего (точка).")
                    else:
                        if not txt.endswith(";"):
                            issues.append("Пункт должен оканчиваться точкой с запятой, кроме последнего (точка).")
                else:
                    if not txt.endswith("."):
                        issues.append("Последний пункт списка должен оканчиваться точкой.")
                if not last and re.search(r"\.\s+[А-ЯЁA-Z]", txt):
                    issues.append("Внутри пункта не допускаются новые предложения.")

            # лог и аннотации
            admin.append(f"[List][Стр. {page_num}] пунктов={len(fl.items)} | уровни~{sorted(set(i.level for i in fl.items))}")
            if issues:
                error_pages.add(page_num)
                admin.extend("  - " + m for m in issues)
                if annotate_pdf:
                    try:
                        ann = page.add_text_annot(fitz.Point(fl.bbox.x0, fl.bbox.y0),
                                                  "Список: нарушения\n" + "\n".join(f"• {m}" for m in issues))
                        ann.set_info(title="Сервис нормоконтроля", content="\n".join(issues))
                        ann.update()
                    except Exception:
                        pass
            else:
                if annotate_pdf:
                    try:
                        ann = page.add_text_annot(fitz.Point(fl.bbox.x0, fl.bbox.y0),
                                                  f"Список корректен ({len(fl.items)} п.)")
                        ann.set_info(title="Сервис нормоконтроля", content="Список корректен")
                        ann.update()
                    except Exception:
                        pass

    # --- Пользовательская сводка ---
    user_summary = ("⚠️Проверка списков: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(error_pages)))
                    ) if error_pages else "✅Проверка списков"

    admin_details = (f"[Lists] Найдено списков: {n_lists}"
                     + ("\n" + "\n".join(admin) if admin else ""))

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
    }
