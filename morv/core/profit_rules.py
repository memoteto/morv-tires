"""Pure profit-sharing rules: basis points, reserve, and exact allocation of an amount to partners."""
from decimal import Decimal, InvalidOperation

from morv.core.money import to_latin

TOTAL_BP = 10_000


class ShareError(ValueError):
    pass


def parse_percent(text) -> int:
    """'12.5' -> 1250 basis points; at most two decimals, between 0 and 100."""
    s = to_latin(text).strip().rstrip("%").strip()
    try:
        value = Decimal(s)
    except InvalidOperation as e:
        raise ShareError("النسبة غير صالحة") from e
    bp = value * 100
    if bp != bp.to_integral_value() or not 0 <= bp <= TOTAL_BP:
        raise ShareError("النسبة بين 0 و100 وبحد أقصى خانتين عشريتين")
    return int(bp)


def fmt_percent(bp: int) -> str:
    whole, frac = divmod(bp, 100)
    return f"{whole}.{frac:02d}".rstrip("0").rstrip(".")


def reserve_for(period_net: int, reserve_bp: int) -> int:
    """Reserve is a share of a positive period result only; a loss creates no negative reserve."""
    return period_net * reserve_bp // TOTAL_BP if period_net > 0 else 0


def split(amount: int, shares: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """(partner id, bp) -> (partner id, piasters); sums exactly to amount, largest remainder first."""
    if sum(bp for _, bp in shares) != TOTAL_BP:
        raise ShareError("مجموع الحصص يجب أن يساوي 100%")
    if type(amount) is not int or amount < 0:
        raise ShareError("المبلغ عدد صحيح موجب بالقرش")
    base = [(pid, amount * bp // TOTAL_BP, amount * bp % TOTAL_BP) for pid, bp in shares]
    left = amount - sum(b for _, b, _ in base)
    order = sorted(range(len(base)), key=lambda i: (-base[i][2], base[i][0]))
    extra = {order[i] for i in range(left)}
    return [(pid, b + (1 if i in extra else 0)) for i, (pid, b, _) in enumerate(base)]
