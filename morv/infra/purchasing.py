"""فاتورة الشراء واستلام البضاعة: ترحيل واحد ذري ينشئ الدفعات الفعلية وطبقات FIFO والقيد."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass

from morv.core import dot as dotmod
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

INVENTORY_ACCOUNT = "1410"     # مخزون الإطارات والبطاريات
SUPPLIERS_ACCOUNT = "2110"     # الموردون


class PurchaseError(ValueError):
    """فاتورة شراء مرفوضة، برسالة تصلح للعرض."""


@dataclass(frozen=True)
class PurchaseLine:
    """سطر شراء: صنف وكمية وتكلفة الوحدة بالقرش، ورمز DOT (إلزامي للإطارات)."""
    item_id: int
    qty: int
    unit_cost: int
    dot: str | None = None


@dataclass(frozen=True)
class PostedInvoice:
    """نتيجة الترحيل؛ replayed=True إن كان الطلب مكررًا فأُعيد المستند الأصلي دون تكرار."""
    id: int
    number: str
    total: int
    replayed: bool


def _clean_lines(con, lines) -> list:
    """فحص الأسطر وتوحيد DOT؛ يعيد [(PurchaseLine نظيف, نوع الصنف)]."""
    if not lines:
        raise PurchaseError("الفاتورة بلا أسطر")
    out = []
    for i, ln in enumerate(lines, start=1):
        if type(ln.qty) is not int or ln.qty <= 0:
            raise PurchaseError(f"السطر {i}: الكمية عدد صحيح موجب")
        if type(ln.unit_cost) is not int or ln.unit_cost <= 0:
            raise PurchaseError(f"السطر {i}: التكلفة بالقرش عدد صحيح موجب")
        row = con.execute("SELECT kind, active FROM items WHERE id=?", (ln.item_id,)).fetchone()
        if not row:
            raise PurchaseError(f"السطر {i}: الصنف غير موجود")
        if not row[1]:
            raise PurchaseError(f"السطر {i}: الصنف موقوف")
        if row[0] not in ("tire", "battery"):
            raise PurchaseError(f"السطر {i}: الخدمات لا تُشترى ولا تُخزَّن")
        d = ln.dot
        if row[0] == "tire" and not d:
            raise PurchaseError(f"السطر {i}: رمز DOT إلزامي للإطارات")
        if d:
            try:
                d = dotmod.parse_dot(d)
            except dotmod.DotError as e:
                raise PurchaseError(f"السطر {i}: {e}") from e
        out.append(PurchaseLine(ln.item_id, ln.qty, ln.unit_cost, d or None))
    return out


def _payload_hash(supplier_id, inv_no, inv_date, recv_date, lines) -> str:
    """بصمة محتوى الطلب لكشف إعادة استخدام المفتاح مع محتوى مختلف."""
    body = [supplier_id, inv_no, inv_date, recv_date, [[x.item_id, x.qty, x.unit_cost, x.dot] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def post_invoice(con: sqlite3.Connection, by, *, request_key: str, supplier_id: int, supplier_invoice_no: str,
                 invoice_date: str, lines: list, received_date: str | None = None,
                 notes: str | None = None) -> PostedInvoice:
    """ترحيل فاتورة شراء كاملة في معاملة واحدة: كلها تُحفظ أو لا يُحفظ منها شيء.

    request_key يُولَّد مرة لكل نموذج؛ تكرار ترحيله بنفس المحتوى يعيد الفاتورة الأصلية بلا تكرار.
    """
    if by is None or not has_permission(con, by, "purchases.post"):
        raise PurchaseError("لا تملك صلاحية ترحيل فواتير الشراء")
    key = (request_key or "").strip()
    inv_no = (supplier_invoice_no or "").strip()
    if len(key) < 8:
        raise PurchaseError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if not inv_no:
        raise PurchaseError("رقم فاتورة المورد مطلوب")
    try:
        inv_date = ledger.valid_date(invoice_date, "تاريخ الفاتورة")
        recv_date = ledger.valid_date(received_date or invoice_date, "تاريخ الاستلام")
    except ledger.LedgerError as e:
        raise PurchaseError(str(e)) from e
    with transaction(con):
        clean = _clean_lines(con, lines)
        h = _payload_hash(supplier_id, inv_no.casefold(), inv_date, recv_date, clean)
        old = con.execute("SELECT id, number, total, payload_hash FROM purchase_invoices WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise PurchaseError("مفتاح الطلب سبق استخدامه لفاتورة بمحتوى مختلف")
            return PostedInvoice(old[0], old[1], old[2], True)
        sup = con.execute("SELECT active FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not sup:
            raise PurchaseError("المورد غير موجود")
        if not sup[0]:
            raise PurchaseError("المورد موقوف")
        if con.execute("SELECT 1 FROM purchase_invoices WHERE supplier_id=? AND supplier_invoice_no=?",
                       (supplier_id, inv_no)).fetchone():
            raise PurchaseError("فاتورة بنفس رقم فاتورة المورد مرحّلة من قبل")
        total = sum(x.qty * x.unit_cost for x in clean)
        inv_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM purchase_invoices").fetchone()[0]
        number = f"PI-{inv_id:06d}"
        try:
            entry_id = ledger.post_entry(con, by, recv_date, "purchase_invoice", inv_id, f"فاتورة شراء {number}",
                                         [(INVENTORY_ACCOUNT, total, 0), (SUPPLIERS_ACCOUNT, 0, total)])
        except ledger.LedgerError as e:
            raise PurchaseError(str(e)) from e
        con.execute(
            "INSERT INTO purchase_invoices (id, number, request_key, payload_hash, supplier_id, supplier_invoice_no, "
            "invoice_date, received_date, total, journal_entry_id, notes, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (inv_id, number, key, h, supplier_id, inv_no, inv_date, recv_date, total, entry_id,
             (notes or "").strip() or None, by.id))
        for no, ln in enumerate(clean, start=1):
            _receive_line(con, by, inv_id, number, no, ln, recv_date)
        audit(con, by, "purchase.post", "purchase_invoice", inv_id, f"{number} · {total}")
    return PostedInvoice(inv_id, number, total, False)


def _receive_line(con, by, inv_id: int, number: str, no: int, ln: PurchaseLine, recv_date: str) -> None:
    """سطر واحد: يُنشئ السطر ثم الدفعة الفعلية وحركة الاستلام وطبقة التكلفة."""
    total = ln.qty * ln.unit_cost
    line_id = con.execute(
        "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total) VALUES (?,?,?,?,?,?,?)",
        (inv_id, no, ln.item_id, ln.dot, ln.qty, ln.unit_cost, total)).lastrowid
    lot_id = con.execute(
        "INSERT INTO stock_lots (ref_no, item_id, purchase_line_id, dot, mfg_key, received_date, qty_received, "
        "qty_on_hand, purchase_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"{number}/{no}", ln.item_id, line_id, ln.dot, dotmod.mfg_key(ln.dot) if ln.dot else None, recv_date,
         ln.qty, ln.qty, ln.unit_cost)).lastrowid
    con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_line_id, user_id) "
                "VALUES (?,?,?,?,?,?)", (ln.item_id, lot_id, "receipt", ln.qty, line_id, by.id))
    con.execute("INSERT INTO cost_layers (item_id, purchase_line_id, received_date, qty_received, cost_total, "
                "qty_remaining, cost_remaining) VALUES (?,?,?,?,?,?,?)",
                (ln.item_id, line_id, recv_date, ln.qty, total, ln.qty, total))
