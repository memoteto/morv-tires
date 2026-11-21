"""Reverse the latest recognised interest month of a contract so it can be recognised again."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.infra import interest_recognition as engine
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission


class ReversalError(ValueError):
    """Interest reversal refused; nothing was written."""


@dataclass(frozen=True)
class PostedReversal:
    id: int
    recognition_id: int
    amount: int
    replayed: bool


def reversible(con: sqlite3.Connection) -> list[tuple]:
    """Latest active month per contract: (recognition id, contract number, period, amount, entry date)."""
    return con.execute(
        "SELECT r.id, c.number, r.period, r.amount, r.entry_date FROM active_interest_recognitions r "
        "JOIN installment_contracts c ON c.id = r.contract_id "
        "WHERE r.period = (SELECT max(x.period) FROM active_interest_recognitions x WHERE x.contract_id = r.contract_id) "
        "AND NOT EXISTS (SELECT 1 FROM sale_returns sr WHERE sr.sale_id = c.sale_id) "
        "AND NOT EXISTS (SELECT 1 FROM installment_return_adjustments a WHERE a.contract_id = c.id) "
        "ORDER BY c.number, r.period").fetchall()


def reverse(con: sqlite3.Connection, by, *, request_key: str, recognition_id: int, reason: str) -> PostedReversal:
    """Reverses the latest active month of a contract with an entry dated today; it can be posted again."""
    if by is None or not has_permission(con, by, "installments.manage"):
        raise ReversalError("لا تملك صلاحية الاعتراف بفوائد التقسيط")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ReversalError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ReversalError("سبب العكس مطلوب (3 أحرف على الأقل)")
    digest = hashlib.sha256(json.dumps([recognition_id, reason], ensure_ascii=False).encode()).hexdigest()
    with transaction(con):
        old = con.execute("SELECT id, recognition_id, payload_hash FROM interest_recognition_reversals "
                          "WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[2] != digest:
                raise ReversalError("مفتاح الطلب سبق استخدامه لعكس بمحتوى مختلف")
            amount = con.execute("SELECT amount FROM interest_recognitions WHERE id=?", (old[1],)).fetchone()[0]
            return PostedReversal(old[0], old[1], amount, True)
        rec = con.execute("SELECT r.id, r.contract_id, r.period, r.amount, r.entry_date, c.number, c.sale_id "
                          "FROM interest_recognitions r JOIN installment_contracts c ON c.id = r.contract_id "
                          "WHERE r.id=?", (recognition_id,)).fetchone()
        if rec is None:
            raise ReversalError("الاعتراف بالفائدة غير موجود")
        if con.execute("SELECT 1 FROM interest_recognition_reversals WHERE recognition_id=?",
                       (recognition_id,)).fetchone():
            raise ReversalError("الاعتراف معكوس سابقًا")
        if con.execute("SELECT 1 FROM sale_returns WHERE sale_id=? LIMIT 1", (rec[6],)).fetchone() or con.execute(
                "SELECT 1 FROM installment_return_adjustments WHERE contract_id=? LIMIT 1", (rec[1],)).fetchone():
            raise ReversalError("عكس الاعتراف موقوف لعقد له مرتجع أو تسوية مديونية")
        later = con.execute("SELECT period FROM active_interest_recognitions WHERE contract_id=? AND period>? "
                            "ORDER BY period LIMIT 1", (rec[1], rec[2])).fetchone()
        if later:
            raise ReversalError(f"اعكس شهر {later[0]} أولًا؛ يُعكس أحدث شهر معترف به فقط")
        today = date.today().isoformat()
        try:
            day = ledger.valid_date(today, "تاريخ العكس")
        except ledger.LedgerError as e:
            raise ReversalError(str(e)) from e
        rev_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM interest_recognition_reversals").fetchone()[0]
        try:
            entry_id = ledger.post_entry(
                con, by, day, "interest_recognition_reversal", rev_id,
                f"عكس اعتراف فوائد {rec[2]} للعقد {rec[5]}: {reason}",
                [(engine.INCOME_ACCOUNT, rec[3], 0), (engine.DEFERRED_ACCOUNT, 0, rec[3])])
        except ledger.LedgerError as e:
            raise ReversalError(str(e)) from e
        con.execute("INSERT INTO interest_recognition_reversals (id, request_key, payload_hash, recognition_id, "
                    "reversal_date, reason, journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?)",
                    (rev_id, key, digest, recognition_id, day, reason, entry_id, by.id))
        audit(con, by, "installment.interest_reverse", "interest_recognition_reversal", rev_id,
              f"{rec[5]} · {rec[2]} · {rec[3]} · {reason}")
    return PostedReversal(rev_id, recognition_id, rec[3], False)
