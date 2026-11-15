"""Tyre DOT code (four digits, week and year): parsing and ordering."""
import re

from morv.core.money import to_latin

_DOT = re.compile(r"^([0-9]{2})([0-9]{2})$")


class DotError(ValueError):
    """Invalid DOT code."""


def parse_dot(text) -> str:
    s = to_latin(str(text or "")).strip().replace(" ", "")
    m = _DOT.match(s)
    if not m or not 1 <= int(m.group(1)) <= 53:
        raise DotError(f"رمز DOT غير صالح (المطلوب أسبوع وسنة مثل 2324): {text!r}")
    return s


def mfg_key(dot: str) -> int:
    d = parse_dot(dot)
    return int(d[2:]) * 100 + int(d[:2])
