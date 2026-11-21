"""Customer receipt vouchers: collect credit-sale debts and reverse a receipt with a mirrored document."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import ledger
from morv.infra.customer_statement import CUSTOMER_ACCOUNT
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

METHODS = ("cash", "card", "transfer", "wallet")
PERMISSION = "receipts.post"
# Old returns have credit_part 0 and refund_method 'credit' meaning the whole total
CREDIT_PART = "CASE WHEN r.refund_method = 'credit' THEN r.total ELSE r.credit_part END"
_MIXED = ("EXISTS (SELECT 1 FROM sale_payments p WHERE p.sale_id = s.id AND p.method = 'credit') AND NOT EXISTS "
          "(SELECT 1 FROM sale_payments p WHERE p.sale_id = s.id AND p.method = 'installment')")


class ReceiptError(Exception):
    pass


@dataclass(frozen=True)
class PostedReceipt:
    id: int
    number: str
    amount: int
    replayed: bool


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, PERMISSION):
        raise ReceiptError("لا تملك صلاحية سندات القبض")


def _key(request_key: str) -> str:
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ReceiptError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def customer_balance(con: sqlite3.Connection, customer_id: int) -> int:
    """Debt owed by the customer: opening balance plus credit sales, less refunds and live receipts."""
    sold = con.execute("SELECT coalesce(sum(p.amount), 0) FROM sale_payments p JOIN sales s ON s.id = p.sale_id "
                       "WHERE s.customer_id=? AND p.method='credit'", (customer_id,)).fetchone()[0]
    refunded = con.execute(f"SELECT coalesce(sum({CREDIT_PART}), 0) FROM sale_returns r JOIN sales s "
                           "ON s.id = r.sale_id WHERE s.customer_id=?", (customer_id,)).fetchone()[0]
    opening = con.execute("SELECT coalesce(sum(amount), 0) FROM active_opening_lines WHERE customer_id=?",
                          (customer_id,)).fetchone()[0]
    return opening + sold - refunded - received(con, customer_id) - applied_deposits(con, customer_id)


_DEPOSIT_JOIN = "FROM customer_deposits d LEFT JOIN customer_deposits o ON o.id = d.reverses_id WHERE d.customer_id=?"


def deposit_balance(con: sqlite3.Connection, customer_id: int) -> int:
    """Customer deposit still held: deposits minus refunds and applications, reversals undone."""
    return con.execute(
        "SELECT coalesce(sum(CASE d.kind WHEN 'deposit' THEN d.amount ELSE CASE WHEN d.kind = 'reversal' "
        "THEN CASE o.kind WHEN 'deposit' THEN -d.amount ELSE d.amount END ELSE -d.amount END END), 0) "
        + _DEPOSIT_JOIN, (customer_id,)).fetchone()[0]


def applied_deposits(con: sqlite3.Connection, customer_id: int) -> int:
    """Deposit money applied against credit debt, net of reversals."""
    return con.execute(
        "SELECT coalesce(sum(CASE WHEN d.kind = 'apply' THEN d.amount WHEN d.kind = 'reversal' AND o.kind = 'apply' "
        "THEN -d.amount ELSE 0 END), 0) " + _DEPOSIT_JOIN, (customer_id,)).fetchone()[0]


def received(con: sqlite3.Connection, customer_id: int) -> int:
    """Net receipts: receipts minus their reversals."""
    return con.execute("SELECT coalesce(sum(CASE kind WHEN 'receipt' THEN amount ELSE -amount END), 0) "
                       "FROM customer_receipts WHERE customer_id=?", (customer_id,)).fetchone()[0]


def _money_paid_and_refunded(con: sqlite3.Connection, sale_id: int) -> tuple[int, int]:
    paid = con.execute("SELECT coalesce(sum(amount), 0) FROM sale_payments WHERE sale_id=? AND method <> 'credit'",
                       (sale_id,)).fetchone()[0]
    refunded = con.execute(f"SELECT coalesce(sum(r.total - ({CREDIT_PART})), 0) FROM sale_returns r "
                           "WHERE r.sale_id=?", (sale_id,)).fetchone()[0]
    return paid, refunded


def sale_money_room(con: sqlite3.Connection, sale_id: int) -> int:
    """Money paid on the invoice that no earlier return has refunded yet."""
    paid, refunded = _money_paid_and_refunded(con, sale_id)
    return max(0, paid - refunded)


def cash_refunded(con: sqlite3.Connection, customer_id: int) -> int:
    """Money refunded beyond what the invoices were paid in money: it consumed collected receipts."""
    used = 0
    for (sale_id,) in con.execute(f"SELECT s.id FROM sales s WHERE s.customer_id=? AND {_MIXED}", (customer_id,)).fetchall():
        paid, refunded = _money_paid_and_refunded(con, sale_id)
        used += max(0, refunded - paid)
    return used


def collected_unrefunded(con: sqlite3.Connection, customer_id: int, day: str) -> int:
    """Receipts up to the day, net of reversals, that no money refund has used yet."""
    net = con.execute("SELECT coalesce(sum(CASE kind WHEN 'receipt' THEN amount ELSE -amount END), 0) "
                      "FROM customer_receipts WHERE customer_id=? AND receipt_date <= ?",
                      (customer_id, day)).fetchone()[0]
    applied = con.execute(
        "SELECT coalesce(sum(CASE WHEN d.kind = 'apply' THEN d.amount WHEN d.kind = 'reversal' AND o.kind = 'apply' "
        "THEN -d.amount ELSE 0 END), 0) " + _DEPOSIT_JOIN + " AND d.doc_date <= ?", (customer_id, day)).fetchone()[0]
    return net + applied - cash_refunded(con, customer_id)


def post_receipt(con: sqlite3.Connection, by, *, request_key: str, customer_id: int, amount: int, method: str,
                 receipt_date: str | None = None, notes: str | None = None) -> PostedReceipt:
    """Collects part or all of a customer's credit debt; overpaying the debt is refused."""
    _require(con, by)
    key = _key(request_key)
    if type(amount) is not int or amount <= 0:
        raise ReceiptError("مبلغ القبض يجب أن يكون عددًا صحيحًا أكبر من صفر")
    if method not in METHODS:
        raise ReceiptError("طريقة القبض غير صالحة")
    try:
        day = ledger.valid_date(receipt_date or date.today().isoformat(), "تاريخ القبض")
    except ledger.LedgerError as e:
        raise ReceiptError(str(e)) from e
    notes = (notes or "").strip() or None

    with transaction(con):
        h = _digest("receipt", customer_id, day, amount, method, notes)
        old = con.execute("SELECT id, number, amount, payload_hash FROM customer_receipts WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ReceiptError("مفتاح الطلب سبق استخدامه لسند بمحتوى مختلف")
            return PostedReceipt(old[0], old[1], old[2], True)
        if not con.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
            raise ReceiptError("العميل غير موجود")
        if day < (con.execute("SELECT min(sale_date) FROM sales WHERE customer_id=?", (customer_id,)).fetchone()[0]
                  or day):
            raise ReceiptError("تاريخ القبض قبل أول فاتورة للعميل")
        owed = customer_balance(con, customer_id)
        if owed <= 0:
            raise ReceiptError("لا يوجد دين آجل على هذا العميل")
        if amount > owed:
            raise ReceiptError("مبلغ القبض أكبر من دين العميل")
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM customer_receipts").fetchone()[0]
        number = f"CR-{day[:4]}-{new_id:06d}"
        try:
            entry_id = ledger.post_entry(con, by, day, "customer_receipt", new_id, f"سند قبض {number}",
                                         [(PAYMENT_ACCOUNT[method], amount, 0), (CUSTOMER_ACCOUNT, 0, amount)])
        except ledger.LedgerError as e:
            raise ReceiptError(str(e)) from e
        con.execute(
            "INSERT INTO customer_receipts (id, number, request_key, payload_hash, customer_id, kind, receipt_date, "
            "method, amount, notes, journal_entry_id, user_id) VALUES (?,?,?,?,?,'receipt',?,?,?,?,?,?)",
            (new_id, number, key, h, customer_id, day, method, amount, notes, entry_id, by.id))
        audit(con, by, "receipt.post", "customer_receipt", new_id, f"{number} · {amount}")
    return PostedReceipt(new_id, number, amount, False)


def reverse_receipt(con: sqlite3.Connection, by, *, request_key: str, receipt_id: int, reason: str) -> PostedReceipt:
    """Reverses one receipt once, with a mirrored entry dated today."""
    _require(con, by)
    key = _key(request_key)
    reason = (reason or "").strip()
    if not reason:
        raise ReceiptError("سبب العكس مطلوب")
    with transaction(con):
        h = _digest("reversal", receipt_id, reason)
        old = con.execute("SELECT id, number, amount, payload_hash FROM customer_receipts WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ReceiptError("مفتاح الطلب سبق استخدامه لعكس مختلف")
            return PostedReceipt(old[0], old[1], old[2], True)
        src = con.execute("SELECT number, customer_id, method, amount, kind FROM customer_receipts WHERE id=?",
                          (receipt_id,)).fetchone()
        if not src or src[4] != "receipt":
            raise ReceiptError("سند القبض غير موجود")
        if con.execute("SELECT 1 FROM customer_receipts WHERE reverses_id=?", (receipt_id,)).fetchone():
            raise ReceiptError("سند القبض معكوس سابقًا")
        if received(con, src[1]) + applied_deposits(con, src[1]) - src[3] < cash_refunded(con, src[1]):
            raise ReceiptError("لا يُعكس السند: رُدّ للعميل نقدًا مبلغ يستند إلى هذا التحصيل")
        today = date.today().isoformat()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM customer_receipts").fetchone()[0]
        number = f"CR-{today[:4]}-{new_id:06d}"
        try:
            entry_id = ledger.post_entry(con, by, today, "customer_receipt", new_id,
                                         f"عكس سند القبض {src[0]} {number}: {reason}",
                                         [(CUSTOMER_ACCOUNT, src[3], 0), (PAYMENT_ACCOUNT[src[2]], 0, src[3])])
        except ledger.LedgerError as e:
            raise ReceiptError(str(e)) from e
        con.execute(
            "INSERT INTO customer_receipts (id, number, request_key, payload_hash, customer_id, kind, receipt_date, "
            "method, amount, notes, reverses_id, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,?,'reversal',?,?,?,?,?,?,?)",
            (new_id, number, key, h, src[1], today, src[2], src[3], reason, receipt_id, entry_id, by.id))
        audit(con, by, "receipt.reverse", "customer_receipt", new_id, f"{number} عكس {src[0]}")
    return PostedReceipt(new_id, number, src[3], False)


def history(con: sqlite3.Connection, customer_id: int) -> list[tuple]:
    """Receipts and reversals of one customer, newest first, with a flag for receipts already reversed."""
    return con.execute("SELECT r.id, r.number, r.kind, r.receipt_date, r.method, r.amount, r.notes, "
                       "EXISTS (SELECT 1 FROM customer_receipts x WHERE x.reverses_id = r.id) "
                       "FROM customer_receipts r WHERE r.customer_id=? ORDER BY r.id DESC", (customer_id,)).fetchall()
