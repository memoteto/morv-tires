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


_KIND = {                                    # A fully returned contract reads differently from one paid off
    "returned": ("مرتجع بالكامل", "ok", "done"),
    "late": ("متأخر", "danger", "late"),
    "today": ("مستحق اليوم", "warn", "today"),
    "done": ("مكتمل", "ok", "done"),
    "active": ("نشط", "", "active"),
}

# Per-row sums are grouped once rather than re-read per aggregate, and the schedule is reached
# through installment_rows(contract_id, no), so a search touches only the contracts it matched.
_NET = "(r.amount - coalesce(canc.amt, 0) - coalesce(paid.amt, 0))"
_DUE = "(r.amount - coalesce(canc.amt, 0))"

_AGG_CTE = f"""
WITH paid AS (SELECT row_id, sum(amount) AS amt FROM installment_payment_lines GROUP BY row_id),
canc AS (SELECT row_id, sum(amount) AS amt FROM installment_return_allocations GROUP BY row_id),
back AS (SELECT contract_id, sum(principal) AS principal
         FROM installment_return_adjustments GROUP BY contract_id),
agg AS (
  SELECT c.id AS id, c.number AS number, cu.name AS cname, coalesce(cu.mobile, '') AS cmobile,
         coalesce(sum({_NET}), 0) AS remaining_total,
         coalesce(sum(CASE WHEN {_DUE} > coalesce(paid.amt, 0) AND r.due_date < :today
                           THEN {_NET} ELSE 0 END), 0) AS overdue_amt,
         coalesce(sum(CASE WHEN {_DUE} > coalesce(paid.amt, 0) AND r.due_date = :today
                           THEN {_NET} ELSE 0 END), 0) AS today_amt,
         coalesce(sum(CASE WHEN {_DUE} > coalesce(paid.amt, 0)
                           AND r.due_date > :today AND r.due_date <= :week
                           THEN {_NET} ELSE 0 END), 0) AS week_amt,
         count(r.no) AS row_count,
         coalesce(sum(CASE WHEN {_DUE} > 0 AND coalesce(paid.amt, 0) >= {_DUE}
                           THEN 1 ELSE 0 END), 0) AS paid_rows,
         -- The zero padded number sorts numerically, so the min carries the first open row's date
         min(CASE WHEN {_DUE} > coalesce(paid.amt, 0)
                  THEN printf('%08d|', r.no) || r.due_date END) AS next_tag,
         max(CASE WHEN coalesce(back.principal, 0) >= c.cash_price THEN 1 ELSE 0 END) AS all_returned
  FROM installment_contracts c
  JOIN customers cu ON cu.id = c.customer_id
  LEFT JOIN installment_rows r ON r.contract_id = c.id
  LEFT JOIN paid ON paid.row_id = r.id
  LEFT JOIN canc ON canc.row_id = r.id
  LEFT JOIN back ON back.contract_id = c.id
  WHERE (:q = '' OR cu.name LIKE :like OR cu.mobile LIKE :like OR c.number LIKE :like)
  GROUP BY c.id
),
shaped AS (
  SELECT agg.*, CASE WHEN all_returned = 1 THEN 'returned'
                     WHEN overdue_amt > 0 THEN 'late'
                     WHEN today_amt > 0 THEN 'today'
                     WHEN remaining_total <= 0 THEN 'done'
                     ELSE 'active' END AS kind,
         -- Window totals are taken before the category filter, so KPIs cover the whole search
         coalesce(sum(overdue_amt > 0) OVER (), 0) AS k_late_count,
         coalesce(sum(CASE WHEN overdue_amt > 0 THEN overdue_amt ELSE 0 END) OVER (), 0) AS k_late_amount,
         coalesce(sum(today_amt > 0) OVER (), 0) AS k_today_count,
         coalesce(sum(CASE WHEN today_amt > 0 THEN today_amt ELSE 0 END) OVER (), 0) AS k_today_amount,
         coalesce(sum(week_amt > 0) OVER (), 0) AS k_week_count,
         coalesce(sum(CASE WHEN week_amt > 0 THEN week_amt ELSE 0 END) OVER (), 0) AS k_week_amount,
         coalesce(sum(remaining_total > 0) OVER (), 0) AS k_active_count,
         coalesce(sum(CASE WHEN remaining_total > 0 THEN remaining_total ELSE 0 END) OVER (), 0)
             AS k_active_amount
  FROM agg
)
"""

_KPI_SQL = _AGG_CTE + """
SELECT coalesce(sum(overdue_amt > 0), 0), coalesce(sum(CASE WHEN overdue_amt > 0 THEN overdue_amt ELSE 0 END), 0),
       coalesce(sum(today_amt > 0), 0), coalesce(sum(CASE WHEN today_amt > 0 THEN today_amt ELSE 0 END), 0),
       coalesce(sum(week_amt > 0), 0), coalesce(sum(CASE WHEN week_amt > 0 THEN week_amt ELSE 0 END), 0),
       coalesce(sum(remaining_total > 0), 0),
       coalesce(sum(CASE WHEN remaining_total > 0 THEN remaining_total ELSE 0 END), 0)
FROM agg
"""

_PAGE_SQL = _AGG_CTE + """
SELECT id, number, cname, cmobile, next_tag, overdue_amt, today_amt, kind, paid_rows, row_count,
       remaining_total, k_late_count, k_late_amount, k_today_count, k_today_amount,
       k_week_count, k_week_amount, k_active_count, k_active_amount
FROM shaped
WHERE :fk = 'all' OR (:fk = 'week' AND week_amt > 0)
      OR :fk = (CASE WHEN kind = 'returned' THEN 'done' ELSE kind END)
ORDER BY id DESC LIMIT :limit OFFSET :offset
"""


def list_view(con: sqlite3.Connection, query: str = "", filter_key: str = "all", today: str | None = None,
              limit: int | None = None, offset: int = 0) -> ListView:
    """One row per contract with its urgency category; KPIs cover the whole query, not the page."""
    today = today or date.today().isoformat()
    args = {"q": query, "like": _like(query), "today": today,
            "week": (date.fromisoformat(today) + timedelta(days=7)).isoformat()}
    page = con.execute(_PAGE_SQL, {**args, "fk": filter_key,
                                   "limit": -1 if limit is None else limit, "offset": offset}).fetchall()
    rows = []
    for cid, number, name, mobile, next_tag, overdue_amt, today_amt, kind, paid_rows, row_count, remaining, *_k \
            in page:
        status, tone, _cat = _KIND[kind]
        rows.append(ListRow(cid, number, name, mobile, next_tag.split("|", 1)[1] if next_tag else "",
                            overdue_amt + today_amt, status, tone, f"{paid_rows} من {row_count} قسطًا",
                            remaining, remaining > 0))
    # The page carries the window totals; only an empty page has to ask for them on their own
    kpis = ListKpis(*page[0][11:]) if page else ListKpis(*con.execute(_KPI_SQL, args).fetchone())
    return ListView(kpis, tuple(rows))


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
