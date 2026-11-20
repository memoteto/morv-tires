"""Financial period lock: freezes ledger dates up to a day, with immutable lock and unlock documents."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core.money import fmt
from morv.infra import cash_closing, interest_review, ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission


class LockError(ValueError):
    pass


@dataclass(frozen=True)
class Locked:
    id: int
    number: str
    through_date: str
    replayed: bool


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, "period.lock"):
        raise LockError("لا تملك صلاحية قفل الفترة المالية")


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()


def latest_lock(con: sqlite3.Connection) -> tuple | None:
    """(id, number, through date, actual time) of the live lock with the latest date."""
    return con.execute(
        "SELECT id, number, through_date, at FROM period_locks l WHERE kind = 'lock' "
        "AND NOT EXISTS (SELECT 1 FROM period_locks x WHERE x.cancels_id = l.id) "
        "ORDER BY through_date DESC, id DESC LIMIT 1").fetchone()


def _till_blockers(con: sqlite3.Connection, through: str) -> list[str]:
    cov = cash_closing.coverage(con, through)
    if cov.never_closed:
        return ["يوجد في الصندوق حركات ولم يُقفل الصندوق قط؛ أقفل الصندوق أولًا"]
    out = []
    if cov.missing_days:
        shown = "، ".join(cov.missing_days[:5]) + (" ..." if len(cov.missing_days) > 5 else "")
        out.append(f"{len(cov.missing_days)} يوم بحركة صندوق بلا إقفال: {shown}")
    if cov.late_lines:
        out.append(f"{cov.late_lines} حركة صندوق مؤرخة داخل الفترة رُحِّلت بعد آخر إقفال؛ "
                   "أقفل الصندوق مرة أخرى ثم أعد المحاولة")
    return out


def _interest_blockers(con: sqlite3.Connection, through: str) -> list[str]:
    items = interest_review.review(con, through)
    out = []

    def lines(found, template):
        shown = [template(i) for i in found[:5]]
        return shown + ([f"... و{len(found) - 5} غيرها"] if len(found) > 5 else [])

    ready = [i for i in items if i.status == interest_review.READY]
    if ready:
        total = sum(i.amount for i in ready)
        out.append(f"فوائد تقسيط مستحقة لم يُعترف بها: {len(ready)} شهرًا بمجموع {fmt(total)}؛ اعترف بها من شاشة التقسيط")
        out += lines(ready, lambda i: f"   • عقد {i.number} شهر {i.period}: {fmt(i.amount)}")
    blocked = [i for i in items if i.status == interest_review.BLOCKED]
    if blocked:
        out.append(f"{len(blocked)} عقد خارج نطاق الاعتراف الآلي ولا يُقفل ما دام على حاله:")
        out += lines(blocked, lambda i: f"   • عقد {i.number} شهر {i.period}"
                     + (f" ({fmt(i.amount)})" if i.amount else "") + f": {i.reason}")
    late = [i for i in items if i.status == interest_review.MISDATED]
    if late:
        out.append("فوائد مُعترف بها بتاريخ بعد نهاية الفترة، فإيرادها خارجها:")
        out += lines(late, lambda i: f"   • عقد {i.number} شهر {i.period}: {fmt(i.amount)} {i.reason}")
    return out


def blockers(con: sqlite3.Connection, through: str) -> list[str]:
    """Reasons a lock through that date would strand work; empty when it is safe."""
    out = _till_blockers(con, through) + _interest_blockers(con, through)
    opened = con.execute("SELECT count(*) FROM journal_entries WHERE status <> 'posted' AND entry_date <= ?",
                         (through,)).fetchone()[0]
    if opened:
        out.append(f"يوجد {opened} قيد غير مرحّل بتاريخ داخل الفترة")
    return out


def lock_period(con: sqlite3.Connection, by, *, request_key: str, through_date: str,
                memo: str | None = None) -> Locked:
    _require(con, by)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise LockError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    try:
        day = ledger.valid_date(through_date, "تاريخ القفل")
    except ledger.LedgerError as e:
        raise LockError(str(e)) from e
    if day >= date.today().isoformat():
        raise LockError("لا تُقفل الفترة إلا حتى يوم سابق لليوم")
    memo = (memo or "").strip() or None
    with transaction(con):
        h = _digest("lock", day, memo)
        old = con.execute("SELECT id, number, through_date, payload_hash FROM period_locks WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise LockError("مفتاح الطلب سبق استخدامه لقفل بمحتوى مختلف")
            return Locked(old[0], old[1], old[2], True)
        current = ledger.locked_through(con)
        if current and day <= current:
            raise LockError(f"الفترة مقفلة بالفعل حتى {current}")
        found = blockers(con, day)
        if found:
            raise LockError("لا يمكن القفل: " + "؛ ".join(found))
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM period_locks").fetchone()[0]
        number = f"PL-{day[:4]}-{new_id:06d}"
        con.execute("INSERT INTO period_locks (id, number, request_key, payload_hash, kind, through_date, memo, "
                    "user_id) VALUES (?,?,?,?,'lock',?,?,?)", (new_id, number, key, h, day, memo, by.id))
        audit(con, by, "period.lock", "period_lock", new_id, f"{number} · حتى {day}")
    return Locked(new_id, number, day, False)


def unlock_period(con: sqlite3.Connection, by, *, request_key: str, lock_id: int, memo: str) -> Locked:
    """Voids the newest live lock only, with a mandatory reason; earlier locks stay in force."""
    _require(con, by)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise LockError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    memo = (memo or "").strip()
    if not memo:
        raise LockError("سبب فتح الفترة مطلوب")
    with transaction(con):
        h = _digest("unlock", lock_id, memo)
        old = con.execute("SELECT id, number, through_date, payload_hash FROM period_locks WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise LockError("مفتاح الطلب سبق استخدامه لفتح بمحتوى مختلف")
            return Locked(old[0], old[1], old[2], True)
        latest = latest_lock(con)
        if latest is None or latest[0] != lock_id:
            raise LockError("يُفتح آخر قفل ساري فقط")
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM period_locks").fetchone()[0]
        number = f"PL-{latest[2][:4]}-{new_id:06d}"
        con.execute("INSERT INTO period_locks (id, number, request_key, payload_hash, kind, through_date, "
                    "cancels_id, memo, user_id) VALUES (?,?,?,?,'unlock',?,?,?,?)",
                    (new_id, number, key, h, latest[2], lock_id, memo, by.id))
        audit(con, by, "period.unlock", "period_lock", new_id, f"{number} فتح {latest[1]}")
    return Locked(new_id, number, latest[2], False)
