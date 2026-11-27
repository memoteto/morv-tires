"""Sale invoice: one atomic post writing the invoice, revenue entry and FIFO issue."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import sales_rules as rules, tax
from morv.core.money import MoneyError
from morv.core.sales_rules import Payment, SaleRuleError
from morv.infra import customer_receipts, inventory, ledger, settings
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission, permission_limit

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


@dataclass(frozen=True)
class SaleLineTotals:
    invoice_share: int
    payable: int
    tax_treatment: str
    tax_rate: int
    tax_amount: int

    @property
    def revenue(self) -> int:
        return self.payable - self.tax_amount


@dataclass(frozen=True)
class SaleTotals:
    subtotal: int
    line_discount: int
    tax_enabled: bool
    prices_tax_inclusive: bool
    lines: tuple[SaleLineTotals, ...]

    @property
    def total(self) -> int:
        return sum(line.payable for line in self.lines)

    @property
    def tax_total(self) -> int:
        return sum(line.tax_amount for line in self.lines)


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


def compute_sale_totals(con, lines: list, invoice_discount: int = 0, *,
                        prices_tax_inclusive: bool | None = None) -> SaleTotals:
    """Shared read-only quote for preview and posting; payments use gross payable.

    Legacy callers keep the old behavior with tax disabled. An enabled shop must
    provide a price basis explicitly; the POS must not silently post an old draft.
    """
    if prices_tax_inclusive is not None and type(prices_tax_inclusive) is not bool:
        raise SaleError("طريقة أسعار الضريبة غير صحيحة")
    if type(invoice_discount) is not int or invoice_discount < 0:
        raise SaleError("خصم الفاتورة عدد صحيح بالقرش لا يقل عن صفر")
    shaped = _shape(lines)
    enabled = settings.get(con, "tax.enabled", "0") == "1"
    if enabled and prices_tax_inclusive is None:
        raise SaleError("حدد أساس أسعار البيع الضريبي")
    inclusive = enabled and bool(prices_tax_inclusive)
    gross = [ln.qty * ln.unit_price for ln in shaped]
    bases = [g - ln.line_discount for g, ln in zip(gross, shaped, strict=True)]
    try:
        shares = rules.allocate(bases, invoice_discount)
        result = []
        item_rates = {}
        if enabled:
            general_rate = int(settings.get(con, "tax.rate", ""))
            if general_rate < 0:
                raise MoneyError("النسبة العامة سالبة")
        for ln, base, share in zip(shaped, bases, shares, strict=True):
            entered = base - share
            treatment, rate = "none", 0
            if enabled:
                if ln.item_id not in item_rates:
                    row = con.execute("SELECT tax_treatment, tax_rate FROM items WHERE id=?",
                                      (ln.item_id,)).fetchone()
                    if row is None:
                        raise SaleError("الصنف غير موجود")
                    item_rates[ln.item_id] = (row[0], tax.resolve_rate(row[0], row[1], general_rate))
                treatment, rate = item_rates[ln.item_id]
            amount = (tax.split_inclusive(entered, rate)[1] if inclusive
                      else tax.tax_on_exclusive(entered, rate)) if enabled else 0
            payable = entered if inclusive else entered + amount
            result.append(SaleLineTotals(share, payable, treatment, rate, amount))
    except (SaleRuleError, MoneyError) as e:
        raise SaleError(str(e)) from e
    except ValueError as e:
        if isinstance(e, SaleError):
            raise
        raise SaleError("إعداد نسبة الضريبة غير صحيح") from e
    return SaleTotals(sum(gross), sum(ln.line_discount for ln in shaped),
                      enabled, inclusive, tuple(result))


def _check_pricing(con, by, lines: list, invoice_discount: int) -> list:
    """Enforce the price and discount rules; returns notes for the audit trail."""
    notes = []
    if not has_permission(con, by, "sales.price_override"):
        for i, ln in enumerate(lines, start=1):
            row = con.execute("SELECT price_retail, price_wholesale, price_super FROM items WHERE id=?",
                              (ln.item_id,)).fetchone()
            if row and ln.unit_price not in row:
                raise SaleError(f"السطر {i}: السعر ليس من أسعار الصنف ولا تملك صلاحية تعديل السعر")
    else:
        for ln in lines:
            row = con.execute("SELECT price_retail, price_wholesale, price_super FROM items WHERE id=?",
                              (ln.item_id,)).fetchone()
            if row and ln.unit_price not in row:
                notes.append(f"سعر معدّل للصنف {ln.item_id}: {ln.unit_price}")
    discount = invoice_discount + sum(ln.line_discount for ln in lines)
    if discount:
        gross = sum(ln.qty * ln.unit_price for ln in lines)
        if not has_permission(con, by, "sales.discount"):
            raise SaleError("لا تملك صلاحية منح خصم")
        cap = permission_limit(con, by, "sales.discount")
        if cap is not None and discount * 100 > cap * gross:
            raise SaleError(f"الخصم يتجاوز حدك المسموح ({cap}%)")
        notes.append(f"خصم {discount}")
    return notes


def _check_customer(con, customer_id, payments: list) -> None:
    credit = sum(p.amount for p in payments if p.method == "credit")
    instalment = sum(p.amount for p in payments if p.method == "installment")
    if customer_id is None:
        if credit:
            raise SaleError("البيع الآجل يحتاج عميلًا مسجلًا")
        if instalment:
            raise SaleError("البيع بالتقسيط يحتاج عميلًا مسجلًا")
        return
    row = con.execute("SELECT active, credit_limit FROM customers WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise SaleError("العميل غير موجود")
    if not row[0]:
        raise SaleError("العميل موقوف")
    if credit:
        room = row[1] + max(0, customer_receipts.deposit_balance(con, customer_id))   # a held deposit backs credit
        if room <= 0:
            raise SaleError("لهذا العميل حد ائتمان صفر ولا عربون؛ لا يُقبل آجل")
        outstanding = customer_receipts.customer_balance(con, customer_id)
        if outstanding + credit > room:
            raise SaleError("يتجاوز الآجل حد ائتمان العميل")


def _payload_hash(customer_id, sale_date, lines, payments, invoice_discount, odometer, plate) -> str:
    body = [customer_id, sale_date, invoice_discount, odometer, plate,
            [[x.item_id, x.qty, x.unit_price, x.line_discount, sorted((x.picks or {}).items())] for x in lines],
            [[p.method, p.amount, p.received, p.reference] for p in payments]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _vat_payload_hash(legacy_hash: str, inclusive: bool) -> str:
    return hashlib.sha256(f"sale-vat-v1:{legacy_hash}:{int(inclusive)}".encode("utf-8")).hexdigest()


def next_number_preview(con: sqlite3.Connection) -> str:
    """Best-effort preview of the invoice number the next post will get."""
    next_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM sales").fetchone()[0]
    return f"SI-{next_id:06d}"


def post_sale(con: sqlite3.Connection, by, *, request_key: str, customer_id: int | None, lines: list,
              payments: list, sale_date: str | None = None, invoice_discount: int = 0,
              odometer: int | None = None, vehicle_plate: str | None = None,
              notes: str | None = None, prices_tax_inclusive: bool | None = None) -> PostedSale:
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
    if prices_tax_inclusive is not None and type(prices_tax_inclusive) is not bool:
        raise SaleError("طريقة أسعار الضريبة غير صحيحة")
    plate = (vehicle_plate or "").strip() or None
    payments = [p if isinstance(p, Payment) else Payment(*p) for p in payments]
    with transaction(con):
        if by is None or not has_permission(con, by, "sales.post"):
            raise SaleError("لا تملك صلاحية ترحيل فواتير البيع")
        shaped = _shape(lines)
        h = _payload_hash(customer_id, day, shaped, payments, invoice_discount, odometer, plate)
        old = con.execute("SELECT id, number, total, payload_hash FROM sales WHERE request_key=?", (key,)).fetchone()
        vat_hash = (_vat_payload_hash(h, prices_tax_inclusive)
                    if prices_tax_inclusive is not None else None)
        if old:
            # Replay the immutable original; current item rates or tax setting
            # cannot change a posted invoice. Legacy untaxed hashes stay valid.
            if old[3] not in (h, vat_hash):
                raise SaleError("مفتاح الطلب سبق استخدامه لفاتورة بمحتوى مختلف")
            return PostedSale(old[0], old[1], old[2], True)
        kinds = _check_items(con, shaped)
        totals = compute_sale_totals(con, shaped, invoice_discount,
                                     prices_tax_inclusive=prices_tax_inclusive)
        total = totals.total
        if totals.tax_enabled:
            h = vat_hash
        try:
            rules.check_payments(total, payments)
        except SaleRuleError as e:
            raise SaleError(str(e)) from e
        notes_audit = _check_pricing(con, by, shaped, invoice_discount)
        _check_customer(con, customer_id, payments)
        sale_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM sales").fetchone()[0]
        number = f"SI-{sale_id:06d}"
        revenue: dict[str, int] = {}
        for ln, amounts in zip(shaped, totals.lines, strict=True):
            acc = rules.REVENUE_ACCOUNT[kinds[ln.item_id]]
            revenue[acc] = revenue.get(acc, 0) + amounts.revenue
        debit = [(rules.PAYMENT_ACCOUNT[p.method], p.amount, 0) for p in payments]
        credit = [(acc, 0, amt) for acc, amt in sorted(revenue.items()) if amt]
        if totals.tax_total:
            credit.append(("2350", 0, totals.tax_total))
        try:
            entry_id = ledger.post_entry(con, by, day, "sale", sale_id, f"فاتورة بيع {number}",
                                         debit + credit)
        except ledger.LedgerError as e:
            raise SaleError(str(e)) from e
        columns = ("id, number, request_key, payload_hash, customer_id, sale_date, subtotal, "
                   "line_discount, invoice_discount, total, odometer, vehicle_plate, notes, journal_entry_id, user_id")
        values = (sale_id, number, key, h, customer_id, day, totals.subtotal, totals.line_discount,
                  invoice_discount, total, odometer, plate, (notes or "").strip() or None, entry_id, by.id)
        if totals.tax_enabled:
            columns += ", tax_enabled, prices_tax_inclusive, tax_total"
            values += (1, int(totals.prices_tax_inclusive), totals.tax_total)
        con.execute(f"INSERT INTO sales ({columns}) VALUES ({','.join('?' for _ in values)})", values)
        for no, (ln, amounts) in enumerate(zip(shaped, totals.lines, strict=True), start=1):
            columns = ("sale_id, line_no, item_id, qty, unit_price, line_discount, invoice_share, net_total")
            values = (sale_id, no, ln.item_id, ln.qty, ln.unit_price, ln.line_discount,
                      amounts.invoice_share, amounts.payable)
            if totals.tax_enabled:
                columns += ", tax_treatment, tax_rate, tax_amount, prices_tax_inclusive"
                values += (amounts.tax_treatment, amounts.tax_rate, amounts.tax_amount,
                           int(totals.prices_tax_inclusive))
            cur = con.execute(f"INSERT INTO sale_lines ({columns}) VALUES ({','.join('?' for _ in values)})", values)
            if kinds[ln.item_id] in STOCK_KINDS:
                try:
                    issued = inventory.issue_in_tx(con, by, ln.item_id, ln.qty, source_type="sale", source_id=sale_id,
                                          source_line_id=cur.lastrowid, picks=ln.picks,
                                          reason=ln.pick_reason, issue_date=day)
                except inventory.StockError as e:
                    raise SaleError(f"السطر {no}: {e}") from e
                if amounts.revenue < issued.cost_total:
                    if not has_permission(con, by, "sales.below_cost"):
                        raise SaleError(f"السطر {no}: السعر أقل من التكلفة ولا تملك صلاحية ذلك")
                    notes_audit.append(f"بأقل من التكلفة: السطر {no}")
        for p in payments:
            con.execute("INSERT INTO sale_payments (sale_id, method, amount, received, reference) VALUES (?,?,?,?,?)",
                        (sale_id, p.method, p.amount, p.received, (p.reference or "").strip() or None))
        audit(con, by, "sale.post", "sale", sale_id,
              f"{number} · {total}" + ("".join(f" · {n}" for n in notes_audit)))
    return PostedSale(sale_id, number, total, False)
