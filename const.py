import re

#структурные элементы
MM_TO_PT = 2.8346456693
CM_TO_PT = 28.35
LEFT_MARGIN_PT   = 3.0 * CM_TO_PT
RIGHT_MARGIN_PT  = 1.5 * CM_TO_PT
TOP_MARGIN_PT    = 2.0 * CM_TO_PT
BOTTOM_MARGIN_PT = 2.0 * CM_TO_PT

# Допуски
CENTER_TOL_PT = 6.0
EDGE_TOL_PT   = 4.0
FONT_MIN_PT   = 12.0
FONT_MAX_PT   = 14.0
FONT_TOL_PT   = 0.1

# Набор обязательных заголовков
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

# Обработка приложений
APP_REGEX = re.compile(r"^приложение(?:\s+[0-9a-zа-я])?$", re.IGNORECASE)
