"""Money as integer piasters."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_POUNDS = 10_000_000_000      # ten billion: far above any shop, well below the 64-bit storage limit
MAX_MINOR = MAX_POUNDS * 100

# Arabic and Persian digits and separators mapped to Latin
_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬",
    "01234567890123456789.,",
)


# Accepted shape: optional minus, an integer part (plain digits, or thousands
# groups of exactly three), then an optional fraction. [0-9] and not \d,
# because \d accepts digits we cannot weigh.
_AMOUNT = re.compile(r"^(?P<sign>-)?(?P<int>[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)?(?:\.(?P<frac>[0-9]*))?$")


class MoneyError(ValueError):
    """Bad money input: empty, not a number, or more than two decimals."""


def to_latin(text: str) -> str:
    return str(text).translate(_DIGITS)


def parse_money(text) -> int:
    if isinstance(text, int):
        raise MoneyError("مرّر المبلغ نصًا؛ العدد الصحيح غامض (جنيه أم قرش؟)")
    s = to_latin(text).strip()
    if not s:
        raise MoneyError("المبلغ فارغ")
    m = _AMOUNT.match(s)
    if not m or not (m.group("int") or m.group("frac")):
        if "," in s and s.replace(",", "") and _AMOUNT.match(s.replace(",", "")):
            raise MoneyError(f"الفاصلة لفصل الآلاف فقط مثل 1,234.50 — وللكسور استخدم النقطة: {text!r}")
        raise MoneyError(f"ليس مبلغًا صحيحًا: {text!r}")
    frac = m.group("frac") or ""
    if len(frac) > 2:
        raise MoneyError(f"أكثر من خانتين عشريتين: {text!r}")
    pounds = int((m.group("int") or "0").replace(",", ""))
    if pounds > MAX_POUNDS:
        raise MoneyError("المبلغ أكبر من الحد المسموح")
    minor = pounds * 100 + int(frac.ljust(2, "0") or "0")
    return -minor if m.group("sign") else minor


def fmt(minor: int) -> str:
    if not isinstance(minor, int):
        raise MoneyError("fmt تقبل قروشًا (int) فقط")
    sign = "-" if minor < 0 else ""
    pounds, piasters = divmod(abs(minor), 100)
    return f"{sign}{pounds:,}.{piasters:02d}"


def percent_of(minor: int, rate: str, rounding=ROUND_HALF_UP) -> int:
    try:
        r = Decimal(to_latin(rate).strip())
    except InvalidOperation as e:
        raise MoneyError(f"نسبة غير صحيحة: {rate!r}") from e
    if not r.is_finite():
        raise MoneyError(f"نسبة غير صحيحة: {rate!r}")
    if r < 0:
        raise MoneyError("النسبة سالبة")
    return int((Decimal(minor) * r / 100).quantize(Decimal(1), rounding=rounding))
