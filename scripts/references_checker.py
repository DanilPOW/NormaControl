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

def _annot(page: fitz.Page, x: float, y: float, msg: str):
    try:
        a = page.add_text_annot(fitz.Point(x, y), msg)
        a.set_info(title=PREFIX, content=msg)
        a.update()
    except:  # noqa: E722
        pass

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

def _parse_number_prefix(spans: List[dict]) -> Tuple[Optional[int], bool, fitz.Rect, int]:
    if not spans: return None, False, fitz.Rect(), 0
    t0 = spans[0].get("text", "")
    m = re.match(r"\s*(\d{1,3})[\.\s]*", t0)
    if m:
        return int(m.group(1)), '.' in t0[:10], fitz.Rect(*spans[0]["bbox"]), 1
    return None, False, fitz.Rect(*spans[0]["bbox"]), 0

def _first_content_x(spans: List[dict], used_spans: int) -> float:
    for s in spans[used_spans:]:
        if re.search(r"[A-Za-zА-Яа-яЁё0-9]", s.get("text", "")):
            return float(s["bbox"][0])
    return float(spans[min(used_spans, len(spans)-1)]["bbox"][2])

def _point_for_annot(spans: List[dict]) -> Tuple[float, float]:
    if not spans: return 10.0, 10.0
    x0, y0, x1, y1 = spans[0]["bbox"]
    return x0 + 2.0, y0 + (y1 - y0) * 0.45

def _analyze_source_candidate(text: str, debug: List[str]) -> Tuple[str, int, bool]:
    text_lower = text.lower()
    features = []
    
    if re.search(r'\d{4}.*\d+.*с', text): features.append("ГОД_СТРАНИЦЫ")
    if 'isbn' in text_lower: features.append("ISBN")
    if 'url:' in text_lower or 'http' in text_lower: features.append("URL")
    if 'дата обращения' in text_lower: features.append("ДАТА_ОБРАЩЕНИЯ")
    if 'учебник' in text_lower: features.append("УЧЕБНИК")
    if 'пособие' in text_lower: features.append("ПОСОБИЕ") 
    if 'сборник' in text_lower: features.append("СБОРНИК")
    if 'закон' in text_lower: features.append("ЗАКОН")
    if 'гост' in text_lower: features.append("ГОСТ")
    if 'автореферат' in text_lower: features.append("АВТОРЕФЕРАТ")
    if 'диссертация' in text_lower: features.append("ДИССЕРТАЦИЯ")
    if '//' in text: features.append("СТАТЬЯ_СЛЭШ")
    if 'журнал' in text_lower: features.append("ЖУРНАЛ")
    if any(word in text_lower for word in ['под ред', 'ред.']): features.append("РЕДАКТОР")
    
    type_scores = {
        "Книга/учебник": len([f for f in features if f in ["УЧЕБНИК", "ПОСОБИЕ", "ISBN", "ГОД_СТРАНИЦЫ"]]),
        "Сборник": len([f for f in features if f in ["СБОРНИК", "РЕДАКТОР", "ISBN"]]),
        "Нормативный акт": len([f for f in features if f in ["ЗАКОН", "ФЕДЕРАЛЬНЫЙ"]]),
        "Стандарт": len([f for f in features if f in ["ГОСТ", "СТАНДАРТ", "URL"]]),
        "Диссертация": len([f for f in features if f in ["АВТОРЕФЕРАТ", "ДИССЕРТАЦИЯ", "URL"]]),
        "Статья": len([f for f in features if f in ["СТАТЬЯ_СЛЭШ", "ЖУРНАЛ", "Т.", "С."]]),
        "Электронный ресурс": len([f for f in features if f in ["URL", "ДАТА_ОБРАЩЕНИЯ", "САЙТ"]])
    }
    
    probable_type, score = max(type_scores.items(), key=lambda x: x[1])
    format_ok = _validate_source_format(probable_type, text, debug)
    
    debug.append(f"КАНДИДАТ: '{text[:80]}...'")
    debug.append(f"  ПРИЗНАКИ: {features}")
    debug.append(f"  ТИП: {probable_type} (баллы: {score})")
    debug.append(f"  РЕГУЛЯРКИ: {'ПРОШЕЛ' if format_ok else 'НЕ ПРОШЕЛ'}")
    debug.append("")
    
    return probable_type, score, format_ok

def _validate_source_format(source_type: str, text: str, debug: List[str]) -> bool:
    validation_patterns = {
        "Книга/учебник": [
            (r'[А-Я][а-я]+ [А-Я]\.[А-Я]\.', "автор с инициалами"),
            (r'.*—.*:.*\d{4}', "год издания с тире"),
            (r'.*—.*\d+.*с', "количество страниц")
        ],
        "Сборник": [
            (r'сборник.*трудов', "сборник трудов"),
            (r'под ред\.', "редактор")
        ],
        "Нормативный акт": [
            (r'Российская Федерация', "государственная принадлежность"),
            (r'Федеральный закон', "тип документа"),
            (r'№ \d+-ФЗ', "номер закона")
        ],
        "Стандарт": [
            (r'ГОСТ.*\d{4}', "обозначение стандарта"),
            (r'национальный стандарт', "статус")
        ],
        "Диссертация": [
            (r'автореферат', "тип работы"),
            (r'на соискание.*степени', "ученая степень")
        ],
        "Статья": [
            (r'//', "двойной слеш"),
            (r'—.*\d{4}.*—', "год издания"),
            (r'[ТС]\.\d+', "том/страницы")
        ],
        "Электронный ресурс": [
            (r'URL:', "URL"),
            (r'дата обращения', "дата обращения")
        ]
    }
    
    if source_type not in validation_patterns:
        return True
    
    failed = []
    for pattern, description in validation_patterns[source_type]:
        if not re.search(pattern, text, re.IGNORECASE):
            failed.append(description)
    
    if failed:
        debug.append(f"  ОШИБКИ ОФОРМЛЕНИЯ: {failed}")
    
    return len(failed) == 0

def check_references(doc: fitz.Document) -> dict:
    user_pages_viol: set[int] = set()
    debug: List[str] = []

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

    entries = []
    page_rows_cache: Dict[int, List[Tuple[float,float,List[dict],str]]] = {}
    page_margin_cache: Dict[int, Tuple[float,float]] = {}
    starts = []
    
    for pno in range(p_start, p_end_exc):
        rows = _page_text_rows(doc[pno])
        page_rows_cache[pno] = rows
        page_margin_cache[pno] = _page_margins(rows)
        for i, (y0, y1, sp, txt) in enumerate(rows):
            num, okfmt, bbox, used = _parse_number_prefix(sp)
            if num is not None:
                starts.append((pno, i, num, okfmt, used))

    starts.sort(key=lambda t: (t[0], page_rows_cache[t[0]][t[1]][0]))
    
    for k, (pno, idx, num, okfmt, used) in enumerate(starts):
        rows = page_rows_cache[pno]
        tail = [(pno, idx)]
        
        next_pno, next_idx = (starts[k+1][0], starts[k+1][1]) if k+1 < len(starts) else (None, None)
        for j in range(idx+1, len(rows)):
            if (pno == next_pno and j >= next_idx) or _parse_number_prefix(rows[j][2])[0] is not None:
                break
            tail.append((pno, j))
            
        entries.append({"num": num, "okfmt": okfmt, "head": (pno, idx), "body": tail, "used": used})

    total = len(entries)
    per_page_counts: Dict[int, int] = {}
    expected = 1
    
    for e in entries:
        pno, idx = e["head"]
        per_page_counts[pno] = per_page_counts.get(pno, 0) + 1
        page = doc[pno]
        y0, y1, sp, _ = page_rows_cache[pno][idx]
        x_annot, y_annot = _point_for_annot(sp)

        tail_text = []
        for (pp, jj) in e["body"]:
            tail_text.append("".join(s.get("text","") for s in page_rows_cache[pp][jj][2]))
        full_text = " ".join(t.strip() for t in tail_text).rstrip()
        
        source_type, score, format_ok = _analyze_source_candidate(full_text, debug)
        
        if not format_ok and source_type != "Неопределен":
            _annot(page, x_annot, y_annot, f"{ERR_FORMAT} '{source_type}'")
            user_pages_viol.add(pno+1)

        checks = [
            (e["num"] != expected, ERR_NUMSEQ),
            (not e["okfmt"], ERR_NUMFMT),
            (not full_text.endswith("."), ERR_ENDDOT)
        ]
        
        left_margin, _ = page_margin_cache[pno]
        cx = _first_content_x(sp, e["used"])
        if not (left_margin + INDENT_PT - TOL <= cx <= left_margin + INDENT_PT + TOL):
            checks.append((True, ERR_INDENT))

        for condition, err_msg in checks:
            if condition:
                _annot(page, x_annot, y_annot, err_msg)
                user_pages_viol.add(pno+1)

        if e["num"] == expected:
            expected += 1

    pages_list = [f"Стр. {p+1}: найдено записей {per_page_counts.get(p,0)}" 
                  for p in range(p_start, p_end_exc)]
    
    admin = f"""[References]
Диапазон страниц: {p_start+1}–{p_end_exc}
Найдено записей всего: {total}
{"".join(pages_list)}"""

    if debug:
        admin += "\n\n[ReferencesDebug]\n" + "\n".join(debug)

    user = ("✅Список источников: нарушений не обнаружено" if not user_pages_viol else
            f"⚠️Список источников: нарушения на страницах {', '.join(map(str, sorted(user_pages_viol)))}")
    
    return {"user_summary": user, "admin_details": admin}
