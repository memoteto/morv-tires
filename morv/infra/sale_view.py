"""قراءة فواتير البيع للقائمة والمعاينة: صفوف جاهزة للعرض، بلا كتابة."""
import sqlite3
from dataclasses import dataclass



@dataclass(frozen=True)
class SaleLineView:
    """سطر في معاينة الفاتورة."""
    line_no: int
    item_name: str
    qty: int
    unit_price: int
    line_discount: int
    invoice_share: int
    net_total: int


@dataclass(frozen=True)
class SaleView:
    """فاتورة كاملة للمعاينة: رأسها وأسطرها ودفعاتها."""
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


def _like(query: str) -> str:
    """نمط بحث LIKE من النص الحر."""
    return f"%{(query or '').strip()}%"


def list_sales(con: sqlite3.Connection, query: str = "", limit: int = 300, offset: int = 0) -> list[tuple]:
    """صفحة قائمة: (id, رقم، العميل، التاريخ، الإجمالي، وسائل الدفع). الترتيب الأحدث أولًا."""
    return con.execute(
        "SELECT s.id, s.number, coalesce(c.name, 'عميل نقدي'), s.sale_date, s.total, "
        "(SELECT group_concat(p.method, '، ') FROM sale_payments p WHERE p.sale_id = s.id) "
        "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id "
        "WHERE (? = '%%' OR s.number LIKE ? OR c.name LIKE ? OR c.mobile LIKE ?) "
        "ORDER BY s.id DESC LIMIT ? OFFSET ?",
        (_like(query), _like(query), _like(query), _like(query), limit, offset)).fetchall()


def count(con: sqlite3.Connection) -> int:
    """عدد الفواتير المرحّلة."""
    return con.execute("SELECT count(*) FROM sales").fetchone()[0]


def get_sale(con: sqlite3.Connection, sale_id: int) -> SaleView:
    """فاتورة كاملة للمعاينة."""
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
    return SaleView(*h, payments=tuple(pays), lines=tuple(SaleLineView(*r) for r in lines))


