"""Purchase invoice: one atomic post creating lots, FIFO layers and the entry."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass

from morv.core import dot as dotmod
from morv.core import money
from morv.core import tax as taxmod
from morv.infra import ledger, settings
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

INVENTORY_ACCOUNT = "1410"     # tyre and battery stock
SUPPLIERS_ACCOUNT = "2110"     # suppliers
INPUT_VAT_ACCOUNT = "1520"     # deductible input VAT (P04-4b)
FREIGHT_BASES = ("qty", "value", "manual")


class PurchaseError(ValueError):
    """Purchase invoice refused, with a message fit to show."""


@dataclass(frozen=True)
class PurchaseLine:
    item_id: int
    qty: int
    unit_cost: int
    dot: str | None = None
    line_discount: int = 0
    freight_share: int | None = None   # only read when freight_basis == "manual"
    input_tax_rate: int | None = None  # supplier invoice, independent of output VAT
    input_tax_deductible: bool | None = None


@dataclass(frozen=True)
class PostedInvoice:
    id: int
    number: str
    total: int
    replayed: bool


@dataclass(frozen=True)
class LineAllocation:
    invoice_share: int
    freight_share: int
    net_total: int


@dataclass(frozen=True)
class LineTax:
    tax_base: int
    rate_bp: int
    tax_amount: int
    deductible_amount: int
    cost_amount: int


def next_number_preview(con: sqlite3.Connection) -> str:
    """Best-effort preview of the invoice number the next post will get."""
    next_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM purchase_invoices").fetchone()[0]
    return f"PI-{next_id:06d}"


def last_cost(con: sqlite3.Connection, item_id: int, *, prices_tax_inclusive: bool | None = None) -> int | None:
    """Latest entered price, comparing only matching tax price bases when requested."""
    if prices_tax_inclusive is None:
        row = con.execute("SELECT purchase_unit_cost FROM stock_lots WHERE item_id=? AND adjustment_line_id IS NULL "
                          "ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
        return row[0] if row else None
    row = con.execute(
        "SELECT l.purchase_unit_cost, p.prices_tax_inclusive FROM stock_lots l "
        "LEFT JOIN purchase_lines pl ON pl.id=l.purchase_line_id "
        "LEFT JOIN purchase_invoices p ON p.id=pl.invoice_id "
        "WHERE l.item_id=? AND l.adjustment_line_id IS NULL ORDER BY l.id DESC LIMIT 1",
        (item_id,)).fetchone()
    return row[0] if row and row[1] == int(prices_tax_inclusive) else None


def _shape_lines(lines, freight_basis: str) -> list:
    if not lines:
        raise PurchaseError("الفاتورة بلا أسطر")
    out = []
    for i, ln in enumerate(lines, start=1):
        if type(ln.qty) is not int or ln.qty <= 0:
            raise PurchaseError(f"السطر {i}: الكمية عدد صحيح موجب")
        if type(ln.unit_cost) is not int or ln.unit_cost <= 0:
            raise PurchaseError(f"السطر {i}: التكلفة بالقرش عدد صحيح موجب")
        if type(ln.line_discount) is not int or not 0 <= ln.line_discount <= ln.qty * ln.unit_cost:
            raise PurchaseError(f"السطر {i}: خصم السطر أكبر من قيمة السطر أو غير صحيح")
        if freight_basis == "manual":
            if type(ln.freight_share) is not int or ln.freight_share < 0:
                raise PurchaseError(f"السطر {i}: نصيب الشحن اليدوي عدد صحيح غير سالب")
        elif ln.freight_share is not None:
            raise PurchaseError(f"السطر {i}: نصيب الشحن اليدوي لا يُستخدم إلا مع التوزيع اليدوي")
        if ln.input_tax_rate is not None and (type(ln.input_tax_rate) is not int or ln.input_tax_rate < 0):
            raise PurchaseError(f"السطر {i}: نسبة ضريبة الشراء عدد صحيح غير سالب")
        if ln.input_tax_deductible is not None and type(ln.input_tax_deductible) is not bool:
            raise PurchaseError(f"السطر {i}: قابلية خصم ضريبة الشراء قيمة منطقية")
        d = None
        if ln.dot:
            try:
                d = dotmod.parse_dot(ln.dot)
            except dotmod.DotError as e:
                raise PurchaseError(f"السطر {i}: {e}") from e
        out.append(PurchaseLine(ln.item_id, ln.qty, ln.unit_cost, d, ln.line_discount, ln.freight_share,
                                ln.input_tax_rate, ln.input_tax_deductible))
    return out


def _check_items(con, lines: list) -> None:
    for i, ln in enumerate(lines, start=1):
        row = con.execute("SELECT kind, active FROM items WHERE id=?", (ln.item_id,)).fetchone()
        if not row:
            raise PurchaseError(f"السطر {i}: الصنف غير موجود")
        if not row[1]:
            raise PurchaseError(f"السطر {i}: الصنف موقوف")
        if row[0] not in ("tire", "battery"):
            raise PurchaseError(f"السطر {i}: الخدمات لا تُشترى ولا تُخزَّن")
        if row[0] == "tire" and not ln.dot:
            raise PurchaseError(f"السطر {i}: رمز DOT إلزامي للإطارات")


def _payload_hash(supplier_id, inv_no, inv_date, recv_date, lines, freight_amount, freight_basis,
                  invoice_discount, prices_tax_inclusive, freight_taxable) -> str:
    body = [supplier_id, inv_no, inv_date, recv_date, freight_amount, freight_basis, invoice_discount,
            int(prices_tax_inclusive), int(freight_taxable),
            [[x.item_id, x.qty, x.unit_cost, x.dot, x.line_discount, x.freight_share,
              x.input_tax_rate, x.input_tax_deductible] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _pre_tax_payload_hash(supplier_id, inv_no, inv_date, recv_date, lines, freight_amount, freight_basis,
                          invoice_discount) -> str:
    """Pre-P04-4b signature (#175 and earlier), kept so in-flight request_key replays still match."""
    body = [supplier_id, inv_no, inv_date, recv_date, freight_amount, freight_basis, invoice_discount,
            [[x.item_id, x.qty, x.unit_cost, x.dot, x.line_discount, x.freight_share] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _legacy_payload_hash(supplier_id, inv_no, inv_date, recv_date, lines) -> str:
    body = [supplier_id, inv_no, inv_date, recv_date, [[x.item_id, x.qty, x.unit_cost, x.dot] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _allocate_freight(clean: list, freight_amount: int, freight_basis: str, nets_before_freight: list) -> list:
    if freight_basis == "manual":
        shares = [ln.freight_share for ln in clean]
        if sum(shares) != freight_amount:
            raise PurchaseError("مجموع نصيب الشحن اليدوي لا يساوي مبلغ الشحن")
        return shares
    weights = [ln.qty for ln in clean] if freight_basis == "qty" else nets_before_freight
    try:
        return money.allocate(weights, freight_amount)
    except money.MoneyError as e:
        raise PurchaseError("تعذّر توزيع الشحن: أساس التوزيع صفري؛ استخدم أساسًا آخر أو التوزيع اليدوي") from e


def compute_allocations(clean: list, freight_amount: int, freight_basis: str,
                        invoice_discount: int) -> list[LineAllocation]:
    """The P02 order (line value -> line discount -> invoice-discount share -> + freight share),

    shared by post_invoice and the purchase form's live preview so the two can never diverge.
    clean must already be shaped (PurchaseLine.line_discount/freight_share validated, e.g. by
    _shape_lines). Raises PurchaseError for a discount exceeding the value it reduces, a
    zero-basis freight split, a manual-freight sum mismatch, or a zero/negative net cost.
    """
    gross = [ln.qty * ln.unit_cost for ln in clean]
    after_line_discount = [g - ln.line_discount for g, ln in zip(gross, clean, strict=True)]
    if invoice_discount > sum(after_line_discount):     # a discount, unlike freight, may never exceed the value it reduces
        raise PurchaseError("خصم الفاتورة أكبر من صافي قيمة الأصناف بعد خصم السطور")
    try:
        invoice_shares = money.allocate(after_line_discount, invoice_discount)
    except money.MoneyError as e:
        raise PurchaseError("خصم الفاتورة أكبر من صافي قيمة الأصناف بعد خصم السطور") from e
    nets_before_freight = [b - s for b, s in zip(after_line_discount, invoice_shares, strict=True)]
    freight_shares = _allocate_freight(clean, freight_amount, freight_basis, nets_before_freight)
    net_totals = [b + f for b, f in zip(nets_before_freight, freight_shares, strict=True)]
    for i, (ln, net) in enumerate(zip(clean, net_totals, strict=True), start=1):
        if net <= 0:
            raise PurchaseError(f"السطر {i}: التكلفة الصافية بعد الخصم والشحن صفر أو سالبة")
        # Positive cost per remaining unit is required by the FIFO layer and issue constraints.
        if net < ln.qty:
            raise PurchaseError(f"السطر {i}: التكلفة الصافية لا تكفي قرشًا واحدًا على الأقل لكل قطعة")
    return [LineAllocation(s, f, n) for s, f, n in zip(invoice_shares, freight_shares, net_totals, strict=True)]


def compute_line_taxes(lines: list[PurchaseLine], allocations: list[LineAllocation], *,
                       tax_enabled: bool, prices_tax_inclusive: bool,
                       freight_taxable: bool) -> list[LineTax]:
    """Shared preview and posting calculation. No DB writes or implicit deduction decision."""
    tax_calcs = []
    for i, (ln, a) in enumerate(zip(lines, allocations, strict=True), start=1):
        if tax_enabled:
            if ln.input_tax_rate is None or ln.input_tax_deductible is None:
                raise PurchaseError(f"السطر {i}: حدد نسبة ضريبة فاتورة المورد وقابليتها للخصم")
            rate_bp = ln.input_tax_rate
        else:
            if ln.input_tax_rate is not None or ln.input_tax_deductible is not None:
                raise PurchaseError(f"السطر {i}: الضريبة غير مفعلة")
            rate_bp = 0
        tax_base, tax_amount, cost_amount = taxmod.purchase_line_split(
            a.net_total, a.freight_share, freight_taxable, rate_bp, prices_tax_inclusive)
        deductible = tax_amount if ln.input_tax_deductible else 0
        cost_amount += tax_amount - deductible
        if cost_amount < ln.qty:
            raise PurchaseError(f"السطر {i}: التكلفة بعد استبعاد الضريبة لا تكفي قرشًا واحدًا لكل قطعة")
        tax_calcs.append(LineTax(tax_base, rate_bp, tax_amount, deductible, cost_amount))
    return tax_calcs


def post_invoice(con: sqlite3.Connection, by, *, request_key: str, supplier_id: int, supplier_invoice_no: str,
                 invoice_date: str, lines: list, received_date: str | None = None,
                 freight_amount: int = 0, freight_basis: str = "qty", invoice_discount: int = 0,
                 prices_tax_inclusive: bool = False, freight_taxable: bool = False,
                 notes: str | None = None) -> PostedInvoice:
    if by is None or not has_permission(con, by, "purchases.post"):
        raise PurchaseError("لا تملك صلاحية ترحيل فواتير الشراء")
    key = (request_key or "").strip()
    inv_no = (supplier_invoice_no or "").strip()
    if len(key) < 8:
        raise PurchaseError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if not inv_no:
        raise PurchaseError("رقم فاتورة المورد مطلوب")
    if type(freight_amount) is not int or freight_amount < 0:
        raise PurchaseError("مبلغ الشحن عدد صحيح بالقرش لا يقل عن صفر")
    if freight_basis not in FREIGHT_BASES:
        raise PurchaseError("أساس توزيع الشحن غير معروف")
    if type(invoice_discount) is not int or invoice_discount < 0:
        raise PurchaseError("خصم الفاتورة عدد صحيح بالقرش لا يقل عن صفر")
    if type(prices_tax_inclusive) is not bool or type(freight_taxable) is not bool:
        raise PurchaseError("خيارا الضريبة الشاملة وخضوع الشحن قيمتان منطقيتان")
    try:
        inv_date = ledger.valid_date(invoice_date, "تاريخ الفاتورة")
        recv_date = ledger.valid_date(received_date or invoice_date, "تاريخ الاستلام")
    except ledger.LedgerError as e:
        raise PurchaseError(str(e)) from e
    with transaction(con):
        clean = _shape_lines(lines, freight_basis)
        h = _payload_hash(supplier_id, inv_no.casefold(), inv_date, recv_date, clean, freight_amount,
                          freight_basis, invoice_discount, prices_tax_inclusive, freight_taxable)
        old = con.execute("SELECT id, number, total, payload_hash FROM purchase_invoices WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            no_tax_input = (not prices_tax_inclusive and not freight_taxable
                            and all(ln.input_tax_rate is None and ln.input_tax_deductible is None for ln in clean))
            legacy_match = (no_tax_input and freight_amount == 0 and freight_basis == "qty" and invoice_discount == 0
                            and all(ln.line_discount == 0 and ln.freight_share is None for ln in clean)
                            and old[3] == _legacy_payload_hash(supplier_id, inv_no.casefold(), inv_date, recv_date, clean))
            pre_tax_match = (no_tax_input
                            and old[3] == _pre_tax_payload_hash(supplier_id, inv_no.casefold(), inv_date, recv_date,
                                                                clean, freight_amount, freight_basis, invoice_discount))
            if old[3] != h and not legacy_match and not pre_tax_match:
                raise PurchaseError("مفتاح الطلب سبق استخدامه لفاتورة بمحتوى مختلف")
            return PostedInvoice(old[0], old[1], old[2], True)
        _check_items(con, clean)  # After the replay check: deactivating an item later must not block a replay
        sup = con.execute("SELECT active FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not sup:
            raise PurchaseError("المورد غير موجود")
        if not sup[0]:
            raise PurchaseError("المورد موقوف")
        if con.execute("SELECT 1 FROM purchase_invoices WHERE supplier_id=? AND supplier_invoice_no=?",
                       (supplier_id, inv_no)).fetchone():
            raise PurchaseError("فاتورة بنفس رقم فاتورة المورد مرحّلة من قبل")
        allocations = compute_allocations(clean, freight_amount, freight_basis, invoice_discount)
        tax_enabled = settings.get(con, "tax.enabled", "0") == "1"
        tax_calcs = compute_line_taxes(clean, allocations, tax_enabled=tax_enabled,
                                        prices_tax_inclusive=prices_tax_inclusive, freight_taxable=freight_taxable)
        tax_total = sum(c.tax_amount for c in tax_calcs)
        deductible_total = sum(c.deductible_amount for c in tax_calcs)
        total = sum(c.cost_amount + c.deductible_amount for c in tax_calcs)
        inv_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM purchase_invoices").fetchone()[0]
        number = f"PI-{inv_id:06d}"
        entry_lines = [(INVENTORY_ACCOUNT, total - deductible_total, 0)]
        if deductible_total > 0:
            entry_lines.append((INPUT_VAT_ACCOUNT, deductible_total, 0))
        entry_lines.append((SUPPLIERS_ACCOUNT, 0, total))
        try:
            entry_id = ledger.post_entry(con, by, recv_date, "purchase_invoice", inv_id, f"فاتورة شراء {number}",
                                         entry_lines)
        except ledger.LedgerError as e:
            raise PurchaseError(str(e)) from e
        con.execute(
            "INSERT INTO purchase_invoices (id, number, request_key, payload_hash, supplier_id, supplier_invoice_no, "
            "invoice_date, received_date, total, freight_amount, freight_basis, invoice_discount, "
            "prices_tax_inclusive, freight_taxable, tax_total, journal_entry_id, notes, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (inv_id, number, key, h, supplier_id, inv_no, inv_date, recv_date, total, freight_amount,
             freight_basis, invoice_discount, int(prices_tax_inclusive), int(freight_taxable), tax_total,
             entry_id, (notes or "").strip() or None, by.id))
        for no, (ln, a, c) in enumerate(zip(clean, allocations, tax_calcs, strict=True), start=1):
            _receive_line(con, by, inv_id, number, no, ln, recv_date, a.invoice_share, a.freight_share, a.net_total, c)
        audit(con, by, "purchase.post", "purchase_invoice", inv_id, f"{number} · {total}")
    return PostedInvoice(inv_id, number, total, False)


def _receive_line(con, by, inv_id: int, number: str, no: int, ln: PurchaseLine, recv_date: str,
                  invoice_share: int, freight_share: int, net_total: int, tax: LineTax) -> None:
    gross = ln.qty * ln.unit_cost
    line_id = con.execute(
        "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total, "
        "line_discount, invoice_share, freight_share, net_total, tax_base, tax_rate, tax_amount, "
        "deductible_tax_amount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (inv_id, no, ln.item_id, ln.dot, ln.qty, ln.unit_cost, gross, ln.line_discount, invoice_share,
         freight_share, net_total, tax.tax_base, tax.rate_bp, tax.tax_amount, tax.deductible_amount)).lastrowid
    lot_id = con.execute(
        "INSERT INTO stock_lots (ref_no, item_id, purchase_line_id, dot, mfg_key, received_date, qty_received, "
        "qty_on_hand, purchase_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"{number}/{no}", ln.item_id, line_id, ln.dot, dotmod.mfg_key(ln.dot) if ln.dot else None, recv_date,
         ln.qty, ln.qty, ln.unit_cost)).lastrowid
    con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_line_id, user_id) "
                "VALUES (?,?,?,?,?,?)", (ln.item_id, lot_id, "receipt", ln.qty, line_id, by.id))
    # FIFO includes non-deductible acquisition VAT, but never deductible input VAT.
    con.execute("INSERT INTO cost_layers (item_id, purchase_line_id, received_date, qty_received, cost_total, "
                "qty_remaining, cost_remaining) VALUES (?,?,?,?,?,?,?)",
                (ln.item_id, line_id, recv_date, ln.qty, tax.cost_amount, ln.qty, tax.cost_amount))
