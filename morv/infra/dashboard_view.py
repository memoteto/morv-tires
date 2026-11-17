"""Read-only aggregates for the home dashboard: period KPIs, alerts, and recent activity."""
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import backup, installment_view, ledger

CASH_ACCOUNTS = ("1110", "1210", "1220", "1230")
OLD_DOT_YEARS = 3
PERIODS = (("today", "اليوم"), ("week", "هذا الأسبوع"), ("month", "هذا الشهر"))


@dataclass(frozen=True)
class Alert:
    tone: str
    kind: str              # "late", "due_today", "old_dot", or "backup"; the UI builds the wording and formats amounts
    count: int
    amount: int
    detail: str             # a customer name, an item name, or the backup message verbatim; "" if none
    nav_key: str | None


@dataclass(frozen=True)
class DashboardView:
    period: str
    sales_total: int
    sales_count: int
    collected_total: int
    due_count: int
    due_amount: int
    cash_balance: int
    chart_days: tuple            # (label_date, total) x7, oldest first
    alerts: tuple
    recent: tuple                 # (number, customer_name, methods, total, sale_date)
    pay_today: tuple              # (method, amount), today only
    top_month: tuple              # (item_name, qty), this month only
    account_balances: tuple[tuple[str, int], ...] = ()


def _period_start(period: str, today: date) -> str:
    if period == "week":
        return (today - timedelta(days=today.weekday())).isoformat()
    if period == "month":
        return today.replace(day=1).isoformat()
    return today.isoformat()


def _sales_in(con: sqlite3.Connection, start: str, end: str) -> tuple[int, int]:
    return con.execute("SELECT coalesce(sum(total), 0), count(*) FROM sales WHERE sale_date BETWEEN ? AND ?",
                       (start, end)).fetchone()


def _collections_in(con: sqlite3.Connection, start: str, end: str) -> tuple:
    rows = con.execute(
        "SELECT l.account_code, sum(l.debit) FROM journal_lines l "
        "JOIN journal_entries e ON e.id = l.entry_id "
        "WHERE e.status = 'posted' AND e.entry_date BETWEEN ? AND ? "
        "AND e.source_type IN ('sale', 'installment_contract', 'installment_payment') "
        "AND l.account_code IN ('1110', '1210', '1220', '1230') AND l.debit > 0 "
        "GROUP BY l.account_code ORDER BY sum(l.debit) DESC, l.account_code", (start, end)).fetchall()
    methods = {code: method for method, code in PAYMENT_ACCOUNT.items()}
    return tuple((methods[code], amount) for code, amount in rows)


def _due_alerts(con: sqlite3.Connection, today_s: str) -> list[Alert]:
    alerts = []
    late = installment_view.list_view(con, filter_key="late", today=today_s)
    if late.kpis.late_count:
        oldest = max((date.fromisoformat(today_s) - date.fromisoformat(r.next_due)).days for r in late.rows)
        alerts.append(Alert("bad", "late", late.kpis.late_count, late.kpis.late_amount,
                            f"أقدمها متأخر {oldest} يومًا", "installments"))
    due_today = installment_view.list_view(con, filter_key="today", today=today_s)
    if due_today.kpis.today_count:
        customers = con.execute(
            "SELECT DISTINCT c.id, cu.name FROM installment_rows r "
            "JOIN installment_contracts c ON c.id = r.contract_id "
            "JOIN customers cu ON cu.id = c.customer_id WHERE r.due_date = ? "
            "AND r.amount > coalesce((SELECT sum(p.amount) FROM installment_payment_lines p "
            "WHERE p.row_id = r.id), 0) + coalesce((SELECT sum(a.amount) "
            "FROM installment_return_allocations a WHERE a.row_id=r.id), 0)", (today_s,)).fetchall()
        detail = customers[0][1] if len(customers) == 1 else ""
        alerts.append(Alert("warn", "due_today", due_today.kpis.today_count, due_today.kpis.today_amount,
                            detail, "installments"))
    return alerts


def _old_dot_alert(con: sqlite3.Connection, today: date) -> Alert | None:
    year = today.year - OLD_DOT_YEARS
    anniversary = date(year, today.month, min(today.day, monthrange(year, today.month)[1]))
    # DOT identifies a week, so wait until the whole manufacturing week has aged.
    sunday = anniversary - timedelta(days=anniversary.isoweekday() % 7)
    iso_year, week, _ = sunday.isocalendar()
    cutoff = (iso_year % 100) * 100 + week
    rows = con.execute(
        "SELECT l.qty_on_hand, i.name FROM stock_lots l JOIN items i ON i.id = l.item_id "
        "WHERE i.kind = 'tire' AND l.qty_on_hand > 0 AND l.mfg_key IS NOT NULL "
        "AND l.mfg_key <= ? ORDER BY l.mfg_key, l.id", (cutoff,)).fetchall()
    count = sum(qty for qty, _ in rows)
    oldest_name = rows[0][1] if rows else ""
    if not count:
        return None
    return Alert("warn", "old_dot", count, 0, oldest_name or "", "inventory")


def get(con: sqlite3.Connection, period: str = "today", today: str | None = None) -> DashboardView:
    today_d = date.fromisoformat(today) if today else date.today()
    today_s = today_d.isoformat()
    start = _period_start(period, today_d)
    sales_total, sales_count = _sales_in(con, start, today_s)
    collected_total = sum(amount for _, amount in _collections_in(con, start, today_s))

    due = installment_view.list_view(con, filter_key="all", today=today_s)
    due_count = sum(row.amount_now > 0 for row in due.rows)
    due_amount = due.kpis.late_amount + due.kpis.today_amount
    account_balances = tuple((code, ledger.account_balance(con, code)) for code in CASH_ACCOUNTS)
    cash_balance = sum(amount for _, amount in account_balances)

    chart_days = []
    for i in range(6, -1, -1):
        d = (today_d - timedelta(days=i)).isoformat()
        total = con.execute("SELECT coalesce(sum(total), 0) FROM sales WHERE sale_date = ?", (d,)).fetchone()[0]
        chart_days.append((d, total))

    alerts = _due_alerts(con, today_s)
    old_dot = _old_dot_alert(con, today_d)
    if old_dot:
        alerts.append(old_dot)
    level, msg = backup.health(con)
    alerts.append(Alert({"ok": "ok", "warn": "warn", "danger": "bad"}[level], "backup", 0, 0, msg, "settings"))

    recent = con.execute(
        "SELECT s.number, coalesce(c.name, 'عميل نقدي'), "
        "(SELECT group_concat(DISTINCT p.method) FROM sale_payments p WHERE p.sale_id = s.id), s.total, s.sale_date "
        "FROM sales s LEFT JOIN customers c ON c.id = s.customer_id ORDER BY s.id DESC LIMIT 5").fetchall()

    pay_today = _collections_in(con, today_s, today_s)

    month_start = today_d.replace(day=1).isoformat()
    top_month = con.execute(
        "SELECT i.name, sum(sl.qty) q FROM sale_lines sl JOIN sales s ON s.id = sl.sale_id "
        "JOIN items i ON i.id = sl.item_id WHERE s.sale_date BETWEEN ? AND ? "
        "GROUP BY sl.item_id ORDER BY q DESC LIMIT 5", (month_start, today_s)).fetchall()

    return DashboardView(period, sales_total, sales_count, collected_total, due_count, due_amount, cash_balance,
                         tuple(chart_days), tuple(alerts), tuple(recent), tuple(pay_today), tuple(top_month), account_balances)
