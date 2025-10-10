# scripts/headings_checker.py
from __future__ import annotations
import re, fitz
from typing import Dict, List

PREFIX = "Сервис нормоконтроля: заголовки"
ERR_CASE = "Сервис нормоконтроля: ошибка заголовков. Все буквы кроме первой должны быть строчными"
ERR_NEWPAGE = "Сервис нормоконтроля: ошибка заголовков. Заголовки разделов должны начинаться с новой страницы"
ERR_SPACING = "Сервис нормоконтроля: ошибка заголовков. Нарушены интервалы/пустые строки вокруг заголовка"
ERR_BETWEEN = "Сервис нормоконтроля: ошибка заголовков. Между заголовками разных уровней — ровно одна пустая строка и без текста"

HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\s+(\S.*)$")
BOLD_TOKENS = ("bold","bd","black","semibold","semi-bold","demi","demibold","heavy","extrabold","ultrabold")

def _is_bold(span): 
    return any(t in (span.get("font") or "").lower() for t in BOLD_TOKENS)

def _union(spans):
    r=None
    for s in spans:
        x0,y0,x1,y1=s["bbox"]
        r=fitz.Rect(x0,y0,x1,y1) if r is None else r|fitz.Rect(x0,y0,x1,y1)
    return r

def _level(num:str)->int: return num.count(".")+1

def _is_titlecase(s:str)->bool:
    words=re.findall(r"[А-Яа-яA-Za-zЁё]+",s)
    if not words: return True
    first,rest=words[0]," ".join(words[1:])
    ok_first=(first[:1].upper()+first[1:].lower())==first
    ok_rest=all(ch==ch.lower() for ch in re.findall(r"[А-Яа-яA-Za-zЁё]",rest))
    return ok_first and ok_rest

def check_headings(doc:fitz.Document,*,start_page:int=3,annotate_pdf:bool=True)->dict:
    found={}; viol=set()
    for pno in range(max(0,start_page-1),len(doc)):
        page=doc[pno]; blocks=page.get_text("dict").get("blocks",[])
        lines=[]
        for b in blocks:
            for l in b.get("lines",[]):
                spans=l.get("spans",[]); 
                if not spans: continue
                txt="".join(s.get("text","") for s in spans).strip()
                if not txt or txt.startswith(PREFIX): continue
                y0=min(s["bbox"][1] for s in spans); y1=max(s["bbox"][3] for s in spans)
                sz=max(float(s.get("size",0)) for s in spans)
                lines.append((y0,y1,txt,sz,spans))
        lines.sort(key=lambda t:t[0])
        found[pno]=[]
        for y0,y1,txt,sz,spans in lines:
            if not all(_is_bold(s) for s in spans): continue
            m=HEAD_RE.match(txt)
            if not m: continue
            num,tail=m.groups(); lvl=_level(num)
            rect=_union(spans)
            found[pno].append((rect,txt,lvl,sz))

        for rect,txt,lvl,sz in found[pno]:
            # 1. Проверка регистра
            tail=HEAD_RE.match(txt).group(2)
            if not _is_titlecase(tail):
                if annotate_pdf:
                    a=page.add_rect_annot(rect);a.set_colors(stroke=(1,0,0))
                    a.set_info(title=PREFIX,content=ERR_CASE);a.update()
                viol.add(pno+1)
            # 2. Заголовок раздела (ур.1) — первая строка страницы
            if lvl==1:
                if any(y1<=rect.y0-0.5 for y0,y1,_,_,_ in lines if y1<rect.y0):
                    if annotate_pdf:
                        a=page.add_rect_annot(rect);a.set_colors(stroke=(1,0,0))
                        a.set_info(title=PREFIX,content=ERR_NEWPAGE);a.update()
                    viol.add(pno+1)
            # 3. Интервалы до/после (эвристика)
            idx=lines.index((rect.y0,rect.y1,txt,sz,spans))
            prev_gap=(rect.y0-lines[idx-1][1]) if idx>0 else sz
            next_gap=(lines[idx+1][0]-rect.y1) if idx<len(lines)-1 else sz
            ok_before=prev_gap>=0.8*sz; ok_after=next_gap>=0.8*sz
            if not (ok_before and ok_after):
                if annotate_pdf:
                    a=page.add_rect_annot(rect);a.set_colors(stroke=(1,0,0))
                    a.set_info(title=PREFIX,content=ERR_SPACING);a.update()
                viol.add(pno+1)
        # 4. Между заголовками разных уровней
        items=found[pno]
        for (r1,_,l1,_),(r2,_,l2,_) in zip(items,items[1:]):
            if l1==l2: continue
            gap=r2.y0-r1.y1
            if not (0.8*12<=gap<=1.8*14):
                if annotate_pdf:
                    a=page.add_rect_annot(r1|r2);a.set_colors(stroke=(1,0,0))
                    a.set_info(title=PREFIX,content=ERR_BETWEEN);a.update()
                viol.add(pno+1)
    total=sum(len(v) for v in found.values())
    admin="[Headings]\nНайдено заголовков всего: %d\n%s"%(total,"\n".join(f"Стр.{p+1}: {len(v)}" for p,v in found.items() if v))
    user="✅Проверка заголовков: нарушений не обнаружено" if not viol else f"⚠️Проверка заголовков: нарушения на страницах {', '.join(map(str,sorted(viol)))}"
    return {"user_summary":user,"admin_details":admin}
