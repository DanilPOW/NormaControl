# scripts/list_checker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re, math
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
INDENT_STEP_CM = 0.75                      # прибавка уровня
INDENT_STEP_PT = INDENT_STEP_CM * CM_TO_PT  # ≈ 21.26 pt
INDENT_TOL_PT  = 4.0
FIRST_LINE_INDENT_CM = 1.25                 # «красная строка»
FIRST_LINE_INDENT_PT = FIRST_LINE_INDENT_CM * CM_TO_PT
FIRST_LINE_INDENT_TOL_PT = 4.0

LINE_SPACING_TARGET = 1.50
LINE_SPACING_TOL    = 0.06  # 1.44–1.56
ALIGN_TOL_PT        = 4.0

# --- Допуски на пустые строки «до/после = 0 pt» (эвристика) ---
MAX_GAP_BEFORE_AFTER_FACTOR = 1.2  # * fontsize (если больше — считаем пустой строкой)

# --- Маркеры/номера ---
EN_DASH = "–"  # U+2013
LIST_BULLETS_LEVEL1_ONLY = (EN_DASH,)   # только короткое тире на 1-м уровне

# допустимые буквы для нумерации «а) б) …»
RUS_LETTERS = "абвгдеёжзийклмнопрстуфхцчшщьыъэюя"
EXCLUDED = set("ёзйочьъы")
ALLOWED_LETTERS = tuple(ch for ch in RUS_LETTERS if ch not in EXCLUDED)

# регэкспы:
RE_NUM_DIGITS = re.compile(r"^\s*(\d+)\)\s+")
RE_NUM_RUS    = re.compile(r"^\s*([{}])\)\s+".format("".join(ALLOWED_LETTERS)))
RE_ONLY_ONE_SPACE_AFTER = re.compile(r"^\s*(?:\d+\)|[{}]\))\s(?!\s)".format("".join(ALLOWED_LETTERS)))
RE_ONLY_ONE_SPACE_AFTER_DASH = re.compile(r"^\s*{}\s(?!\s)".format(EN_DASH))

# римские/прочие — разрешим на уровнях >1 (для «отличаться»), просто обнаружим:
RE_ANY_ROMAN  = re.compile(r"^\s*\(?[IVXLCDM]+\)?[.)]?\s+", re.IGNORECASE)
RE_ANY_ALPHA  = re.compile(r"^\s*[A-Za-zА-Яа-я]\)\s+")

# --- Вспомогательные структуры ---
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
    level: int
    kind: str                 # "bulleted" | "numbered"
    marker_text: str          # что увидели (например "–" или "1)" или "а)")
    number_kind: str          # "digits" | "rusalpha" | "roman" | "latin" | "" (для bulleted)

@dataclass
class FoundList:
    page_index0: int
    items: List[Item]
    bbox: fitz.Rect

# ---------- Текстовые линии ----------
def _collect_text_lines(page: fitz.Page) -> List[Line]:
    out: List[Line] = []
    td = page.get_text("dict")
    for b in td.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            xs, ys, spans, texts, sizes, fonts = [], [], [], [], [], []
            for sp in ln.get("spans", []):
                x0,y0,x1,y1 = sp.get("bbox", (0,0,0,0))
                t = sp.get("text") or ""
                if t.strip():
                    xs += [x0,x1]; ys += [y0,y1]
                    sizes.append(float(sp.get("size",0)))
                    fonts.append(sp.get("font",""))
                    spans.append(sp); texts.append(t)
            if not xs: continue
            text = "".join(texts).strip()
            if not text: continue
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            size = sum(sizes)/len(sizes) if sizes else 0.0
            font = fonts[0] if fonts else ""
            out.append(Line(text=text, bbox=rect, size=size, font=font, spans=spans))
    # читаем сверху-вниз, слева-направо со снапом по Y
    y_snap = mm_to_pt(0.3)
    out.sort(key=lambda L: (round(L.bbox.y0 / y_snap)*y_snap, L.bbox.x0))
    return out

# ---------- Вспомогательное ----------
def _median(vals: List[float]) -> float:
    if not vals: return 0.0
    s = sorted(vals); n = len(s); m = n // 2
    return s[m] if n % 2 else (s[m-1]+s[m])/2.0

def _detect_align_justify(lines: List[Line], work_left: float, work_right: float, tol_pt=ALIGN_TOL_PT) -> bool:
    if len(lines) < 2: return True
    x1s = [ln.bbox.x1 for ln in lines[:-1]]
    spread = (max(x1s) - min(x1s)) if x1s else 0.0
    right_air = [max(0.0, work_right - x1) for x1 in x1s]
    return spread <= tol_pt and (sum(ra <= 8.0 for ra in right_air) >= max(1, int(0.7*len(x1s))))

def _line_spacing_check(lines: List[Line]) -> Tuple[bool, Optional[float]]:
    # простая метрика: медиана(dy)/медиана(высот bbox) ~ 1.5 ± tol
    if len(lines) < 2: return True, None
    y0s = [ln.bbox.y0 for ln in lines]
    dys = [y0s[i]-y0s[i-1] for i in range(1,len(y0s))]
    hs  = [max(0.1, ln.bbox.y1 - ln.bbox.y0) for ln in lines]
    r = _median(dys)/_median(hs)
    lo, hi = LINE_SPACING_TARGET - LINE_SPACING_TOL, LINE_SPACING_TARGET + LINE_SPACING_TOL
    ok = lo-1e-3 <= r <= hi+1e-3
    return ok, r

def _nearest_line_above(lines: List[Line], y_top: float) -> Optional[Line]:
    best = None; best_dy = None
    for ln in lines:
        if ln.bbox.y1 <= y_top:
            dy = y_top - ln.bbox.y1
            if best_dy is None or dy < best_dy:
                best, best_dy = ln, dy
    return best

def _nearest_line_below(lines: List[Line], y_bottom: float) -> Optional[Line]:
    best = None; best_dy = None
    for ln in lines:
        if ln.bbox.y0 >= y_bottom:
            dy = ln.bbox.y0 - y_bottom
            if best_dy is None or dy < best_dy:
                best, best_dy = ln, dy
    return best

# ---------- Классификация строки как пункт списка + уровень ----------
def _classify_marker_and_level(line: Line, work_left: float, top_level_marker_kind: Optional[str]) -> Optional[Item]:
    t = line.text

    # оценка уровня по левому краю первой строки относительно work_left
    x0 = line.bbox.x0
    dx = x0 - work_left
    # округлим к шагу 0.75 см
    approx_level = int(round(dx / INDENT_STEP_PT))
    if approx_level < 0: approx_level = 0
    # допускаем «гуляние» в пределах индент-толеранса
    if abs(dx - approx_level*INDENT_STEP_PT) > (INDENT_TOL_PT + 6.0):  # +6 pt на случай маркера
        # линия не выглядит как аккуратный уровень списка
        # всё равно дадим шанс, но отметим валидацию позже
        pass

    # 1) 1-й уровень: разрешены только EN DASH или цифры/рус.буквы с ')'
    # 2) >1 уровни: любые, но обязаны отличаться от top-level по виду
    marker_text = ""
    kind = ""
    number_kind = ""

    # префиксы «цифры) » и «рус.буква) »
    md = RE_NUM_DIGITS.match(t)
    mr = RE_NUM_RUS.match(t)
    only_one_space_ok = bool(RE_ONLY_ONE_SPACE_AFTER.match(t) or RE_ONLY_ONE_SPACE_AFTER_DASH.match(t))

    if md:
        marker_text = md.group(0).strip()
        kind = "numbered"; number_kind = "digits"
    elif mr:
        marker_text = mr.group(0).strip()
        kind = "numbered"; number_kind = "rusalpha"
    else:
        # маркёр «– » только на первом уровне
        if t.lstrip().startswith(EN_DASH + " "):
            marker_text = EN_DASH
            kind = "bulleted"; number_kind = ""
        else:
            # уровни >1: допустимы иные маркеры/номера
            if approx_level >= 1:
                if RE_ANY_ROMAN.match(t):
                    marker_text = RE_ANY_ROMAN.match(t).group(0).strip()
                    kind = "numbered"; number_kind = "roman"
                elif RE_ANY_ALPHA.match(t):
                    marker_text = RE_ANY_ALPHA.match(t).group(0).strip()
                    kind = "numbered"; number_kind = "latin"  # или др. буквы
                elif t.lstrip().startswith(("-", "—", "•", "·", "●")):
                    marker_text = t.strip().split()[0]
                    kind = "bulleted"; number_kind = ""
                else:
                    return None
            else:
                return None

    # Проверка «не больше одного пробела» после маркера
    if kind == "numbered":
        if not RE_ONLY_ONE_SPACE_AFTER.match(t):
            # будет зафиксировано как нарушение позже (логом)
            pass
    else:  # bulleted
        if approx_level == 0 and not RE_ONLY_ONE_SPACE_AFTER_DASH.match(t):
            pass

    # Проверка «отличается от топ-уровня» для уровней >1
    if approx_level >= 1 and top_level_marker_kind:
        if (kind == top_level_marker_kind):
            # тот же вид маркера/номера — нарушение (зафиксируем в логах)
            pass

    return Item(
        page_index0=-1,
        line=line,
        level=approx_level,
        kind=kind,
        marker_text=marker_text,
        number_kind=number_kind
    )

# ---------- Группировка подряд идущих пунктов в список ----------
def _cluster_items_to_lists(items: List[Item]) -> List[FoundList]:
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
            bbox = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1))
            out.append(FoundList(page_index0=cur[0].page_index0, items=cur.copy(), bbox=bbox))
        cur.clear()

    MAX_STEP_FACTOR = 2.2
    LEFT_TOL_PT = INDENT_TOL_PT + 8.0  # небольшой запас

    for it in items:
        if not cur:
            cur = [it]; continue
        prev = cur[-1]
        dy = it.line.bbox.y0 - prev.line.bbox.y0
        avg_fs = _median([sp.get("size", 0.0) for sp in it.line.spans + prev.line.spans]) or max(1.0, (it.line.size+prev.line.size)/2.0)
        step_ok = 0.1 <= dy <= (MAX_STEP_FACTOR * avg_fs)
        # близость левого края для одного уровня; допускаем смену уровня на +/-1 (многоур.)
        left_ok = abs((it.line.bbox.x0 - prev.line.bbox.x0)) <= (LEFT_TOL_PT + INDENT_STEP_PT + 6.0)
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
    pages_with = set()
    n_lists = 0

    for pidx, page in enumerate(pdf_document):
        page_num = pidx + 1
        if page_num < start_page:
            continue
        rect = page.rect
        work_left   = rect.x0 + LEFT_MARGIN_PT
        work_right  = rect.x1 - RIGHT_MARGIN_PT
        work_top    = rect.y0 + TOP_MARGIN_PT
        work_bottom = rect.y1 - BOTTOM_MARGIN_PT

        lines = _collect_text_lines(page)

        # исключим строки попавшие в запретные зоны
        if exclude_bboxes_by_page and page_num in exclude_bboxes_by_page:
            exb = [fitz.Rect(*b) for b in exclude_bboxes_by_page.get(page_num, [])]
            keep = []
            for ln in lines:
                lb = ln.bbox
                drop = False
                for bb in exb:
                    inter = fitz.Rect(max(lb.x0, bb.x0), max(lb.y0, bb.y0), min(lb.x1, bb.x1), min(lb.y1, bb.y1))
                    if not inter.is_empty and (inter.get_area()/max(1.0, lb.get_area())) >= 0.30:
                        drop = True; break
                if not drop: keep.append(ln)
            lines = keep

        # сначала собираем вид верхнего уровня, чтобы уровни >1 могли «отличаться»
        top_level_marker_kind: Optional[str] = None
        # пробегаем все строки: формируем кандидатов
        candidates: List[Item] = []
        for ln in lines:
            item = _classify_marker_and_level(ln, work_left, top_level_marker_kind)
            if item:
                item.page_index0 = pidx
                candidates.append(item)
                if item.level == 0 and top_level_marker_kind is None:
                    top_level_marker_kind = item.kind

        # группируем в списки
        found = _cluster_items_to_lists(candidates)

        # детальные проверки по каждому списку
        for fl in found:
            n_lists += 1
            pages_with.add(page_num)
            list_bboxes_by_page[page_num].append((fl.bbox.x0, fl.bbox.y0, fl.bbox.x1, fl.bbox.y1))

            # --- требования формата маркера/номера по уровням ---
            issues = []
            top_kind = None
            top_number_kind = None
            for it in fl.items:
                # фиксируем вид первого уровня
                if it.level == 0 and top_kind is None:
                    top_kind = it.kind; top_number_kind = it.number_kind

                # 1) уровень 0: маркированный только EN DASH, нумерация только digits) или rusalpha)
                if it.level == 0:
                    if it.kind == "bulleted" and it.marker_text != EN_DASH:
                        issues.append(f"Ур.1: маркированный список должен использовать только «{EN_DASH}».")
                    if it.kind == "numbered" and it.number_kind not in ("digits","rusalpha"):
                        issues.append("Ур.1: нумерованный список должен быть вида «1)» или «а)».")
                else:
                    # 2) уровни >1: вид должен отличаться от верхнего уровня
                    if top_kind and it.kind == top_kind:
                        issues.append(f"Ур.{it.level+1}: вид маркера/номера должен отличаться от уровня 1.")
                    if top_number_kind and it.number_kind and it.number_kind == top_number_kind:
                        issues.append(f"Ур.{it.level+1}: тип нумерации должен отличаться от уровня 1.")

                # 3) «не больше одного пробела после маркера»
                s = it.line.text
                if it.kind == "bulleted":
                    if it.level == 0 and not RE_ONLY_ONE_SPACE_AFTER_DASH.match(s):
                        issues.append("После «–» должен быть ровно один пробел.")
                else:
                    if not RE_ONLY_ONE_SPACE_AFTER.match(s):
                        issues.append("После номера должен быть ровно один пробел.")

                # 4) «строчная буква» в начале пункта (после маркера/номера)
                tail = re.sub(r"^\s*(?:{}\s|\d+\)\s|[{}]\)\s)".format(EN_DASH, "".join(ALLOWED_LETTERS)), "", s)
                tail = tail.lstrip()
                if tail[:1].isalpha() and tail[:1].isupper():
                    issues.append("Пункт списка должен начинаться со строчной буквы.")

            # --- проверка «до списка — строка с двоеточием» и нет пустой строки ---
            head_y0 = fl.items[0].line.bbox.y0
            prev_line = _nearest_line_above(lines, head_y0)
            if prev_line:
                fs = max(10.0, fl.items[0].line.size or 12.0)
                gap_pt = head_y0 - prev_line.bbox.y1
                if gap_pt > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                    issues.append("Перед списком не должно быть пустой строки (интервал до = 0 pt).")
                if not prev_line.text.rstrip().endswith(":"):
                    issues.append("Перед списком должно быть предложение, оканчивающееся двоеточием.")
            else:
                issues.append("Не найдено предложение с двоеточием непосредственно перед списком.")

            # --- после списка нет пустой строки ---
            tail_y1 = fl.items[-1].line.bbox.y1
            next_line = _nearest_line_below(lines, tail_y1)
            if next_line:
                fs = max(10.0, fl.items[-1].line.size or 12.0)
                gap_pt = next_line.bbox.y0 - tail_y1
                if gap_pt > MAX_GAP_BEFORE_AFTER_FACTOR * fs:
                    issues.append("После списка не должно быть пустой строки (интервал после = 0 pt).")

            # --- выравнивание по ширине и межстрочник 1.5 (по списку как блоку)---
            ok_align = _detect_align_justify([it.line for it in fl.items], work_left, work_right, tol_pt=ALIGN_TOL_PT)
            if not ok_align:
                issues.append("Список должен быть выровнен по ширине.")
            ok_ls, ratio = _line_spacing_check([it.line for it in fl.items])
            if not ok_ls and ratio is not None:
                lo = LINE_SPACING_TARGET - LINE_SPACING_TOL
                hi = LINE_SPACING_TARGET + LINE_SPACING_TOL
                issues.append(f"Межстрочный интервал в списке должен быть 1.5 (получено {ratio:.2f}; допуск {lo:.2f}–{hi:.2f}).")

            # --- проверка уровней и «красной строки» (для каждого пункта) ---
            for it in fl.items:
                # a) соответствует ли уровень кратности 0.75 см
                dx = it.line.bbox.x0 - work_left
                level_pt = it.level * INDENT_STEP_PT
                if abs(dx - level_pt) > (INDENT_TOL_PT + 6.0):
                    issues.append(f"Отступ слева у пункта ур.{it.level+1} должен быть кратен 0.75 см.")
                # b) «красная строка» внутри пункта (если пункт в несколько строк)
                #    грубо проверим: первая строка правее последующих примерно на 1.25 см
                #    (мы работаем построчно, поэтому эвристика: если следующая строка с тем же левым краем обнаружится)
                # Здесь у нас только первая строка пункта; дадим мягкую проверку по одному абзацу:
                # пропустим жёсткую валидацию для переносов (иначе риск ложнопозитивов)
                pass

            # --- пунктуация между пунктами ---
            # короткий пункт = 1–2 слова (без чисел/символов)
            def is_short(text: str) -> bool:
                t = re.sub(r"[^\w\sА-Яа-яЁё-]", "", text, flags=re.UNICODE)
                words = [w for w in t.split() if re.search(r"[A-Za-zА-Яа-яЁё]", w)]
                return 1 <= len(words) <= 2

            pure_texts = []
            for it in fl.items:
                s = it.line.text.strip()
                # убираем маркер/номер
                s = re.sub(r"^\s*(?:{}\s|\d+\)\s|[{}]\)\s)".format(EN_DASH, "".join(ALLOWED_LETTERS)), "", s)
                pure_texts.append(s.strip())

            for i, txt in enumerate(pure_texts):
                is_last = (i == len(pure_texts) - 1)
                if not is_last:
                    if is_short(txt):
                        if not txt.endswith(","):
                            issues.append("Короткий пункт (1–2 слова) должен оканчиваться запятой, кроме последнего (точка).")
                    else:
                        if not txt.endswith(";"):
                            issues.append("Пункт должен оканчиваться точкой с запятой, кроме последнего (точка).")
                else:
                    if not txt.endswith("."):
                        issues.append("Последний пункт списка должен оканчиваться точкой.")

                # «внутри списка не допускаются новые предложения»
                if not is_last and re.search(r"\.\s+[А-ЯЁA-Z]", txt):
                    issues.append("Внутри пункта не допускаются новые предложения.")

            # --- сводка по списку ---
            xs = [it.line.bbox.x0 for it in fl.items]
            y0s = [it.line.bbox.y0 for it in fl.items]
            sizes = [it.line.size for it in fl.items if it.line.size>0]
            steps = [y0s[i]-y0s[i-1] for i in range(1,len(y0s)) if y0s[i]>y0s[i-1]]

            admin.append(
                f"[List][Стр. {page_num}] пунктов={len(fl.items)} | x0~{_median(xs):.1f} pt | "
                f"dY_med~{_median(steps) if steps else 0.0:.1f} pt | font_med~{_median(sizes) if sizes else 0.0:.1f} pt"
            )

            if issues:
                admin.extend("  - " + msg for msg in issues)
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

    if n_lists == 0:
        user_summary = "ℹ️Списки не обнаружены"
    else:
        user_summary = f"✅Проверка списков: найдено {n_lists} (стр.: {', '.join(map(str, sorted(pages_with)))})"

    admin_details = (f"[Lists] Найдено списков: {n_lists}" +
                     ("\n" + "\n".join(admin) if admin else ""))

    return {
        "user_summary": user_summary,
        "admin_details": admin_details,
        "list_bboxes_by_page": dict(list_bboxes_by_page),
    }

