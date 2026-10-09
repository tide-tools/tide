"""« · » внутри имён сессий — не разделитель списка (общий разборщик).

Вынесено из :mod:`tide.launcher.seed_draft` (SOC-143 F70), чтобы и сид
(``block_next``), и ``tide thread layer --main`` (:func:`tide.arc.thread_layers.split_main`)
резали по « · » одинаково и не рвали «роль · NN».
"""

from __future__ import annotations

import re
from typing import List

# «·» inside a session's name is not a list separator (SOC-101 F8): «проект — нить ·
# 04», «ao-and-order · 04», «… · 01 · tide-stack-8» (SOC-72) stay whole.
# F37: «… · 02 (tide-stack-20)» — имя с AO-id в скобках — тоже целое.
# F52: «designer · 06 = app-development-22» — имя с AO-id через «=» — тоже.
# F58: «роль · NN» с любым продолжением — имя сессии («с main · 04 12.10», «+ main
# · 04 и management-18», «да designer · 08 на ready-v3»): « · » после латинского
# слова-роли перед номером в 1–3 цифры — не разделитель. Русский глагол перед
# числом («проверить · 10 тестов»), число («шаг 1 · шаг 2») и дата («· 12.10 …»)
# по-прежнему режут.
NAME_DOT = re.compile(r"\s·\s(?=\d{2}\b(?:\s·|\s*$|[,;:)]|\s*=|\s*\())"
                      r"|(?<=\b\d{2})\s·\s(?=[A-Za-z][\w.-]*-\d+\b)"
                      r"|(?<=[A-Za-z])\s·\s(?=\d{1,3}\b(?![.:]\d))"
                      # F64: « · NN» перед «→», скобкой, запятой или концом строки —
                      # номер сессии при любом слове слева («у нити · 04 → пара 14»)
                      r"|\s·\s(?=\d{1,3}(?:\s*→|\s*\(|,|\s*$))")
KEEP = "\x00"
# «проект — нить · NN» с любым хвостом (SOC-101): «management — main · 02 через ao
# send», «… · NN (management-11)» — номер сессии стоит за именем вида «X — Y»,
# и « · » перед ним имени не рвёт, что бы ни шло следом.
DASH_NAME_DOT = re.compile(r"(?<=\S)(\s—\s[\w.@-]+)\s·\s(?=(?:\d{2}|NN)\b)")


def keep_name_dots(line: str) -> str:
    """Спрятать « · » внутри имён сессий, чтобы ``split`` по « · » их не рвал."""
    line = DASH_NAME_DOT.sub(lambda m: m.group(1) + KEEP, line)
    return NAME_DOT.sub(KEEP, line)


def split_dots(line: str) -> List[str]:
    """Резать строку по « · », не трогая имён сессий «роль · NN»; куски — как есть."""
    return [p.replace(KEEP, " · ") for p in keep_name_dots(line).split(" · ")]
