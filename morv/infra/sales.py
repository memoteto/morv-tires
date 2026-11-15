"""Sale invoice: one atomic post writing the invoice, revenue entry and FIFO issue."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import sales_rules as rules
from morv.core.sales_rules import Payment, SaleRuleError
from morv.infra import inventory, ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

STOCK_KINDS = ("tire", "battery")


class SaleError(ValueError):
    """Sale invoice refused, with a message fit to show."""


@dataclass(frozen=True)
class SaleLineIn:
    item_id: int
    qty: int
    unit_price: int
    line_discount: int = 0
    picks: dict | None = None
    pick_reason: str | None = None


@dataclass(frozen=True)
class PostedSale:
    id: int
    number: str
    total: int
    replayed: bool


def _shape(lines) -> list:
    if not lines:
        raise SaleError("الفاتورة بلا أسطر")
    for i, ln in enumerate(lines, start=1):
        if type(ln.qty) is not int or ln.qty <= 0:
            raise SaleError(f"السطر {i}: الكمية عدد صحيح موجب")
        if type(ln.unit_price) is not int or ln.unit_price <= 0:
            raise SaleError(f"السطر {i}: السعر بالقرش عدد صحيح موجب")
        if type(ln.line_discount) is not int or not 0 <= ln.line_discount <= ln.qty * ln.unit_price:
            raise SaleError(f"السطر {i}: الخصم أكبر من قيمة السطر أو غير صحيح")
    return list(lines)


def _check_items(con, lines: list) -> dict:
    kinds = {}
    for i, ln in enumerate(lines, start=1):
        row = con.execute("SELECT kind, active FROM items WHERE id=?", (ln.item_id,)).fetchone()
        if not row:
            raise SaleError(f"السطر {i}: الصنف غير موجود")
        if not row[1]:
            raise SaleError(f"السطر {i}: الصنف موقوف")
        kinds[ln.item_id] = row[0]
    return kinds


def _check_customer(con, customer_id, payments: list) -> None:
    credit = sum(p.amount for p in payments if p.method == "credit")
    if customer_id is None:
        if credit:
            raise SaleError("البيع الآجل يحتاج عميلًا مسجلًا")
        return
    row = con.execute("SELECT active, credit_limit FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise SaleError("العميل غير موجود")
    if not row[0]:
        raise SaleError("العميل موقوف")
    if credit:
        if row[1] <= 0:
            raise SaleError("لهذا العميل حد ائتمان صفر؛ لا يُقبل آجل")
        sold = con.execute(
            "SELECT coalesce(sum(p.amount), 0) FROM sale_payments p JOIN sales s ON s.id = p.sale_id "
            "WHERE s.customer_id=? AND p.method='credit'", (customer_id,)).fetchone()[0]
        # A credit refund frees the customer's used credit limit
        refunded = con.execute(
            "SELECT coalesce(sum(r.total), 0) FROM sale_returns r JOIN sales s ON s.id = r.sale_id "
            "WHERE s.customer_id=? AND r.refund_method='credit'", (customer_id,)).fetchone()[0]
        outstanding = sold - refunded
        if outstanding + credit > row[1]:
            raise SaleError("يتجاوز الآجل حد ائتمان العميل")


def _payload_hash(customer_id, sale_date, lines, payments, invoice_discount, odometer, plate) -> str:
    body = [customer_id, sale_date, invoice_discount, odometer, plate,
            [[x.item_id, x.qty, x.unit_price, x.line_discount, sorted((x.picks or {}).items())] for x in lines],
            [[p.method, p.amount, p.received, p.reference] for p in payments]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def post_sale(con: sqlite3.Connection, by, *, request_key: str, customer_id: int | None, lines: list,
              payments: list, sale_date: str | None = None, invoice_discount: int = 0,
              odometer: int | None = None, vehicle_plate: str | None = None,
              notes: str | None = None) -> PostedSale:
    if by is None or not has_permission(con, by, "sales.post"):
        raise SaleError("لا تملك صلاحية ترحيل فواتير البيع")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise SaleError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    try:
        day = ledger.valid_date(sale_date or date.today().isoformat(), "تاريخ الفاتورة")
    except ledger.LedgerError as e:
        raise SaleError(str(e)) from e
    if type(invoice_discount) is not int or invoice_discount < 0:
        raise SaleError("خصم الفاتورة عدد صحيح بالقرش لا يقل عن صفر")
    if odometer is not None and (type(odometer) is not int or odometer < 0):
        raise SaleError("عداد الكيلومترات عدد صحيح موجب")
    plate = (vehicle_plate or "").strip() or None
    payments = [p if isinstance(p, Payment) else Payment(*p) for p in payments]
    with transaction(con):
        shaped = _shape(lines)
        h = _payload_hash(customer_id, day, shaped, payments, invoice_discount, odometer, plate)
        old = con.execute("SELECT id, number, total, payload_hash FROM sales WHERE request_key=?", (key,)).fetchone()
        if old:
            if old[3] != h:
                raise SaleError("مفتاح الطلب سبق استخدامه لفاتورة بمحتوى مختلف")
            return PostedSale(old[0], old[1], old[2], True)
        kinds = _check_items(con, shaped)
        gross = [ln.qty * ln.unit_price for ln in shaped]
        bases = [g - ln.line_discount for g, ln in zip(gross, shaped, strict=True)]
        try:
            shares = rules.allocate(bases, invoice_discount)
            rules.check_payments(sum(bases) - invoice_discount, payments)
        except SaleRuleError as e:
            raise SaleError(str(e)) from e
        nets = [b - s for b, s in zip(bases, shares, strict=True)]
        subtotal, line_disc = sum(gross), sum(ln.line_discount for ln in shaped)
        total = sum(nets)
        _check_customer(con, customer_id, payments)
        sale_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM sales").fetchone()[0]
        number = f"SI-{sale_id:06d}"
        revenue: dict[str, int] = {}
        for ln, net in zip(shaped, nets, strict=True):
            acc = rules.REVENUE_ACCOUNT[kinds[ln.item_id]]
            revenue[acc] = revenue.get(acc, 0) + net
        debit = [(rules.PAYMENT_ACCOUNT[p.method], p.amount, 0) for p in payments]
        try:
            entry_id = ledger.post_entry(con, by, day, "sale", sale_id, f"فاتورة بيع {number}",
                                         debit + [(acc, 0, amt) for acc, amt in sorted(revenue.items())])
        except ledger.LedgerError as e:
            raise SaleError(str(e)) from e
        con.execute(
            "INSERT INTO sales (id, number, request_key, payload_hash, customer_id, sale_date, subtotal, "
            "line_discount, invoice_discount, total, odometer, vehicle_plate, notes, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sale_id, number, key, h, customer_id, day, subtotal, line_disc, invoice_discount, total,
             odometer, plate, (notes or "").strip() or None, entry_id, by.id))
        for no, (ln, share, net) in enumerate(zip(shaped, shares, nets, strict=True), start=1):
            cur = con.execute(
                "INSERT INTO sale_lines (sale_id, line_no, item_id, qty, unit_price, line_discount, invoice_share, "
                "net_total) VALUES (?,?,?,?,?,?,?,?)",
                (sale_id, no, ln.item_id, ln.qty, ln.unit_price, ln.line_discount, share, net))
            if kinds[ln.item_id] in STOCK_KINDS:
                try:
                    inventory.issue_in_tx(con, by, ln.item_id, ln.qty, source_type="sale", source_id=sale_id,
                                          source_line_id=cur.lastrowid, picks=ln.picks,
                                          reason=ln.pick_reason, issue_date=day)
                except inventory.StockError as e:
                    raise SaleError(f"السطر {no}: {e}") from e
        for p in payments:
            con.execute("INSERT INTO sale_payments (sale_id, method, amount, received, reference) VALUES (?,?,?,?,?)",
                        (sale_id, p.method, p.amount, p.received, (p.reference or "").strip() or None))
        audit(con, by, "sale.post", "sale", sale_id, f"{number} · {total}")
    return PostedSale(sale_id, number, total, False)
