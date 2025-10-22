from __future__ import annotations
import re, fitz
from typing import List, Tuple, Dict, Optional

PREFIX = "Сервис нормоконтроля: ошибка списка источников"
ERR_NUMSEQ   = f"{PREFIX}. Нарушена сквозная нумерация записей"
ERR_NUMFMT   = f"{PREFIX}. Номер записи должен иметь вид '1.'"
ERR_INDENT   = f"{PREFIX}. У записи должен быть абзацный отступ 1.25 см"
ERR_ENDDOT   = f"{PREFIX}. Запись должна заканчиваться точкой"
ERR_FORMAT   = f"{PREFIX}. Неправильное оформление для типа источника"
HEAD_START   = "список использованных источников"
HEAD_STOP_RE = re.compile(r"^\s*приложение", re.IGNORECASE)

CM = 28.3464567
INDENT_PT = 1.25 * CM
TOL = 4.5

# Паттерны для определения типов источников
PATTERNS = {
    "Книга/учебник": [
        r".*учебник.*/.*\..*—.*:.*\d{4}.*\d+.*с",
        r".*учебное пособие.*/.*\..*—.*:.*\d{4}",
        r".*монография.*/.*\..*—.*:.*\d{4}"
    ],
    "Сборник": [
        r".*сборник.*трудов.*/.*под ред\..*—.*:.*\d{4}",
        r".*сборник.*научных.*работ.*—.*:.*\d{4}"
    ],
    "Нормативный акт": [
        r".*Федеральный закон.*№.*—.*Москва.*\d{4}",
        r".*Российская Федерация\..*Законы.*",
        r".*ГОСТ.*\d{4}.*Библиографическая запись"
    ],
    "Стандарт": [
        r"ГОСТ.*\d{4}.*стандарт.*Российской.*Федерации",
        r".*национальный стандарт.*"
    ],
    "Диссертация": [
        r".*автореферат.*на соискание.*степени.*",
        r".*диссертация.*кандидат.*наук.*"
    ],
    "Статья": [
        r".*//.*журнал.*—.*\d{4}.*—.*Т\.\d+.*№\d+",
        r".*//.*—.*\d{4}.*—.*№\d+.*—.*С\.\d+",
        r".*//.*сборник.*—.*\d{4}.*—.*С\.\d+"
    ],
    "Электронный ресурс": [
        r".*URL:.*https?://.*дата обращения",
        r".*электронный.*ресурс.*URL:",
        r".*сайт.*—.*URL:.*https?://"
    ]
}

def _annot(page: fitz.Page, x: float, y: float, msg: str):
    try:
        a = page.add_text_annot(fitz.Point(x, y), msg)
        a.set_info(title=PREFIX, content=msg)
        a.update()
    except:  # noqa: E722
        pass

def _union_bbox(spans: List[dict]) -> fitz.Rect:
    r = None
    for s in spans:
        x0, y0, x1, y1 = s["bbox"]
        rr = fitz.Rect(x0, y0, x1, y1)
        r = rr if r is None else r | rr
    return r or fitz.Rect(0, 0, 0, 0)

def _first_line_text(blocks: List[dict]) -> str:
    for b in blocks:
        for ln in b.get("lines", []):
            txt = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
            if txt:
                return txt
    return ""

def _page_text_rows(page: fitz.Page) -> List[Tuple[float, float, List[dict], str]]:
    rows = []
    for b in page.get_text("dict").get("blocks", []):
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            if not spans: continue
            y0 = min(s["bbox"][1] for s in spans)
            y1 = max(s["bbox"][3] for s in spans)
            txt = "".join(s.get("text", "") for s in spans)
            rows.append((y0, y1, spans, txt))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows

def _page_margins(rows: List[Tuple[float, float, List[dict], str]]) -> Tuple[float, float]:
    xs = [s["bbox"][0] for _,_,sp,txt in rows for s in sp if txt.strip()]
    xe = [s["bbox"][2] for _,_,sp,txt in rows for s in sp if txt.strip()]
    return (min(xs), max(xe)) if xs and xe else (72.0, 523.0)

def _parse_number_prefix(spans: List[dict]) -> Tuple[Optional[int], bool, fitz.Rect, int, bool, bool]:
    if not spans: return None, False, fitz.Rect(), 0, False, False
    t0 = spans[0].get("text", "")
    m_same = re.match(r"\s*(\d{1,3})\.\s*", t0)
    if m_same:
        return int(m_same.group(1)), True, fitz.Rect(*spans[0]["bbox"]), 1, True, False
    m_num = re.match(r"\s*(\d{1,3})\s*$", t0)
    if m_num and len(spans) >= 2 and re.match(r"^\.\s*", spans[1].get("text", "")):
        return int(m_num.group(1)), True, _union_bbox(spans[:2]), 2, False, True
    m_loose = re.match(r"\s*(\d{1,3})\s+[.]\s*", t0)
    if m_loose:
        return int(m_loose.group(1)), True, fitz.Rect(*spans[0]["bbox"]), 1, True, False
    return None, False, fitz.Rect(*spans[0]["bbox"]), 0, False, False

def _first_content_x(spans: List[dict], used_spans: int) -> float:
    for s in spans[used_spans:]:
        if re.search(r"[A-Za-zА-Яа-яЁё0-9]", s.get("text", "")):
            return float(s["bbox"][0])
    return float(spans[min(used_spans, len(spans)-1)]["bbox"][2])

def _point_for_annot(spans: List[dict]) -> Tuple[float, float]:
    if not spans: return 10.0, 10.0
    x0, y0, x1, y1 = spans[0]["bbox"]
    return x0 + 2.0, y0 + (y1 - y0) * 0.45

def _detect_source_type(text: str) -> Tuple[str, bool]:
    """Определяет тип источника и проверяет оформление"""
    text_lower = text.lower()
    
    for source_type, patterns in PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return source_type, True
                
    # Дополнительные проверки для каждого типа
    if re.search(r".*//.*журнал.*|.*//.*сборник.*", text_lower):
        return "Статья", re.search(r".*—.*\d{4}.*—.*[ТС]\.", text_lower) is not None
    elif "url:" in text_lower and "http" in text_lower:
        return "Электронный ресурс", "дата обращения" in text_lower
    elif re.search(r".*учебник.*|.*учебное пособие.*", text_lower):
        return "Книга/учебник", re.search(r".*—.*:.*\d{4}.*—.*\d+.*с", text_lower) is not None
    elif "гост" in text_lower:
        return "Стандарт", "национальный стандарт" in text_lower
    elif "закон" in text_lower and "федеральный" in text_lower:
        return "Нормативный акт", True
        
    return "Неопределен", False

def check_references(doc: fitz.Document) -> dict:
    user_pages_viol: set[int] = set()
    debug: List[str] = []
    refs_pages: List[int] = []

    # Поиск раздела библиографии
    p_start, p_end_exc = None, None
    for pno in range(len(doc)):
        first = _first_line_text(doc[pno].get_text("dict").get("blocks", [])).strip().strip(":").lower()
        if first == HEAD_START:
            p_start = pno
            break
    if p_start is None:
        return {
            "user_summary": "⚠️Список источников: раздел не найден",
            "admin_details": "[References]\nРаздел 'СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ' не обнаружен."
        }

    for pno in range(p_start, len(doc)):
        first = _first_line_text(doc[pno].get_text("dict").get("blocks", []))
        if HEAD_STOP_RE.match(first):
            p_end_exc = pno
            break
    p_end_exc = p_end_exc or len(doc)

    # Сбор записей
    entries = []
    page_rows_cache: Dict[int, List[Tuple[float,float,List[dict],str]]] = {}
    page_margin_cache: Dict[int, Tuple[float,float]] = {}
    starts = []
    
    for pno in range(p_start, p_end_exc):
        rows = _page_text_rows(doc[pno])
        page_rows_cache[pno] = rows
        page_margin_cache[pno] = _page_margins(rows)
        for i, (y0, y1, sp, txt) in enumerate(rows):
            num, okfmt, bbox, used, dot_same, dot_next = _parse_number_prefix(sp)
            if num is not None:
                starts.append((pno, i, num, okfmt, used, dot_same, dot_next))

    starts.sort(key=lambda t: (t[0], page_rows_cache[t[0]][t[1]][0]))
    
    for k, (pno, idx, num, okfmt, used, dot_same, dot_next) in enumerate(starts):
        rows = page_rows_cache[pno]
        tail = [(pno, idx)]
        
        next_pno, next_idx = (starts[k+1][0], starts[k+1][1]) if k+1 < len(starts) else (None, None)
        for j in range(idx+1, len(rows)):
            if (pno == next_pno and j >= next_idx) or _parse_number_prefix(rows[j][2])[0] is not None:
                break
            tail.append((pno, j))
            
        pp = pno + 1
        while k+1 < len(starts) and pp < (next_pno or p_end_exc):
            rws = page_rows_cache.get(pp, _page_text_rows(doc[pp]))
            page_rows_cache[pp] = rws
            for j in range(len(rws)):
                if (pp == next_pno and j >= next_idx) or _parse_number_prefix(rws[j][2])[0] is not None:
                    break
                tail.append((pp, j))
            pp += 1

        entries.append({"num": num, "okfmt": okfmt, "head": (pno, idx), "body": tail, "used": used})

    # Проверки
    total = len(entries)
    per_page_counts: Dict[int, int] = {}
    expected = 1
    source_types = {}
    
    for e in entries:
        pno, idx = e["head"]
        per_page_counts[pno] = per_page_counts.get(pno, 0) + 1
        page = doc[pno]
        y0, y1, sp, _ = page_rows_cache[pno][idx]
        x_annot, y_annot = _point_for_annot(sp)

        # Сбор полного текста записи
        tail_text = []
        for (pp, jj) in e["body"]:
            tail_text.append("".join(s.get("text","") for s in page_rows_cache[pp][jj][2]))
        full_text = " ".join(t.strip() for t in tail_text).rstrip()
        
        # Определение типа источника и проверка оформления
        source_type, format_ok = _detect_source_type(full_text)
        source_types[e["num"]] = source_type
        
        if not format_ok and source_type != "Неопределен":
            _annot(page, x_annot, y_annot, f"{ERR_FORMAT} '{source_type}'")
            user_pages_viol.add(pno+1)
            debug.append(f"Стр.{pno+1} FORMAT неправильное оформление для {source_type}: {full_text[:100]}...")

        # Базовые проверки
        checks = [
            (e["num"] != expected, ERR_NUMSEQ, f"NUMSEQ ожидается {expected}, найдено {e['num']}"),
            (not e["okfmt"], ERR_NUMFMT, f"NUMFMT без точки рядом с номером: {e['num']}"),
            (not full_text.endswith("."), ERR_ENDDOT, f"ENDDOT запись №{e['num']} не оканчивается точкой")
        ]
        
        left_margin, _ = page_margin_cache[pno]
        cx = _first_content_x(sp, e["used"])
        indent_ok = left_margin + INDENT_PT - TOL <= cx <= left_margin + INDENT_PT + TOL
        checks.append((not indent_ok, ERR_INDENT, f"INDENT x={cx:.1f} ожидалось ~{left_margin+INDENT_PT:.1f}"))

        for condition, err_msg, debug_msg in checks:
            if condition:
                _annot(page, x_annot, y_annot, err_msg)
                user_pages_viol.add(pno+1)
                debug.append(f"Стр.{pno+1} {debug_msg}")

        if e["num"] == expected:
            expected += 1

    # Формирование отчета
    pages_list = [f"Стр. {p+1}: найдено записей {per_page_counts.get(p,0)}" 
                  for p in range(p_start, p_end_exc)]
    
    type_stats = "\n".join([f"- {typ}: {list(source_types.values()).count(typ)}" 
                           for typ in set(source_types.values())])
    
    admin = f"""[References]
Диапазон страниц: {p_start+1}–{p_end_exc}
Найдено записей всего: {total}
Распределение по типам:
{type_stats}

{"".join(pages_list)}"""

    if debug:
        admin += "\n\n[ReferencesDebug]\n" + "\n".join(debug)

    user = ("✅Список источников: нарушений не обнаружено" if not user_pages_viol else
            f"⚠️Список источников: нарушения на страницах {', '.join(map(str, sorted(user_pages_viol)))}")
    
    return {"user_summary": user, "admin_details": admin}
