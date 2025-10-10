# scripts/headings_checker.py
from __future__ import annotations
import re, fitz
from typing import Dict, List, Tuple

PREFIX = "Сервис нормоконтроля: заголовки"
HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})\s+\S", re.U)  # 1 / 1.2 / 1.2.3 ... + пробел + текст

BOLD_TOKENS = ("bold","bd","black","semibold","semi-bold","demi","demibold","heavy","extrabold","ultrabold")

def _is_bold(span: dict) -> bool:
    # Надёжнее всего: по названию шрифта
    fname = (span.get("font") or "").lower()
    if any(tok in fname for tok in BOLD_TOKENS):
        return True
    # Иногда «жирность» упакована в variation / wght, если она есть
    try:
        if span.get("wmode") == 0 and float(span.get("size", 0)) >= 0:  # заглушка, чтобы не падать
            pass
    except:
        pass
    return False

def check_headings(doc: fitz.Document, *, start_page:int=3, annotate_pdf:bool=False) -> dict:
    """
    Находит НУМЕРОВАННЫЕ жирные заголовки вида '1', '1.2', '1.2.3 ...' + пробел + текст.
    Возвращает user_summary, admin_details. При annotate_pdf=True — обводит рамкой.
    """
    found: Dict[int, List[str]] = {}
    for pno in range(max(0, start_page-1), len(doc)):
        page = doc[pno]
        try:
            blocks = page.get_text("dict").get("blocks", [])
        except Exception:
            blocks = []

        for b in blocks:
            for line in b.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                # Требуем, чтобы ВЕСЬ заголовок был жирным (обычно так в ГОСТовых стилях)
                if not all(_is_bold(s) for s in spans):
                    continue

                txt = "".join(s.get("text", "") for s in spans).strip()
                if not txt or txt.startswith(PREFIX):
                    continue

                if HEAD_RE.match(txt):
                    # Сохраняем
                    found.setdefault(pno+1, []).append(txt)

                    if annotate_pdf:
                        # Рисуем одну объединённую рамку вокруг строки
                        # (можно было бы по каждому span, но одна рамка «чище»)
                        rects = []
                        for s in spans:
                            try:
                                # bbox = [x0, y0, x1, y1]
                                x0, y0, x1, y1 = s.get("bbox", [0,0,0,0])
                                rects.append(fitz.Rect(x0, y0, x1, y1))
                            except Exception:
                                pass
                        if rects:
                            u = rects[0]
                            for r in rects[1:]:
                                u |= r
                            # Добавим заметную рамку (square annot). Можно и highlight, но рамка не «желтит» текст.
                            annot = page.add_rect_annot(u)
                            annot.set_border(width=1)
                            annot.set_colors(stroke=(0, 0, 1))  # синий контур
                            annot.set_info(title=PREFIX, content="Найден заголовок: " + txt[:120])
                            annot.update()

    total = sum(len(v) for v in found.values())
    per_page = [f"Стр. {k}: найдено заголовков {len(v)}" for k, v in sorted(found.items())]
    admin_details = (
        "[Headings]\n"
        f"Найдено заголовков всего: {total}\n" +
        ("\n".join(per_page) if per_page else "Заголовков не найдено.")
    )
    # Если заголовков 0 — пусть пользователь это увидит явно
    user_summary = ("✅ Заголовки: найдены и помечены" if total > 0 
                    else "ℹ️ Заголовки: нумерованных жирных заголовков не обнаружено")
    return {"user_summary": user_summary, "admin_details": admin_details}
