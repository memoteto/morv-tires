"""Read-only views of instalment contracts, their schedules and invoices still waiting for one."""
import sqlite3
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ContractView:
    id: int
    number: str
    sale_number: str
    customer_name: str
    customer_mobile: str
    contract_date: str
    cash_price: int
    down: int
    financed: int
    rate_bp: int
    markup: int
    months: int
    instalments_total: int
    contract_total: int
    national_id: str
    address: str
    work_place: str
    whatsapp_ok: int
    guarantor_name: str
    rows: tuple


def _like(query: str) -> str:
    return f"%{(query or '').strip()}%"


def list_contracts(con: sqlite3.Connection, query: str = "", limit: int = 300, offset: int = 0) -> list[tuple]:
    return con.execute(
        "SELECT c.id, c.number, cu.name, c.contract_date, c.contract_total, c.months, "
        "(SELECT coalesce(sum(r.amount), 0) FROM installment_rows r WHERE r.contract_id = c.id) "
        "FROM installment_contracts c JOIN customers cu ON cu.id = c.customer_id "
        "WHERE (? = '%%' OR c.number LIKE ? OR cu.name LIKE ? OR cu.mobile LIKE ?) "
        "ORDER BY c.id DESC LIMIT ? OFFSET ?",
        (_like(query), _like(query), _like(query), _like(query), limit, offset)).fetchall()


def count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM installment_contracts").fetchone()[0]


@dataclass(frozen=True)
class CollectRow:
    no: int
    due_date: str
    amount: int
    paid: int
    remaining: int
    status: str
    tone: str


@dataclass(frozen=True)
class CollectionView:
    contract_id: int
    number: str
    sale_number: str
    customer_name: str
    customer_mobile: str
    national_id: str
    monthly: int
    paid_total: int
    remaining_total: int
    overdue_count: int
    overdue_amount: int
    next_due: tuple | None       # (due_date, remaining) of the first open row, or None if settled
    rows: tuple


def collection_view(con: sqlite3.Connection, contract_id: int, today: str | None = None) -> CollectionView:
    """Schedule rows with paid/remaining/status, and the summary the Collect screen shows."""
    today = today or date.today().isoformat()
    head = con.execute(
        "SELECT c.number, s.number, cu.name, coalesce(cu.mobile, ''), c.national_id, c.months, c.instalments_total "
        "FROM installment_contracts c JOIN sales s ON s.id = c.sale_id JOIN customers cu ON cu.id = c.customer_id "
        "WHERE c.id=?", (contract_id,)).fetchone()
    if not head:
        raise LookupError("العقد غير موجود")
    number, sale_number, cust_name, cust_mobile, nid, months, instalments_total = head
    raw = con.execute(
        "SELECT r.id, r.no, r.due_date, r.amount, coalesce((SELECT sum(l.amount) FROM installment_payment_lines l "
        "WHERE l.row_id = r.id), 0) FROM installment_rows r WHERE r.contract_id=? ORDER BY r.no", (contract_id,)
    ).fetchall()
    rows, paid_total, overdue_count, overdue_amount, next_due = [], 0, 0, 0, None
    for _, no, due_date, amount, paid in raw:
        paid_total += paid
        remaining = amount - paid
        if remaining <= 0:
            status, tone = "مدفوع", "ok"
        elif due_date < today:
            days = (date.fromisoformat(today) - date.fromisoformat(due_date)).days
            status = (("جزئي · " if paid else "") + f"متأخر {days} يومًا")
            tone = "danger"
            overdue_count += 1
            overdue_amount += remaining
        elif due_date == today:
            status, tone = "مستحق اليوم", "warn"
        else:
            status, tone = ("مدفوع جزئيًا" if paid else "لم يحن"), ""
        if remaining > 0 and next_due is None:
            next_due = (due_date, remaining)
        rows.append(CollectRow(no, due_date, amount, paid, remaining, status, tone))
    return CollectionView(contract_id, number, sale_number, cust_name, cust_mobile, nid,
                          instalments_total // months, paid_total, instalments_total - paid_total,
                          overdue_count, overdue_amount, next_due, tuple(rows))


def get_contract(con: sqlite3.Connection, contract_id: int) -> ContractView:
    head = con.execute(
        "SELECT c.id, c.number, s.number, cu.name, coalesce(cu.mobile, ''), c.contract_date, c.cash_price, c.down, "
        "c.financed, c.rate_bp, c.markup, c.months, c.instalments_total, c.contract_total, c.national_id, "
        "coalesce(c.address, ''), coalesce(c.work_place, ''), c.whatsapp_ok, coalesce(c.guarantor_name, '') "
        "FROM installment_contracts c JOIN sales s ON s.id = c.sale_id JOIN customers cu ON cu.id = c.customer_id "
        "WHERE c.id=?", (contract_id,)).fetchone()
    if not head:
        raise LookupError("العقد غير موجود")
    rows = con.execute("SELECT no, due_date, amount, principal, markup, balance_after FROM installment_rows "
                       "WHERE contract_id=? ORDER BY no", (contract_id,)).fetchall()
    return ContractView(*head, rows=tuple(rows))


def invoices_without_contract(con: sqlite3.Connection) -> list[tuple]:
    """Invoices sold on instalments whose contract was never approved: they sit on 1320 with no schedule."""
    return con.execute(
        "SELECT s.id, s.number, cu.name, s.sale_date, s.total, cu.mobile FROM sales s "
        "JOIN customers cu ON cu.id = s.customer_id "
        "WHERE EXISTS (SELECT 1 FROM sale_payments p WHERE p.sale_id = s.id AND p.method = 'installment') "
        "AND NOT EXISTS (SELECT 1 FROM installment_contracts c WHERE c.sale_id = s.id) "
        "ORDER BY s.id DESC").fetchall()


def invoice_for_contract(con: sqlite3.Connection, sale_id: int) -> tuple:
    rows = [r for r in invoices_without_contract(con) if r[0] == sale_id]
    if not rows:
        raise LookupError("الفاتورة ليست بانتظار عقد تقسيط")
    return rows[0]
