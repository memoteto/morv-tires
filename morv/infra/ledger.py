"""Post balanced journal entries and read balances, inside the caller's transaction."""
import sqlite3
from datetime import date

from morv.infra.users import audit


class LedgerError(ValueError):
    """Entry refused: unbalanced, or an invalid amount or account."""


def valid_date(text, what: str = "التاريخ") -> str:
    try:
        d = date.fromisoformat(str(text))
    except ValueError as e:
        raise LedgerError(f"{what} غير صالح (المطلوب YYYY-MM-DD): {text!r}") from e
    if d > date.today():
        raise LedgerError(f"{what} في المستقبل: {text}")
    return d.isoformat()


def locked_through(con: sqlite3.Connection) -> str | None:
    """Last date of the live period lock, or None when no period is locked."""
    return con.execute(
        "SELECT max(l.through_date) FROM period_locks l WHERE l.kind = 'lock' "
        "AND NOT EXISTS (SELECT 1 FROM period_locks x WHERE x.cancels_id = l.id)").fetchone()[0]


def post_entry(con: sqlite3.Connection, by, entry_date: str, source_type: str, source_id: int,
               memo: str, lines: list) -> int:
    if not con.in_transaction:
        raise LedgerError("ترحيل القيد يجب أن يكون داخل معاملة")
    if len(lines) < 2:
        raise LedgerError("القيد يحتاج سطرين على الأقل")
    for _, d, c in lines:
        if type(d) is not int or type(c) is not int:
            raise LedgerError("المبالغ أعداد صحيحة بالقرش فقط")
    if sum(d for _, d, _ in lines) != sum(c for _, _, c in lines):
        raise LedgerError("القيد غير متوازن")
    total = sum(d for _, d, _ in lines)
    try:
        cur = con.execute(
            "INSERT INTO journal_entries (entry_date, source_type, source_id, memo, line_count, total, user_id, status) "
            "VALUES (?,?,?,?,?,?,?,'open')",
            (entry_date, source_type, source_id, memo, len(lines), total, by.id if by else None))
    except sqlite3.IntegrityError as e:     # the period-lock trigger speaks Arabic; surface it as a ledger refusal
        raise LedgerError(str(e)) from e
    entry_id = cur.lastrowid
    try:
        for no, (acc, d, c) in enumerate(lines, start=1):
            con.execute("INSERT INTO journal_lines (entry_id, line_no, account_code, debit, credit) VALUES (?,?,?,?,?)",
                        (entry_id, no, acc, d, c))
    except sqlite3.IntegrityError as e:
        raise LedgerError(f"سطر قيد مرفوض: {e}") from e
    except sqlite3.DatabaseError as e:
        raise LedgerError(str(e)) from e
    try:
        con.execute("UPDATE journal_entries SET status='posted' WHERE id=?", (entry_id,))   # Post only once every line is written
    except sqlite3.DatabaseError as e:
        raise LedgerError(str(e)) from e
    audit(con, by, "journal.post", "journal_entry", entry_id, f"{source_type}#{source_id}")
    return entry_id


def account_balance(con: sqlite3.Connection, code: str) -> int:
    return con.execute("SELECT coalesce(sum(l.debit) - sum(l.credit), 0) FROM journal_lines l "
                       "JOIN journal_entries e ON e.id = l.entry_id WHERE e.status='posted' AND l.account_code=?",
                       (code,)).fetchone()[0]


def unbalanced_entries(con: sqlite3.Connection) -> list:
    rows = con.execute("SELECT entry_id FROM journal_lines GROUP BY entry_id HAVING sum(debit) <> sum(credit)")
    return [r[0] for r in rows]


def incomplete_entries(con: sqlite3.Connection) -> list:
    rows = con.execute("SELECT e.id FROM journal_entries e LEFT JOIN journal_lines l ON l.entry_id = e.id "
                       "GROUP BY e.id HAVING count(l.id) <> e.line_count")
    return [r[0] for r in rows]


def open_entries(con: sqlite3.Connection) -> list:
    return [r[0] for r in con.execute("SELECT id FROM journal_entries WHERE status='open'")]
