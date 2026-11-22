"""Read-only views of posted purchase invoices."""
from dataclasses import dataclass

from morv.core.text import norm_ar


@dataclass(frozen=True)
class InvoiceLine:
    line_no: int
    item_name: str
    item_code: str
    dot: str | None
    qty: int
    unit_cost: int
    line_total: int


@dataclass(frozen=True)
class InvoiceView:
    id: int
    number: str
    supplier_id: int
    supplier_name: str
    supplier_invoice_no: str
    invoice_date: str
    received_date: str
    total: int
    notes: str
    returned: int
    lines: list


def list_invoices(con, query: str = "", limit: int = 300, offset: int = 0) -> list[tuple]:
    q = (query or "").strip()
    where, params = "", []
    if q:
        where = " WHERE (p.number LIKE ? OR s.name_norm LIKE ? OR p.supplier_invoice_no LIKE ?)"
        params = [f"%{q}%", f"%{norm_ar(q)}%", f"%{q}%"]
    sql = ("SELECT p.id, p.number, s.name, p.supplier_invoice_no, p.invoice_date, p.total, "
           "(SELECT coalesce(sum(CASE WHEN r.kind = 'return' THEN rl.value ELSE -rl.value END), 0) "
           "FROM purchase_return_lines rl JOIN purchase_returns r ON r.id = rl.return_id "
           "JOIN purchase_lines pl ON pl.id = rl.purchase_line_id WHERE pl.invoice_id = p.id) "
           "FROM purchase_invoices p JOIN suppliers s ON s.id = p.supplier_id" + where +
           " ORDER BY p.id DESC LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def count(con) -> int:
    return con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0]


def get_invoice(con, invoice_id: int) -> InvoiceView:
    h = con.execute(
        "SELECT p.id, p.number, p.supplier_id, s.name, p.supplier_invoice_no, p.invoice_date, p.received_date, "
        "p.total, p.notes, "
        "(SELECT coalesce(sum(CASE WHEN r.kind = 'return' THEN rl.value ELSE -rl.value END), 0) "
           "FROM purchase_return_lines rl JOIN purchase_returns r ON r.id = rl.return_id "
           "JOIN purchase_lines pl ON pl.id = rl.purchase_line_id WHERE pl.invoice_id = p.id) "
        "FROM purchase_invoices p JOIN suppliers s ON s.id = p.supplier_id WHERE p.id = ?", (invoice_id,)).fetchone()
    if not h:
        raise KeyError(invoice_id)
    lines = [InvoiceLine(*r) for r in con.execute(
        "SELECT l.line_no, i.name, i.code, l.dot, l.qty, l.unit_cost, l.line_total "
        "FROM purchase_lines l JOIN items i ON i.id = l.item_id WHERE l.invoice_id = ? ORDER BY l.line_no",
        (invoice_id,))]
    return InvoiceView(h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], h[8] or "", h[9], lines)


def supplier_totals(con, supplier_id: int) -> tuple[int, int]:
    return tuple(con.execute("SELECT count(*), coalesce(sum(total), 0) FROM purchase_invoices WHERE supplier_id = ?",
                             (supplier_id,)).fetchone())
