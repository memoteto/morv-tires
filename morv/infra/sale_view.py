"""Read-only views of posted sales for the list and the preview."""
import sqlite3
from dataclasses import dataclass



@dataclass(frozen=True)
class SaleLineView:
    line_no: int
    item_name: str
    qty: int
    unit_price: int
    line_discount: int
    invoice_share: int
    net_total: int


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
        "s.invoice_discount, s.total, s.odometer, coalesce(s.vehicle_plate, '') "
        "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id WHERE s.id=?", (sale_id,)).fetchone()
    if not h:
        raise LookupError("الفاتورة غير موجودة")
    lines = con.execute(
        "SELECT l.line_no, i.name, l.qty, l.unit_price, l.line_discount, l.invoice_share, l.net_total "
        "FROM sale_lines l JOIN items i ON i.id = l.item_id WHERE l.sale_id=? ORDER BY l.line_no", (sale_id,)).fetchall()
    pays = con.execute("SELECT method, amount, received, reference FROM sale_payments WHERE sale_id=? ORDER BY id",
                       (sale_id,)).fetchall()
    rets = con.execute("SELECT number, return_date, total, reason, refund_method FROM sale_returns "
                       "WHERE sale_id=? ORDER BY id", (sale_id,)).fetchall()
    return SaleView(*h, payments=tuple(pays), lines=tuple(SaleLineView(*r) for r in lines),
                    returned=sum(r[2] for r in rets), returns=tuple(rets))


def returnable_lines(con: sqlite3.Connection, sale_id: int) -> list[ReturnableLine]:
    rows = con.execute(
        "SELECT l.id, l.line_no, i.name, l.qty, "
        "(SELECT coalesce(sum(x.qty), 0) FROM sale_return_lines x WHERE x.sale_line_id = l.id), l.net_total "
        "FROM sale_lines l JOIN items i ON i.id = l.item_id WHERE l.sale_id=? ORDER BY l.line_no",
        (sale_id,))
    return [ReturnableLine(*r) for r in rows]


def refund_methods(con: sqlite3.Connection, sale_id: int) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT method FROM sale_payments WHERE sale_id=? ORDER BY method", (sale_id,))]


