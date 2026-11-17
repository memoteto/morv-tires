"""Read-only views of instalment contracts, their schedules and invoices still waiting for one."""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


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
        "WHERE l.row_id = r.id), 0), coalesce((SELECT sum(a.amount) FROM installment_return_allocations a "
        "WHERE a.row_id=r.id),0) FROM installment_rows r WHERE r.contract_id=? ORDER BY r.no", (contract_id,)
    ).fetchall()
    rows, paid_total, overdue_count, overdue_amount, next_due = [], 0, 0, 0, None
    for _, no, due_date, original, paid, canceled in raw:
        amount = original - canceled
        paid_total += paid
        remaining = amount - paid
        if remaining <= 0:
            status, tone = ("مُسوّى بالمرتجع" if canceled else "مدفوع"), "ok"
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
                          instalments_total // months, paid_total, sum(r.remaining for r in rows),
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


@dataclass(frozen=True)
class ListRow:
    contract_id: int
    number: str
    customer_name: str
    customer_mobile: str
    next_due: str
    amount_now: int
    status: str
    tone: str
    progress_text: str
    remaining_total: int
    can_collect: bool


@dataclass(frozen=True)
class ListKpis:
    late_count: int
    late_amount: int
    today_count: int
    today_amount: int
    week_count: int
    week_amount: int
    active_count: int
    active_amount: int


@dataclass(frozen=True)
class ListView:
    kpis: ListKpis
    rows: tuple


def _category(overdue_amt: int, today_amt: int, remaining_total: int) -> tuple[str, str, str]:
    if overdue_amt > 0:
        return "متأخر", "danger", "late"
    if today_amt > 0:
        return "مستحق اليوم", "warn", "today"
    if remaining_total <= 0:
        return "مكتمل", "ok", "done"
    return "نشط", "", "active"


def list_view(con: sqlite3.Connection, query: str = "", filter_key: str = "all",
              today: str | None = None) -> ListView:
    """One row per contract with its urgency category; KPI totals cover the query but not filter_key."""
    today = today or date.today().isoformat()
    week_end = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    contracts = con.execute(
        "SELECT c.id, c.number, cu.name, coalesce(cu.mobile, ''), c.instalments_total "
        "FROM installment_contracts c JOIN customers cu ON cu.id = c.customer_id "
        "WHERE (? = '' OR cu.name LIKE ? OR cu.mobile LIKE ? OR c.number LIKE ?) ORDER BY c.id DESC",
        (query, _like(query), _like(query), _like(query))).fetchall()
    by_contract: dict[int, list[tuple]] = {}
    for cid, no, due_date, original, paid, canceled in con.execute(
            "SELECT r.contract_id, r.no, r.due_date, r.amount, coalesce((SELECT sum(l.amount) "
            "FROM installment_payment_lines l WHERE l.row_id = r.id), 0), "
            "coalesce((SELECT sum(a.amount) FROM installment_return_allocations a WHERE a.row_id=r.id),0) "
            "FROM installment_rows r ORDER BY r.contract_id, r.no"):
        by_contract.setdefault(cid, []).append((no, due_date, original - canceled, paid))
    returned = dict(con.execute(
        "SELECT contract_id, sum(principal) FROM installment_return_adjustments GROUP BY contract_id"))
    sale_prices = dict(con.execute("SELECT id, cash_price FROM installment_contracts"))

    late_count = late_amount = today_count = today_amount = 0
    week_count = week_amount = active_count = active_amount = 0
    result = []
    for cid, number, name, mobile, _instalments_total in contracts:
        rows = by_contract.get(cid, [])
        remaining_total = sum(a - p for _, _, a, p in rows)
        overdue_amt = sum(a - p for _, d, a, p in rows if a > p and d < today)
        due_today_amt = sum(a - p for _, d, a, p in rows if a > p and d == today)
        due_week_amt = sum(a - p for _, d, a, p in rows if a > p and today < d <= week_end)
        next_open = next(((d, a - p) for _, d, a, p in rows if a > p), None)
        status, tone, cat = _category(overdue_amt, due_today_amt, remaining_total)
        if returned.get(cid, 0) >= sale_prices[cid]:
            status, tone, cat = "مرتجع بالكامل", "ok", "done"
        paid_rows = sum(1 for _, _, a, p in rows if a > 0 and p >= a)

        if overdue_amt > 0:
            late_count += 1
            late_amount += overdue_amt
        if due_today_amt > 0:
            today_count += 1
            today_amount += due_today_amt
        if due_week_amt > 0:
            week_count += 1
            week_amount += due_week_amt
        if remaining_total > 0:
            active_count += 1
            active_amount += remaining_total

        matches = (filter_key == "all" or filter_key == cat
                   or (filter_key == "week" and due_week_amt > 0))
        if matches:
            result.append(ListRow(
                cid, number, name, mobile, next_open[0] if next_open else "",
                overdue_amt + due_today_amt, status, tone, f"{paid_rows} من {len(rows)} قسطًا",
                remaining_total, remaining_total > 0))

    kpis = ListKpis(late_count, late_amount, today_count, today_amount,
                    week_count, week_amount, active_count, active_amount)
    return ListView(kpis, tuple(result))


def invoices_without_contract(con: sqlite3.Connection) -> list[tuple]:
    """Invoices sold on instalments whose contract was never approved: they sit on 1320 with no schedule."""
    return con.execute(
        "SELECT s.id, s.number, cu.name, s.sale_date, s.total, cu.mobile FROM sales s "
        "JOIN customers cu ON cu.id = s.customer_id "
        "WHERE EXISTS (SELECT 1 FROM sale_payments p WHERE p.sale_id = s.id AND p.method = 'installment') "
        "AND NOT EXISTS (SELECT 1 FROM installment_contracts c WHERE c.sale_id = s.id) "
        "AND NOT EXISTS (SELECT 1 FROM sale_returns r WHERE r.sale_id = s.id) "
        "ORDER BY s.id DESC").fetchall()


def invoice_for_contract(con: sqlite3.Connection, sale_id: int) -> tuple:
    rows = [r for r in invoices_without_contract(con) if r[0] == sale_id]
    if not rows:
        raise LookupError("الفاتورة ليست بانتظار عقد تقسيط")
    return rows[0]
