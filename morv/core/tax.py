"""Pure VAT rate-resolution and base/tax split, by integer piasters and basis points."""
from decimal import Decimal

from morv.core import money

TREATMENTS = ("standard", "exempt", "zero")


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
    """tax = round_half_up(total*rate/(10000+rate)); exactly reverses tax_on_exclusive, tested."""
    _int(total, "الإجمالي")
    _int(rate_bp, "النسبة")
    if total < 0 or rate_bp < 0:
        raise money.MoneyError("قيمة أو نسبة سالبة")
    if rate_bp == 0:
        return total, 0
    tax_amount = int((Decimal(total * rate_bp) / Decimal(10000 + rate_bp))
                      .quantize(Decimal(1), rounding=money.ROUND_HALF_UP))
    return total - tax_amount, tax_amount
