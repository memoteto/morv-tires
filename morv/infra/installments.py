"""Instalment contract: collects the down payment and books the markup against deferred interest."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import installments as calc
from morv.core import validate
from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

RECEIVABLE_ACCOUNT = "1320"
DEFERRED_INTEREST_ACCOUNT = "2410"
DOWN_METHODS = ("cash", "card", "transfer", "wallet")


class ContractError(ValueError):
    """Contract refused, with a message fit to show."""


@dataclass(frozen=True)
class Guarantor:
    name: str = ""
    mobile: str = ""
    national_id: str = ""
    relation: str = ""


@dataclass(frozen=True)
class PostedContract:
    id: int
    number: str
    months: int
    monthly: int
    contract_total: int
    replayed: bool


@dataclass(frozen=True)
class PostedPayment:
    id: int
    number: str
    amount: int
    replayed: bool


COLLECT_METHODS = ("cash", "card", "transfer", "wallet")


def _hash(sale_id, day, down, rate_bp, months, first_due, down_method) -> str:
    body = [sale_id, day, down, rate_bp, months, first_due, down_method]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _sale(con: sqlite3.Connection, sale_id: int) -> tuple:
    row = con.execute("SELECT id, number, sale_date, total, customer_id FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not row:
        raise ContractError("الفاتورة غير موجودة")
    if row[4] is None:
        raise ContractError("فاتورة بلا عميل مسجّل؛ لا يُبنى عليها عقد")
    owed = con.execute("SELECT coalesce(sum(amount), 0) FROM sale_payments WHERE sale_id=? AND method='installment'",
                       (sale_id,)).fetchone()[0]
    if owed != row[3]:
        raise ContractError("هذه الفاتورة لم تُرحَّل بوسيلة التقسيط بكامل قيمتها")
    if con.execute("SELECT 1 FROM installment_contracts WHERE sale_id=?", (sale_id,)).fetchone():
        raise ContractError("لهذه الفاتورة عقد تقسيط بالفعل")
    return row


def post_contract(con: sqlite3.Connection, by, *, request_key: str, sale_id: int, down: int, rate_bp: int,
                  months: int, first_due: str, national_id: str, down_method: str | None = None,
                  contract_date: str | None = None, address: str = "", work_place: str = "",
                  whatsapp_ok: bool = False, guarantor: Guarantor | None = None,
                  notes: str | None = None) -> PostedContract:
    """Posts the contract, its schedule and its entry together, or writes nothing at all."""
    if by is None or not has_permission(con, by, "installments.manage"):
        raise ContractError("لا تملك صلاحية اعتماد عقود التقسيط")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ContractError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    try:
        day = ledger.valid_date(contract_date or date.today().isoformat(), "تاريخ العقد")
        nid = validate.national_id(national_id)
    except (ledger.LedgerError, validate.ValidationError) as e:
        raise ContractError(str(e)) from e
    if down and down_method not in DOWN_METHODS:
        raise ContractError("طريقة تحصيل المقدم مطلوبة")
    if not down:
        down_method = None

    with transaction(con):
        h = _hash(sale_id, day, down, rate_bp, months, first_due, down_method)
        old = con.execute("SELECT id, number, months, instalments_total, contract_total, payload_hash "
                          "FROM installment_contracts WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[5] != h:
                raise ContractError("مفتاح الطلب سبق استخدامه لعقد بمحتوى مختلف")
            return PostedContract(old[0], old[1], old[2], old[3] // old[2], old[4], True)
        sale = _sale(con, sale_id)
        if day < sale[2]:
            raise ContractError("تاريخ العقد قبل تاريخ الفاتورة")
        try:
            terms = calc.plan(sale[3], down, rate_bp, months, first_due)
        except calc.InstalmentError as e:
            raise ContractError(str(e)) from e
        if terms.rows[0].due_date < day:
            raise ContractError("تاريخ أول قسط قبل تاريخ العقد")
        customer = con.execute("SELECT active FROM customers WHERE id=?", (sale[4],)).fetchone()
        if not customer[0]:
            raise ContractError("العميل موقوف")

        contract_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM installment_contracts").fetchone()[0]
        number = f"IC-{day[:4]}-{contract_id:06d}"
        entry = []
        if down:
            entry += [(PAYMENT_ACCOUNT[down_method], down, 0), (RECEIVABLE_ACCOUNT, 0, down)]
        if terms.markup:
            entry += [(RECEIVABLE_ACCOUNT, terms.markup, 0), (DEFERRED_INTEREST_ACCOUNT, 0, terms.markup)]
        entry_id = None
        if entry:
            try:
                entry_id = ledger.post_entry(con, by, day, "installment_contract", contract_id,
                                             f"عقد تقسيط {number} على {sale[1]}", entry)
            except ledger.LedgerError as e:
                raise ContractError(str(e)) from e
        g = guarantor or Guarantor()
        con.execute(
            "INSERT INTO installment_contracts (id, number, request_key, payload_hash, sale_id, customer_id, "
            "contract_date, cash_price, down, financed, rate_bp, markup, months, instalments_total, contract_total, "
            "first_due, down_method, national_id, address, work_place, whatsapp_ok, guarantor_name, guarantor_mobile, "
            "guarantor_nid, guarantor_relation, notes, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (contract_id, number, key, h, sale_id, sale[4], day, terms.cash_price, down, terms.financed, rate_bp,
             terms.markup, months, terms.instalments_total, terms.contract_total, terms.rows[0].due_date, down_method,
             nid, address.strip() or None, work_place.strip() or None, int(bool(whatsapp_ok)),
             g.name.strip() or None, g.mobile.strip() or None, g.national_id.strip() or None,
             g.relation.strip() or None, (notes or "").strip() or None, entry_id, by.id))
        for r in terms.rows:
            con.execute("INSERT INTO installment_rows (contract_id, no, due_date, principal, markup, amount, "
                        "balance_after) VALUES (?,?,?,?,?,?,?)",
                        (contract_id, r.no, r.due_date, r.principal, r.markup, r.amount, r.balance_after))
        audit(con, by, "installment.contract", "installment_contract", contract_id,
              f"{number} · {terms.contract_total} · {months}")
    return PostedContract(contract_id, number, months, terms.monthly, terms.contract_total, False)


def _payment_hash(contract_id, day, amount, method) -> str:
    return hashlib.sha256(json.dumps([contract_id, day, amount, method], ensure_ascii=False).encode("utf-8")).hexdigest()


def remaining_rows(con: sqlite3.Connection, contract_id: int) -> list[tuple]:
    """Unpaid/partly-paid rows, oldest due first: (row_id, no, due_date, amount, remaining)."""
    rows = con.execute(
        "SELECT r.id, r.no, r.due_date, r.amount, "
        "r.amount - coalesce((SELECT sum(l.amount) FROM installment_payment_lines l "
        "WHERE l.row_id=r.id),0) - coalesce((SELECT sum(a.amount) "
        "FROM installment_return_allocations a WHERE a.row_id=r.id),0) AS remaining "
        "FROM installment_rows r WHERE r.contract_id=? ORDER BY r.no", (contract_id,)).fetchall()
    return [r for r in rows if r[4] > 0]


def collect_payment(con: sqlite3.Connection, by, *, request_key: str, contract_id: int, amount: int, method: str,
                    payment_date: str | None = None, notes: str | None = None) -> PostedPayment:
    """Posts a payment against a contract's outstanding rows (oldest due first), or writes nothing at all."""
    if by is None or not has_permission(con, by, "installments.manage"):
        raise ContractError("لا تملك صلاحية تحصيل أقساط التقسيط")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ContractError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if type(amount) is not int or amount <= 0:
        raise ContractError("مبلغ التحصيل يجب أن يكون عددًا صحيحًا أكبر من صفر")
    if method not in COLLECT_METHODS:
        raise ContractError("طريقة التحصيل غير صالحة")
    try:
        day = ledger.valid_date(payment_date or date.today().isoformat(), "تاريخ التحصيل")
    except ledger.LedgerError as e:
        raise ContractError(str(e)) from e

    with transaction(con):
        h = _payment_hash(contract_id, day, amount, method)
        old = con.execute("SELECT id, number, amount, payload_hash FROM installment_payments WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ContractError("مفتاح الطلب سبق استخدامه لتحصيل بمحتوى مختلف")
            return PostedPayment(old[0], old[1], old[2], True)
        contract = con.execute("SELECT id, number, contract_date FROM installment_contracts WHERE id=?",
                               (contract_id,)).fetchone()
        if not contract:
            raise ContractError("العقد غير موجود")
        if day < contract[2]:
            raise ContractError("تاريخ التحصيل قبل تاريخ العقد")
        due = remaining_rows(con, contract_id)
        if not due:
            raise ContractError("لا يوجد مبلغ مستحق على هذا العقد")
        if amount > sum(r[4] for r in due):
            raise ContractError("مبلغ التحصيل أكبر من المتبقي على العقد")

        payment_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM installment_payments").fetchone()[0]
        number = f"IP-{day[:4]}-{payment_id:06d}"
        entry_id = ledger.post_entry(con, by, day, "installment_payment", payment_id,
                                     f"تحصيل قسط {number} على {contract[1]}",
                                     [(PAYMENT_ACCOUNT[method], amount, 0), (RECEIVABLE_ACCOUNT, 0, amount)])
        con.execute(
            "INSERT INTO installment_payments (id, number, request_key, payload_hash, contract_id, payment_date, "
            "method, amount, notes, journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (payment_id, number, key, h, contract_id, day, method, amount, (notes or "").strip() or None,
             entry_id, by.id))
        left = amount
        for row_id, _, _, _, remaining in due:
            if left <= 0:
                break
            take = min(left, remaining)
            con.execute("INSERT INTO installment_payment_lines (payment_id, row_id, amount) VALUES (?,?,?)",
                        (payment_id, row_id, take))
            left -= take
        audit(con, by, "installment.payment", "installment_payment", payment_id, f"{number} · {amount}")
    return PostedPayment(payment_id, number, amount, False)
