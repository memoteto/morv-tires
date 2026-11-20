"""Read-only treasury view: balances and movements of the cash, bank and wallet accounts."""
import sqlite3
from dataclasses import dataclass

ACCOUNTS = ("1110", "1210", "1220", "1230")
_IN = "(" + ",".join(f"'{a}'" for a in ACCOUNTS) + ")"


@dataclass(frozen=True)
class EntryView:
    entry_date: str
    source_type: str
    source_id: int
    memo: str
    posted_at: str
    user_name: str
    lines: tuple      # (account_code, account_name, debit, credit)


def balances(con: sqlite3.Connection) -> tuple[tuple[str, int], ...]:
    found = dict(con.execute(
        "SELECT l.account_code, sum(l.debit) - sum(l.credit) FROM journal_lines l "
        "JOIN journal_entries e ON e.id = l.entry_id "
        f"WHERE e.status = 'posted' AND l.account_code IN {_IN} GROUP BY l.account_code").fetchall())
    return tuple((code, found.get(code, 0)) for code in ACCOUNTS)


def _where(account: str | None) -> tuple[str, tuple]:
    if account in ACCOUNTS:
        return "l.account_code = ?", (account,)
    return f"l.account_code IN {_IN}", ()


def movements(con: sqlite3.Connection, account: str | None = None, query: str = "",
              limit: int = 300, offset: int = 0) -> list[tuple]:
    """Newest posting first. Without an account the table is walked newest-first and stops at the limit;
    the account index would force sorting every cash line (measured 99ms vs 0.5ms on 200k lines)."""
    where, args = _where(account)
    index_hint = "" if account in ACCOUNTS else "NOT INDEXED"
    text = (query or "").strip()
    if text:
        where += " AND (instr(e.memo, ?) > 0 OR instr(e.entry_date, ?) > 0)"
        args += (text, text)
    return con.execute(
        "SELECT l.id, e.entry_date, l.account_code, coalesce(e.memo, ''), e.source_type, l.debit, l.credit "
        f"FROM journal_lines l {index_hint} JOIN journal_entries e ON e.id = l.entry_id "
        f"WHERE e.status = 'posted' AND {where} ORDER BY l.id DESC LIMIT ? OFFSET ?",
        args + (limit, offset)).fetchall()


def account_names(con: sqlite3.Connection, codes) -> dict[str, str]:
    marks = ",".join("?" * len(codes))
    return dict(con.execute(f"SELECT code, name FROM accounts WHERE code IN ({marks})", tuple(codes)).fetchall())


def count(con: sqlite3.Connection) -> int:
    return con.execute(f"SELECT count(*) FROM journal_lines l WHERE l.account_code IN {_IN}").fetchone()[0]


def get_entry(con: sqlite3.Connection, line_id: int) -> EntryView:
    head = con.execute(
        "SELECT e.id, e.entry_date, e.source_type, e.source_id, coalesce(e.memo, ''), e.posted_at, coalesce(u.display_name, '') "
        "FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
        "LEFT JOIN users u ON u.id = e.user_id WHERE l.id = ?", (line_id,)).fetchone()
    if head is None:
        raise LookupError(f"سطر قيد غير موجود: {line_id}")
    lines = con.execute(
        "SELECT l.account_code, a.name, l.debit, l.credit FROM journal_lines l "
        "JOIN accounts a ON a.code = l.account_code WHERE l.entry_id = ? ORDER BY l.line_no", (head[0],)).fetchall()
    return EntryView(head[1], head[2], head[3], head[4], head[5], head[6], tuple(lines))
