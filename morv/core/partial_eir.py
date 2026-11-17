"""Read-only EIR remeasurement of an explicitly agreed modified instalment schedule.

This module NEVER posts a return, generates customer terms, or replaces historical
contractual instalments. Callers must verify classification, dates, receipts and
the ledger carrying amount before they may use a result for accounting.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext

DAY_BASIS = Decimal(365)
POLICY = "ACT365F_MOD_V1"
NON_DERECOGNITION = "non_derecognition"


class ModificationError(ValueError):
    """Unapproved, incomplete or mathematically inconsistent modified cash flows."""


@dataclass(frozen=True)
class ModificationValuation:
    effective_date: str
    original_annual_rate: Decimal
    carrying_before: int
    original_future_pv: int
    modified_future_pv: int
    modification_gain_loss: int  # positive gain, negative loss (seller's asset)
    received: int
    approved_total_due: int
    applied_receipts: int
    refundable_excess: int
    remaining_claim: int
    modified_future: tuple[tuple[str, int], ...]
    fingerprint: str


def _day(value: str) -> date:
    if not isinstance(value, str):
        raise ModificationError("Dates must have the canonical YYYY-MM-DD format")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ModificationError("Dates must have the canonical YYYY-MM-DD format") from exc
    if day.isoformat() != value:
        raise ModificationError("Dates must have the canonical YYYY-MM-DD format")
    return day


def _money(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ModificationError(f"{name} must be nonnegative integer piasters")
    return value


def _cashflows(value, as_of: date, label: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, (tuple, list)) or len(value) > 24:
        raise ModificationError(f"{label} requires at most 24 ordered instalments")
    result = []
    last = as_of
    for row in value:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise ModificationError(f"{label} cash flows must be (due_date, amount)")
        due, amount = row
        current = _day(due)
        if current <= last:
            raise ModificationError(f"{label} due dates must be strictly future, unique and ordered")
        if type(amount) is not int or amount <= 0:
            raise ModificationError(f"{label} instalment amounts must be positive integer piasters")
        result.append((due, amount))
        last = current
    return tuple(result)


def _present_value(as_of: date, annual_rate: Decimal, flows) -> int:
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            ctx.rounding = ROUND_HALF_UP
            factor = Decimal(1) + annual_rate
            value = sum(
                (Decimal(amount) / (factor ** (Decimal((_day(due) - as_of).days) / DAY_BASIS))
                 for due, amount in flows),
                Decimal(0),
            )
            return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (DecimalException, OverflowError, ValueError) as exc:
        raise ModificationError("Unable to discount the approved cash flows") from exc


def remeasure(*, effective_date: str, original_annual_rate: Decimal,
              original_future: list[tuple[str, int]] | tuple,
              modified_future: list[tuple[str, int]] | tuple,
              carrying_before: int, received: int, approved_total_due: int,
              classification: str) -> ModificationValuation:
    """Value modified future cash flows at the ORIGINAL EIR, ACT/365 fixed.

    original_future includes all unpaid original contractual cash flows strictly
    after effective_date. carrying_before must be the verified ledger net asset
    at that very date, including any required accrued interest. Refuse stale
    carrying balances rather than inventing backdated interest.

    approved_total_due is an independently agreed, signed contractual claim
    inclusive of any retained/earned finance amounts. No price-based markup
    proration or automatic replacement of the customer agreement is allowed.

    The returned gain/loss is mathematical only; NO ledger account is selected.
    """
    if classification != NON_DERECOGNITION:
        raise ModificationError("The modification must first be classified as non-derecognition")
    if type(original_annual_rate) is not Decimal or not original_annual_rate.is_finite() \
            or original_annual_rate < 0:
        raise ModificationError("The original effective annual rate must be a finite Decimal >= 0")
    as_of = _day(effective_date)
    original = _cashflows(original_future, as_of, "Original")
    modified = _cashflows(modified_future, as_of, "Modified")
    carrying = _money(carrying_before, "Original carrying amount")
    collected = _money(received, "Collected payments")
    agreed = _money(approved_total_due, "Approved total claim")
    applied = min(collected, agreed)
    excess = collected - applied
    outstanding = agreed - applied
    if sum(amount for _, amount in modified) != outstanding:
        raise ModificationError("Modified instalments must total the agreed claim less applied receipts")
    orig_pv = _present_value(as_of, original_annual_rate, original)
    if abs(orig_pv - carrying) > 1:
        raise ModificationError("Original cash flows do not reconcile with the booked carrying amount")
    new_pv = _present_value(as_of, original_annual_rate, modified)
    fingerprint = hashlib.sha256(json.dumps(
        [POLICY, effective_date, str(original_annual_rate), original, modified,
         carrying, collected, agreed, classification],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return ModificationValuation(
        effective_date, original_annual_rate, carrying, orig_pv, new_pv,
        new_pv - carrying, collected, agreed, applied, excess, outstanding,
        modified, fingerprint,
    )
