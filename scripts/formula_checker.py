# scripts/formula_checker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import re, fitz
from collections import defaultdict
from const import *  # ожидаются LEFT/RIGHT/TOP/BOTTOM_MARGIN_PT, DEBUG_DIAGNOSTICS

MATH_FONTS = {"Cambria Math"}
TOL_Y, TOL_CENTER, TOL_RIGHT = 4.5, 6.0, 6.0
MIN_OVERLAP_X, MAX_VGAP_FACTOR, NUM_MAX_WIDTH = 0.30, 1.5, 80.0
GAP_MIN_PT, GAP_MAX_PT, GAP_SCALE = 8.0, 11.0, 0.75
NBSP = "\u00A0\u202F\u2009"; SPACE = f"[ \\t{NBSP}]"
NUM_TAIL_RE = re.compile(r"\(" + SPACE + r"*([0-9]+)" + SPACE + r"*\)\s*$")
NUM_ANY_RE  = re.compile(r"\(" + SPACE + r"*([0-9]+)" + SPACE + r"*\)")

@dataclass
class FormulaLineRaw:
    spans: list
    bbox: fitz.Rect
    y_mid: float
    core_bbox: Optional[fitz.Rect]
    math_ratio: float

@dataclass
class FormulaBlock:
    page_index0: int
    core_bbox: fitz.Rect
    bbox: fitz.Rect
    number: Optional[int]
    number_bbox: Optional[fitz.Rect]

def _U(rects: List[fitz.Rect]) -> Optional[fitz.Rect]:
    if not rects: return None
    r = fitz.Rect(rects[0])
    for x in rects[1:]: r |= x
    return r

def _center_ok(bb: fitz.Rect, left: float, right: float, tol=TOL_CENTER) -> bool:
    return abs((bb.x0+bb.x1-right-left)/2) <= tol

def _inside_work(bb: fitz.Rect, page_bound: fitz.Rect) -> bool:
    return (bb.x0 >= page_bound.x0+LEFT_MARGIN_PT-0.5 and
            bb.x1 <= page_bound.x1-RIGHT_MARGIN_PT+0.5 and
            bb.y0 >= page_bound.y0+TOP_MARGIN_PT-0.5 and
            bb.y1 <= page_bound.y1-BOTTOM_MARGIN_PT+0.5)

def _is_math_char(c: str) -> bool:
    if not c: return False
    u = ord(c); return (0x0370 <= u <= 0x03FF) or (0x2200 <= u <= 0x22FF)

def _y_close(a: float, b: float, tol: float = TOL_Y) -> bool: return abs(a-b) <= tol

def _y_overlap_ok(a: fitz.Rect, b: fitz.Rect, frac: float = 0.5) -> bool:
    top, bot = max(a.y0,b.y0), min(a.y1,b.y1)
    inter = max(0.0, bot-top); minh = max(1.0, min(a.height,b.height))
    return inter/minh >= frac

def _x_overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    left, right = max(a.x0,b.x0), min(a.x1,b.x1)
    return max(0.0, right-left)/max(1.0, min(a.width,b.width))

def _line_text(spans: list) -> str:
    out=[]; 
    for sp in spans:
        for ch in sp.get("chars",[]) or []:
            c=ch.get("c",""); 
            if c: out.append(c)
    return "".join(out)

def _line_core_bbox_and_ratio(spans: list) -> Tuple[Optional[fitz.Rect], float]:
    rects=[]; total=math_total=0
    for sp in spans:
        font=str(sp.get("font",""))
        for ch in sp.get("chars",[]) or []:
            c=ch.get("c",""); 
            if not c: continue
            total+=1
            if font in MATH_FONTS or _is_math_char(c):
                bb=ch.get("bbox"); 
                if bb: rects.append(fitz.Rect(*map(float,bb))); math_total+=1
    return (_U(rects), math_total/total if total else 0.0)

def _collect_lines_raw(page: fitz.Page) -> List[FormulaLineRaw]:
    out=[]; d=page.get_text("rawdict")
    for b in d.get("blocks",[]):
        if b.get("type")!=0: continue
        for ln in b.get("lines",[]):
            spans=ln.get("spans",[])
            xs=[]; ys=[]
            for sp in spans:
                bb=sp.get("bbox",[0,0,0,0]); xs+= [bb[0],bb[2]]; ys+=[bb[1],bb[3]]
            if not xs: continue
            rect=fitz.Rect(min(xs),min(ys),max(xs),max(ys))
            core_bb, ratio=_line_core_bbox_and_ratio(spans)
            out.append(FormulaLineRaw(spans,rect,(rect.y0+rect.y1)/2,core_bb,ratio))
    return out

def _find_trailing_number_same_line(spans: list) -> Tuple[Optional[int], Optional[fitz.Rect]]:
    chars=[]
    for sp in spans:
        for ch in sp.get("chars",[]) or []:
            c=ch.get("c",""); bb=ch.get("bbox")
            if c and bb: chars.append((c, fitz.Rect(*map(float,bb))))
    if not chars: return (None,None)
    chars.sort(key=lambda t:(t[1].y0,t[1].x0))
    text="".join(c for c,_ in chars); m=NUM_TAIL_RE.search(text)
    if not m: return (None,None)
    try: n=int(m.group(1))
    except: return (None,None)
    s,e=m.span(); 
    if not (0<=s<e<=len(chars)): return (None,None)
    return (n, _U([chars[i][1] for i in range(s,e)]))

def _same_height_or_overlap(a: FormulaLineRaw, b: FormulaLineRaw) -> bool:
    return _y_close(a.y_mid,b.y_mid) or _y_overlap_ok(a.bbox,b.bbox,0.5)

def _find_number_right_same_height(all_lines: List[FormulaLineRaw], ref: FormulaLineRaw, core_bb: fitz.Rect):
    best=None
    for ln in all_lines:
        if ln is ref or not _same_height_or_overlap(ln,ref): continue
        txt=_line_text(ln.spans).strip(); m=NUM_TAIL_RE.fullmatch(txt)
        if not m: continue
        bb=ln.bbox
        if bb.x0 >= core_bb.x1-0.5 and bb.width <= NUM_MAX_WIDTH:
            try: best=max(best,(int(m.group(1)),bb), key=lambda t:t[1].x1) if best else (int(m.group(1)),bb)
            except: pass
    return best if best else (None,None)

def _find_number_on_adjacent_line(all_lines, block_bb, ref_font, work_right, direction):
    max_vgap=max(8.0, ref_font*1.2); best=None
    for ln in all_lines:
        vgap = (ln.bbox.y0 - block_bb.y1) if direction>0 else (block_bb.y0 - ln.bbox.y1)
        cond = (ln.bbox.y0 >= block_bb.y1-0.5) if direction>0 else (ln.bbox.y1 <= block_bb.y0+0.5)
        if not cond or vgap < -0.5 or vgap > max_vgap: continue
        txt=_line_text(ln.spans).strip(); m=NUM_TAIL_RE.fullmatch(txt)
        if not m: continue
        try: n=int(m.group(1))
        except: continue
        bb=ln.bbox
        if abs(work_right-bb.x1) <= max(TOL_RIGHT,6.0) and bb.width <= NUM_MAX_WIDTH:
            best = (n,bb,vgap) if (not best or vgap<best[2]) else best
    return (best[0],best[1]) if best else (None,None)

def _group_math_lines_to_blocks(lines: List[FormulaLineRaw], ref_font: float):
    math_lines=[ln for ln in lines if ln.core_bbox and ln.math_ratio>=0.6]
    if not math_lines: return []
    math_lines.sort(key=lambda ln: ln.bbox.y0)
    blocks=[]; cur=[math_lines[0]]
    for ln in math_lines[1:]:
        prev=cur[-1]; vgap=ln.bbox.y0-prev.bbox.y1; xov=_x_overlap_ratio(prev.core_bbox, ln.core_bbox)
        if vgap <= MAX_VGAP_FACTOR*max(10.0,ref_font) and xov >= MIN_OVERLAP_X: cur.append(ln)
        else: blocks.append(cur); cur=[ln]
    blocks.append(cur)
    out=[]; 
    for bl in blocks:
        vis=_U([ln.bbox for ln in bl])
        if vis: out.append((bl,vis))
    return out

def _gather_formula_blocks_on_page(page: fitz.Page, pidx: int, work_right: float):
    sizes=[]
    d=page.get_text("dict")
    for b in d.get("blocks",[]):
        if b.get("type")!=0: continue
        for ln in b.get("lines",[]):
            for sp in ln.get("spans",[]): 
                sz=float(sp.get("size",0)) or 0.0
                if sz>0: sizes.append(sz)
    ref_font = sorted(sizes)[len(sizes)//2] if sizes else 12.0
    raw_lines=_collect_lines_raw(page)
    blocks_raw=_group_math_lines_to_blocks(raw_lines, ref_font)
    out=[]
    for bl_lines, core_bb in blocks_raw:
        last=bl_lines[-1]
        num,num_bb=_find_trailing_number_same_line(last.spans)
        if num is None:
            for cand in reversed(bl_lines):
                num,num_bb=_find_number_right_same_height(raw_lines,cand,core_bb)
                if num is not None: break
        if num is None: num,num_bb=_find_number_on_adjacent_line(raw_lines, core_bb, ref_font, work_right, +1)
        if num is None: num,num_bb=_find_number_on_adjacent_line(raw_lines, core_bb, ref_font, work_right, -1)
        row_bb=_U([core_bb,num_bb] if num_bb else [core_bb])
        out.append(FormulaBlock(pidx, core_bb, row_bb, num, num_bb))
    return out, ref_font

def _collect_all_refs(doc: fitz.Document, start_page: int):
    refs=defaultdict(list)
    for pidx in range(doc.page_count):
        pg=pidx+1
        if pg<start_page: continue
        d=doc[pidx].get_text("dict")
        for b in d.get("blocks",[]):
            if b.get("type")!=0: continue
            for ln in b.get("lines",[]):
                txt="".join(sp.get("text","") or "" for sp in ln.get("spans",[])).strip()
                for m in NUM_ANY_RE.finditer(txt):
                    try: n=int(m.group(1))
                    except: continue
                    xs=[]; ys=[]
                    for sp in ln.get("spans",[]):
                        bb=sp.get("bbox",[0,0,0,0]); xs+=[bb[0],bb[2]]; ys+=[bb[1],bb[3]]
                    if xs: refs[pg].append((n, fitz.Rect(min(xs),min(ys),max(xs),max(ys))))
    return refs

def _nearest_text_line_gaps(page: fitz.Page, formula_bb: fitz.Rect, ig: List[fitz.Rect]):
    lines=[]; d=page.get_text("dict")
    for b in d.get("blocks",[]):
        if b.get("type")!=0: continue
        for ln in b.get("lines",[]):
            xs=[]; ys=[]; sizes=[]
            for sp in ln.get("spans",[]):
                bb=sp.get("bbox",[0,0,0,0]); xs += [bb[0],bb[2]]; ys += [bb[1],bb[3]]
                sz=float(sp.get("size",0)) or 0.0
                if sz>0: sizes.append(sz)
            if not xs: continue
            rect=fitz.Rect(min(xs),min(ys),max(xs),max(ys))
            if any(rect.intersects(ir) for ir in ig): continue
            avg=(sum(sizes)/len(sizes)) if sizes else 12.0
            lines.append((rect,avg))
    if not lines: return (None,None,12.0)
    above=[x for x in lines if x[0].y1 <= formula_bb.y0]
    below=[x for x in lines if x[0].y0 >= formula_bb.y1]
    gap_above=(formula_bb.y0 - max(above, key=lambda x:x[0].y1)[0].y1) if above else None
    gap_below=(min(below, key=lambda x:x[0].y0)[0].y0 - formula_bb.y1) if below else None
    sizes_all=[sz for _,sz in lines if sz>0]
    ref=sorted(sizes_all)[len(sizes_all)//2] if sizes_all else 12.0
    return (gap_above,gap_below,ref)

def check_formulas(pdf_document: fitz.Document, *, annotate_pdf: bool = True, start_page: int = 2) -> Dict[str, object]:
    admin=[]; boxes=defaultdict(list); errors=set(); n=0
    all_refs=_collect_all_refs(pdf_document, start_page=start_page)
    for pidx,page in enumerate(pdf_document):
        pg=pidx+1
        if pg<start_page: continue
        page_bound=page.bound(); work_left=page_bound.x0+LEFT_MARGIN_PT; work_right=page_bound.x1-RIGHT_MARGIN_PT
        blocks, ref_font=_gather_formula_blocks_on_page(page,pidx,work_right)
        if DEBUG_DIAGNOSTICS: admin.append(f"[Formulas][p{pg}] candidates={len(blocks)}")
        for blk in blocks:
            n+=1; boxes[pg].append((blk.bbox.x0,blk.bbox.y0,blk.bbox.x1,blk.bbox.y1))
            issues=[]
            ig=[blk.number_bbox] if blk.number_bbox else []
            ga, gb, ref=_nearest_text_line_gaps(page, blk.bbox, ig)
            ref_use=ref if ref and ref>0 else ref_font
            need=max(GAP_MIN_PT, min(GAP_MAX_PT, (ref_use or 12.0)*GAP_SCALE))
            if ga is None or ga+1e-3 < need: issues.append(f"Перед формулой должна быть как минимум одна свободная строка (~≥{need:.1f} pt).")
            if gb is None or gb+1e-3 < need: issues.append(f"После формулы должна быть как минимум одна свободная строка (~≥{need:.1f} pt).")
            if not _center_ok(blk.core_bbox, work_left, work_right): issues.append("Формула должна быть выровнена по центру строки.")
            if blk.number is None: issues.append("Формула должна иметь порядковый номер в круглых скобках справа, например (1).")
            else:
                if blk.number_bbox and abs(work_right-blk.number_bbox.x1) > TOL_RIGHT:
                    issues.append("Номер формулы должен располагаться у правого края рабочей области строки.")
                refs=[(p,bb) for p,items in all_refs.items() for (nn,bb) in items if nn==blk.number]
                refs=[(p,bb) for (p,bb) in refs if not (p==pg and bb.intersects(blk.bbox))]
                if not refs: issues.append(f"Нет ссылок в тексте вида «({blk.number})» на эту формулу (6.8.4).")
            if not _inside_work(blk.bbox, page_bound): issues.append("Формула/номер выходят за пределы рабочих полей страницы.")
            if issues:
                errors.add(pg)
                if annotate_pdf:
                    try: page.add_text_annot(fitz.Point(blk.bbox.x0, max(blk.bbox.y0-8, page_bound.y0+TOP_MARGIN_PT)), "Формула: нарушения\n" + "\n".join("• "+i for i in issues))
                    except: pass
                admin.append(f"[p{pg}] Формула: проблемы:"); admin.extend("  - "+i for i in issues)
            else:
                if annotate_pdf:
                    try: page.add_text_annot(fitz.Point(blk.bbox.x0, max(blk.bbox.y0-8, page_bound.y0+TOP_MARGIN_PT)), f"Формула корректна{f' (№{blk.number})' if blk.number else ''}")
                    except: pass
                admin.append(f"[p{pg}] Формула корректна{f' (№{blk.number})' if blk.number else ''}.")
    user_summary = ("⚠️Проверка формул: обнаружены нарушения на стр. " + ", ".join(map(str, sorted(errors)))) if errors else "✅Проверка формул"
    admin_details = (f"[Formulas] Найдено формул: {n}" + ("\n" + "\n".join(admin) if admin else ""))
    return {"user_summary": user_summary, "admin_details": admin_details, "formula_bboxes_by_page": dict(boxes)}
