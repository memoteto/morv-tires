"""Customer deposits: take, refund or apply against credit debt, with mirrored reversal documents."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import customer_receipts, ledger
from morv.infra.customer_receipts import METHODS, PERMISSION
from morv.infra.customer_statement import CUSTOMER_ACCOUNT
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

DEPOSIT_ACCOUNT = "2340"
LABELS = {"deposit": "إيداع عربون", "refund": "رد عربون", "apply": "تسوية على دين", "reversal": "عكس"}


class DepositError(Exception):
    pass


@dataclass(frozen=True)
class PostedDeposit:
    id: int
    number: str
    amount: int
    replayed: bool


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, PERMISSION):
        raise DepositError("لا تملك صلاحية سندات العملاء")


def _key(request_key: str) -> str:
    key = (request_key or "").strip()
    if len(key) < 8:
        raise DepositError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def _lines(kind: str, method: str | None, amount: int, mirrored: bool = False) -> list:
    if kind == "deposit":
        lines = [(PAYMENT_ACCOUNT[method], amount, 0), (DEPOSIT_ACCOUNT, 0, amount)]
    elif kind == "refund":
        lines = [(DEPOSIT_ACCOUNT, amount, 0), (PAYMENT_ACCOUNT[method], 0, amount)]
    else:
        lines = [(DEPOSIT_ACCOUNT, amount, 0), (CUSTOMER_ACCOUNT, 0, amount)]
    return [(acc, c, d) for acc, d, c in lines] if mirrored else lines


def _post(con, by, *, key, digest, customer_id, kind, amount, method, doc_date, notes, reverses=None,
          memo="") -> PostedDeposit:
    """Shared body of every document; the caller has validated its own rules inside the same transaction."""
    new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM customer_deposits").fetchone()[0]
    number = f"CD-{doc_date[:4]}-{new_id:06d}"
    source = con.execute("SELECT kind FROM customer_deposits WHERE id=?", (reverses,)).fetchone()[0] if reverses else kind
    try:
        entry_id = ledger.post_entry(con, by, doc_date, "customer_deposit", new_id, f"{LABELS[kind]} {number}{memo}",
                                     _lines(source, method, amount, mirrored=bool(reverses)))
    except ledger.LedgerError as e:
        raise DepositError(str(e)) from e
    con.execute(
        "INSERT INTO customer_deposits (id, number, request_key, payload_hash, customer_id, kind, doc_date, method, "
        "amount, notes, reverses_id, journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (new_id, number, key, digest, customer_id, kind, doc_date, method, amount, notes,
         reverses, entry_id, by.id))
    audit(con, by, f"deposit.{kind}", "customer_deposit", new_id, f"{number} · {amount}")
    return PostedDeposit(new_id, number, amount, False)


def _replay(con, key: str, digest: str) -> PostedDeposit | None:
    old = con.execute("SELECT id, number, amount, payload_hash FROM customer_deposits WHERE request_key=?",
                      (key,)).fetchone()
    if old is None:
        return None
    if old[3] != digest:
        raise DepositError("مفتاح الطلب سبق استخدامه لمستند بمحتوى مختلف")
    return PostedDeposit(old[0], old[1], old[2], True)


def _check_backing(con, customer_id: int, held_after: int) -> None:
    """A deposit that backs credit cannot leave while the debt exceeds the limit plus what stays held."""
    limit = con.execute("SELECT credit_limit FROM customers WHERE id=?", (customer_id,)).fetchone()[0]
    if customer_receipts.customer_balance(con, customer_id) > limit + max(0, held_after):
        raise DepositError("العربون يغطي دينًا آجلًا يتجاوز حد العميل؛ سوِّ الدين أولًا")


def post_document(con: sqlite3.Connection, by, *, request_key: str, customer_id: int, kind: str, amount: int,
                  method: str | None = None, doc_date: str | None = None, notes: str | None = None) -> PostedDeposit:
    """Takes, refunds or applies a deposit; refunds and applications never exceed the balance."""
    _require(con, by)
    key = _key(request_key)
    if kind not in ("deposit", "refund", "apply"):
        raise DepositError("نوع المستند غير صالح")
    if type(amount) is not int or amount <= 0:
        raise DepositError("المبلغ يجب أن يكون عددًا صحيحًا أكبر من صفر")
    if kind == "apply":
        method = None
    elif method not in METHODS:
        raise DepositError("طريقة الدفع غير صالحة")
    try:
        day = ledger.valid_date(doc_date or date.today().isoformat(), "تاريخ المستند")
    except ledger.LedgerError as e:
        raise DepositError(str(e)) from e
    notes = (notes or "").strip() or None
    with transaction(con):
        digest = _digest(kind, customer_id, day, amount, method, notes)
        replay = _replay(con, key, digest)
        if replay:
            return replay
        if not con.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
            raise DepositError("العميل غير موجود")
        held = customer_receipts.deposit_balance(con, customer_id)
        if kind in ("refund", "apply") and amount > held:
            raise DepositError("المبلغ أكبر من عربون العميل المتاح")
        if kind == "apply" and amount > customer_receipts.customer_balance(con, customer_id):
            raise DepositError("المبلغ أكبر من دين العميل الآجل المتبقي")
        if kind == "refund":
            _check_backing(con, customer_id, held - amount)
        return _post(con, by, key=key, digest=digest, customer_id=customer_id, kind=kind, amount=amount,
                     method=method, doc_date=day, notes=notes)


def reverse_document(con: sqlite3.Connection, by, *, request_key: str, document_id: int, reason: str) -> PostedDeposit:
    """Reverses one document once, dated today; a reversal that breaks a balance is refused."""
    _require(con, by)
    key = _key(request_key)
    reason = (reason or "").strip()
    if not reason:
        raise DepositError("سبب العكس مطلوب")
    with transaction(con):
        digest = _digest("reversal", document_id, reason)
        replay = _replay(con, key, digest)
        if replay:
            return replay
        src = con.execute("SELECT number, customer_id, kind, method, amount FROM customer_deposits WHERE id=?",
                          (document_id,)).fetchone()
        if not src or src[2] == "reversal":
            raise DepositError("المستند غير موجود")
        if con.execute("SELECT 1 FROM customer_deposits WHERE reverses_id=?", (document_id,)).fetchone():
            raise DepositError("المستند معكوس سابقًا")
        cid, kind, amount = src[1], src[2], src[4]
        if kind == "deposit":
            if customer_receipts.deposit_balance(con, cid) < amount:
                raise DepositError("لا يُعكس الإيداع: صُرف جزء من العربون")
            _check_backing(con, cid, customer_receipts.deposit_balance(con, cid) - amount)
        if kind == "apply":
            collected = (customer_receipts.received(con, cid) + customer_receipts.applied_deposits(con, cid) - amount)
            if collected < customer_receipts.cash_refunded(con, cid):
                raise DepositError("لا تُعكس التسوية: رُدّ للعميل نقدًا مبلغ يستند إلى هذا التحصيل")
        return _post(con, by, key=key, digest=digest, customer_id=cid, kind="reversal", amount=amount,
                     method=src[3], doc_date=date.today().isoformat(), notes=reason, reverses=document_id,
                     memo=f" عكس {src[0]}: {reason}")


def history(con: sqlite3.Connection, customer_id: int) -> list[tuple]:
    """Documents of one customer, newest first: (id, number, kind, date, method, amount, notes, reversed)."""
    return con.execute(
        "SELECT d.id, d.number, d.kind, d.doc_date, d.method, d.amount, d.notes, "
        "EXISTS (SELECT 1 FROM customer_deposits x WHERE x.reverses_id = d.id) FROM customer_deposits d "
        "WHERE d.customer_id=? ORDER BY d.id DESC", (customer_id,)).fetchall()
