"""Pure VAT rate-resolution and base/tax split, by integer piasters and basis points."""
from decimal import Decimal

from morv.core import money

TREATMENTS = ("standard", "exempt", "zero")


def resolve_rate(treatment: str, item_rate: int | None, general_rate: int) -> int:
    """exempt/zero never carry the general rate even if an item_rate override is set."""
    if treatment in ("exempt", "zero"):
        return 0
    return item_rate if item_rate is not None else general_rate


def split_inclusive(total: int, rate_bp: int) -> tuple[int, int]:
    """total already contains the tax; floors the net so net+tax reconstructs total exactly."""
    if total < 0 or rate_bp < 0:
        raise money.MoneyError("قيمة أو نسبة سالبة")
    if rate_bp == 0:
        return total, 0
    net = total * 10000 // (10000 + rate_bp)
    return net, total - net


def tax_on_exclusive(base: int, rate_bp: int) -> int:
    if base < 0 or rate_bp < 0:
        raise money.MoneyError("قيمة أو نسبة سالبة")
    return money.percent_of(base, str(Decimal(rate_bp) / 100))
