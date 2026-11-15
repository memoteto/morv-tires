"""Pure instalment maths: financed amount, flat markup, the monthly schedule. No database."""
from dataclasses import dataclass
from datetime import date

MIN_MONTHS = 2
MAX_MONTHS = 24
MAX_RATE_BP = 10000          # 100%, as the approved contract screen allows


class InstalmentError(ValueError):
    """Contract terms refused, with a message fit to show."""


@dataclass(frozen=True)
class Instalment:
    no: int
    due_date: str
    principal: int
    markup: int
    amount: int
    balance_after: int


@dataclass(frozen=True)
class Plan:
    cash_price: int
    down: int
    financed: int
    rate_bp: int
    markup: int
    months: int
    monthly: int
    instalments_total: int
    contract_total: int
    rows: tuple


def add_months(day: str, count: int) -> str:
    """Same day of month, count months on; a day past the month's end lands on its last day."""
    y, m, d = (int(x) for x in day.split("-"))
    total = y * 12 + (m - 1) + count
    year, month = total // 12, total % 12 + 1
    last = _days_in_month(year, month)
    return f"{year:04d}-{month:02d}-{min(d, last):02d}"


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def markup_of(financed: int, rate_bp: int) -> int:
    """Flat markup on the financed amount, never on the down payment. Half-up in piasters."""
    return (financed * rate_bp + 5000) // 10000


def plan(cash_price: int, down: int, rate_bp: int, months: int, first_due: str) -> Plan:
    """The contract as the approved screen computes it; the last instalment carries the piaster difference."""
    for name, value in (("سعر الكاش", cash_price), ("المقدم", down), ("نسبة الفائدة", rate_bp)):
        if type(value) is not int or value < 0:
            raise InstalmentError(f"{name}: عدد صحيح غير سالب")
    if cash_price <= 0:
        raise InstalmentError("سعر الكاش يجب أن يكون أكبر من صفر")
    if down > cash_price:
        raise InstalmentError("المقدم أكبر من سعر الكاش")
    if down == cash_price:
        raise InstalmentError("المقدم يغطي السعر كاملًا؛ هذا بيع نقدي لا عقد تقسيط")
    if rate_bp > MAX_RATE_BP:
        raise InstalmentError("نسبة الفائدة بين 0 و100")
    if type(months) is not int or not MIN_MONTHS <= months <= MAX_MONTHS:
        raise InstalmentError(f"عدد الأشهر بين {MIN_MONTHS} و{MAX_MONTHS}")
    _check_date(first_due)

    financed = cash_price - down
    markup = markup_of(financed, rate_bp)
    principal_base, markup_base = financed // months, markup // months
    rows, balance = [], financed + markup
    for i in range(months):
        last = i == months - 1
        principal = financed - principal_base * (months - 1) if last else principal_base
        share = markup - markup_base * (months - 1) if last else markup_base
        balance -= principal + share
        rows.append(Instalment(i + 1, add_months(first_due, i), principal, share, principal + share, balance))
    return Plan(cash_price, down, financed, rate_bp, markup, months, principal_base + markup_base,
                financed + markup, down + financed + markup, tuple(rows))


def _check_date(day: str) -> None:
    try:
        date.fromisoformat(day)
    except (TypeError, ValueError) as e:
        raise InstalmentError("تاريخ أول قسط غير صالح") from e
