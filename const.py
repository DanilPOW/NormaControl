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



#Подписи таблиц
LEFT_MARGIN_PT   = 3 * 28.35
RIGHT_MARGIN_PT  = 1.5 * 28.35
TOP_MARGIN_PT    = 2 * 28.35
BOTTOM_MARGIN_PT = 2 * 28.35
TOLERANCE_PT     = 2
CONT_NEAR_TOP_EXTRA_MM = 20.0 
CONT_NEAR_TOP_EXTRA_PT = CONT_NEAR_TOP_EXTRA_MM * MM_TO_PT
CONT_MAX_GAP_PT = 14.0
