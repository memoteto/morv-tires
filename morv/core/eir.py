"""Pure ACT/365 Fixed EIR calculations for contractual instalment cash flows.

No database, posting, or assumption about actual collection dates is involved.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext

DAY_BASIS = Decimal(365)


class EirError(ValueError):
    """Contractual cash flows do not support a finite nonnegative EIR."""


@dataclass(frozen=True)
class EirCashflow:
    due_date: str
    amount: int
    interest: int
    principal: int
    closing_balance: int


@dataclass(frozen=True)
class EirMonth:
    period: str
    interest: int


@dataclass(frozen=True)
class EirSchedule:
    contract_date: str
    financed: int
    markup: int
    annual_rate: Decimal
    cashflows: tuple[EirCashflow, ...]
    months: tuple[EirMonth, ...]


def _iso(day: str) -> date:
    if not isinstance(day, str):
        raise EirError("Contract date and due dates must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(day)
    except ValueError as exc:
        raise EirError("Contract date and due dates must use YYYY-MM-DD") from exc
    if parsed.isoformat() != day:
        raise EirError("Contract date and due dates must use YYYY-MM-DD")
    return parsed


def _month_end(day: date) -> date:
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _rate(financed: int, start: date, payments: list[tuple[date, int]]) -> Decimal:
    if sum(amount for _, amount in payments) == financed:
        return Decimal(0)
    # Payments made on the contract date cannot be discounted.
    if sum(amount for due, amount in payments if due == start) >= financed:
        raise EirError("No finite EIR for cash flows on the contract date")

    flows = tuple((Decimal(amount), Decimal((due - start).days) / DAY_BASIS)
                  for due, amount in payments)

    def present_value(annual_rate: Decimal) -> Decimal:
        factor = Decimal(1) + annual_rate
        return sum((amount / (factor ** years) for amount, years in flows), Decimal(0))

    lower, upper = Decimal(0), Decimal(1)
    for _ in range(256):
        if present_value(upper) < financed:
            break
        upper *= 2
    else:
        raise EirError("Unable to bracket a finite nonnegative EIR")
    for _ in range(192):
        middle = (lower + upper) / 2
        if present_value(middle) > financed:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2


def calculate(financed: int, contract_date: str,
              payments: list[tuple[str, int]]) -> EirSchedule:
    """Discount fixed contractual instalments and allocate ACT/365F interest by month."""
    if type(financed) is not int or financed <= 0:
        raise EirError("Financed amount must be positive integer piasters")
    if not isinstance(payments, (tuple, list)) or not 2 <= len(payments) <= 24:
        raise EirError("Expected 2 to 24 contractual instalments")
    start = _iso(contract_date)
    flows = []
    previous = None
    for row in payments:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise EirError("Expected each instalment as (due_date, amount)")
        due, amount = row
        day = _iso(due)
        if type(amount) is not int or amount <= 0:
            raise EirError("Instalment amount must be positive integer piasters")
        if day < start or (previous is not None and day <= previous):
            raise EirError("Due dates must be sorted and unique, on or after the contract date")
        flows.append((day, amount))
        previous = day
    markup = sum(amount for _, amount in flows) - financed
    if markup < 0:
        raise EirError("Contractual payments cannot total less than financed amount")

    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = ROUND_HALF_UP
        annual_rate = _rate(financed, start, flows)
        payment_map = dict(flows)
        events = set(payment_map)
        month_end = _month_end(start)
        while month_end <= flows[-1][0]:
            if month_end > start:
                events.add(month_end)
            if month_end == flows[-1][0]:
                break
            month_end = _month_end(_next_month(month_end))

        exact_balance = Decimal(financed)
        accrued_exact = Decimal(0)
        rounded_interest = 0
        paid_total = 0
        previous_event = start
        previous_due_interest = 0
        cashflows = []
        monthly = {}
        for day in sorted(events):
            years = Decimal((day - previous_event).days) / DAY_BASIS
            growth = (Decimal(1) + annual_rate) ** years
            interest_exact = exact_balance * (growth - 1)
            exact_balance += interest_exact
            accrued_exact += interest_exact
            if day == flows[-1][0]:
                total_rounded = markup
            else:
                total_rounded = int(accrued_exact.quantize(Decimal(1)))
            interest = total_rounded - rounded_interest
            rounded_interest = total_rounded
            period = day.isoformat()[:7]
            monthly[period] = monthly.get(period, 0) + interest
            if day in payment_map:
                amount = payment_map[day]
                paid_total += amount
                exact_balance -= Decimal(amount)
                principal = amount - (rounded_interest - previous_due_interest)
                previous_due_interest = rounded_interest
                closing_balance = financed + rounded_interest - paid_total
                cashflows.append(EirCashflow(day.isoformat(), amount,
                                             amount - principal, principal, closing_balance))
            previous_event = day
        return EirSchedule(contract_date, financed, markup, +annual_rate,
                           tuple(cashflows), tuple(EirMonth(k, v) for k, v in monthly.items()))
