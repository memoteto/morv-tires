"""Daily till closing: count vs book at a ledger line cutoff, difference posting, and cancel."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

CASH = "1110"
SHORTAGE = "5291"
OVERAGE = "2320"


class ClosingError(ValueError):
    pass


@dataclass(frozen=True)
class Closed:
    id: int
    number: str
    difference: int
    replayed: bool


def _require_close(con, by) -> None:
    if by is None or not has_permission(con, by, "treasury.close"):
        raise ClosingError("لا تملك صلاحية إقفال الصندوق")


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()


def book_snapshot(con: sqlite3.Connection, account: str = CASH) -> tuple[int, int]:
    """(cutoff line id, book balance of account): all posted so far; later lines fall in the next cycle."""
    cutoff = con.execute("SELECT coalesce(max(l.id), 0) FROM journal_lines l JOIN journal_entries e "
                         "ON e.id = l.entry_id WHERE e.status = 'posted'").fetchone()[0]
    book = con.execute("SELECT coalesce(sum(l.debit) - sum(l.credit), 0) FROM journal_lines l JOIN journal_entries e "
                       "ON e.id = l.entry_id WHERE e.status = 'posted' AND l.account_code = ? AND l.id <= ?",
                       (account, cutoff)).fetchone()[0]
    return cutoff, book


def latest_closing(con: sqlite3.Connection) -> tuple | None:
    """(id, number, closing date, actual time, difference) of the newest live closing."""
    return con.execute(
        "SELECT id, number, closing_date, at, difference FROM cash_closings c WHERE kind = 'close' "
        "AND NOT EXISTS (SELECT 1 FROM cash_closings x WHERE x.cancels_id = c.id) "
        "ORDER BY closing_date DESC, id DESC LIMIT 1").fetchone()


def last_closed_date(con: sqlite3.Connection) -> str | None:
    row = latest_closing(con)
    return row[2] if row else None


@dataclass(frozen=True)
class Coverage:
    never_closed: bool            # till activity exists but no live closing at all
    missing_days: tuple[str, ...]  # days with till activity and no live closing dated that day
    late_lines: int               # till lines dated in range but posted after the newest closing's cutoff


def coverage(con: sqlite3.Connection, through: str) -> Coverage:
    """Review state of the till up to a date; the one rule behind the period lock and the dashboard count.

    Days are checked from the first live closing on, since that closing counted all cash before it.
    """
    live = con.execute(
        "SELECT closing_date, cutoff_line_id FROM cash_closings c WHERE kind = 'close' "
        "AND NOT EXISTS (SELECT 1 FROM cash_closings x WHERE x.cancels_id = c.id)").fetchall()
    first = min((d for d, _ in live), default="")
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT e.entry_date FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
        "WHERE e.status = 'posted' AND l.account_code = ? AND e.entry_date >= ? AND e.entry_date <= ? "
        "ORDER BY e.entry_date", (CASH, first, through))]
    closed_days = {d for d, _ in live}
    late = con.execute(
        "SELECT count(*) FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
        "WHERE e.status = 'posted' AND l.account_code = ? AND e.entry_date <= ? AND e.source_type <> 'cash_closing' "
        "AND l.id > ?", (CASH, through, max((c for _, c in live), default=0))).fetchone()[0]
    return Coverage(not live and bool(days), tuple(d for d in days if d not in closed_days), late)


def unclosed_days(con: sqlite3.Connection, today: str | None = None) -> int:
    """Dates with till activity and no live closing, up to today (all of them while nothing was closed)."""
    return len(coverage(con, today or date.today().isoformat()).missing_days)


def _diff_lines(difference: int, reverse: bool = False) -> list:
    """Shortage: expense/till; overage: till/liability. A reversal swaps sides on the same two accounts."""
    amount = abs(difference)
    other = SHORTAGE if difference < 0 else OVERAGE
    lines = [(other, amount, 0), (CASH, 0, amount)] if difference < 0 else [(CASH, amount, 0), (other, 0, amount)]
    return [(acct, c, d) for acct, d, c in lines] if reverse else lines


def refuse_locked(con: sqlite3.Connection, day: str) -> None:
    locked = ledger.locked_through(con)
    if locked and day <= locked:
        raise ClosingError(f"التاريخ داخل فترة مالية مقفلة حتى {locked}")


def close_in_tx(con, by, key: str, day: str, counted: int, expected_book: int, memo: str | None) -> Closed:
    """Body of close_day; the caller owns the transaction."""
    h = _digest("close", day, counted, expected_book, memo)
    old = con.execute("SELECT id, number, difference, payload_hash FROM cash_closings WHERE request_key=?",
                      (key,)).fetchone()
    if old:
        if old[3] != h:
            raise ClosingError("مفتاح الطلب سبق استخدامه لإقفال بمحتوى مختلف")
        return Closed(old[0], old[1], old[2], True)
    last = last_closed_date(con)
    if last and day <= last:
        raise ClosingError(f"التاريخ يجب أن يلي آخر إقفال معتمد ({last})")
    refuse_locked(con, day)
    cutoff, book = book_snapshot(con)
    if book != expected_book:
        raise ClosingError("تغيّر رصيد الصندوق الدفتري أثناء العدّ؛ أعد فتح الإقفال وعُدّ من جديد")
    difference = counted - book
    if difference and not memo:
        raise ClosingError("سبب الفرق مطلوب")
    closing_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM cash_closings").fetchone()[0]
    number = f"CC-{day[:4]}-{closing_id:06d}"
    entry_id = None
    if difference:
        entry_id = ledger.post_entry(con, by, day, "cash_closing", closing_id,
                                     f"فرق إقفال الصندوق {number}: {memo}", _diff_lines(difference))
    con.execute(
        "INSERT INTO cash_closings (id, number, request_key, payload_hash, kind, closing_date, cutoff_line_id, "
        "book_balance, counted, difference, memo, journal_entry_id, user_id) VALUES (?,?,?,?,'close',?,?,?,?,?,?,?,?)",
        (closing_id, number, key, h, day, cutoff, book, counted, difference, memo, entry_id, by.id))
    audit(con, by, "cash.close", "cash_closing", closing_id, f"{number} · فرق {difference}")
    return Closed(closing_id, number, difference, False)


def prepare_close(con, by, request_key: str, closing_date: str, counted: int) -> tuple[str, str]:
    """Validates a closing request without writing; returns (request key, closing date)."""
    _require_close(con, by)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ClosingError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if type(counted) is not int or counted < 0:
        raise ClosingError("العد الفعلي عدد صحيح بالقرش ولا يقل عن صفر")
    try:
        day = ledger.valid_date(closing_date, "تاريخ الإقفال")
    except ledger.LedgerError as e:
        raise ClosingError(str(e)) from e
    if day > date.today().isoformat():
        raise ClosingError("لا يُقفل تاريخ مستقبلي")
    return key, day


def close_day(con: sqlite3.Connection, by, *, request_key: str, closing_date: str, counted: int,
              expected_book: int, memo: str | None = None) -> Closed:
    """Records the count; expected_book is what the cashier saw, so a sale in between forces a recount."""
    key, day = prepare_close(con, by, request_key, closing_date, counted)
    memo = (memo or "").strip() or None
    with transaction(con):
        return close_in_tx(con, by, key, day, counted, expected_book, memo)


def cancel_closing(con: sqlite3.Connection, by, *, request_key: str, closing_id: int, memo: str) -> Closed:
    """Voids the newest live closing with a reversing row and, if it had a difference, a reversing entry."""
    _require_close(con, by)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ClosingError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    memo = (memo or "").strip()
    if not memo:
        raise ClosingError("سبب الإلغاء مطلوب")
    with transaction(con):
        h = _digest("cancel", closing_id, memo)
        old = con.execute("SELECT id, number, difference, payload_hash FROM cash_closings WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ClosingError("مفتاح الطلب سبق استخدامه لإلغاء بمحتوى مختلف")
            return Closed(old[0], old[1], old[2], True)
        latest = latest_closing(con)
        if latest is None or latest[0] != closing_id:
            raise ClosingError("يُلغى آخر إقفال معتمد فقط")
        src = con.execute("SELECT number, closing_date, cutoff_line_id, book_balance, counted, difference, "
                          "journal_entry_id FROM cash_closings WHERE id=?", (closing_id,)).fetchone()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM cash_closings").fetchone()[0]
        number = f"CC-{src[1][:4]}-{new_id:06d}"
        entry_id = None
        if src[5]:
            entry_id = ledger.post_entry(con, by, date.today().isoformat(), "cash_closing", new_id,
                                         f"إلغاء إقفال الصندوق {src[0]} {number}: {memo}", _diff_lines(src[5], reverse=True))
        con.execute(
            "INSERT INTO cash_closings (id, number, request_key, payload_hash, kind, closing_date, cutoff_line_id, "
            "book_balance, counted, difference, memo, cancels_id, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,'cancel',?,?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, src[1], src[2], src[3], src[4], src[5], memo, closing_id, entry_id, by.id))
        audit(con, by, "cash.close.cancel", "cash_closing", new_id, f"{number} إلغاء {src[0]}")
    return Closed(new_id, number, src[5], False)
