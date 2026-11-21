"""Customer statement read from the 1310 ledger account: opening balance, movements, closing."""
import sqlite3
from dataclasses import dataclass

from morv.infra import ledger

CUSTOMER_ACCOUNT = "1310"
TEXTS = {"sale": "فاتورة بيع آجلة", "sale_return": "مرتجع بيع", "customer_receipt": "سند قبض", "customer_deposit": "تسوية عربون على الدين"}

_MOVES = (
    "SELECT e.entry_date, l.debit, l.credit, e.source_type, e.source_id, "
    "coalesce(s.number, r.number, cr.number, cd.number, e.source_type || '#' || e.source_id), "
    "coalesce(s.customer_id, rs.customer_id, cr.customer_id, cd.customer_id) "
    "FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
    "LEFT JOIN sales s ON e.source_type = 'sale' AND s.id = e.source_id "
    "LEFT JOIN sale_returns r ON e.source_type = 'sale_return' AND r.id = e.source_id "
    "LEFT JOIN sales rs ON rs.id = r.sale_id "
    "LEFT JOIN customer_receipts cr ON e.source_type = 'customer_receipt' AND cr.id = e.source_id "
    "LEFT JOIN customer_deposits cd ON e.source_type = 'customer_deposit' AND cd.id = e.source_id "
    f"WHERE l.account_code = '{CUSTOMER_ACCOUNT}' "
    "ORDER BY e.entry_date, e.id, l.line_no")


@dataclass(frozen=True)
class StatementRow:
    date: str
    doc: str
    text: str
    debit: int
    credit: int
    balance: int


@dataclass(frozen=True)
class Statement:
    customer_id: int
    customer_name: str
    mobile: str
    date_from: str
    date_to: str
    opening: int
    debit_total: int
    credit_total: int
    closing: int
    rows: tuple


def statement(con: sqlite3.Connection, customer_id: int, date_from: str = "", date_to: str = "") -> Statement:
    head = con.execute("SELECT name, coalesce(mobile, '') FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not head:
        raise LookupError("العميل غير موجود")
    date_from = ledger.valid_date(date_from, "تاريخ البداية") if date_from else ""
    date_to = ledger.valid_date(date_to, "تاريخ النهاية") if date_to else ""
    if date_from and date_to and date_from > date_to:
        raise ledger.LedgerError("تاريخ البداية بعد تاريخ النهاية")
    opening = closing = balance = debit = credit = 0
    rows = []
    for day, dr, cr, source_type, _source_id, doc, owner in con.execute(_MOVES):
        if owner != customer_id:
            continue
        balance += dr - cr
        if date_from and day < date_from:
            opening = closing = balance
            continue
        if date_to and day > date_to:
            continue                                  # later movements leave the closing balance alone
        debit += dr
        credit += cr
        closing = balance
        rows.append(StatementRow(day, doc, TEXTS.get(source_type, source_type), dr, cr, balance))
    return Statement(customer_id, head[0], head[1], date_from, date_to, opening, debit, credit, closing, tuple(rows))


def unassigned(con: sqlite3.Connection) -> list[tuple]:
    """Ledger movements on the customers account that belong to no customer: a mapping gap, never normal."""
    return [r for r in con.execute(_MOVES) if r[6] is None]
