"""Report catalog: each report builds one uniform read-only Table from posted ledger data."""
import csv
import sqlite3
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, timedelta

from morv.core.money import fmt
from morv.infra import income_statement, ledger, suspended_balances

TEXT, CODE, MONEY, DATE = "text", "code", "money", "date"
KEYS = ("income", "sales_profit", "trial", "ledger", "aging", "partner", "suspended")
TITLES = {"income": "قائمة الدخل", "sales_profit": "المبيعات وربحية الأصناف", "trial": "ميزان المراجعة",
          "ledger": "كشف حساب", "aging": "أعمار ديون التقسيط", "partner": "كشف حساب الشريك",
          "suspended": "الأرصدة المعلّقة"}
NEEDS_ACCOUNT = {"ledger", "partner"}   # reports that need one choice from param_choices


@dataclass(frozen=True)
class Table:
    key: str
    title: str
    subtitle: str
    headers: tuple[str, ...]
    kinds: tuple[str, ...]                   # one of TEXT/CODE/MONEY/DATE per column
    rows: tuple[tuple[str, tuple], ...]      # (row kind: head/line/total/grand, cells)
    stretch: int = 0                         # the column that takes the spare width


class ReportError(ValueError):
    pass


def account_choices(con: sqlite3.Connection) -> list[tuple[str, str]]:
    return list(con.execute("SELECT code, code || ' ' || name FROM accounts WHERE postable = 1 ORDER BY code"))


def param_choices(con: sqlite3.Connection, key: str) -> list[tuple[str, str]]:
    if key == "partner":
        return list(con.execute("SELECT current_account, name FROM partners ORDER BY name"))
    return account_choices(con)


def _range(start: str, end: str) -> tuple[str, str]:
    try:
        start, end = date.fromisoformat((start or "").strip()).isoformat(), date.fromisoformat((end or "").strip()).isoformat()
    except ValueError as e:
        raise ReportError("تاريخ غير صالح؛ اكتبه بصيغة YYYY-MM-DD") from e
    if start > end:
        raise ReportError("تاريخ البداية بعد تاريخ النهاية")
    return start, end


def previous_range(start: str, end: str) -> tuple[str, str]:
    """Same span one calendar month or year earlier when the range sits in one month or year; else shifted back."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if s.year < 2:
        raise ReportError("لا توجد فترة سابقة لهذه الفترة")
    if s.day == 1 and (e.year, e.month) == (s.year, s.month):
        last_prev = s - timedelta(days=1)
        return last_prev.replace(day=1).isoformat(), last_prev.replace(day=min(e.day, last_prev.day)).isoformat()
    if (s.month, s.day) == (1, 1) and e.year == s.year:
        py = s.year - 1
        return date(py, 1, 1).isoformat(), date(py, e.month, min(e.day, monthrange(py, e.month)[1])).isoformat()
    span = (e - s).days
    prev_end = s - timedelta(days=1)
    return (prev_end - timedelta(days=span)).isoformat(), prev_end.isoformat()


def _aligned(current: income_statement.Statement, prior: income_statement.Statement):
    """Both statements with the union of accounts, so their flat rows line up one to one."""
    def merge(a, b):
        names = {line.code: line.name for line in (*a, *b)}
        amounts = {line.code: line.amount for line in a}
        return tuple(income_statement.Line(c, names[c], amounts.get(c, 0)) for c in sorted(names))
    out = []
    for st, other in ((current, prior), (prior, current)):
        out.append(replace(st, revenue=merge(st.revenue, other.revenue),
                           cost_of_sales=merge(st.cost_of_sales, other.cost_of_sales),
                           expenses=merge(st.expenses, other.expenses)))
    return out


def income(con, start: str, end: str, compare: bool = False) -> Table:
    start, end = _range(start, end)
    current = income_statement.build(con, start, end)
    if not compare:
        rows = tuple((kind, (code, name, amount)) for _i, kind, code, name, amount
                     in income_statement.table_rows(current))
        return Table("income", TITLES["income"], f"من {start} إلى {end}", ("الحساب", "البند", "المبلغ"),
                     (CODE, TEXT, MONEY), rows, 1)
    p_start, p_end = previous_range(start, end)
    cur, prev = _aligned(current, income_statement.build(con, p_start, p_end))
    rows, in_suspense = [], False
    for a, b in zip(income_statement.table_rows(cur), income_statement.table_rows(prev), strict=True):
        _i, kind, code, name, amount = a
        in_suspense = in_suspense or name.startswith("أرصدة معلّقة")
        if amount is None:
            rows.append((kind, (code, name, None, None, None, "")))
        elif in_suspense and kind == "line":
            rows.append((kind, (code, name, amount, None, None, "")))      # balances, not period activity
        else:
            before = b[4]
            rows.append((kind, (code, name, amount, before, amount - before, _bp_text(amount - before, abs(before)))))
    return Table("income", TITLES["income"], f"من {start} إلى {end} مقارنة بـ {p_start} إلى {p_end}",
                 ("الحساب", "البند", "الفترة", "الفترة السابقة", "التغيّر", "النسبة"),
                 (CODE, TEXT, MONEY, MONEY, MONEY, CODE), tuple(rows), 1)


def trial_balance(con, end: str) -> Table:
    """Debit and credit totals per account up to the end date; both totals must be equal."""
    _s, end = _range("0001-01-01", end)
    data = con.execute(
        "SELECT a.code, a.name, coalesce(sum(l.debit), 0), coalesce(sum(l.credit), 0) FROM accounts a "
        "JOIN journal_lines l ON l.account_code = a.code JOIN journal_entries e ON e.id = l.entry_id "
        "WHERE e.status = 'posted' AND e.entry_date <= ? GROUP BY a.code ORDER BY a.code", (end,)).fetchall()
    rows, debit_total, credit_total = [], 0, 0
    for code, name, debit, credit in data:
        net = debit - credit
        d, c = (net, 0) if net > 0 else (0, -net)
        rows.append(("line", (code, name, d, c)))
        debit_total, credit_total = debit_total + d, credit_total + c
    rows.append(("grand", ("", "الإجمالي", debit_total, credit_total)))
    note = "متوازن" if debit_total == credit_total else f"غير متوازن: الفرق {fmt(debit_total - credit_total)}"
    return Table("trial", TITLES["trial"], f"حتى {end} · {note}", ("الحساب", "اسم الحساب", "مدين", "دائن"),
                 (CODE, TEXT, MONEY, MONEY), tuple(rows), 1)


def account_statement(con, account: str, start: str, end: str) -> Table:
    start, end = _range(start, end)
    acc = con.execute("SELECT code, name, normal_side FROM accounts WHERE code = ? AND postable = 1",
                      (account,)).fetchone()
    if acc is None:
        raise ReportError("اختر حسابًا")
    sign = 1 if acc[2] == "debit" else -1             # the balance is shown on the account's normal side
    opening = sign * con.execute(
        "SELECT coalesce(sum(l.debit) - sum(l.credit), 0) FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id WHERE e.status = 'posted' AND l.account_code = ? AND e.entry_date < ?",
        (account, start)).fetchone()[0]
    rows = [("head", ("", "", "رصيد افتتاحي", 0, 0, opening))]
    balance, debit_total, credit_total = opening, 0, 0
    for day, doc, memo, debit, credit in con.execute(
            "SELECT e.entry_date, e.source_type || '#' || e.source_id, coalesce(e.memo, ''), l.debit, l.credit "
            "FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id WHERE e.status = 'posted' "
            "AND l.account_code = ? AND e.entry_date BETWEEN ? AND ? ORDER BY e.entry_date, e.id, l.line_no",
            (account, start, end)):
        balance += sign * (debit - credit)
        debit_total, credit_total = debit_total + debit, credit_total + credit
        rows.append(("line", (day, doc, memo, debit, credit, balance)))
    rows.append(("grand", ("", "", "الإجمالي والرصيد الختامي", debit_total, credit_total, balance)))
    return Table("ledger", f"كشف حساب {acc[0]} {acc[1]}", f"من {start} إلى {end}",
                 ("التاريخ", "المستند", "البيان", "مدين", "دائن", "الرصيد"),
                 (DATE, CODE, TEXT, MONEY, MONEY, MONEY), tuple(rows), 2)


def suspended(con) -> Table:
    r = suspended_balances.build(con)
    rows: list[tuple[str, tuple]] = [("head", ("", "", "", f"عجز غير مسوّى (مصروف): {fmt(r.shortage_total)}"))]
    rows += [("line", (d.day, d.source, d.number, f"{fmt(d.amount)} · {d.memo}")) for d in r.shortages]
    rows.append(("head", ("", "", "", f"زيادة قيد التحقيق (التزام): {fmt(r.overage_total)}")))
    rows += [("line", (d.day, d.source, d.number, f"{fmt(d.amount)} · {d.memo}")) for d in r.overages]
    rows.append(("head", ("", "", "", "شركاء جاريهم مدين: " + ("لا يوجد" if not r.partner_debts else ""))))
    rows += [("line", ("", "جاري شريك", d.name, fmt(d.amount))) for d in r.partner_debts]
    note = ""
    if r.shortage_drift or r.overage_drift:
        note = f" · تحذير: المستندات لا تطابق الدفتر (عجز {fmt(r.shortage_drift)}، زيادة {fmt(r.overage_drift)})"
    return Table("suspended", TITLES["suspended"], "للقراءة فقط" + note, ("التاريخ", "المصدر", "المرجع", "التفاصيل"),
                 (DATE, TEXT, CODE, TEXT), tuple(rows), 3)


def _bp_text(part: int, whole: int) -> str:
    """Percentage with one decimal from integers, empty when there is no base."""
    if whole <= 0:
        return ""
    tenths = (abs(part) * 1000 + whole // 2) // whole
    return f"{'-' if part < 0 else ''}{tenths // 10}.{tenths % 10}%"


def sales_profit(con, start: str, end: str) -> Table:
    """Per item: sales and returns against the real stock cost; services carry no cost."""
    start, end = _range(start, end)
    items: dict[int, list] = {}

    def slot(item_id, code, name):
        return items.setdefault(item_id, [code, name, 0, 0, 0, 0, 0])      # qty, sales, returns, cost, returned cost

    for item_id, code, name, qty, net in con.execute(
            "SELECT i.id, i.code, i.name, sum(sl.qty), sum(sl.net_total) FROM sale_lines sl JOIN sales s "
            "ON s.id = sl.sale_id JOIN items i ON i.id = sl.item_id WHERE s.sale_date BETWEEN ? AND ? "
            "GROUP BY i.id", (start, end)):
        row = slot(item_id, code, name)
        row[2], row[3] = qty, net
    for item_id, cost in con.execute(
            "SELECT si.item_id, sum(si.cost_total) FROM stock_issues si JOIN sales s ON s.id = si.source_id "
            "WHERE si.source_type = 'sale' AND s.sale_date BETWEEN ? AND ? GROUP BY si.item_id", (start, end)):
        if item_id in items:
            items[item_id][5] = cost
    for item_id, code, name, qty, value, cost in con.execute(
            "SELECT i.id, i.code, i.name, sum(rl.qty), sum(rl.value), sum(rl.cost) FROM sale_return_lines rl "
            "JOIN sale_returns r ON r.id = rl.return_id JOIN sale_lines sl ON sl.id = rl.sale_line_id "
            "JOIN items i ON i.id = sl.item_id WHERE r.return_date BETWEEN ? AND ? GROUP BY i.id", (start, end)):
        row = slot(item_id, code, name)
        row[2] -= qty
        row[4] += value
        row[6] += cost
    rows, totals = [], [0, 0, 0, 0]
    for code, name, qty, sales, returns, cost, returned_cost in sorted(items.values()):
        net_sales, net_cost = sales - returns, cost - returned_cost
        profit = net_sales - net_cost
        rows.append(("line", (code, name, str(qty), sales, returns, net_cost, profit, _bp_text(profit, net_sales))))
        totals = [totals[0] + sales, totals[1] + returns, totals[2] + net_cost, totals[3] + profit]
    rows.append(("grand", ("", "الإجمالي", "", totals[0], totals[1], totals[2], totals[3],
                           _bp_text(totals[3], totals[0] - totals[1]))))
    return Table("sales_profit", TITLES["sales_profit"],
                 f"من {start} إلى {end} · التكلفة من تخصيص طبقات المخزون الفعلية",
                 ("الكود", "الصنف", "الكمية الصافية", "المبيعات", "المرتجعات", "التكلفة", "الربح", "الهامش"),
                 (CODE, TEXT, CODE, MONEY, MONEY, MONEY, MONEY, CODE), tuple(rows), 1)


BUCKETS = ((0, "لم يحل"), (30, "1-30 يومًا"), (60, "31-60 يومًا"), (90, "61-90 يومًا"), (None, "أكثر من 90"))


def installment_aging(con, end: str) -> Table:
    """Unpaid instalment rows as of the end date, per contract, by how many days past due they are."""
    _s, end = _range("0001-01-01", end)
    today = date.fromisoformat(end)
    per: dict[str, list] = {}
    for number, name, due, remaining in con.execute(
            "SELECT c.number, cu.name, r.due_date, r.amount - "
            "coalesce((SELECT sum(l.amount) FROM installment_payment_lines l JOIN installment_payments p "
            "ON p.id = l.payment_id WHERE l.row_id = r.id AND p.payment_date <= ?), 0) - "
            "coalesce((SELECT sum(a.amount) FROM installment_return_allocations a JOIN sale_returns sr "
            "ON sr.id = a.return_id WHERE a.row_id = r.id AND sr.return_date <= ?), 0) "
            "FROM installment_rows r JOIN installment_contracts c ON c.id = r.contract_id "
            "JOIN customers cu ON cu.id = c.customer_id WHERE c.contract_date <= ? ORDER BY c.number, r.no",
            (end, end, end)):
        if remaining <= 0:
            continue
        late = (today - date.fromisoformat(due)).days
        idx = next(i for i, (limit, _t) in enumerate(BUCKETS) if limit is None or late <= limit)
        row = per.setdefault(number, [name, [0] * len(BUCKETS)])
        row[1][idx] += remaining
    rows = [("line", (number, name, *buckets, sum(buckets))) for number, (name, buckets) in per.items()]
    column_totals = [sum(r[1][2 + i] for r in rows) for i in range(len(BUCKETS) + 1)]
    rows.append(("grand", ("", "الإجمالي", *column_totals)))
    return Table("aging", TITLES["aging"], f"حتى {end} · حسب عمر القسط المستحق",
                 ("العقد", "العميل", *(t for _l, t in BUCKETS), "الإجمالي"),
                 (CODE, TEXT, *([MONEY] * (len(BUCKETS) + 1))), tuple(rows), 1)


def partner_statement(con, account: str, start: str, end: str) -> Table:
    """The partner's current account: a positive balance is owed to the partner by the shop."""
    row = con.execute("SELECT name, capital_account FROM partners WHERE current_account = ?", (account,)).fetchone()
    if row is None:
        raise ReportError("اختر شريكًا")
    table = account_statement(con, account, start, end)
    capital = -ledger.account_balance(con, row[1])
    return Table("partner", f"كشف حساب الشريك {row[0]}",
                 f"{table.subtitle} · رأس المال {fmt(capital)} · الرصيد الموجب مستحق للشريك على المحل",
                 table.headers, table.kinds, table.rows, table.stretch)


def build(con, key: str, start: str, end: str, account: str = "", compare: bool = False) -> Table:
    if key == "income":
        return income(con, start, end, compare)
    if key == "trial":
        return trial_balance(con, end)
    if key == "ledger":
        return account_statement(con, account, start, end)
    if key == "sales_profit":
        return sales_profit(con, start, end)
    if key == "aging":
        return installment_aging(con, end)
    if key == "partner":
        return partner_statement(con, account, start, end)
    if key == "suspended":
        return suspended(con)
    raise ReportError("تقرير غير معروف")


def _cell(value, kind: str) -> str:
    return fmt(value) if kind == MONEY and type(value) is int else ("" if value is None else str(value))


def to_csv(table: Table, path: str) -> None:
    """UTF-8 with BOM so Excel opens Arabic correctly; money as plain pounds with two decimals."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([table.title])
        w.writerow([table.subtitle])
        w.writerow(table.headers)
        for _kind, cells in table.rows:
            w.writerow([_cell(v, k).replace(",", "") if k == MONEY else _cell(v, k)
                        for v, k in zip(cells, table.kinds, strict=True)])
