"""Read-only income statement from posted ledger entries: revenue, cost of sales, expenses, net result."""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

PERIODS = (("month", "هذا الشهر"), ("last_month", "الشهر الماضي"), ("year", "هذه السنة"),
           ("last_year", "السنة الماضية"), ("all", "كل الفترات"))
SUSPENSE_ACCOUNTS = ("2320", "2330")


@dataclass(frozen=True)
class Line:
    code: str
    name: str
    amount: int


@dataclass(frozen=True)
class Statement:
    start: str
    end: str
    revenue: tuple[Line, ...]
    cost_of_sales: tuple[Line, ...]
    expenses: tuple[Line, ...]
    revenue_total: int
    cost_total: int
    gross_profit: int
    expense_total: int
    net_result: int
    suspense: tuple[Line, ...]     # unresolved cash overages: liabilities, never part of the result


def period_range(key: str, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    if key == "month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if key == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat()
    if key == "year":
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    if key == "last_year":
        return date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()
    if key == "all":
        return "0001-01-01", today.isoformat()
    raise ValueError(f"فترة غير معروفة: {key}")


def _sums(con: sqlite3.Connection, start: str, end: str) -> dict[str, tuple[str, int, int]]:
    rows = con.execute(
        "SELECT a.code, a.name, coalesce(sum(l.debit), 0), coalesce(sum(l.credit), 0) FROM accounts a "
        "JOIN journal_lines l ON l.account_code = a.code JOIN journal_entries e ON e.id = l.entry_id "
        "WHERE e.status = 'posted' AND e.entry_date BETWEEN ? AND ? AND a.postable = 1 "
        "AND (a.code LIKE '4%' OR a.code LIKE '5%') GROUP BY a.code ORDER BY a.code", (start, end)).fetchall()
    return {code: (name, debit, credit) for code, name, debit, credit in rows}


def build(con: sqlite3.Connection, start: str, end: str) -> Statement:
    """Revenue is credit minus debit (sales returns reduce it); costs and expenses are debit minus credit."""
    sums = _sums(con, start, end)
    revenue = tuple(Line(c, n, cr - d) for c, (n, d, cr) in sums.items() if c.startswith("4"))
    cost = tuple(Line(c, n, d - cr) for c, (n, d, cr) in sums.items() if c.startswith("51"))
    expenses = tuple(Line(c, n, d - cr) for c, (n, d, cr) in sums.items() if c.startswith("52"))
    revenue_total = sum(line.amount for line in revenue)
    cost_total = sum(line.amount for line in cost)
    expense_total = sum(line.amount for line in expenses)
    suspense = tuple(Line(code, name, -balance) for code, name, balance in con.execute(
        "SELECT a.code, a.name, coalesce(sum(l.debit) - sum(l.credit), 0) FROM accounts a "
        "LEFT JOIN journal_lines l ON l.account_code = a.code "
        "LEFT JOIN journal_entries e ON e.id = l.entry_id AND e.status = 'posted' "
        f"WHERE a.code IN ({','.join('?' * len(SUSPENSE_ACCOUNTS))}) GROUP BY a.code ORDER BY a.code",
        SUSPENSE_ACCOUNTS) if balance)
    return Statement(start, end, revenue, cost, expenses, revenue_total, cost_total, revenue_total - cost_total,
                     expense_total, revenue_total - cost_total - expense_total, suspense)


def table_rows(st: Statement) -> list[tuple]:
    """Flat rows (id, kind, code, name, amount) for a list screen; kind is head, line, total or grand."""
    out: list[tuple] = []

    def add(kind, code, name, amount=None):
        out.append((len(out) + 1, kind, code, name, amount))

    add("head", "", "الإيرادات")
    for line in st.revenue:
        add("line", line.code, line.name, line.amount)
    add("total", "", "صافي الإيرادات", st.revenue_total)
    add("head", "", "تكلفة المبيعات")
    for line in st.cost_of_sales:
        add("line", line.code, line.name, line.amount)
    add("total", "", "إجمالي تكلفة المبيعات", st.cost_total)
    add("total", "", "مجمل الربح", st.gross_profit)
    add("head", "", "المصروفات التشغيلية")
    for line in st.expenses:
        add("line", line.code, line.name, line.amount)
    add("total", "", "إجمالي المصروفات", st.expense_total)
    add("grand", "", "صافي النتيجة", st.net_result)
    if st.suspense:
        add("head", "", "أرصدة معلّقة لا تدخل النتيجة (زيادة نقدية قيد التحقيق)")
        for line in st.suspense:
            add("line", line.code, line.name, line.amount)
    return out


