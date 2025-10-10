# scripts/headings_checker.py
from __future__ import annotations
import re, fitz
from typing import Dict, List, Tuple

PREFIX = "Сервис нормоконтроля: заголовки"
ERR_CASE    = "Сервис нормоконтроля: ошибка заголовков. Все буквы кроме первой должны быть строчными"
ERR_NEWPAGE = "Сервис нормоконтроля: ошибка заголовков. Заголовки разделов должны начинаться с новой страницы"
ERR_SPACING = "Сервис нормоконтроля: ошибка заголовков. Нарушены интервалы/пустые строки вокруг заголовка"
ERR_BETWEEN = "Сервис нормоконтроля: ошибка заголовков. Между заголовками разных уровней — ровно одна пустая строка и без текста"

HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\s+(\S.*)$")
BOLD_TOKENS = ("bold","bd","black","semibold","semi-bold","demi","demibold","heavy","extrabold","ultrabold")

def _is_bold(sp): 
    return any(t in (sp.get("font") or "").lower() for t in BOLD_TOKENS)

def _union_bbox(spans)->fitz.Rect:
    r=None
    for s in spans:
        x0,y0,x1,y1 = s["bbox"]
        rr = fitz.Rect(x0,y0,x1,y1)
        r = rr if r is None else r|rr
    return r or fitz.Rect(0,0,0,0)

def _level(num:str)->int: return num.count(".")+1

def _is_titlecase(s:str)->bool:
    words=re.findall(r"[А-Яа-яA-Za-zЁё]+",s)
    if not words: return True
    first=words[0]
    ok_first=(first[:1].upper()+first[1:].lower())==first
    rest_letters=re.findall(r"[А-Яа-яA-Za-zЁё]", s[len(first):])
    ok_rest=all(ch==ch.lower() for ch in rest_letters)
    return ok_first and ok_rest

def check_headings(doc:fitz.Document,*,start_page:int=3,annotate_pdf:bool=True)->dict:
    found: Dict[int,List[Tuple[fitz.Rect,str,int,float,int,float,float,bool,bool,bool]]] = {}
    # tuple: rect, txt, lvl, size, idx_line, prev_gap, next_gap, multiline, ok_before, ok_after, ok_after_8pt
    page_lines: Dict[int,List[Tuple[float,float,str,float]]] = {}
    debug_lines: List[str] = []

    # --- сбор строк и кандидатов
    for pno in range(max(0,start_page-1), len(doc)):
        page = doc[pno]
        blocks = page.get_text("dict").get("blocks", [])
        lines = []
        for b in blocks:
            for ln in b.get("lines",[]):
                spans = ln.get("spans",[])
                if not spans: continue
                txt = "".join(s.get("text","") for s in spans).strip()
                if not txt or txt.startswith(PREFIX): continue
                y0 = min(s["bbox"][1] for s in spans)
                y1 = max(s["bbox"][3] for s in spans)
                sz = max(float(s.get("size",0)) for s in spans) or 12.0
                lines.append((y0,y1,txt,sz,spans))
        lines.sort(key=lambda t:t[0])
        page_lines[pno] = [(y0,y1,txt,sz) for y0,y1,txt,sz,_ in lines]

        # кандидаты + предварительные метрики
        for idx,(y0,y1,txt,sz,spans) in enumerate(lines):
            if not all(_is_bold(s) for s in spans): 
                continue
            m = HEAD_RE.match(txt)
            if not m: 
                continue
            num, tail = m.groups()
            lvl = _level(num)
            rect = _union_bbox(spans)

            prev_gap = (y0 - lines[idx-1][1]) if idx>0 else sz
            next_gap = (lines[idx+1][0] - y1) if idx < len(lines)-1 else sz
            multiline = (rect.height > 1.6*sz)
            ok_before = prev_gap >= 0.8*sz
            ok_after  = next_gap >= 0.8*sz
            ok_after_8pt = 6.0 <= next_gap <= 12.0

            found.setdefault(pno, []).append(
                (rect, txt, lvl, sz, idx, prev_gap, next_gap, multiline, ok_before, ok_after, ok_after_8pt)
            )

    violations_pages=set()
    # --- валидации + аннотации (желтый хайлайт)
    for pno, items in found.items():
        page = doc[pno]
        lines = page_lines[pno]  # (y0,y1,txt,sz)

        # 1) регистр
        for rect, txt, lvl, sz, idx, prev_gap, next_gap, multiline, ok_before, ok_after, ok_after_8pt in items:
            tail = HEAD_RE.match(txt).group(2)
            if not _is_titlecase(tail):
                if annotate_pdf:
                    a=page.add_highlight_annot(rect); a.set_info(title=PREFIX, content=ERR_CASE); a.update()
                violations_pages.add(pno+1)

        # 2) раздел (уровень 1) — ПЕРВАЯ строка страницы
        for rect, txt, lvl, sz, idx, *_ in items:
            if lvl==1 and idx!=0:
                if annotate_pdf:
                    a=page.add_highlight_annot(rect); a.set_info(title=PREFIX, content=ERR_NEWPAGE); a.update()
                violations_pages.add(pno+1)

        # 3) интервалы / многострочность
        for rect, txt, lvl, sz, idx, prev_gap, next_gap, multiline, ok_before, ok_after, ok_after_8pt in items:
            ok_multils = True
            if multiline:
                n = max(2, round(rect.height/sz))
                step = rect.height/(n-1)
                ok_multils = 0.8*sz <= step <= 1.4*sz
            if not (ok_before and ok_after and ok_after_8pt and ok_multils):
                if annotate_pdf:
                    a=page.add_highlight_annot(rect); a.set_info(title=PREFIX, content=ERR_SPACING); a.update()
                violations_pages.add(pno+1)

        # 4) между заголовками разных уровней: одна пустая строка и без текста
        items_sorted = sorted(items, key=lambda t: t[4])  # по idx_line
        for (r1,t1,l1,s1,i1, *_),(r2,t2,l2,s2,i2, *__) in zip(items_sorted, items_sorted[1:]):
            if l1==l2: 
                continue
            # текст между строками
            between = [L for k,L in enumerate(lines) if i1<k<i2]
            has_text = any(len(re.findall(r"[А-Яа-яA-Za-z0-9Ёё]", t))>=3 for _,_,t,_ in between)
            gap = r2.y0 - r1.y1
            sz_ref = max(s1,s2)
            ok_gap = 0.8*sz_ref <= gap <= 1.8*sz_ref
            if has_text or not ok_gap:
                if annotate_pdf:
                    a=page.add_highlight_annot(r1|r2); a.set_info(title=PREFIX, content=ERR_BETWEEN); a.update()
                violations_pages.add(pno+1)

        # --- временный короткий debug-вывод по странице
        for rect, txt, lvl, sz, idx, prev_gap, next_gap, multiline, ok_before, ok_after, ok_after_8pt in items:
            debug_lines.append(
                f"Стр.{pno+1} idx={idx} lvl={lvl} sz={sz:.1f} "
                f"prev_gap={prev_gap:.1f} next_gap={next_gap:.1f} "
                f"multiline={int(multiline)} "
                f"ok_before={int(ok_before)} ok_after={int(ok_after)} ok_after_8pt={int(ok_after_8pt)} :: {txt[:80]}"
            )

    total = sum(len(v) for v in found.values())
    per_page = [f"Стр. {k}: найдено заголовков {len(v)}" for k,v in sorted(found.items())]
    admin_details = "[Headings]\n" + f"Найдено заголовков всего: {total}\n" + ("\n".join(per_page) if per_page else "Заголовков не найдено.")
    if debug_lines:
        admin_details += "\n\n[HeadingsDebug]\n" + "\n".join(debug_lines)

    user_summary = ("✅Проверка заголовков: нарушений не обнаружено"
                    if not violations_pages
                    else f"⚠️Проверка заголовков: нарушения на страницах {', '.join(map(str,sorted(violations_pages)))}")
    return {"user_summary": user_summary, "admin_details": admin_details}
