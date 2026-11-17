"""Tyre DOT code (four digits, week and year): parsing and ordering."""
import re
from datetime import date, timedelta

from morv.core.money import to_latin

_DOT = re.compile(r"^([0-9]{2})([0-9]{2})$")


class DotError(ValueError):
    """Invalid DOT code."""


def parse_dot(text) -> str:
    s = to_latin(str(text or "")).strip().replace(" ", "")
    m = _DOT.match(s)
    if not m or not 1 <= int(m.group(1)) <= 53:
        raise DotError(f"رمز DOT غير صالح (المطلوب أسبوع وسنة مثل 2324): {text!r}")
    week = int(m.group(1))
    year = 2000 + int(m.group(2))
    jan_first = date(year, 1, 1)
    first_sunday = jan_first + timedelta(days=(6 - jan_first.weekday()) % 7)
    week_start = first_sunday + timedelta(weeks=week - 1)
    if week_start.year != year:
        raise DotError(f"أسبوع DOT {week:02d} غير موجود في سنة {year}")
    if week_start > date.today():
        raise DotError(f"تاريخ تصنيع DOT في المستقبل: {s}")
    return s


def mfg_key(dot: str) -> int:
    d = parse_dot(dot)
    return int(d[2:]) * 100 + int(d[:2])
