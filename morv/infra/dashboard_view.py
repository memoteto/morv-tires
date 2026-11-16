"""Read-only aggregates for the home dashboard: period KPIs, alerts, and recent activity."""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

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


def _collected_in(con: sqlite3.Connection, start: str, end: str) -> int:
    return con.execute(
        "SELECT coalesce(sum(p.amount), 0) FROM sale_payments p JOIN sales s ON s.id = p.sale_id "
        "WHERE s.sale_date BETWEEN ? AND ? AND p.method != 'credit'", (start, end)).fetchone()[0]


def _due_alerts(con: sqlite3.Connection, today_s: str) -> list[Alert]:
    alerts = []
    late = installment_view.list_view(con, filter_key="late", today=today_s)
    if late.kpis.late_count:
        oldest = max((date.fromisoformat(today_s) - date.fromisoformat(r.next_due)).days for r in late.rows)
        alerts.append(Alert("bad", "late", late.kpis.late_count, late.kpis.late_amount,
                            f"أقدمها متأخر {oldest} يومًا", "installments"))
    due_today = installment_view.list_view(con, filter_key="today", today=today_s)
    if due_today.kpis.today_count:
        detail = due_today.rows[0].customer_name if due_today.kpis.today_count == 1 else ""
        alerts.append(Alert("warn", "due_today", due_today.kpis.today_count, due_today.kpis.today_amount,
                            detail, "installments"))
    return alerts


def _old_dot_alert(con: sqlite3.Connection, today: date) -> Alert | None:
    cutoff = (today.year - OLD_DOT_YEARS) * 100 + 53
    row = con.execute(
        "SELECT count(*), (SELECT i2.name FROM stock_lots l2 JOIN items i2 ON i2.id = l2.item_id "
        "WHERE l2.qty_on_hand > 0 AND l2.mfg_key IS NOT NULL AND l2.mfg_key <= ? ORDER BY l2.mfg_key LIMIT 1) "
        "FROM stock_lots l JOIN items i ON i.id = l.item_id "
        "WHERE l.qty_on_hand > 0 AND l.mfg_key IS NOT NULL AND l.mfg_key <= ?", (cutoff, cutoff)).fetchone()
    count, oldest_name = row
    if not count:
        return None
    return Alert("warn", "old_dot", count, 0, oldest_name or "", "inventory")


def get(con: sqlite3.Connection, period: str = "today", today: str | None = None) -> DashboardView:
    today_d = date.fromisoformat(today) if today else date.today()
    today_s = today_d.isoformat()
    start = _period_start(period, today_d)
    sales_total, sales_count = _sales_in(con, start, today_s)
    collected_total = _collected_in(con, start, today_s)

    due = installment_view.list_view(con, filter_key="all", today=today_s)
    due_count = due.kpis.late_count + due.kpis.today_count
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

    pay_today = con.execute(
        "SELECT p.method, sum(p.amount) FROM sale_payments p JOIN sales s ON s.id = p.sale_id "
        "WHERE s.sale_date = ? GROUP BY p.method ORDER BY sum(p.amount) DESC", (today_s,)).fetchall()

    month_start = today_d.replace(day=1).isoformat()
    top_month = con.execute(
        "SELECT i.name, sum(sl.qty) q FROM sale_lines sl JOIN sales s ON s.id = sl.sale_id "
        "JOIN items i ON i.id = sl.item_id WHERE s.sale_date BETWEEN ? AND ? "
        "GROUP BY sl.item_id ORDER BY q DESC LIMIT 5", (month_start, today_s)).fetchall()

    return DashboardView(period, sales_total, sales_count, collected_total, due_count, due_amount, cash_balance,
                         tuple(chart_days), tuple(alerts), tuple(recent), tuple(pay_today), tuple(top_month), account_balances)
