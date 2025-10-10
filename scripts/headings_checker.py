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

def _union(spans)->fitz.Rect:
    r=None
    for s in spans:
        x0,y0,x1,y1=s["bbox"]; rr=fitz.Rect(x0,y0,x1,y1)
        r = rr if r is None else r|rr
    return r or fitz.Rect(0,0,0,0)

def _lvl(num:str)->int: return num.count(".")+1

def _titlecase_ok(s:str)->bool:
    words=re.findall(r"[А-Яа-яA-Za-zЁё]+",s)
    if not words: return True
    first=words[0]
    ok_first=(first[:1].upper()+first[1:].lower())==first
    rest=re.findall(r"[А-Яа-яA-Za-zЁё]", s[len(first):])
    return ok_first and all(ch==ch.lower() for ch in rest)

def check_headings(doc:fitz.Document,*,start_page:int=3,annotate_pdf:bool=True)->dict:
    found: Dict[int,List[Tuple[fitz.Rect,str,int,float,int]]] = {}   # rect, txt, lvl, size, idx_line
    page_lines: Dict[int,List[Tuple[float,float,str,float]]] = {}    # y0,y1,txt,sz
    debug: List[str] = []
    BLANK_K, TOL_PT = 1.3, 6.0  # пустая строка ~ >=1.3*ref_step; допуск для 8pt

    for pno in range(max(0,start_page-1), len(doc)):
        page=doc[pno]
        lines=[]
        for b in page.get_text("dict").get("blocks",[]):
            for ln in b.get("lines",[]):
                sp=ln.get("spans",[]); 
                if not sp: continue
                txt="".join(s.get("text","") for s in sp).strip()
                if not txt or txt.startswith(PREFIX): continue
                y0=min(s["bbox"][1] for s in sp); y1=max(s["bbox"][3] for s in sp)
                sz=max(float(s.get("size",0)) for s in sp) or 12.0
                lines.append((y0,y1,txt,sz,sp))
        lines.sort(key=lambda t:t[0])
        page_lines[pno]=[(y0,y1,txt,sz) for y0,y1,txt,sz,_ in lines]

        # ref_step: медиана нормальных межстрочных зазоров
        gaps=[]
        for i in range(1,len(page_lines[pno])):
            g=page_lines[pno][i][0]-page_lines[pno][i-1][1]
            if 6.0<=g<=30.0: gaps.append(g)
        if gaps:
            gaps.sort(); ref_step=gaps[len(gaps)//2]
        else:
            ref_step=1.3*(page_lines[pno][0][3] if page_lines[pno] else 12.0)

        for idx,(y0,y1,txt,sz,sp) in enumerate(lines):
            if not all(_is_bold(s) for s in sp): continue
            m=HEAD_RE.match(txt)
            if not m: continue
            rect=_union(sp); lvl=_lvl(m.group(1))
            found.setdefault(pno,[]).append((rect,txt,lvl,sz,idx))

        # валидируем и аннотируем
        viol=set()
        items=found.get(pno,[])
        for rect,txt,lvl,sz,idx in items:
            tail=HEAD_RE.match(txt).group(2)

            # Кейс букв
            if not _titlecase_ok(tail):
                if annotate_pdf: page.add_highlight_annot(rect).set_info(title=PREFIX,content=ERR_CASE)
                viol.add(pno+1)

            # Раздел = первая строка страницы
            if lvl==1 and idx!=0:
                if annotate_pdf: page.add_highlight_annot(rect).set_info(title=PREFIX,content=ERR_NEWPAGE)
                viol.add(pno+1)

            # Интервалы
            prev_gap=(rect.y0 - page_lines[pno][idx-1][1]) if idx>0 else ref_step
            next_gap=(page_lines[pno][idx+1][0] - rect.y1) if idx<len(page_lines[pno])-1 else ref_step
            multiline = rect.height > 1.6*sz
            has_blank_before = prev_gap >= BLANK_K*ref_step
            has_blank_after  = next_gap >= BLANK_K*ref_step

            ok_after = has_blank_after if not multiline else (has_blank_after and abs(next_gap-(2.0*ref_step+8.0))<=TOL_PT)
            ok_before = has_blank_before

            if not (ok_before and ok_after):
                if annotate_pdf: page.add_highlight_annot(rect).set_info(title=PREFIX,content=ERR_SPACING)
                viol.add(pno+1)

            debug.append(
                f"Стр.{pno+1} idx={idx} lvl={lvl} sz={sz:.1f} "
                f"ref_step={ref_step:.1f} prev_gap={prev_gap:.1f} next_gap={next_gap:.1f} "
                f"multiline={int(multiline)} blank_before={int(has_blank_before)} blank_after={int(has_blank_after)} "
                f"rule_after={'blank' if not multiline else 'blank+8'} ok_after={int(ok_after)} :: {txt[:80]}"
            )

        # Между заголовками разных уровней — 1 пустая строка и без текста
        items_sorted=sorted(items,key=lambda t:t[4])
        for (r1,t1,l1,s1,i1),(r2,t2,l2,s2,i2) in zip(items_sorted,items_sorted[1:]):
            if l1==l2: continue
            between=[L for k,L in enumerate(page_lines[pno]) if i1<k<i2]
            has_text=any(len(re.findall(r"[А-Яа-яA-Za-z0-9Ёё]",t))>=3 for _,_,t,_ in between)
            gap=r2.y0-r1.y1; ok_gap=(0.8*ref_step<=gap<=1.8*ref_step)
            if has_text or not ok_gap:
                if annotate_pdf: page.add_highlight_annot(r1|r2).set_info(title=PREFIX,content=ERR_BETWEEN)
                viol.add(pno+1)

        # обновляем user summary для страницы (накапливаем в общем ниже)
        found[pno]=items
        # (violations учитываются при финальном формировании суммарного вывода)
        if viol: pass

    total = sum(len(v) for v in found.values())
    per_page = [f"Стр. {k}: найдено заголовков {len(v)}" for k,v in sorted(found.items())]
    admin = "[Headings]\n" + f"Найдено заголовков всего: {total}\n" + ("\n".join(per_page) if per_page else "Заголовков не найдено.")
    if debug: admin += "\n\n[HeadingsDebug]\n" + "\n".join(debug)

    # страницы с нарушениями (по жёлтым аннотациям определить нельзя здесь), поэтому пересчитаем быстро:
    # простая переоценка: если в debug у строки ok_after=0 или blank_*==0 или сработали NEWPAGE/CASE/BETWEEN — мы их уже добавляли в viol.
    # Для краткости сформируем из debug:
    viol_pages=set()
    for row in debug:
        if "ok_after=0" in row or "blank_before=0" in row or "rule_after" in row and ("::" in row):
            try:
                pg=int(row.split("Стр.")[1].split()[0])
                viol_pages.add(pg)
            except: pass
    user = ("✅Проверка заголовков: нарушений не обнаружено" 
            if not viol_pages else f"⚠️Проверка заголовков: нарушения на страницах {', '.join(map(str,sorted(viol_pages)))}")
    return {"user_summary": user, "admin_details": admin}
