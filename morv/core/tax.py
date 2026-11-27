"""Pure VAT rate-resolution and base/tax split, by integer piasters and basis points."""
from decimal import Decimal

from morv.core import money

TREATMENTS = ("standard", "exempt", "zero")
_SEARCH_RADIUS = 2   # the floor estimate is off by at most 1; +1 margin for the adjustment itself


def _int(value, label: str) -> int:
    if type(value) is not int:
        raise money.MoneyError(f"{label} عدد صحيح بالقرش")
    return value


def resolve_rate(treatment: str, item_rate: int | None, general_rate: int) -> int:
    """exempt/zero never carry the general rate even if an item_rate override is set."""
    if treatment not in TREATMENTS:
        raise money.MoneyError(f"معاملة ضريبية غير معروفة: {treatment!r}")
    if item_rate is not None:
        _int(item_rate, "نسبة الصنف")
        if item_rate < 0:
            raise money.MoneyError("نسبة الصنف سالبة")
    _int(general_rate, "النسبة العامة")
    if general_rate < 0:
        raise money.MoneyError("النسبة العامة سالبة")
    if treatment in ("exempt", "zero"):
        return 0
    return item_rate if item_rate is not None else general_rate


def tax_on_exclusive(base: int, rate_bp: int) -> int:
    """The one rounding rule for a rate on a net amount: money.percent_of, half up."""
    _int(base, "القيمة")
    _int(rate_bp, "النسبة")
    if base < 0 or rate_bp < 0:
        raise money.MoneyError("قيمة أو نسبة سالبة")
    return money.percent_of(base, str(Decimal(rate_bp) / 100))


def split_inclusive(total: int, rate_bp: int) -> tuple[int, int]:
    """Reverses tax_on_exclusive exactly: the net it returns always re-derives this same tax.

    A total genuinely built as net + tax_on_exclusive(net, rate) must decode back to that exact
    (net, tax) pair, or the inclusive and exclusive paths would silently disagree on the same
    sale. A plain floor/divide does not guarantee this (rounding boundaries can land either side),
    so this searches the handful of candidates around the floor estimate for the one whose
    tax_on_exclusive reproduces the remaining amount. A total nobody's rate ever actually produced
    this way (a sticker price typed by hand) has no exact match; it then falls back to the floor
    estimate, which always keeps net + tax == total and never favours the shop's own revenue.
    """
    _int(total, "الإجمالي")
    _int(rate_bp, "النسبة")
    if total < 0 or rate_bp < 0:
        raise money.MoneyError("قيمة أو نسبة سالبة")
    if rate_bp == 0:
        return total, 0
    floor_net = total * 10000 // (10000 + rate_bp)
    for net in range(floor_net - _SEARCH_RADIUS, floor_net + _SEARCH_RADIUS + 1):
        if 0 <= net <= total and tax_on_exclusive(net, rate_bp) == total - net:
            return net, total - net
    return floor_net, total - floor_net
