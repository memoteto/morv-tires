"""Read-only review of installment interest by period: unrecognised, beyond the engine, or mis-dated."""
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from morv.core import eir
from morv.infra import interest_recognition as engine
from morv.infra import ledger

READY = "ready"            # the engine can post it from the installments screen
BLOCKED = "blocked"        # the engine refuses this contract/month; needs a decision, never auto-posted
MISDATED = "misdated"      # recognised, but dated after the end of the range, so that range lacks the income


@dataclass(frozen=True)
class Item:
    contract_id: int
    number: str
    period: str
    amount: int | None
    status: str
    reason: str = ""


def month_end(period: str) -> date:
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, monthrange(year, month)[1])


def default_through(today: date | None = None) -> str:
    """Last day of the previous month: the newest month that is fully over."""
    return date.fromordinal((today or date.today()).replace(day=1).toordinal() - 1).isoformat()


def _periods(start: str, last_due: str) -> list[str]:
    year, month = int(start[:4]), int(start[5:7])
    out = []
    while f"{year:04d}-{month:02d}" <= last_due[:7]:
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _scheduled(con: sqlite3.Connection, contract_id: int, period: str) -> int | None:
    """Scheduled interest of a month even when the engine refuses it; None if it cannot be computed."""
    try:
        _id, start, financed = con.execute(
            "SELECT id, contract_date, financed FROM installment_contracts WHERE id=?", (contract_id,)).fetchone()
        rows = con.execute("SELECT due_date, amount FROM installment_rows WHERE contract_id=? ORDER BY no",
                           (contract_id,)).fetchall()
        return {m.period: m.interest for m in eir.calculate(financed, start, rows).months}.get(period)
    except (eir.EirError, TypeError, ValueError):
        return None


def review(con: sqlite3.Connection, through: str) -> list[Item]:
    """Interest of months ending on or before `through` that is not yet in the ledger, or sits after it."""
    items: list[Item] = []
    contracts = con.execute("SELECT id, number, contract_date FROM installment_contracts ORDER BY id").fetchall()
    for cid, number, start in contracts:
        last_due = con.execute("SELECT max(due_date) FROM installment_rows WHERE contract_id=?",
                               (cid,)).fetchone()[0] or start
        simulated: dict[str, int] = {}
        for period in _periods(start, last_due):
            end = month_end(period)
            if end.isoformat() > through:
                break
            try:
                state = engine.evaluate(con, cid, period, end, simulated)
            except engine.RecognitionError as exc:
                items.append(Item(cid, number, period, _scheduled(con, cid, period), BLOCKED, str(exc)))
                break
            if state[0] == "ready":
                simulated[period] = state[1]
                items.append(Item(cid, number, period, state[1], READY))
    for rcid, rnumber, period, amount, entry_date in con.execute(
            "SELECT r.contract_id, c.number, r.period, r.amount, r.entry_date FROM active_interest_recognitions r "
            "JOIN installment_contracts c ON c.id = r.contract_id ORDER BY r.contract_id, r.period"):
        if month_end(period).isoformat() <= through < entry_date:
            items.append(Item(rcid, rnumber, period, amount, MISDATED, f"مرحّل بتاريخ {entry_date}"))
    return items


@dataclass(frozen=True)
class PostResult:
    posted: int
    failed: tuple[str, ...]


def post_ready(con: sqlite3.Connection, by, items: list[Item]) -> PostResult:
    """Post READY months dated at month end; a refusal stops only that contract's later months."""
    posted, failed, stopped = 0, [], set()
    for it in sorted((i for i in items if i.status == READY), key=lambda i: (i.contract_id, i.period)):
        if it.contract_id in stopped:
            continue
        try:
            engine.recognise_month(con, by, contract_id=it.contract_id, period=it.period,
                                   entry_date=month_end(it.period).isoformat())
            posted += 1
        except (engine.RecognitionError, ledger.LedgerError) as exc:
            stopped.add(it.contract_id)
            failed.append(f"{it.number} {it.period}: {exc}")
    return PostResult(posted, tuple(failed))
