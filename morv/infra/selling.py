"""فاتورة البيع: ترحيل ذري يصرف المخزون FIFO ويسجل الإيراد والتحصيل، مع المعاينة والتنبيهات والمطابقة."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass

from morv.core import costing
from morv.core import sales as calc
from morv.infra import inventory, ledger
from morv.infra.db.connection import transaction
from morv.infra.inventory import StockError
from morv.infra.users import audit, has_permission


class SaleError(ValueError):
    """فاتورة بيع مرفوضة، برسالة تصلح للعرض."""


@dataclass(frozen=True)
class SaleLine:
    """سطر بيع: صنف وكمية وسعر وحدة بالقرش (None = السعر الافتراضي لفئة العميل) واختيار دفعة اختياري."""
    item_id: int
    qty: int
    unit_price: int | None = None
    picks: dict | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SaleResult:
    """نتيجة الترحيل؛ replayed=True لطلب مكرر أُعيدت فاتورته الأصلية، وwarnings تنبيهات لا تمنع الحفظ."""
    id: int
    number: str
    total: int
    paid: int
    credit: int
    replayed: bool
    warnings: tuple = ()


def default_price(con, item_id: int, customer_id: int | None = None) -> int:
    """سعر الصنف الافتراضي لفئة العميل؛ بلا عميل = القطاعي."""
    level = None
    if customer_id is not None:
        row = con.execute("SELECT price_level FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not row:
            raise SaleError("العميل غير موجود")
        level = row[0]
    row = con.execute(f"SELECT {calc.price_column(level)} FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise SaleError("الصنف غير موجود")
    return row[0]


def preview_cost(con, item_id: int, qty: int) -> int | None:
    """تكلفة FIFO المتوقعة لكمية (قراءة فقط)؛ None إن لم يكف المخزون. التكلفة المعتمدة هي ما يعيده الصرف."""
    layers = con.execute("SELECT id, qty_remaining, cost_remaining FROM cost_layers "
                         "WHERE item_id=? AND qty_remaining > 0 ORDER BY id", (item_id,)).fetchall()
    try:
        return sum(c for _, _, c in costing.plan_fifo(layers, qty))
    except costing.InsufficientStock:
        return None


def suggested_dots(con, item_id: int, qty: int) -> list | None:
    """الدفعات المقترحة للصرف [(DOT, كمية)] من الأقدم تصنيعًا؛ None إن لم يكف المخزون أو كان الصنف خدمة."""
    try:
        picks = inventory.suggest_lots(con, item_id, qty)
    except StockError:
        return None
    out = []
    for lot_id, q in picks.items():
        out.append((con.execute("SELECT dot FROM stock_lots WHERE id=?", (lot_id,)).fetchone()[0], q))
    return out


def customer_outstanding(con, customer_id: int) -> int:
    """مجموع آجل فواتير العميل. لا تحصيل قبل المرحلة 6، فهو سقف للرصيد الفعلي."""
    return con.execute("SELECT coalesce(sum(credit), 0) FROM sales WHERE customer_id=?", (customer_id,)).fetchone()[0]


def _shape_lines(lines) -> list:
    """فحص شكل الأسطر بلا قراءة قاعدة البيانات."""
    if not lines:
        raise SaleError("الفاتورة بلا أسطر")
    for i, ln in enumerate(lines, start=1):
        if type(ln.item_id) is not int:
            raise SaleError(f"السطر {i}: صنف غير صالح")
        if type(ln.qty) is not int or ln.qty <= 0:
            raise SaleError(f"السطر {i}: الكمية عدد صحيح موجب")
        if ln.unit_price is not None and (type(ln.unit_price) is not int or ln.unit_price <= 0):
            raise SaleError(f"السطر {i}: السعر بالقرش عدد صحيح موجب")
    return list(lines)


def _payload_hash(customer_id, day, lines, payments) -> str:
    """بصمة محتوى الطلب لكشف إعادة استخدام المفتاح بمحتوى مختلف؛ الملاحظات خارجها عمدًا."""
    body = [customer_id, day,
            [[x.item_id, x.qty, x.unit_price, sorted((x.picks or {}).items()), (x.reason or "").strip()] for x in lines],
            sorted(payments.items())]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def post_sale(con: sqlite3.Connection, by, *, request_key: str, customer_id: int | None, sale_date: str,
              lines: list, payments: dict | None = None, notes: str | None = None) -> SaleResult:
    """ترحيل فاتورة بيع كاملة في معاملة واحدة: كلها تُحفظ أو لا يُحفظ منها شيء.

    payments = {cash|transfer|card: قروش}؛ الباقي من الإجمالي آجل على العميل. تكرار نفس الطلب يعيد الأصلية.
    """
    if by is None or not has_permission(con, by, "sales.post"):
        raise SaleError("لا تملك صلاحية ترحيل فواتير البيع")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise SaleError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    try:
        day = ledger.valid_date(sale_date, "تاريخ الفاتورة")
    except ledger.LedgerError as e:
        raise SaleError(str(e)) from e
    pays = dict(payments or {})
    warnings: list = []
    with transaction(con):
        clean = _shape_lines(lines)
        h = _payload_hash(customer_id, day, clean, pays)
        old = con.execute("SELECT id, number, total, paid, credit, payload_hash FROM sales WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[5] != h:
                raise SaleError("مفتاح الطلب سبق استخدامه لفاتورة بمحتوى مختلف")
            return SaleResult(old[0], old[1], old[2], old[3], old[4], True)
        limit = 0
        if customer_id is not None:
            cust = con.execute("SELECT active, credit_limit FROM customers WHERE id=?", (customer_id,)).fetchone()
            if not cust:
                raise SaleError("العميل غير موجود")
            if not cust[0]:
                raise SaleError("العميل موقوف")
            limit = cust[1]
        sale_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM sales").fetchone()[0]
        number = f"SI-{sale_id:06d}"
        resolved = []
        for i, ln in enumerate(clean, start=1):
            item = con.execute("SELECT kind, active FROM items WHERE id=?", (ln.item_id,)).fetchone()
            if not item:
                raise SaleError(f"السطر {i}: الصنف غير موجود")
            if not item[1]:
                raise SaleError(f"السطر {i}: الصنف موقوف")
            listed = default_price(con, ln.item_id, customer_id)
            price = listed if ln.unit_price is None else ln.unit_price
            if price != listed:
                if not has_permission(con, by, "sales.price_override"):
                    raise SaleError(f"السطر {i}: لا تملك صلاحية تغيير السعر عن الافتراضي")
                audit(con, by, "sale.price_override", "sale", sale_id,
                      f"سطر {i} · صنف {ln.item_id} · الافتراضي {listed} ← {price}")
            resolved.append((ln, item[0], listed, price, calc.line_total(ln.qty, price)))
        total = sum(r[4] for r in resolved)
        try:
            paid, credit = calc.split_payments(total, pays)
        except calc.SaleCalcError as e:
            raise SaleError(str(e)) from e
        if credit and customer_id is None:
            raise SaleError("الآجل يحتاج عميلًا")
        if credit:
            msg = calc.credit_warning(limit, customer_outstanding(con, customer_id), credit)
            if msg:
                warnings.append(msg)
                audit(con, by, "sale.credit_warning", "sale", sale_id, f"{msg} · آجل {credit}")
        costed = []
        for i, (ln, kind, listed, price, line_sum) in enumerate(resolved, start=1):
            issue_id, cost = None, 0
            if kind != "service":
                try:
                    res = inventory.issue_in_tx(con, by, ln.item_id, ln.qty, source_type="sale", source_id=sale_id,
                                                source_line_id=i, picks=ln.picks, reason=ln.reason, issue_date=day)
                except StockError as e:
                    raise SaleError(f"السطر {i}: {e}") from e
                issue_id, cost = res.issue_id, res.cost_total
                if calc.is_below_cost(line_sum, cost):
                    warnings.append(f"السطر {i}: البيع تحت التكلفة")
                    audit(con, by, "sale.below_cost", "sale", sale_id, f"سطر {i} · بيع {line_sum} · تكلفة {cost}")
            costed.append((ln, kind, listed, price, line_sum, cost, issue_id))
        revenue = calc.revenue_by_account([(r[1], r[4]) for r in resolved])
        try:
            entry_id = ledger.post_entry(con, by, day, "sale", sale_id, f"فاتورة بيع {number}",
                                         calc.entry_lines(pays, credit, revenue))
        except ledger.LedgerError as e:
            raise SaleError(str(e)) from e
        con.execute("INSERT INTO sales (id, number, request_key, payload_hash, customer_id, sale_date, total, paid, "
                    "credit, line_count, payment_count, journal_entry_id, notes, user_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sale_id, number, key, h, customer_id, day, total, paid, credit, len(costed), len(pays),
                     entry_id, (notes or "").strip() or None, by.id))
        for i, (ln, kind, listed, price, line_sum, cost, issue_id) in enumerate(costed, start=1):
            con.execute("INSERT INTO sale_lines (sale_id, line_no, item_id, item_kind, qty, list_price, unit_price, "
                        "line_total, cost_total, issue_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (sale_id, i, ln.item_id, kind, ln.qty, listed, price, line_sum, cost, issue_id))
        for method, amount in pays.items():
            con.execute("INSERT INTO sale_payments (sale_id, method, amount) VALUES (?,?,?)", (sale_id, method, amount))
        audit(con, by, "sale.post", "sale", sale_id, f"{number} · {total}")
    return SaleResult(sale_id, number, total, paid, credit, False, tuple(warnings))


def reconcile_sales(con: sqlite3.Connection) -> list:
    """مطابقة المبيعات مع السطور والدفعات والقيد وسجل الصرف؛ قائمة فارغة = متطابق."""
    bad = []
    q = con.execute
    for r in q("SELECT s.id FROM sales s WHERE "
               "s.line_count <> (SELECT count(*) FROM sale_lines WHERE sale_id = s.id) "
               "OR s.total <> (SELECT coalesce(sum(line_total), 0) FROM sale_lines WHERE sale_id = s.id) "
               "OR s.payment_count <> (SELECT count(*) FROM sale_payments WHERE sale_id = s.id) "
               "OR s.paid <> (SELECT coalesce(sum(amount), 0) FROM sale_payments WHERE sale_id = s.id)"):
        bad.append(("sale_totals", r[0]))
    for r in q("SELECT s.id FROM sales s JOIN journal_entries e ON e.id = s.journal_entry_id "
               "WHERE e.source_type <> 'sale' OR e.source_id <> s.id OR e.status <> 'posted' OR e.total <> s.total"):
        bad.append(("sale_entry_header", r[0]))
    want: dict = {}
    for sid, kind, amount in q("SELECT sale_id, item_kind, sum(line_total) FROM sale_lines GROUP BY sale_id, item_kind"):
        want.setdefault(sid, {}).setdefault(("c", calc.REVENUE_ACCOUNTS[kind]), 0)
        want[sid][("c", calc.REVENUE_ACCOUNTS[kind])] += amount
    for sid, method, amount in q("SELECT sale_id, method, amount FROM sale_payments"):
        want.setdefault(sid, {})[("d", calc.PAYMENT_ACCOUNTS[method])] = amount
    for sid, credit in q("SELECT id, credit FROM sales WHERE credit > 0"):
        want.setdefault(sid, {})[("d", calc.CREDIT_ACCOUNT)] = credit
    got: dict = {}
    for sid, acc, d, c in q("SELECT e.source_id, l.account_code, sum(l.debit), sum(l.credit) FROM journal_entries e "
                            "JOIN journal_lines l ON l.entry_id = e.id WHERE e.source_type = 'sale' "
                            "GROUP BY e.source_id, l.account_code"):
        if d:
            got.setdefault(sid, {})[("d", acc)] = d
        if c:
            got.setdefault(sid, {})[("c", acc)] = c
    for sid in sorted(set(want) | set(got)):
        if want.get(sid, {}) != got.get(sid, {}):
            bad.append(("sale_entry_lines", sid))
    for r in q("SELECT l.id FROM sale_lines l LEFT JOIN stock_issues i ON i.id = l.issue_id WHERE l.item_kind <> 'service' "
               "AND (i.id IS NULL OR i.source_type <> 'sale' OR i.source_id <> l.sale_id OR i.source_line_id <> l.line_no "
               "OR i.item_id <> l.item_id OR i.qty <> l.qty OR i.cost_total <> l.cost_total)"):
        bad.append(("sale_line_issue", r[0]))
    for r in q("SELECT i.id FROM stock_issues i WHERE i.source_type = 'sale' AND NOT EXISTS "
               "(SELECT 1 FROM sale_lines l WHERE l.issue_id = i.id)"):
        bad.append(("orphan_sale_issue", r[0]))
    return bad
