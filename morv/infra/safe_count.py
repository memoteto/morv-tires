"""Periodic safe (1130) count: count vs book at a ledger line cutoff, difference posting, cancel."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.infra import cash_closing, ledger
from morv.infra.cash_closing import ClosingError
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

SAFE = "1130"
SHORTAGE = "5292"
OVERAGE = "2330"
ALERT_AFTER_DAYS = 7


@dataclass(frozen=True)
class Counted:
    id: int
    number: str
    difference: int
    replayed: bool


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, "treasury.close"):
        raise ClosingError("لا تملك صلاحية جرد الخزنة")


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()


def latest_count(con: sqlite3.Connection) -> tuple | None:
    """(id, number, count date, actual time, difference) of the newest live count."""
    return con.execute(
        "SELECT id, number, count_date, at, difference FROM safe_counts c WHERE kind = 'count' "
        "AND NOT EXISTS (SELECT 1 FROM safe_counts x WHERE x.cancels_id = c.id) "
        "ORDER BY count_date DESC, id DESC LIMIT 1").fetchone()


def days_since_count(con: sqlite3.Connection, today: str | None = None) -> int | None:
    """Days since the last live count, or None when the safe was never counted."""
    last = latest_count(con)
    if last is None:
        return None
    return (date.fromisoformat(today or date.today().isoformat()) - date.fromisoformat(last[2])).days


def _lines(difference: int, reverse: bool = False) -> list:
    amount = abs(difference)
    other = SHORTAGE if difference < 0 else OVERAGE
    lines = [(other, amount, 0), (SAFE, 0, amount)] if difference < 0 else [(SAFE, amount, 0), (other, 0, amount)]
    return [(acct, c, d) for acct, d, c in lines] if reverse else lines


def count_safe(con: sqlite3.Connection, by, *, request_key: str, count_date: str, counted: int,
               expected_book: int, memo: str | None = None) -> Counted:
    """Records the count; expected_book is what the counter saw, so a movement in between forces a recount."""
    _require(con, by)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ClosingError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if type(counted) is not int or counted < 0:
        raise ClosingError("العد الفعلي عدد صحيح بالقرش ولا يقل عن صفر")
    try:
        day = ledger.valid_date(count_date, "تاريخ الجرد")
    except ledger.LedgerError as e:
        raise ClosingError(str(e)) from e
    memo = (memo or "").strip() or None
    with transaction(con):
        h = _digest("count", day, counted, expected_book, memo)
        old = con.execute("SELECT id, number, difference, payload_hash FROM safe_counts WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ClosingError("مفتاح الطلب سبق استخدامه لجرد بمحتوى مختلف")
            return Counted(old[0], old[1], old[2], True)
        last = latest_count(con)
        if last and day <= last[2]:
            raise ClosingError(f"التاريخ يجب أن يلي آخر جرد معتمد ({last[2]})")
        cutoff, book = cash_closing.book_snapshot(con, SAFE)
        if book != expected_book:
            raise ClosingError("تغيّر رصيد الخزنة الدفتري أثناء العدّ؛ أعد فتح الجرد وعُدّ من جديد")
        difference = counted - book
        if difference and not memo:
            raise ClosingError("سبب الفرق مطلوب")
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM safe_counts").fetchone()[0]
        number = f"SC-{day[:4]}-{new_id:06d}"
        entry_id = None
        if difference:
            entry_id = ledger.post_entry(con, by, day, "safe_count", new_id,
                                         f"فرق جرد الخزنة {number}: {memo}", _lines(difference))
        con.execute(
            "INSERT INTO safe_counts (id, number, request_key, payload_hash, kind, count_date, cutoff_line_id, "
            "book_balance, counted, difference, memo, journal_entry_id, user_id) VALUES (?,?,?,?,'count',?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, day, cutoff, book, counted, difference, memo, entry_id, by.id))
        audit(con, by, "safe.count", "safe_count", new_id, f"{number} · فرق {difference}")
    return Counted(new_id, number, difference, False)


def cancel_count(con: sqlite3.Connection, by, *, request_key: str, count_id: int, memo: str) -> Counted:
    """Voids the newest live count with a reversing row and, if it had a difference, a reversing entry."""
    _require(con, by)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ClosingError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    memo = (memo or "").strip()
    if not memo:
        raise ClosingError("سبب الإلغاء مطلوب")
    with transaction(con):
        h = _digest("cancel", count_id, memo)
        old = con.execute("SELECT id, number, difference, payload_hash FROM safe_counts WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ClosingError("مفتاح الطلب سبق استخدامه لإلغاء بمحتوى مختلف")
            return Counted(old[0], old[1], old[2], True)
        latest = latest_count(con)
        if latest is None or latest[0] != count_id:
            raise ClosingError("يُلغى آخر جرد معتمد فقط")
        src = con.execute("SELECT number, count_date, cutoff_line_id, book_balance, counted, difference "
                          "FROM safe_counts WHERE id=?", (count_id,)).fetchone()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM safe_counts").fetchone()[0]
        number = f"SC-{src[1][:4]}-{new_id:06d}"
        entry_id = None
        if src[5]:
            entry_id = ledger.post_entry(con, by, date.today().isoformat(), "safe_count", new_id,
                                         f"إلغاء جرد الخزنة {src[0]} {number}: {memo}", _lines(src[5], reverse=True))
        con.execute(
            "INSERT INTO safe_counts (id, number, request_key, payload_hash, kind, count_date, cutoff_line_id, "
            "book_balance, counted, difference, memo, cancels_id, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,'cancel',?,?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, src[1], src[2], src[3], src[4], src[5], memo, count_id, entry_id, by.id))
        audit(con, by, "safe.count.cancel", "safe_count", new_id, f"{number} إلغاء {src[0]}")
    return Counted(new_id, number, src[5], False)
