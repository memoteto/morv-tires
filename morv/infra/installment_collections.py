"""Instalment collection: a receipt that pays the oldest instalments and clears the receivable."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import installments as calc
from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.installments import RECEIVABLE_ACCOUNT
from morv.infra.users import audit, has_permission

METHODS = ("cash", "card", "transfer", "wallet")


class CollectionError(ValueError):
    """Collection refused, with a message fit to show."""


@dataclass(frozen=True)
class PostedCollection:
    id: int
    number: str
    amount: int
    remaining: int
    replayed: bool


def outstanding(con: sqlite3.Connection, contract_id: int) -> list[tuple]:
    """Each instalment and what is still owed on it, oldest first."""
    return con.execute(
        "SELECT r.id, r.amount - coalesce((SELECT sum(a.amount) FROM installment_allocations a "
        "WHERE a.row_id = r.id), 0) FROM installment_rows r WHERE r.contract_id=? ORDER BY r.no", (contract_id,)
    ).fetchall()


def remaining(con: sqlite3.Connection, contract_id: int) -> int:
    return sum(left for _, left in outstanding(con, contract_id))


def _hash(contract_id, day, amount, method) -> str:
    body = [contract_id, day, amount, method]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def collect(con: sqlite3.Connection, by, *, request_key: str, contract_id: int, amount: int, method: str,
            collect_date: str | None = None, reference: str | None = None,
            notes: str | None = None) -> PostedCollection:
    """Posts the receipt, its allocation and its entry together, or writes nothing at all."""
    if by is None or not has_permission(con, by, "installments.collect"):
        raise CollectionError("لا تملك صلاحية تحصيل الأقساط")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise CollectionError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if method not in METHODS:
        raise CollectionError("طريقة التحصيل غير معروفة")
    try:
        day = ledger.valid_date(collect_date or date.today().isoformat(), "تاريخ التحصيل")
    except ledger.LedgerError as e:
        raise CollectionError(str(e)) from e

    with transaction(con):
        h = _hash(contract_id, day, amount, method)
        old = con.execute("SELECT id, number, amount, payload_hash FROM installment_collections "
                          "WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[3] != h:
                raise CollectionError("مفتاح الطلب سبق استخدامه لسند بمحتوى مختلف")
            return PostedCollection(old[0], old[1], old[2], remaining(con, contract_id), True)
        contract = con.execute("SELECT number, contract_date FROM installment_contracts WHERE id=?",
                               (contract_id,)).fetchone()
        if not contract:
            raise CollectionError("العقد غير موجود")
        if day < contract[1]:
            raise CollectionError("تاريخ التحصيل قبل تاريخ العقد")
        try:
            shares = calc.spread(outstanding(con, contract_id), amount)
        except calc.InstalmentError as e:
            raise CollectionError(str(e)) from e

        collection_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM installment_collections").fetchone()[0]
        number = f"RC-{day[:4]}-{collection_id:06d}"
        try:
            entry_id = ledger.post_entry(con, by, day, "installment_collection", collection_id,
                                         f"تحصيل قسط {number} على {contract[0]}",
                                         [(PAYMENT_ACCOUNT[method], amount, 0), (RECEIVABLE_ACCOUNT, 0, amount)])
        except ledger.LedgerError as e:
            raise CollectionError(str(e)) from e
        con.execute(
            "INSERT INTO installment_collections (id, number, request_key, payload_hash, contract_id, collect_date, "
            "amount, method, reference, notes, journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (collection_id, number, key, h, contract_id, day, amount, method,
             (reference or "").strip() or None, (notes or "").strip() or None, entry_id, by.id))
        for row_id, share in shares:
            con.execute("INSERT INTO installment_allocations (collection_id, row_id, amount) VALUES (?,?,?)",
                        (collection_id, row_id, share))
        audit(con, by, "installment.collect", "installment_collection", collection_id, f"{number} · {amount}")
        left = remaining(con, contract_id)
    return PostedCollection(collection_id, number, amount, left, False)
