# scripts/headings_checker.py
from __future__ import annotations
import re, fitz
from typing import List, Tuple, Dict

PREFIX = "Сервис нормоконтроля: ошибка заголовков"
ERR_CASE    = f"{PREFIX}. Все буквы кроме первой должны быть строчными"
ERR_NEWPAGE = f"{PREFIX}. Заголовки разделов должны начинаться с новой страницы"
ERR_SPACING = f"{PREFIX}. Нарушены интервалы/пустые строки вокруг заголовка"
ERR_BETWEEN = f"{PREFIX}. Между заголовками разных уровней — ровно одна пустая строка и без текста"

HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\s+(\S.*)$")
BOLD_TOKENS = ("bold","bd","black","semibold","semi-bold","demi","demibold","heavy","extrabold","ultrabold")
ALLOW_FIRST = {
    "содержание","термины и определения","перечень сокращений и обозначений","введение",
    "заключение","список использованных источников","приложения"
}

def _is_bold(sp:dict)->bool: return any(t in (sp.get("font") or "").lower() for t in BOLD_TOKENS)
def _union(spans:List[dict])->fitz.Rect:
    r=None
    for s in spans:
        x0,y0,x1,y1=s["bbox"]; rr=fitz.Rect(x0,y0,x1,y1); r=rr if r is None else r|rr
    return r or fitz.Rect(0,0,0,0)
def _lvl(num:str)->int: return num.count(".")+1
def _titlecase_ok(s:str)->bool:
    w=re.findall(r"[А-Яа-яA-Za-zЁё]+",s); 
    if not w: return True
    f=w[0]; ok1=(f[:1].upper()+f[1:].lower())==f
    rest=re.findall(r"[А-Яа-яA-Za-zЁё]", s[len(f):])
    return ok1 and all(ch==ch.lower() for ch in rest)
def _pin_point(spans:List[dict])->Tuple[float,float]:
    if not spans: return 0.0,0.0
    x0,y0,x1,y1=map(float,spans[0]["bbox"]); return x0+2.0, y0+(y1-y0)*0.45
def _pin_from_rect(r:fitz.Rect)->Tuple[float,float]: return float(r.x0)+2.0, float(r.y0)+(float(r.y1-r.y0))*0.45
def _annot(page:fitz.Page, x:float,y:float, msg:str):
    try: a=page.add_text_annot(fitz.Point(x,y),msg); a.set_info(title=PREFIX,content=msg); a.update()
    except: pass

def check_headings(doc:fitz.Document,*,start_page:int=3,annotate_pdf:bool=True)->dict:
    found:Dict[int,List[Tuple[fitz.Rect,str,int,float,int,int,List[dict]]]]={}
    debug:List[str]=[]
    BLANK_K,TOL_PT=1.3,6.0

    for pno in range(max(0,start_page-1),len(doc)):
        page=doc[pno]
        raw=[]  # y0,y1,txt,sz,spans
        for b in page.get_text("dict").get("blocks",[]):
            for ln in b.get("lines",[]):
                sp=ln.get("spans",[]); 
                if not sp: continue
                txt="".join(s.get("text","") for s in sp).strip()
                if not txt: continue
                y0=min(s["bbox"][1] for s in sp); y1=max(s["bbox"][3] for s in sp)
                sz=max(float(s.get("size",0)) for s in sp) or 12.0
                raw.append((y0,y1,txt,sz,sp))
        raw.sort(key=lambda t:t[0])
        # референтный шаг
        gaps=[raw[i][0]-raw[i-1][1] for i in range(1,len(raw)) if 6.0<=raw[i][0]-raw[i-1][1]<=30.0]
        ref_step = (sorted(gaps)[len(gaps)//2] if gaps else (1.3*(raw[0][3] if raw else 12.0)))
        # первая непустая строка страницы (для послабления NEWPAGE)
        first_txt = next((t for _,_,t,_,_ in raw if t.strip()), "")
        first_txt_norm = first_txt.lower().strip().rstrip(":")
        allow_newpage_relax = first_txt_norm in ALLOW_FIRST

        # кандидаты и склейка вперёд
        heads=[i for i,(_,_,t,_,sp) in enumerate(raw) if all(_is_bold(s) for s in sp) and HEAD_RE.match(t)]
        items=[]
        for i in heads:
            y0,y1,txt,sz,sp=raw[i]; rect=_union(sp); fst=i; j=i
            while j+1<len(raw):
                y0n,y1n,txtn,szn,spn=raw[j+1]
                if not all(_is_bold(s) for s in spn): break
                if HEAD_RE.match(txtn): break
                if (y0n-rect.y1) > 0.6*ref_step: break
                rect|=_union(spn); txt=(txt+" "+txtn).strip(); j+=1
            lvl=_lvl(HEAD_RE.match(raw[i][2]).group(1))
            items.append((rect,txt,lvl,sz,fst,j,sp))
        found[pno]=items

        # проверки
        for rect,txt,lvl,sz,fst,lst,sp0 in items:
            tail=HEAD_RE.match(txt).group(2)

            if not _titlecase_ok(tail) and annotate_pdf:
                _annot(page,*_pin_point(sp0),ERR_CASE)

            if lvl==1 and fst!=0 and not allow_newpage_relax and annotate_pdf:
                _annot(page,*_pin_point(sp0),ERR_NEWPAGE)

            prev_gap = (raw[fst][0]-raw[fst-1][1]) if fst>0 else None
            next_gap = (raw[lst+1][0]-rect.y1) if lst<len(raw)-1 else ref_step
            multiline = (lst>fst)
            has_blank_before = True if fst==0 else (prev_gap is not None and prev_gap>=BLANK_K*ref_step)
            has_blank_after  = next_gap>=BLANK_K*ref_step
            ok_after = has_blank_after if not multiline else (has_blank_after and abs(next_gap-(2.0*ref_step+8.0))<=TOL_PT)

            if not (has_blank_before and ok_after) and annotate_pdf:
                _annot(page,*_pin_point(sp0),ERR_SPACING)

            debug.append(
                f"Стр.{pno+1} idx={fst}..{lst} lvl={lvl} sz={sz:.1f} ref_step={ref_step:.1f} "
                f"prev_gap={'-' if fst==0 else f'{prev_gap:.1f}'} next_gap={next_gap:.1f} "
                f"multiline={int(multiline)} blank_before={int(has_blank_before)} blank_after={int(has_blank_after)} "
                f"rule_after={'blank' if not multiline else 'blank+8'} ok_after={int(ok_after)} :: {txt[:80]}"
            )

        # между заголовками разных уровней
        items_sorted=sorted(items,key=lambda t:t[4])
        for (r1,t1,l1,s1,f1,l1e,sp1),(r2,t2,l2,s2,f2,l2e,sp2) in zip(items_sorted,items_sorted[1:]):
            if l1==l2: continue
            between=[R for k,R in enumerate(raw) if l1e<k<f2]
            has_text=any(len(re.findall(r"[А-Яа-яA-Za-z0-9Ёё]",R[2]))>=3 for R in between)
            gap=r2.y0-r1.y1; ok_gap=(0.8*ref_step<=gap<=1.8*ref_step)
            if has_text or not ok_gap:
                if annotate_pdf: _annot(page,*_pin_from_rect(r1|r2),ERR_BETWEEN)

    total=sum(len(v) for v in found.values())
    per_page=[f"Стр. {k}: найдено заголовков {len(v)}" for k,v in sorted(found.items())]
    admin="[Headings]\n"+f"Найдено заголовков всего: {total}\n"+("\n".join(per_page) if per_page else "Заголовков не найдено.")
    if debug: admin+="\n\n[HeadingsDebug]\n"+"\n".join(debug)

    viol_pages=set()
    for row in debug:
        if "ok_after=0" in row or "blank_before=0" in row:
            try: viol_pages.add(int(row.split("Стр.")[1].split()[0]))
            except: pass
    user = ("✅Проверка заголовков: нарушений не обнаружено"
            if not viol_pages else f"⚠️Проверка заголовков: нарушения на страницах {', '.join(map(str,sorted(viol_pages)))}")
    return {"user_summary": user, "admin_details": admin}
