"""Read-only views of posted sales for the list and the preview."""
import sqlite3
from dataclasses import dataclass
from morv.core.money import fmt
from morv.infra import customer_receipts



@dataclass(frozen=True)
class SaleLineView:
    line_no: int
    item_name: str
    qty: int
    unit_price: int
    line_discount: int
    invoice_share: int
    net_total: int
    tax_treatment: str = "none"
    tax_rate: int = 0
    tax_amount: int = 0
    prices_tax_inclusive: bool = False

    @property
    def tax_label(self) -> str:
        if self.tax_treatment == "exempt":
            return "معفى"
        if self.tax_treatment == "zero":
            return "نسبة صفر"
        if self.tax_treatment == "standard":
            return f"ضريبة {fmt(self.tax_rate).rstrip('0').rstrip('.')}%"
        return "بدون ضريبة"


@dataclass(frozen=True)
class SaleView:
    id: int
    number: str
    customer_name: str
    sale_date: str
    subtotal: int
    line_discount: int
    invoice_discount: int
    total: int
    odometer: int | None
    vehicle_plate: str
    payments: tuple
    lines: tuple
    returned: int
    returns: tuple
    tax_enabled: bool = False
    prices_tax_inclusive: bool = False
    tax_total: int = 0

    @property
    def tax_basis_label(self) -> str:
        return "شامل الضريبة" if self.prices_tax_inclusive else "غير شامل الضريبة"

    @property
    def value_before_tax(self) -> int:
        return self.total - self.tax_total


@dataclass(frozen=True)
class ReturnableLine:
    sale_line_id: int
    line_no: int
    item_name: str
    sold: int
    returned: int
    net_total: int

    @property
    def available(self) -> int:
        return self.sold - self.returned


def _like(query: str) -> str:
    return f"%{(query or '').strip()}%"


def list_sales(con: sqlite3.Connection, query: str = "", limit: int = 300, offset: int = 0) -> list[tuple]:
    return con.execute(
        "SELECT s.id, s.number, coalesce(c.name, 'عميل نقدي'), s.sale_date, s.total, "
        "(SELECT group_concat(p.method, '، ') FROM sale_payments p WHERE p.sale_id = s.id), "
        "(SELECT coalesce(sum(r.total), 0) FROM sale_returns r WHERE r.sale_id = s.id) "
        "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id "
        "WHERE (? = '%%' OR s.number LIKE ? OR c.name LIKE ? OR c.mobile LIKE ?) "
        "ORDER BY s.id DESC LIMIT ? OFFSET ?",
        (_like(query), _like(query), _like(query), _like(query), limit, offset)).fetchall()


def count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM sales").fetchone()[0]


def customer_stats(con: sqlite3.Connection, customer_id: int) -> tuple[int, str | None]:
    r = con.execute("SELECT count(*), max(sale_date) FROM sales WHERE customer_id=?", (customer_id,)).fetchone()
    return r[0], r[1]


def get_sale(con: sqlite3.Connection, sale_id: int) -> SaleView:
    h = con.execute(
        "SELECT s.id, s.number, coalesce(c.name, 'عميل نقدي'), s.sale_date, s.subtotal, s.line_discount, "
        "s.invoice_discount, s.total, s.odometer, coalesce(s.vehicle_plate, ''), "
        "s.tax_enabled, s.prices_tax_inclusive, s.tax_total "
        "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id WHERE s.id=?", (sale_id,)).fetchone()
    if not h:
        raise LookupError("الفاتورة غير موجودة")
    lines = con.execute(
        "SELECT l.line_no, i.name, l.qty, l.unit_price, l.line_discount, l.invoice_share, l.net_total, "
        "l.tax_treatment, l.tax_rate, l.tax_amount, l.prices_tax_inclusive "
        "FROM sale_lines l JOIN items i ON i.id = l.item_id WHERE l.sale_id=? ORDER BY l.line_no", (sale_id,)).fetchall()
    pays = con.execute("SELECT method, amount, received, reference FROM sale_payments WHERE sale_id=? ORDER BY id",
                       (sale_id,)).fetchall()
    rets = con.execute("SELECT number, return_date, total, reason, refund_method FROM sale_returns "
                       "WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
    return SaleView(*h[:10], payments=tuple(pays),
                    lines=tuple(SaleLineView(*r[:10], prices_tax_inclusive=bool(r[10])) for r in lines),
                    returned=sum(r[2] for r in rets), returns=tuple(rets),
                    tax_enabled=bool(h[10]), prices_tax_inclusive=bool(h[11]), tax_total=h[12])


def returnable_lines(con: sqlite3.Connection, sale_id: int) -> list[ReturnableLine]:
    rows = con.execute(
        "SELECT l.id, l.line_no, i.name, l.qty, "
        "(SELECT coalesce(sum(x.qty), 0) FROM sale_return_lines x WHERE x.sale_line_id = l.id), l.net_total "
        "FROM sale_lines l JOIN items i ON i.id = l.item_id WHERE l.sale_id=? ORDER BY l.line_no",
        (sale_id,))
    return [ReturnableLine(*r) for r in rows]


def refund_methods(con: sqlite3.Connection, sale_id: int) -> list[str]:
    """Original payment methods; a credit-only invoice may also be refunded in money once collected."""
    found = [r[0] for r in con.execute(
        "SELECT DISTINCT method FROM sale_payments WHERE sale_id=? ORDER BY method", (sale_id,))]
    return found + list(customer_receipts.METHODS) if found == ["credit"] else found


