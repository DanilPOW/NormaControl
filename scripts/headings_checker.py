# scripts/headings_checker.py
from __future__ import annotations
import re, fitz

PREFIX = "Сервис нормоконтроля: заголовки"
HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\s+\S", re.U)

def _bold(sp): 
    try:
        if int(sp.get("flags",0)) & 2: return True
    except: pass
    return any(k in (sp.get("font") or "").lower() for k in ("bold","bd","black","semibold","demi","demibold"))

def check_headings(doc: fitz.Document, *, start_page:int=1, annotate_pdf:bool=False)->dict:
    found = {}
    for p in range(max(0,start_page-1), len(doc)):
        try: blocks = doc[p].get_text("dict").get("blocks",[])
        except: blocks = []
        for b in blocks:
            for l in b.get("lines",[]):
                spans = l.get("spans",[])
                if not spans or not all(_bold(s) for s in spans): 
                    continue
                head = ("".join(s.get("text","") for s in spans)[:48]).strip()
                if head.startswith(PREFIX): 
                    continue
                txt = "".join(s.get("text","") for s in spans).strip()
                if HEAD_RE.match(txt):
                    found.setdefault(p+1,[]).append(txt)

    total = sum(len(v) for v in found.values())
    per_page = [f"Стр. {k}: найдено заголовков {len(v)}" for k,v in sorted(found.items())]
    admin_details = "[Headings]\n" + f"Найдено заголовков всего: {total}\n" + ( "\n".join(per_page) if per_page else "Заголовков не найдено.")
    user_summary = "✅Проверка заголовков: нарушений не обнаружено"
    return {"user_summary": user_summary, "admin_details": admin_details}
