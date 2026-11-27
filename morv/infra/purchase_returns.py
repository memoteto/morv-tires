"""Returns of purchased stock to the supplier at book value, with a mirrored reversal document."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import costing
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

SUPPLIERS_ACCOUNT = "2110"
INVENTORY_ACCOUNT = "1410"
SETTLEMENT_DIFF_ACCOUNT = "5160"   # qualifying difference between the agreed settlement and the book value removed
STOCKED = ("tire", "battery")


class PurchaseReturnError(Exception):
    pass


@dataclass(frozen=True)
class ReturnLine:
    purchase_line_id: int
    qty: int


@dataclass(frozen=True)
class PostedReturn:
    id: int
    number: str
    total: int
    replayed: bool


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _require(con, by) -> None:
    if by is None or not has_permission(con, by, "purchases.post"):
        raise PurchaseReturnError("لا تملك صلاحية مرتجعات الموردين")


def _key(request_key: str) -> str:
    key = (request_key or "").strip()
    if len(key) < 8:
        raise PurchaseReturnError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def _stock_row(con, purchase_line_id: int, supplier_id: int):
    """Joined purchase line, lot and cost layer: item, kind, cost, date, lot, layer, quantities, and the
    layer's own cost_total and recorded net allocations (an integrity check independent of unit cost)."""
    return con.execute(
        "SELECT pl.item_id, i.kind, pl.unit_cost, p.received_date, l.id, l.qty_on_hand, c.id, c.qty_remaining, "
        "c.cost_remaining, l.ref_no, c.cost_total, "
        "(SELECT coalesce(sum(cost), 0) FROM cost_allocations WHERE layer_id = c.id) "
        "FROM purchase_lines pl JOIN purchase_invoices p ON p.id = pl.invoice_id "
        "JOIN items i ON i.id = pl.item_id JOIN stock_lots l ON l.purchase_line_id = pl.id "
        "JOIN cost_layers c ON c.purchase_line_id = pl.id WHERE pl.id = ? AND p.supplier_id = ?",
        (purchase_line_id, supplier_id)).fetchone()


def returnable(con: sqlite3.Connection, supplier_id: int) -> list[tuple]:
    """Lots of the supplier still in stock: (line id, lot ref, item name, dot, available, unit cost)."""
    rows = con.execute(
        "SELECT pl.id, l.ref_no, i.name, coalesce(l.dot, ''), min(l.qty_on_hand, c.qty_remaining), pl.unit_cost "
        "FROM purchase_lines pl JOIN purchase_invoices p ON p.id = pl.invoice_id JOIN items i ON i.id = pl.item_id "
        "JOIN stock_lots l ON l.purchase_line_id = pl.id JOIN cost_layers c ON c.purchase_line_id = pl.id "
        "WHERE p.supplier_id = ? ORDER BY l.received_date, l.id", (supplier_id,)).fetchall()
    return [r for r in rows if r[4] > 0]


def post_return(con: sqlite3.Connection, by, *, request_key: str, supplier_id: int, lines: list[ReturnLine],
                reason: str, return_date: str | None = None, settlement_amount: int | None = None,
                settlement_reason: str | None = None) -> PostedReturn:
    """Sends lot quantities back at their book value and settles what the shop owes the supplier.

    settlement_amount is the agreed amount the supplier actually credits; it defaults to the book value
    removed. A difference between the two is a qualifying gain or loss on 5160, kept apart from the
    inventory book value and its cost allocations, which are never touched by the settlement figure.
    """
    _require(con, by)
    key = _key(request_key)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise PurchaseReturnError("سبب المرتجع مطلوب (3 أحرف على الأقل)")
    if not lines:
        raise PurchaseReturnError("المرتجع بلا أسطر")
    if len({ln.purchase_line_id for ln in lines}) != len(lines):
        raise PurchaseReturnError("سطر الشراء مكرر في المرتجع؛ اجمعه في سطر واحد")
    if settlement_amount is not None and (type(settlement_amount) is not int or settlement_amount < 0):
        raise PurchaseReturnError("مبلغ التسوية عدد صحيح غير سالب")
    settlement_reason = (settlement_reason or "").strip()
    try:
        day = ledger.valid_date(return_date or date.today().isoformat(), "تاريخ المرتجع")
    except ledger.LedgerError as e:
        raise PurchaseReturnError(str(e)) from e
    with transaction(con):
        digest = _digest("return", supplier_id, day, reason, settlement_amount, settlement_reason,
                         [[x.purchase_line_id, x.qty] for x in lines])
        old = con.execute("SELECT id, number, total, payload_hash FROM purchase_returns WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != digest:
                raise PurchaseReturnError("مفتاح الطلب سبق استخدامه لمرتجع بمحتوى مختلف")
            return PostedReturn(old[0], old[1], old[2], True)
        if not con.execute("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)).fetchone():
            raise PurchaseReturnError("المورد غير موجود")
        shaped = []
        for no, ln in enumerate(lines, start=1):
            if type(ln.qty) is not int or ln.qty <= 0:
                raise PurchaseReturnError(f"السطر {no}: الكمية عدد صحيح موجب")
            row = _stock_row(con, ln.purchase_line_id, supplier_id)
            if row is None or row[1] not in STOCKED:
                raise PurchaseReturnError(f"السطر {no}: سطر شراء غير موجود لهذا المورد أو صنف غير مخزّن")
            if day < row[3]:
                raise PurchaseReturnError(f"السطر {no}: تاريخ المرتجع قبل تاريخ استلام الدفعة")
            if ln.qty > min(row[5], row[7]):
                raise PurchaseReturnError(f"السطر {no}: الكمية أكبر من المتاح في الدفعة {row[9]} ({min(row[5], row[7])})")
            if row[10] - row[11] != row[8]:
                raise PurchaseReturnError(f"السطر {no}: تكلفة الطبقة لا تطابق صافي تخصيصاتها المسجلة؛ راجع المخزون")
            value = costing.slice_cost(row[8], row[7], ln.qty)
            shaped.append((no, ln, row, value))
        total = sum(x[3] for x in shaped)
        settle = total if settlement_amount is None else settlement_amount
        diff = settle - total
        if diff and len(settlement_reason) < 3:
            raise PurchaseReturnError("سبب اختلاف مبلغ التسوية عن القيمة الدفترية مطلوب (3 أحرف على الأقل)")
        return_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM purchase_returns").fetchone()[0]
        number = f"PRT-{day[:4]}-{return_id:06d}"
        memo = f"مرتجع مورد {number}: {reason}"
        je_lines = [(SUPPLIERS_ACCOUNT, settle, 0), (INVENTORY_ACCOUNT, 0, total)]
        if diff < 0:
            je_lines.append((SETTLEMENT_DIFF_ACCOUNT, -diff, 0))
            memo += f" · فرق تسوية مدين {-diff} قرش: {settlement_reason}"
        elif diff > 0:
            je_lines.append((SETTLEMENT_DIFF_ACCOUNT, 0, diff))
            memo += f" · فرق تسوية دائن {diff} قرش: {settlement_reason}"
        try:
            entry_id = ledger.post_entry(con, by, day, "purchase_return", return_id, memo, je_lines)
        except ledger.LedgerError as e:
            raise PurchaseReturnError(str(e)) from e
        con.execute("INSERT INTO purchase_returns (id, number, request_key, payload_hash, supplier_id, kind, "
                    "return_date, total, settlement_amount, reason, journal_entry_id, user_id) "
                    "VALUES (?,?,?,?,?,'return',?,?,?,?,?,?)",
                    (return_id, number, key, digest, supplier_id, day, total, settle, reason, entry_id, by.id))
        for no, ln, row, value in shaped:
            issue_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM stock_issues").fetchone()[0]
            con.execute("INSERT INTO stock_issues (id, item_id, qty, cost_total, source_type, source_id, source_line_id, "
                        "journal_entry_id, user_id) VALUES (?,?,?,?,'purchase_return',?,?,?,?)",
                        (issue_id, row[0], ln.qty, value, return_id, no, entry_id, by.id))
            con.execute("INSERT INTO purchase_return_lines (return_id, line_no, purchase_line_id, qty, value, issue_id) "
                        "VALUES (?,?,?,?,?,?)", (return_id, no, ln.purchase_line_id, ln.qty, value, issue_id))
            con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - ? WHERE id=?", (ln.qty, row[4]))
            con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, issue_id, user_id) "
                        "VALUES (?,?,'issue',?,?,?)", (row[0], row[4], -ln.qty, issue_id, by.id))
            con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining - ?, cost_remaining = cost_remaining - ? "
                        "WHERE id=?", (ln.qty, value, row[6]))
            con.execute("INSERT INTO cost_allocations (issue_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                        (issue_id, row[6], ln.qty, value))
        audit(con, by, "purchase.return", "purchase_return", return_id, f"{number} · {total} · {reason}")
    return PostedReturn(return_id, number, total, False)


def reverse_return(con: sqlite3.Connection, by, *, request_key: str, return_id: int, reason: str) -> PostedReturn:
    """Puts a returned document's stock back into its lots and layers with an entry dated today."""
    _require(con, by)
    key = _key(request_key)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise PurchaseReturnError("سبب العكس مطلوب (3 أحرف على الأقل)")
    with transaction(con):
        digest = _digest("reversal", return_id, reason)
        old = con.execute("SELECT id, number, total, payload_hash FROM purchase_returns WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != digest:
                raise PurchaseReturnError("مفتاح الطلب سبق استخدامه لعكس بمحتوى مختلف")
            return PostedReturn(old[0], old[1], old[2], True)
        src = con.execute("SELECT number, supplier_id, total, kind, settlement_amount FROM purchase_returns "
                          "WHERE id=?", (return_id,)).fetchone()
        if not src or src[3] != "return":
            raise PurchaseReturnError("مرتجع المورد غير موجود")
        if con.execute("SELECT 1 FROM purchase_returns WHERE reverses_id=?", (return_id,)).fetchone():
            raise PurchaseReturnError("مرتجع المورد معكوس سابقًا")
        today = date.today().isoformat()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM purchase_returns").fetchone()[0]
        number = f"PRT-{today[:4]}-{new_id:06d}"
        total, settle = src[2], src[4]
        diff = settle - total
        je_lines = [(INVENTORY_ACCOUNT, total, 0), (SUPPLIERS_ACCOUNT, 0, settle)]
        if diff < 0:
            je_lines.append((SETTLEMENT_DIFF_ACCOUNT, 0, -diff))
        elif diff > 0:
            je_lines.append((SETTLEMENT_DIFF_ACCOUNT, diff, 0))
        try:
            entry_id = ledger.post_entry(con, by, today, "purchase_return", new_id,
                                         f"عكس مرتجع مورد {src[0]} {number}: {reason}", je_lines)
        except ledger.LedgerError as e:
            raise PurchaseReturnError(str(e)) from e
        con.execute("INSERT INTO purchase_returns (id, number, request_key, payload_hash, supplier_id, kind, "
                    "return_date, total, settlement_amount, reason, reverses_id, journal_entry_id, user_id) "
                    "VALUES (?,?,?,?,?,'reversal',?,?,?,?,?,?,?)",
                    (new_id, number, key, digest, src[1], today, total, settle, reason, return_id, entry_id, by.id))
        originals = con.execute("SELECT purchase_line_id, qty, value FROM purchase_return_lines WHERE return_id=? "
                                "ORDER BY line_no", (return_id,)).fetchall()
        for no, (line_id, qty, value) in enumerate(originals, start=1):
            row = _stock_row(con, line_id, src[1])
            line_pk = con.execute("INSERT INTO purchase_return_lines (return_id, line_no, purchase_line_id, qty, value) "
                                  "VALUES (?,?,?,?,?)", (new_id, no, line_id, qty, value)).lastrowid
            con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + ? WHERE id=?", (qty, row[4]))
            con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_return_line_id, user_id) "
                        "VALUES (?,?,'restock',?,?,?)", (row[0], row[4], qty, line_pk, by.id))
            con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining + ?, cost_remaining = cost_remaining + ? "
                        "WHERE id=?", (qty, value, row[6]))
            con.execute("INSERT INTO cost_allocations (purchase_return_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                        (line_pk, row[6], -qty, -value))
        audit(con, by, "purchase.return.reverse", "purchase_return", new_id, f"{number} عكس {src[0]}")
    return PostedReturn(new_id, number, src[2], False)


def history(con: sqlite3.Connection, supplier_id: int | None = None) -> list[tuple]:
    """Returns and reversals, newest first: (id, number, kind, date, supplier, total, reason, reversed)."""
    where, args = ("WHERE r.supplier_id = ?", (supplier_id,)) if supplier_id else ("", ())
    return con.execute(
        "SELECT r.id, r.number, r.kind, r.return_date, s.name, r.total, r.reason, "
        "EXISTS (SELECT 1 FROM purchase_returns x WHERE x.reverses_id = r.id) FROM purchase_returns r "
        f"JOIN suppliers s ON s.id = r.supplier_id {where} ORDER BY r.id DESC", args).fetchall()
