import re

#структурные элементы
MM_TO_PT = 2.8346456693
CENTER_TOL_PT = 6.0
EDGE_TOL_PT   = 4.0
FONT_MIN_PT   = 12.0
FONT_MAX_PT   = 14.0
FONT_TOL_PT   = 0.1
REQUIRED_ORDER_CORE = [
    "содержание",
    "введение",
    "заключение",
    "список использованных источников",
]
OPTIONAL_REFS = "реферат"
OPTIONAL_TERMS = "термины и определения"
OPTIONAL_ABBREV = "перечень сокращений и обозначений"
ALL_ALLOWED_CORE = set(REQUIRED_ORDER_CORE)
ALL_ALLOWED_OPTIONALS = {OPTIONAL_REFS, OPTIONAL_TERMS, OPTIONAL_ABBREV}
APP_REGEX = re.compile(r"^приложение(?:\s+[0-9a-zа-я])?$", re.IGNORECASE)



#Таблицы
LEFT_MARGIN_PT   = 3 * 28.35
RIGHT_MARGIN_PT  = 1.5 * 28.35
TOP_MARGIN_PT    = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT     = 2
CONT_NEAR_TOP_EXTRA_MM = 20.0 
CONT_NEAR_TOP_EXTRA_PT = CONT_NEAR_TOP_EXTRA_MM * MM_TO_PT
CONT_MAX_GAP_PT = 14.0



#Подписи таблиц
DASH_CHARS = "-–—"                 
CAPTION_PREFIX = "Таблица"
CONTINUATION_PREFIX = "Продолжение таблицы"
CAPTION_NUMBER_RE = re.compile(
    rf"^Таблица\s+"
    rf"(?P<prefix>[A-Za-zА-Яа-я])?\.?\s*"           
    rf"(?P<number>\d+(?:\.\d+)*)"                   
    rf"\s–\s"                                       
    rf"(?P<title>.+?)\s*$"
)
CONT_NUMBER_RE = re.compile(
    r"^Продолжение\s+таблицы\s+"
    r"(?P<prefix>[A-Za-zА-Яа-я])?\.?\s*"
    r"(?P<number>\d+(?:\.\d+)*)\s*$",
    re.IGNORECASE
)
BAD_PREFIX_RE = re.compile(r"^\s*таб(?:\.|\b)", re.IGNORECASE)




#Списки
CM_TO_PT = 28.35
INDENT_STEP_CM = 0.75
INDENT_STEP_PT = INDENT_STEP_CM * CM_TO_PT
INDENT_TOL_PT  = 4.0
PARAGRAPH_INDENT_CM = 1.25
PARAGRAPH_INDENT_PT = PARAGRAPH_INDENT_CM * CM_TO_PT  # 35.4375 pt
LINE_SPACING_TARGET = 1.50
LINE_SPACING_TOL    = 0.06
ALIGN_TOL_PT        = 4.0
ALIGN_FRACTION_OK   = 0.70
MAX_GAP_BEFORE_AFTER_FACTOR = 1.2  # * fontsize
EN_DASH = "–"
RUS_LETTERS = "абвгдеёжзийклмнопрстуфхцчшщьыъэюя"
EXCLUDED = set("ёзйочьъы")
ALLOWED_LETTERS = tuple(ch for ch in RUS_LETTERS if ch not in EXCLUDED)
ALLOWED_STR = "".join(ALLOWED_LETTERS)  # noqa: E305
NBSP = "\u00A0"
SPACE_CLS = rf"[ \t{NBSP}]"
RE_START_TIGHT = re.compile(
    rf"^\s*(?:{re.escape(EN_DASH)}|"
    rf"\d+[.)]|"
    rf"[{ALLOWED_STR}][.)]|"
    rf"[IVXLC]+[.)])"
)
RE_START_SIMPLE = re.compile(
    rf"^\s*(?:{re.escape(EN_DASH)}{SPACE_CLS}+|\d+[.)]{SPACE_CLS}+|"
    rf"[{ALLOWED_STR}][.)]{SPACE_CLS}+|[IVXLC]+[.)]{SPACE_CLS}+)"
)
BULLET_CHARS = "•·●∙◦▪▫■□◆►▶▸▹➤➣➢➧➜➔➙➛➟"
PSEUDO_BULLET_CHARS = "oO"  
MARKER_MAX_W_PT = 8.0 * MM_TO_PT
MARKER_MAX_H_PT = 8.0 * MM_TO_PT
DEBUG_DIAGNOSTICS = True



#рисунки
CENTER_TOL_CM = 0.2
MIN_W_MM      = 30      
MIN_H_MM      = 15     
MIN_AREA_PCT  = 0.30    
THIN_LINE_MM  = 1.0     
MARKER_MAX_W_MM = 8.0
MARKER_MAX_H_MM = 8.0

