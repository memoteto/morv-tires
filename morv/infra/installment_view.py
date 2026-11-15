"""Read-only views of instalment contracts, their schedules and invoices still waiting for one."""
import sqlite3
from dataclasses import dataclass


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
        "SELECT s.id, s.number, cu.name, s.sale_date, s.total FROM sales s "
        "JOIN customers cu ON cu.id = s.customer_id "
        "WHERE EXISTS (SELECT 1 FROM sale_payments p WHERE p.sale_id = s.id AND p.method = 'installment') "
        "AND NOT EXISTS (SELECT 1 FROM installment_contracts c WHERE c.sale_id = s.id) "
        "ORDER BY s.id DESC").fetchall()


def invoice_for_contract(con: sqlite3.Connection, sale_id: int) -> tuple:
    rows = [r for r in invoices_without_contract(con) if r[0] == sale_id]
    if not rows:
        raise LookupError("الفاتورة ليست بانتظار عقد تقسيط")
    return rows[0]


def due_instalments(con: sqlite3.Connection, only_unpaid: bool = True) -> list[tuple]:
    """Every instalment with its contract, customer and what is still owed on it, earliest due first."""
    rows = con.execute(
        "SELECT r.id, c.id, c.number, cu.name, coalesce(cu.mobile, ''), r.no, r.due_date, r.amount, "
        "r.amount - coalesce((SELECT sum(a.amount) FROM installment_allocations a WHERE a.row_id = r.id), 0) "
        "FROM installment_rows r JOIN installment_contracts c ON c.id = r.contract_id "
        "JOIN customers cu ON cu.id = c.customer_id ORDER BY r.due_date, r.no").fetchall()
    return [r for r in rows if r[8] > 0] if only_unpaid else rows


def contract_collections(con: sqlite3.Connection, contract_id: int) -> list[tuple]:
    return con.execute(
        "SELECT number, collect_date, amount, method FROM installment_collections "
        "WHERE contract_id=? ORDER BY id", (contract_id,)).fetchall()
