"""Read-only report of unresolved till and safe differences and partners who owe the shop."""
import sqlite3
from dataclasses import dataclass

from morv.infra import ledger

SOURCES = (("cash_closings", "close", "closing_date", "إقفال الصندوق"),
           ("safe_counts", "count", "count_date", "جرد الخزنة"))


@dataclass(frozen=True)
class Difference:
    source: str
    number: str
    day: str
    amount: int          # positive magnitude
    memo: str


@dataclass(frozen=True)
class PartnerDebt:
    name: str
    amount: int


@dataclass(frozen=True)
class Report:
    shortages: tuple[Difference, ...]
    overages: tuple[Difference, ...]
    shortage_total: int
    overage_total: int
    shortage_drift: int      # documents minus ledger (5291, 5292); non-zero means a posting was bypassed
    overage_drift: int       # documents minus ledger (2320, 2330)
    partner_debts: tuple[PartnerDebt, ...]


def _live_differences(con: sqlite3.Connection) -> list[tuple[Difference, bool]]:
    out = []
    for table, kind, date_col, label in SOURCES:
        rows = con.execute(
            f"SELECT c.number, c.{date_col}, c.difference, coalesce(c.memo, '') FROM {table} c "
            f"WHERE c.kind = ? AND c.difference <> 0 "
            f"AND NOT EXISTS (SELECT 1 FROM {table} x WHERE x.cancels_id = c.id) ORDER BY c.{date_col}, c.id",
            (kind,)).fetchall()
        out += [(Difference(label, number, day, abs(diff), memo), diff < 0) for number, day, diff, memo in rows]
    return out


def build(con: sqlite3.Connection) -> Report:
    docs = sorted(_live_differences(con), key=lambda pair: (pair[0].day, pair[0].number))
    shortages = tuple(d for d, is_short in docs if is_short)
    overages = tuple(d for d, is_short in docs if not is_short)
    short_total, over_total = sum(d.amount for d in shortages), sum(d.amount for d in overages)
    ledger_short = ledger.account_balance(con, "5291") + ledger.account_balance(con, "5292")
    ledger_over = -(ledger.account_balance(con, "2320") + ledger.account_balance(con, "2330"))
    debts = []
    for name, account in con.execute("SELECT name, current_account FROM partners ORDER BY name"):
        balance = ledger.account_balance(con, account)
        if balance > 0:
            debts.append(PartnerDebt(name, balance))
    return Report(shortages, overages, short_total, over_total, short_total - ledger_short,
                  over_total - ledger_over, tuple(debts))
