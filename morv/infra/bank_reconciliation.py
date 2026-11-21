"""Bank and wallet reconciliation records: statement vs program balance at a date; no journal entry."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass

from morv.core.money import fmt
from morv.infra import ledger, treasury
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

ACCOUNTS = ("1210", "1220", "1230")


class ReconciliationError(Exception):
    pass


@dataclass(frozen=True)
class Reconciled:
    id: int
    number: str
    difference: int
    replayed: bool


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, "treasury.close"):
        raise ReconciliationError("لا تملك صلاحية مطابقة الحسابات البنكية")


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()


def _key(request_key: str) -> str:
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ReconciliationError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def program_balance(con: sqlite3.Connection, account: str, day: str) -> int:
    """Posted ledger balance of the account up to and including the day, debit-positive."""
    if account not in ACCOUNTS:
        raise ReconciliationError("الحساب غير صالح للمطابقة")
    return con.execute(
        "SELECT coalesce(sum(l.debit) - sum(l.credit), 0) FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id WHERE e.status = 'posted' AND l.account_code = ? AND e.entry_date <= ?",
        (account, day)).fetchone()[0]


def latest(con: sqlite3.Connection, account: str) -> tuple | None:
    """Newest live record of the account: (id, number, date, statement, program, difference, at)."""
    return con.execute(
        "SELECT id, number, statement_date, statement_balance, program_balance, difference, at "
        "FROM bank_reconciliations r WHERE kind = 'reconciliation' AND account_code = ? "
        "AND NOT EXISTS (SELECT 1 FROM bank_reconciliations x WHERE x.cancels_id = r.id) "
        "ORDER BY statement_date DESC, id DESC LIMIT 1", (account,)).fetchone()


def drifted(con: sqlite3.Connection, account: str) -> bool:
    """True when the program balance at the newest record's date differs from the frozen one."""
    last = latest(con, account)
    return last is not None and program_balance(con, account, last[2]) != last[4]


def reconcile(con: sqlite3.Connection, by, *, request_key: str, account: str, statement_date: str,
              statement_balance: int, expected_program: int, reference: str | None = None,
              memo: str | None = None) -> Reconciled:
    """Saves the record; expected_program is what the user saw, so a later entry forces a re-check."""
    _require(con, by)
    key = _key(request_key)
    if account not in ACCOUNTS:
        raise ReconciliationError("الحساب غير صالح للمطابقة")
    if type(statement_balance) is not int:
        raise ReconciliationError("رصيد الكشف عدد صحيح بالقرش")
    try:
        day = ledger.valid_date(statement_date, "تاريخ الرصيد")
    except ledger.LedgerError as e:
        raise ReconciliationError(str(e)) from e
    reference = (reference or "").strip() or None
    memo = (memo or "").strip() or None
    with transaction(con):
        h = _digest("reconcile", account, day, statement_balance, expected_program, reference, memo)
        old = con.execute("SELECT id, number, difference, payload_hash FROM bank_reconciliations "
                          "WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ReconciliationError("مفتاح الطلب سبق استخدامه لمحضر بمحتوى مختلف")
            return Reconciled(old[0], old[1], old[2], True)
        book = program_balance(con, account, day)
        if book != expected_program:
            raise ReconciliationError("تغيّر رصيد البرنامج في هذا التاريخ؛ أعد فتح المحضر")
        difference = statement_balance - book
        if difference and not memo:
            raise ReconciliationError("سبب الفرق مطلوب")
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM bank_reconciliations").fetchone()[0]
        number = f"BR-{day[:4]}-{new_id:06d}"
        try:
            con.execute(
                "INSERT INTO bank_reconciliations (id, number, request_key, payload_hash, kind, account_code, "
                "statement_date, statement_balance, program_balance, difference, reference, memo, user_id) "
                "VALUES (?,?,?,?,'reconciliation',?,?,?,?,?,?,?,?)",
                (new_id, number, key, h, account, day, statement_balance, book, difference, reference, memo, by.id))
        except sqlite3.IntegrityError as e:
            raise ReconciliationError("هذا الحساب مطابَق في هذا التاريخ بالفعل") from e
        audit(con, by, "bank.reconcile", "bank_reconciliation", new_id, f"{number} · فرق {difference}")
    return Reconciled(new_id, number, difference, False)


def reconcile_fee(con: sqlite3.Connection, by, *, request_key: str, account: str, statement_date: str,
                  statement_balance: int, reference: str | None = None) -> Reconciled:
    """Books a statement shortfall as a bank-fee expense, then saves a matching record."""
    _require(con, by)
    key = _key(request_key)
    old = con.execute("SELECT id, number, difference FROM bank_reconciliations WHERE request_key=?", (key,)).fetchone()
    if old:
        return Reconciled(old[0], old[1], old[2], True)
    try:
        day = ledger.valid_date(statement_date, "تاريخ الرصيد")
    except ledger.LedgerError as e:
        raise ReconciliationError(str(e)) from e
    if account not in ACCOUNTS or type(statement_balance) is not int:
        raise ReconciliationError("بيانات المطابقة غير صالحة")
    shortfall = program_balance(con, account, day) - statement_balance
    if shortfall <= 0:
        raise ReconciliationError("الكشف ليس أقل من البرنامج؛ لا يوجد مصروف يُسجَّل")
    try:
        treasury.post_movement(con, by, request_key=f"{key}-fee", kind="expense", amount=shortfall,
                               memo="رسوم بنكية حسب كشف الحساب", money_account=account, other_account="5260",
                               movement_date=day, reference=reference, confirmed=True)
    except (treasury.TreasuryError, ledger.LedgerError) as e:
        raise ReconciliationError(str(e)) from e
    return reconcile(con, by, request_key=key, account=account, statement_date=day,
                     statement_balance=statement_balance, expected_program=program_balance(con, account, day),
                     reference=reference)


def cancel(con: sqlite3.Connection, by, *, request_key: str, reconciliation_id: int, memo: str) -> Reconciled:
    """Voids the newest live record of its account with a second row; the original stays in the log."""
    _require(con, by)
    key = _key(request_key)
    memo = (memo or "").strip()
    if not memo:
        raise ReconciliationError("سبب الإلغاء مطلوب")
    with transaction(con):
        h = _digest("cancel", reconciliation_id, memo)
        old = con.execute("SELECT id, number, difference, payload_hash FROM bank_reconciliations "
                          "WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ReconciliationError("مفتاح الطلب سبق استخدامه لإلغاء بمحتوى مختلف")
            return Reconciled(old[0], old[1], old[2], True)
        src = con.execute("SELECT number, account_code, statement_date, statement_balance, program_balance, "
                          "difference FROM bank_reconciliations WHERE id=? AND kind='reconciliation'",
                          (reconciliation_id,)).fetchone()
        last = latest(con, src[1]) if src else None
        if last is None or last[0] != reconciliation_id:
            raise ReconciliationError("يُلغى آخر محضر ساري للحساب فقط")
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM bank_reconciliations").fetchone()[0]
        number = f"BR-{src[2][:4]}-{new_id:06d}"
        con.execute(
            "INSERT INTO bank_reconciliations (id, number, request_key, payload_hash, kind, account_code, "
            "statement_date, statement_balance, program_balance, difference, memo, cancels_id, user_id) "
            "VALUES (?,?,?,?,'cancel',?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, src[1], src[2], src[3], src[4], src[5], memo, reconciliation_id, by.id))
        audit(con, by, "bank.reconcile.cancel", "bank_reconciliation", new_id, f"{number} إلغاء {src[0]}")
    return Reconciled(new_id, number, src[5], False)


def history(con: sqlite3.Connection, account: str) -> list[tuple]:
    """Every record and cancel of the account, newest first."""
    return con.execute(
        "SELECT id, number, kind, statement_date, statement_balance, program_balance, difference, memo "
        "FROM bank_reconciliations WHERE account_code = ? ORDER BY id DESC", (account,)).fetchall()




def gaps(con: sqlite3.Connection, through: str) -> list[str]:
    """Accounts with activity up to the date that are unreconciled, stale, drifted or off."""
    names = dict(con.execute("SELECT code, name FROM accounts WHERE code IN (?,?,?)", ACCOUNTS).fetchall())
    out = []
    for code in ACCOUNTS:
        last_move = con.execute(
            "SELECT max(e.entry_date) FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
            "WHERE e.status = 'posted' AND l.account_code = ? AND e.entry_date <= ?", (code, through)).fetchone()[0]
        if last_move is None:
            continue
        last = latest(con, code)
        if last is None:
            out.append(f"{names[code]}: لم تُطابَق قط وآخر حركة {last_move}")
        elif last[2] < last_move:
            out.append(f"{names[code]}: آخر مطابقة {last[2]} تسبق آخر حركة {last_move}")
        elif drifted(con, code):
            out.append(f"{names[code]}: تغيّر رصيد البرنامج بعد مطابقة {last[2]}")
        elif last[5]:
            out.append(f"{names[code]}: مطابقة {last[2]} بها فرق غير مسوّى {fmt(last[5])}")
    return out
