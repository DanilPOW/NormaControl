# scripts/references_checker.py
from __future__ import annotations
import re, fitz
from typing import List, Tuple, Dict

PREFIX = "Сервис нормоконтроля: ошибка списка источников"
ERR_NUMSEQ   = f"{PREFIX}. Нарушена сквозная нумерация записей"
ERR_NUMFMT   = f"{PREFIX}. Номер записи должен иметь вид '1.' (точка в том же или соседнем спане)"
ERR_INDENT   = f"{PREFIX}. У записи должен быть абзацный отступ 1.25 см от левого поля"
ERR_ENDDOT   = f"{PREFIX}. Запись должна заканчиваться точкой"
HEAD_START   = "список использованных источников"
HEAD_STOP_RE = re.compile(r"^\s*приложение", re.IGNORECASE)

CM = 28.3464567  # 1 cm in PDF points
INDENT_PT = 1.25 * CM
TOL = 4.5  # pts tolerance

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
    if not xs or not xe: return 72.0, 523.0  # fallbacks
    return min(xs), max(xe)

def _parse_number_prefix(spans: List[dict]) -> Tuple[int|None, bool, fitz.Rect, int, bool, bool]:
    """
    Returns: (num or None, ok_format, num_bbox, used_spans, dot_same, dot_next)
    ok_format==True if 'N.' exists either within first span or as immediate next span starting with '.'
    """
    if not spans: return None, False, fitz.Rect(), 0, False, False
    # Concatenate span texts but keep boundaries
    t0 = spans[0].get("text", "")
    m_same = re.match(r"\s*(\d{1,3})\.\s*", t0)
    if m_same:
        num = int(m_same.group(1))
        bbox = fitz.Rect(*spans[0]["bbox"])
        return num, True, bbox, 1, True, False
    # Case: first span is only number (or number + spaces), next starts with '.'
    m_num = re.match(r"\s*(\d{1,3})\s*$", t0)
    if m_num and len(spans) >= 2:
        s1 = spans[1].get("text", "")
        if re.match(r"^\.\s*", s1):
            num = int(m_num.group(1))
            bbox = _union_bbox(spans[:2])
            return num, True, bbox, 2, False, True
    # Loose: first span "N" and in same span later dot with space between (rare broken OCR)
    m_loose = re.match(r"\s*(\d{1,3})\s+[.]\s*", t0)
    if m_loose:
        num = int(m_loose.group(1))
        bbox = fitz.Rect(*spans[0]["bbox"])
        return num, True, bbox, 1, True, False
    # Not found
    return None, False, fitz.Rect(*spans[0]["bbox"]), 0, False, False

def _first_content_x(spans: List[dict], used_spans: int) -> float:
    # Skip number/dot spans; find first span with a letter/digit
    for s in spans[used_spans:]:
        txt = s.get("text", "")
        if re.search(r"[A-Za-zА-Яа-яЁё0-9]", txt):
            return float(s["bbox"][0])
    # fallback: take next span start or last
    return float(spans[min(used_spans, len(spans)-1)]["bbox"][2])

def _point_for_annot(spans: List[dict]) -> Tuple[float, float]:
    if not spans: return 10.0, 10.0
    x0, y0, x1, y1 = spans[0]["bbox"]
    return x0 + 2.0, y0 + (y1 - y0) * 0.45

def check_references(doc: fitz.Document) -> dict:
    user_pages_viol: set[int] = set()
    debug: List[str] = []
    refs_pages: List[int] = []

    # 1) Найти диапазон страниц списка
    p_start, p_end_exc = None, None
    for pno in range(len(doc)):
        page = doc[pno]
        blocks = page.get_text("dict").get("blocks", [])
        first = _first_line_text(blocks).strip().strip(":").lower()
        if first == HEAD_START:
            p_start = pno
            break
    if p_start is None:
        return {
            "user_summary": "⚠️Список источников: раздел не найден",
            "admin_details": "[References]\nРаздел 'СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ' не обнаружен."
        }
    for pno in range(p_start, len(doc)):
        page = doc[pno]
        first = _first_line_text(page.get_text("dict").get("blocks", []))
        if HEAD_STOP_RE.match(first):
            p_end_exc = pno  # эту страницу не проверяем
            break
    if p_end_exc is None:
        p_end_exc = len(doc)

    # 2) Скан строк, выделение пунктов
    entries = []  # list of dicts per entry
    page_rows_cache: Dict[int, List[Tuple[float,float,List[dict],str]]] = {}
    page_margin_cache: Dict[int, Tuple[float,float]] = {}
    starts = []  # (pno, idx_in_rows)
    for pno in range(p_start, p_end_exc):
        rows = _page_text_rows(doc[pno]); page_rows_cache[pno] = rows
        if pno not in page_margin_cache:
            page_margin_cache[pno] = _page_margins(rows)
        for i,(y0,y1,sp,txt) in enumerate(rows):
            num, okfmt, bbox, used, dot_same, dot_next = _parse_number_prefix(sp)
            if num is not None:
                starts.append((pno, i, num, okfmt, used, dot_same, dot_next))

    # Построить интервалы записей
    starts.sort(key=lambda t: (t[0], page_rows_cache[t[0]][t[1]][0]))
    for k,(pno, idx, num, okfmt, used, dot_same, dot_next) in enumerate(starts):
        rows = page_rows_cache[pno]
        y0, y1, sp, txt = rows[idx]
        # собрать строки до следующего номера/до границы диапазона
        tail = []
        # включительно текущая строка
        tail.append((pno, idx))
        # граница
        next_pno, next_idx = (starts[k+1][0], starts[k+1][1]) if k+1 < len(starts) else (None, None)
        # продолжение на текущей странице
        for j in range(idx+1, len(rows)):
            if (pno == next_pno and j >= next_idx): break
            # если на этой строке снова номер — остановиться
            n2, *_ = _parse_number_prefix(rows[j][2])
            if n2 is not None: break
            tail.append((pno, j))
        # возможно тянется на следующие страницы до next start
        pp = pno + 1
        while k+1 < len(starts) and pp < (next_pno or p_end_exc):
            rws = page_rows_cache[pp] if pp in page_rows_cache else _page_text_rows(doc[pp])
            page_rows_cache[pp] = rws
            for j in range(len(rws)):
                if (pp == next_pno and j >= next_idx): break
                n2, *_ = _parse_number_prefix(rws[j][2])
                if n2 is not None: break
                tail.append((pp, j))
            pp += 1

        entries.append({
            "num": num, "okfmt": okfmt, "head": (pno, idx), "body": tail, "used": used
        })

    # 3) Проверки
    total = len(entries)
    per_page_counts: Dict[int, int] = {}
    expected = 1
    for e in entries:
        pno, idx = e["head"]
        per_page_counts[pno] = per_page_counts.get(pno, 0) + 1
        page = doc[pno]
        y0, y1, sp, _ = page_rows_cache[pno][idx]
        x_annot, y_annot = _point_for_annot(sp)

        # a) Нумерация по порядку
        if e["num"] != expected:
            _annot(page, x_annot, y_annot, ERR_NUMSEQ)
            user_pages_viol.add(pno+1)
            debug.append(f"Стр.{pno+1} NUMSEQ ожидается {expected}, найдено {e['num']}")
            expected = e["num"] + 1 if isinstance(e["num"], int) else expected + 1
        else:
            expected += 1

        # b) Формат номера с точкой
        if not e["okfmt"]:
            _annot(page, x_annot, y_annot, ERR_NUMFMT)
            user_pages_viol.add(pno+1)
            debug.append(f"Стр.{pno+1} NUMFMT без точки рядом с номером: {e['num']}")

        # c) Абзацный отступ 1.25 см
        left_margin, _ = page_margin_cache[pno]
        cx = _first_content_x(sp, e["used"])
        if not (left_margin + INDENT_PT - TOL <= cx <= left_margin + INDENT_PT + TOL):
            _annot(page, x_annot, y_annot, ERR_INDENT)
            user_pages_viol.add(pno+1)
            debug.append(f"Стр.{pno+1} INDENT x={cx:.1f} ожидалось ~{left_margin+INDENT_PT:.1f}")

        # d) Все продолжения строк должны соблюдать тот же отступ
        for (pp, jj) in e["body"][1:]:
            x0_line = min(s["bbox"][0] for s in page_rows_cache[pp][jj][2]) if page_rows_cache[pp][jj][2] else cx
            if x0_line < left_margin + INDENT_PT - TOL:
                _annot(doc[pp], _point_for_annot(page_rows_cache[pp][jj][2])[0],
                       _point_for_annot(page_rows_cache[pp][jj][2])[1], ERR_INDENT)
                user_pages_viol.add(pp+1)
                debug.append(f"Стр.{pp+1} WRAP-INDENT x={x0_line:.1f} < {left_margin+INDENT_PT-TOL:.1f}")
                break

        # e) Запись заканчивается точкой
        # Собрать полный текст записи (от головы до последней строки тела)
        tail_text = []
        for (pp, jj) in e["body"]:
            tail_text.append("".join(s.get("text","") for s in page_rows_cache[pp][jj][2]))
        full = " ".join(t.strip() for t in tail_text).rstrip()
        # Никакие внутренние точки не запрещены — проверяем только финальную
        if not full.endswith("."):
            _annot(page, x_annot, y_annot, ERR_ENDDOT)
            user_pages_viol.add(pno+1)
            debug.append(f"Стр.{pno+1} ENDDOT запись №{e['num']} не оканчивается точкой")

    # 4) Итоги
    pages_list = [f"Стр. {p+1}: найдено записей {per_page_counts.get(p,0)}"
                  for p in range(p_start, p_end_exc)]
    admin = "[References]\n" + \
            f"Диапазон страниц: {p_start+1}–{p_end_exc} (вторая граница исключена)\n" + \
            f"Найдено записей всего: {total}\n" + \
            ("\n".join(pages_list) if pages_list else "Записей не найдено.")
    if debug:
        admin += "\n\n[ReferencesDebug]\n" + "\n".join(debug)

    user = ("✅Список источников: нарушений не обнаружено"
            if not user_pages_viol else
            f"⚠️Список источников: нарушения на страницах {', '.join(map(str, sorted(user_pages_viol)))}")
    return {"user_summary": user, "admin_details": admin}
