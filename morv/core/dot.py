"""رمز DOT لتاريخ تصنيع الإطار (أسبوع + سنة من أربع خانات): فحصه وترتيبه."""
import re

from morv.core.money import to_latin

_DOT = re.compile(r"^([0-9]{2})([0-9]{2})$")


class DotError(ValueError):
    """رمز DOT غير صالح."""


def parse_dot(text) -> str:
    """توحيد رمز DOT إلى WWYY بعد قبول الأرقام العربية والمسافات؛ الأسبوع 01 إلى 53."""
    s = to_latin(str(text or "")).strip().replace(" ", "")
    m = _DOT.match(s)
    if not m or not 1 <= int(m.group(1)) <= 53:
        raise DotError(f"رمز DOT غير صالح (المطلوب أسبوع وسنة مثل 2324): {text!r}")
    return s


def mfg_key(dot: str) -> int:
    """مفتاح ترتيب التصنيع: السنة ثم الأسبوع (الأصغر = الأقدم تصنيعًا)."""
    d = parse_dot(dot)
    return int(d[2:]) * 100 + int(d[:2])
