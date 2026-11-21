"""Report catalog: each report builds one uniform read-only Table from posted ledger data."""
import csv
import sqlite3
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, timedelta

from morv.core.money import fmt
from morv.infra import customer_receipts, income_statement, ledger, suspended_balances

TEXT, CODE, MONEY, DATE = "text", "code", "money", "date"
KEYS = ("income", "daily_sales", "top_items", "sales_profit", "slow_moving", "journal", "trial", "ledger",
        "aging", "credit_aging", "suppliers", "partner", "suspended", "stock_valuation", "expenses", "collections",
        "purchases", "returns", "supplier_aging", "item_card")
TITLES = {"income": "قائمة الدخل", "daily_sales": "ملخص المبيعات اليومي", "top_items": "الأصناف الأكثر مبيعًا",
          "sales_profit": "المبيعات وربحية الأصناف", "slow_moving": "الأصناف بطيئة الحركة",
          "journal": "دفتر اليومية", "trial": "ميزان المراجعة", "ledger": "كشف حساب",
          "aging": "أعمار ديون التقسيط", "credit_aging": "أعمار البيع الآجل",
          "suppliers": "أرصدة الموردين", "partner": "كشف حساب الشريك",
          "suspended": "الأرصدة المعلّقة", "stock_valuation": "تقييم المخزون", "expenses": "المصروفات حسب البند",
          "collections": "المقبوضات", "purchases": "المشتريات حسب المورد والصنف", "returns": "مرتجعات البيع",
          "supplier_aging": "أعمار مستحقات الموردين", "item_card": "كارت الصنف"}
NEEDS_PARAM = {"ledger": "account", "partner": "partner", "slow_moving": "days", "item_card": "item"}   # one choice from param_choices
IDLE_DAYS = (30, 60, 90, 180, 365)
DEFAULT_IDLE_DAYS = 90
JOURNAL_LIMIT = 3000


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
    if key == "slow_moving":
        return [(str(d), f"{d} يومًا") for d in IDLE_DAYS]
    if key == "item_card":
        return [(str(i), f"{c} · {n}") for i, c, n in con.execute(
            "SELECT id, code, name FROM items WHERE kind IN ('tire', 'battery') ORDER BY code")]
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
    """Same span a month or year earlier when inside one; otherwise an equal span just before."""
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


def credit_aging(con, end: str) -> Table:
    """Open credit invoices per customer by days past due; receipts settle the oldest first."""
    _s, end = _range("0001-01-01", end)
    today = date.fromisoformat(end)
    invoices: dict[int, list] = {}      # customer -> [name, [[sale_id, due, open]]]
    by_sale: dict[int, list] = {}
    for sid, cid, name, day, days, amount in con.execute(
            "SELECT s.id, s.customer_id, cu.name, s.sale_date, cu.credit_days, sum(p.amount) FROM sales s "
            "JOIN sale_payments p ON p.sale_id = s.id AND p.method = 'credit' JOIN customers cu ON cu.id = s.customer_id "
            "WHERE s.sale_date <= ? GROUP BY s.id ORDER BY s.sale_date, s.id", (end,)):
        due = (date.fromisoformat(day) + timedelta(days=days)).isoformat()
        entry = [sid, due, amount]
        invoices.setdefault(cid, [name, []])[1].append(entry)
        by_sale[sid] = entry
    for cid, name, day, days, amount in con.execute(
            "SELECT cu.id, cu.name, o.opening_date, cu.credit_days, o.amount FROM active_opening_lines o "
            "JOIN customers cu ON cu.id = o.customer_id WHERE o.opening_date <= ?", (end,)):
        due = (date.fromisoformat(day) + timedelta(days=days)).isoformat()
        invoices.setdefault(cid, [name, []])[1].insert(0, [None, due, amount])   # the opening is the oldest debt
    for sid, refunded in con.execute(
            f"SELECT r.sale_id, sum({customer_receipts.CREDIT_PART}) FROM sale_returns r WHERE r.return_date <= ? "
            "GROUP BY r.sale_id", (end,)):
        if sid in by_sale:
            by_sale[sid][2] -= min(refunded, by_sale[sid][2])
    settled: dict[int, int] = {}
    for cid, net in con.execute(
            "SELECT customer_id, sum(CASE kind WHEN 'receipt' THEN amount ELSE -amount END) "
            "FROM customer_receipts WHERE receipt_date <= ? GROUP BY customer_id", (end,)):
        settled[cid] = net
    for cid, net in con.execute(
            "SELECT d.customer_id, sum(CASE WHEN d.kind = 'apply' THEN d.amount WHEN d.kind = 'reversal' "
            "AND o.kind = 'apply' THEN -d.amount ELSE 0 END) FROM customer_deposits d "
            "LEFT JOIN customer_deposits o ON o.id = d.reverses_id WHERE d.doc_date <= ? GROUP BY d.customer_id",
            (end,)):
        settled[cid] = settled.get(cid, 0) + net
    for cid, net in settled.items():
        left = net
        for entry in invoices.get(cid, (None, []))[1]:
            take = min(left, entry[2])
            entry[2] -= take
            left -= take
    rows = []
    for name, entries in sorted(invoices.values()):
        buckets = [0] * len(BUCKETS)
        for _sid, due, remaining in entries:
            if remaining > 0:
                late = (today - date.fromisoformat(due)).days
                buckets[next(i for i, (limit, _t) in enumerate(BUCKETS) if limit is None or late <= limit)] += remaining
        if sum(buckets):
            rows.append(("line", (name, *buckets, sum(buckets))))
    totals = [sum(r[1][1 + i] for r in rows) for i in range(len(BUCKETS) + 1)]
    rows.append(("grand", ("الإجمالي", *totals)))
    ledger_1310 = con.execute(
        "SELECT coalesce(sum(l.debit) - sum(l.credit), 0) FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id WHERE e.status = 'posted' AND l.account_code = '1310' AND e.entry_date <= ?",
        (end,)).fetchone()[0]
    tie = "يطابق الدفتر" if ledger_1310 == totals[-1] else f"تحذير: الدفتر 1310 = {fmt(ledger_1310)}"
    return Table("credit_aging", TITLES["credit_aging"], f"حتى {end} · حسب تاريخ استحقاق الفاتورة · {tie}",
                 ("العميل", *(t for _l, t in BUCKETS), "الإجمالي"), (TEXT, *([MONEY] * (len(BUCKETS) + 1))),
                 tuple(rows), 0)


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


SOURCE_TEXT = {"sale": "بيع", "sale_return": "مرتجع بيع", "purchase_invoice": "فاتورة شراء",
               "treasury_movement": "حركة خزينة", "cash_closing": "إقفال صندوق", "safe_count": "جرد خزنة",
               "installment_contract": "عقد تقسيط", "installment_payment": "تحصيل قسط",
               "customer_receipt": "سند قبض",
               "interest_recognition": "فوائد تقسيط", "interest_recognition_reversal": "عكس فوائد تقسيط", "opening_balance": "رصيد افتتاحي", "stock_adjustment": "تسوية جرد",
               "customer_deposit": "عربون عميل", "purchase_return": "مرتجع مورد", "profit_distribution": "توزيع أرباح",
               "stock_issue": "صرف مخزون"}


def journal(con, start: str, end: str, full: bool = False) -> Table:
    """Posted entries with all their lines, oldest first; the screen stops at a line limit, exports do not."""
    start, end = _range(start, end)
    rows, shown, truncated = [], 0, False
    debit_total = credit_total = 0
    entries = con.execute("SELECT id, entry_date, source_type, source_id, coalesce(memo, '') FROM journal_entries "
                          "WHERE status = 'posted' AND entry_date BETWEEN ? AND ? ORDER BY entry_date, id",
                          (start, end)).fetchall()
    for entry_id, day, source, source_id, memo in entries:
        lines = con.execute("SELECT l.account_code, a.name, l.debit, l.credit FROM journal_lines l "
                            "JOIN accounts a ON a.code = l.account_code WHERE l.entry_id = ? ORDER BY l.line_no",
                            (entry_id,)).fetchall()
        if not full and shown + len(lines) > JOURNAL_LIMIT:
            truncated = True
            break
        rows.append(("head", (day, f"{SOURCE_TEXT.get(source, source)} #{source_id}", memo, "", None, None)))
        for code, name, debit, credit in lines:
            rows.append(("line", ("", "", "", f"{code} {name}", debit, credit)))
            debit_total, credit_total = debit_total + debit, credit_total + credit
        shown += len(lines)
    rows.append(("grand", ("", "", "الإجمالي", "", debit_total, credit_total)))
    note = f" · عُرضت أول {shown} سطرًا فقط؛ ضيّق الفترة أو صدّر التقرير للكل" if truncated else ""
    return Table("journal", TITLES["journal"], f"من {start} إلى {end} · {len(entries)} قيدًا{note}",
                 ("التاريخ", "المستند", "البيان", "الحساب", "مدين", "دائن"),
                 (DATE, CODE, TEXT, TEXT, MONEY, MONEY), tuple(rows), 2)


def daily_sales(con, start: str, end: str) -> Table:
    """Per day: invoices, sales, returns and net, with sales split by how they were paid."""
    start, end = _range(start, end)
    days: dict[str, list] = {}

    def day(d):
        return days.setdefault(d, [0, 0, 0, 0, 0, 0])      # invoices, sales, returns, cash, electronic, credit

    for d, count, total in con.execute("SELECT sale_date, count(*), sum(total) FROM sales "
                                       "WHERE sale_date BETWEEN ? AND ? GROUP BY sale_date", (start, end)):
        day(d)[0], day(d)[1] = count, total
    for d, method, amount in con.execute(
            "SELECT s.sale_date, p.method, sum(p.amount) FROM sale_payments p JOIN sales s ON s.id = p.sale_id "
            "WHERE s.sale_date BETWEEN ? AND ? GROUP BY s.sale_date, p.method", (start, end)):
        day(d)[{"cash": 3, "credit": 5}.get(method, 4)] += amount
    for d, total in con.execute("SELECT return_date, sum(total) FROM sale_returns "
                                "WHERE return_date BETWEEN ? AND ? GROUP BY return_date", (start, end)):
        day(d)[2] = total
    rows, totals = [], [0] * 6
    for d in sorted(days):
        invoices, sales, returns, cash, electronic, credit = days[d]
        rows.append(("line", (d, str(invoices), sales, returns, sales - returns, cash, electronic, credit)))
        totals = [a + b for a, b in zip(totals, days[d], strict=True)]
    rows.append(("grand", ("الإجمالي", str(totals[0]), totals[1], totals[2], totals[1] - totals[2],
                           totals[3], totals[4], totals[5])))
    return Table("daily_sales", TITLES["daily_sales"], f"من {start} إلى {end} · الصافي بعد المرتجعات",
                 ("التاريخ", "الفواتير", "المبيعات", "المرتجعات", "الصافي", "نقدي", "بطاقة وتحويل ومحفظة", "آجل وتقسيط"),
                 (DATE, CODE, MONEY, MONEY, MONEY, MONEY, MONEY, MONEY), tuple(rows), 0)


def top_items(con, start: str, end: str) -> Table:
    """Items ranked by net sales value after returns, with each item's share of the total."""
    start, end = _range(start, end)
    items: dict[int, list] = {}
    for item_id, code, name, qty, net in con.execute(
            "SELECT i.id, i.code, i.name, sum(sl.qty), sum(sl.net_total) FROM sale_lines sl JOIN sales s "
            "ON s.id = sl.sale_id JOIN items i ON i.id = sl.item_id WHERE s.sale_date BETWEEN ? AND ? "
            "GROUP BY i.id", (start, end)):
        items[item_id] = [code, name, qty, net]
    for item_id, qty, value in con.execute(
            "SELECT sl.item_id, sum(rl.qty), sum(rl.value) FROM sale_return_lines rl JOIN sale_returns r "
            "ON r.id = rl.return_id JOIN sale_lines sl ON sl.id = rl.sale_line_id "
            "WHERE r.return_date BETWEEN ? AND ? GROUP BY sl.item_id", (start, end)):
        if item_id in items:
            items[item_id][2] -= qty
            items[item_id][3] -= value
    ranked = sorted((v for v in items.values() if v[3] > 0 or v[2] > 0), key=lambda v: (-v[3], v[0]))
    total = sum(v[3] for v in ranked)
    rows = [("line", (str(i), code, name, str(qty), net, _bp_text(net, total)))
            for i, (code, name, qty, net) in enumerate(ranked, start=1)]
    rows.append(("grand", ("", "", "الإجمالي", str(sum(v[2] for v in ranked)), total, _bp_text(total, total))))
    return Table("top_items", TITLES["top_items"], f"من {start} إلى {end} · مرتبة بقيمة المبيعات الصافية",
                 ("الترتيب", "الكود", "الصنف", "الكمية الصافية", "المبيعات الصافية", "النسبة"),
                 (CODE, CODE, TEXT, CODE, MONEY, CODE), tuple(rows), 2)


def slow_moving(con, end: str, days: str) -> Table:
    """Stocked items idle N days or more since their last sale, or first receipt if never sold."""
    _s, end = _range("0001-01-01", end)
    try:
        limit = int(days)
    except (TypeError, ValueError) as e:
        raise ReportError("اختر عدد الأيام") from e
    today, rows, total = date.fromisoformat(end), [], 0
    data = con.execute(
        "SELECT i.code, i.name, sum(l.qty_on_hand), min(l.received_date), "
        "(SELECT max(s.sale_date) FROM sale_lines sl JOIN sales s ON s.id = sl.sale_id WHERE sl.item_id = i.id "
        "AND s.sale_date <= ?), coalesce((SELECT sum(c.cost_remaining) FROM cost_layers c WHERE c.item_id = i.id), 0) "
        "FROM items i JOIN stock_lots l ON l.item_id = i.id WHERE i.kind <> 'service' AND l.received_date <= ? "
        "GROUP BY i.id HAVING sum(l.qty_on_hand) > 0", (end, end)).fetchall()
    for code, name, qty, first_in, last_sale, value in data:
        basis = last_sale or first_in
        idle = (today - date.fromisoformat(basis)).days
        if idle >= limit:
            rows.append((idle, ("line", (code, name, str(qty), value, last_sale or "لم يُبع", str(idle)))))
            total += value
    out = [r for _idle, r in sorted(rows, key=lambda x: (-x[0], x[1][1][0]))]
    out.append(("grand", ("", "الإجمالي", "", total, "", "")))
    return Table("slow_moving", TITLES["slow_moving"],
                 f"حتى {end} · بلا بيع منذ {limit} يومًا فأكثر · الرصيد والقيمة حاليان",
                 ("الكود", "الصنف", "الرصيد", "القيمة الدفترية", "آخر بيع", "أيام بلا بيع"),
                 (CODE, TEXT, CODE, MONEY, DATE, CODE), tuple(out), 1)


def supplier_balances(con, end: str) -> Table:
    """Per supplier: invoices net of returns less payments up to the end date, tied to account 2110."""
    _s, end = _range("0001-01-01", end)
    per: dict[int, list] = {}
    for sid, name, total in con.execute(
            "SELECT s.id, s.name, sum(p.total) FROM purchase_invoices p JOIN suppliers s ON s.id = p.supplier_id "
            "JOIN journal_entries e ON e.id = p.journal_entry_id WHERE e.entry_date <= ? GROUP BY s.id", (end,)):
        per[sid] = [name, total, 0]
    for sid, name, back in con.execute(
            "SELECT s.id, s.name, sum(CASE r.kind WHEN 'return' THEN r.total ELSE -r.total END) "
            "FROM purchase_returns r JOIN suppliers s ON s.id = r.supplier_id "
            "JOIN journal_entries e ON e.id = r.journal_entry_id WHERE e.entry_date <= ? GROUP BY s.id", (end,)):
        per.setdefault(sid, [name, 0, 0])[1] -= back
    for sid, name, opening in con.execute(
            "SELECT s.id, s.name, sum(o.amount) FROM active_opening_lines o JOIN suppliers s ON s.id = o.supplier_id "
            "WHERE o.opening_date <= ? GROUP BY s.id", (end,)):
        per.setdefault(sid, [name, 0, 0])[1] += opening
    for sid, name, paid in con.execute(
            "SELECT s.id, s.name, sum(CASE m.kind WHEN 'supplier_payment' THEN m.amount ELSE -m.amount END) "
            "FROM treasury_movements m JOIN suppliers s ON s.id = m.supplier_id "
            "JOIN journal_entries e ON e.id = m.journal_entry_id WHERE e.entry_date <= ? GROUP BY s.id", (end,)):
        per.setdefault(sid, [name, 0, 0])[2] = paid
    rows = [("line", (name, invoiced, paid, invoiced - paid)) for name, invoiced, paid in sorted(per.values())
            if invoiced or paid]
    totals = [sum(r[1][i] for r in rows) for i in (1, 2, 3)]
    rows.append(("grand", ("الإجمالي", *totals)))
    ledger_2110 = con.execute(
        "SELECT coalesce(sum(l.credit) - sum(l.debit), 0) FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id WHERE e.status = 'posted' AND l.account_code = '2110' AND e.entry_date <= ?",
        (end,)).fetchone()[0]
    tie = "يطابق الدفتر" if ledger_2110 == totals[2] else f"تحذير: الدفتر 2110 = {fmt(ledger_2110)}"
    return Table("suppliers", TITLES["suppliers"], f"حتى {end} · {tie}",
                 ("المورد", "الفواتير", "المسدَّد", "المتبقي"), (TEXT, MONEY, MONEY, MONEY), tuple(rows), 0)


def _tie(ledger_value: int, report_value: int, label: str) -> str:
    return "يطابق الدفتر" if ledger_value == report_value else f"تحذير: الدفتر {label} = {fmt(ledger_value)}"


def _ledger_net(con, account: str, start: str, end: str, source: str | None = None, credit: bool = False) -> int:
    """Net posted movement of one account in the range, optionally of one source type only."""
    sign = "l.credit - l.debit" if credit else "l.debit - l.credit"
    sql = (f"SELECT coalesce(sum({sign}), 0) FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
           "WHERE e.status = 'posted' AND l.account_code = ? AND e.entry_date BETWEEN ? AND ?")
    args = [account, start, end]
    if source:
        sql += " AND e.source_type = ?"
        args.append(source)
    return con.execute(sql, args).fetchone()[0]


def stock_valuation(con, end: str) -> Table:
    """Current stock per item at book cost from the FIFO layers, with the oldest DOT, tied to account 1410."""
    _s, end = _range("0001-01-01", end)
    year = date.fromisoformat(end).year
    rows, qty_total, value_total = [], 0, 0
    for item_id, code, name, qty, value in con.execute(
            "SELECT i.id, i.code, i.name, sum(c.qty_remaining), sum(c.cost_remaining) FROM items i "
            "JOIN cost_layers c ON c.item_id = i.id WHERE c.qty_remaining > 0 GROUP BY i.id ORDER BY i.code"):
        dot = con.execute("SELECT dot FROM stock_lots WHERE item_id = ? AND qty_on_hand > 0 AND mfg_key IS NOT NULL "
                          "ORDER BY mfg_key, id LIMIT 1", (item_id,)).fetchone()
        age = str(year - 2000 - int(dot[0][2:])) if dot else ""
        rows.append(("line", (code, name, str(qty), value, (value + qty // 2) // qty, dot[0] if dot else "", age)))
        qty_total, value_total = qty_total + qty, value_total + value
    rows.append(("grand", ("", "الإجمالي", str(qty_total), value_total, "", "", "")))
    ledger_1410 = con.execute("SELECT coalesce(sum(l.debit) - sum(l.credit), 0) FROM journal_lines l JOIN "
                              "journal_entries e ON e.id = l.entry_id WHERE e.status = 'posted' "
                              "AND l.account_code = '1410'").fetchone()[0]
    other = {code: ledger.account_balance(con, code) for code in ("1420", "1430")}
    note = f" · معيب بانتظار المورد 1430: {fmt(other['1430'])}" if other["1430"] else ""
    note += f" · بطاريات قديمة 1420: {fmt(other['1420'])}" if other["1420"] else ""
    return Table("stock_valuation", TITLES["stock_valuation"],
                 f"الرصيد الحالي بتكلفة طبقات FIFO · {_tie(ledger_1410, value_total, '1410')}{note}",
                 ("الكود", "الصنف", "الكمية", "القيمة الدفترية", "متوسط التكلفة", "أقدم DOT", "عمره بالسنوات"),
                 (CODE, TEXT, CODE, MONEY, MONEY, CODE, CODE), tuple(rows), 1)


def expenses(con, start: str, end: str) -> Table:
    """Operating expense accounts (52xx) in the range with each one's share of the total."""
    start, end = _range(start, end)
    data = con.execute(
        "SELECT a.code, a.name, sum(l.debit) - sum(l.credit) FROM accounts a JOIN journal_lines l "
        "ON l.account_code = a.code JOIN journal_entries e ON e.id = l.entry_id WHERE a.parent_code = '52' "
        "AND e.status = 'posted' AND e.entry_date BETWEEN ? AND ? GROUP BY a.code ORDER BY a.code",
        (start, end)).fetchall()
    data = [d for d in data if d[2]]
    total = sum(d[2] for d in data)
    rows = [("line", (code, name, amount, _bp_text(amount, total))) for code, name, amount in data]
    rows.append(("grand", ("", "الإجمالي", total, _bp_text(total, total))))
    return Table("expenses", TITLES["expenses"], f"من {start} إلى {end} · حسابات 52 (دون تكلفة المبيعات)",
                 ("الحساب", "البند", "المبلغ", "النسبة"), (CODE, TEXT, MONEY, CODE), tuple(rows), 1)


def collections(con, start: str, end: str) -> Table:
    """Receipts, instalment payments and net deposits per day, split cash and non-cash, tied to the ledger."""
    start, end = _range(start, end)
    days: dict[str, list] = {}

    def add(day, slot, method, amount):
        row = days.setdefault(day, [0, 0, 0, 0, 0])   # receipts, instalments, deposits, cash, non-cash
        row[slot] += amount
        row[3 if method == "cash" else 4] += amount

    for day, method, amount in con.execute(
            "SELECT receipt_date, method, sum(CASE kind WHEN 'receipt' THEN amount ELSE -amount END) "
            "FROM customer_receipts WHERE receipt_date BETWEEN ? AND ? GROUP BY receipt_date, method", (start, end)):
        add(day, 0, method, amount)
    for day, method, amount in con.execute(
            "SELECT payment_date, method, sum(amount) FROM installment_payments WHERE payment_date BETWEEN ? AND ? "
            "GROUP BY payment_date, method", (start, end)):
        add(day, 1, method, amount)
    deposit_money = applied = 0
    for day, method, kind, original, amount in con.execute(
            "SELECT d.doc_date, d.method, d.kind, o.kind, d.amount FROM customer_deposits d "
            "LEFT JOIN customer_deposits o ON o.id = d.reverses_id WHERE d.doc_date BETWEEN ? AND ?", (start, end)):
        if kind == "apply" or original == "apply":
            applied += amount if kind == "apply" else -amount
            continue
        signed = amount if (kind == "deposit" or original == "refund") else -amount
        deposit_money += signed
        add(day, 2, method, signed)
    rows = [("line", (day, v[0], v[1], v[2], v[0] + v[1] + v[2], v[3], v[4])) for day, v in sorted(days.items())]
    totals = [sum(r[1][i] for r in rows) for i in range(1, 7)]
    rows.append(("grand", ("الإجمالي", *totals)))
    ok_receipts = _ledger_net(con, "1310", start, end, "customer_receipt", credit=True) == totals[0]
    ok_instal = _ledger_net(con, "1320", start, end, "installment_payment", credit=True) == totals[1]
    ok_deposits = _ledger_net(con, "2340", start, end, "customer_deposit", credit=True) + applied == deposit_money
    tie = "يطابق الدفتر" if ok_receipts and ok_instal and ok_deposits else "تحذير: لا يطابق الدفتر"
    return Table("collections", TITLES["collections"], f"من {start} إلى {end} · {tie}",
                 ("التاريخ", "سندات القبض", "أقساط التقسيط", "عربون (صافٍ)", "الإجمالي", "نقدًا", "غير نقدي"),
                 (DATE, MONEY, MONEY, MONEY, MONEY, MONEY, MONEY), tuple(rows), 0)


def purchases(con, start: str, end: str) -> Table:
    """Purchase invoices per supplier with their items, tied to the credits posted on account 2110."""
    start, end = _range(start, end)
    suppliers_rows: dict[int, list] = {}
    for sid, sname, code, name, qty, amount in con.execute(
            "SELECT s.id, s.name, i.code, i.name, sum(pl.qty), sum(pl.line_total) FROM purchase_invoices p "
            "JOIN journal_entries e ON e.id = p.journal_entry_id JOIN suppliers s ON s.id = p.supplier_id "
            "JOIN purchase_lines pl ON pl.invoice_id = p.id JOIN items i ON i.id = pl.item_id "
            "WHERE e.entry_date BETWEEN ? AND ? GROUP BY s.id, i.id ORDER BY s.name, i.code", (start, end)):
        suppliers_rows.setdefault(sid, [sname, []])[1].append((code, name, qty, amount))
    rows, grand = [], 0
    for sname, items in sorted(suppliers_rows.values(), key=lambda v: v[0]):
        total = sum(i[3] for i in items)
        grand += total
        rows.append(("head", (sname, "", "", total, "")))
        rows += [("line", (name, code, str(qty), amount, (amount + qty // 2) // qty)) for code, name, qty, amount in items]
    rows.append(("grand", ("الإجمالي", "", "", grand, "")))
    tie = _tie(_ledger_net(con, "2110", start, end, "purchase_invoice", credit=True), grand, "2110")
    return Table("purchases", TITLES["purchases"], f"من {start} إلى {end} · {tie}",
                 ("المورد / الصنف", "الكود", "الكمية", "الإجمالي", "متوسط التكلفة"),
                 (TEXT, CODE, CODE, MONEY, MONEY), tuple(rows), 0)


def returns(con, start: str, end: str) -> Table:
    """Sale returns by reason and by item, sound against defective, tied to account 4190."""
    start, end = _range(start, end)
    base = ("FROM sale_return_lines rl JOIN sale_returns r ON r.id = rl.return_id JOIN sale_lines sl "
            "ON sl.id = rl.sale_line_id JOIN items i ON i.id = sl.item_id WHERE r.return_date BETWEEN ? AND ? ")
    sums = ("count(DISTINCT r.id), coalesce(sum(CASE rl.condition WHEN 'sound' THEN rl.qty END), 0), "
            "coalesce(sum(CASE rl.condition WHEN 'defective' THEN rl.qty END), 0), sum(rl.value) ")
    rows, total = [], 0
    for title, label, group in (("حسب السبب", "r.reason", "r.reason"), ("حسب الصنف", "i.code || ' · ' || i.name", "i.id")):
        data = con.execute(f"SELECT {label}, {sums}{base}GROUP BY {group} ORDER BY sum(rl.value) DESC, 1",
                           (start, end)).fetchall()
        section = sum(d[4] for d in data)
        rows.append(("head", (title, "", "", "", "")))
        rows += [("line", (name, str(n), str(sound), str(bad), value)) for name, n, sound, bad, value in data]
        rows.append(("total", ("المجموع", "", str(sum(d[2] for d in data)), str(sum(d[3] for d in data)), section)))
        total = section
    tie = _tie(_ledger_net(con, "4190", start, end), total, "4190")
    return Table("returns", TITLES["returns"], f"من {start} إلى {end} · {tie}",
                 ("البيان", "المرتجعات", "سليم", "معيب", "القيمة"),
                 (TEXT, CODE, CODE, CODE, MONEY), tuple(rows), 0)


def supplier_aging(con, end: str) -> Table:
    """Open supplier balances by days past due; returns and payments settle the oldest invoice first."""
    _s, end = _range("0001-01-01", end)
    today = date.fromisoformat(end)
    invoices: dict[int, list] = {}      # supplier -> [name, [[due, open]]]
    for sid, name, days, day, total in con.execute(
            "SELECT s.id, s.name, s.payment_days, p.invoice_date, p.total FROM purchase_invoices p "
            "JOIN suppliers s ON s.id = p.supplier_id JOIN journal_entries e ON e.id = p.journal_entry_id "
            "WHERE e.entry_date <= ? ORDER BY p.invoice_date, p.id", (end,)):
        due = (date.fromisoformat(day) + timedelta(days=days)).isoformat()
        invoices.setdefault(sid, [name, []])[1].append([due, total])
    for sid, name, days, day, amount in con.execute(
            "SELECT s.id, s.name, s.payment_days, o.opening_date, o.amount FROM active_opening_lines o "
            "JOIN suppliers s ON s.id = o.supplier_id WHERE o.opening_date <= ?", (end,)):
        due = (date.fromisoformat(day) + timedelta(days=days)).isoformat()
        invoices.setdefault(sid, [name, []])[1].insert(0, [due, amount])        # the opening is the oldest debt
    settled: dict[int, int] = {}
    for sid, net in con.execute(
            "SELECT r.supplier_id, sum(CASE r.kind WHEN 'return' THEN r.total ELSE -r.total END) "
            "FROM purchase_returns r JOIN journal_entries e ON e.id = r.journal_entry_id "
            "WHERE e.entry_date <= ? GROUP BY r.supplier_id", (end,)):
        settled[sid] = net
    for sid, net in con.execute(
            "SELECT m.supplier_id, sum(CASE m.kind WHEN 'supplier_payment' THEN m.amount ELSE -m.amount END) "
            "FROM treasury_movements m JOIN journal_entries e ON e.id = m.journal_entry_id "
            "WHERE m.supplier_id IS NOT NULL AND e.entry_date <= ? GROUP BY m.supplier_id", (end,)):
        settled[sid] = settled.get(sid, 0) + net
    names = dict(con.execute("SELECT id, name FROM suppliers"))
    rows = []
    for sid in sorted(set(invoices) | set(settled), key=lambda i: names[i]):
        left = settled.get(sid, 0)
        buckets = [0] * len(BUCKETS)
        for due, amount in invoices.get(sid, (None, []))[1]:
            take = min(max(left, 0), amount)
            left -= take
            if amount - take > 0:
                late = (today - date.fromisoformat(due)).days
                buckets[next(i for i, (limit, _t) in enumerate(BUCKETS) if limit is None or late <= limit)] += amount - take
        credit = -max(left, 0)           # paid or returned beyond the invoices: the supplier owes the shop
        if sum(buckets) or credit:
            rows.append(("line", (names[sid], *buckets, credit, sum(buckets) + credit)))
    totals = [sum(r[1][1 + i] for r in rows) for i in range(len(BUCKETS) + 2)]
    rows.append(("grand", ("الإجمالي", *totals)))
    ledger_2110 = con.execute(
        "SELECT coalesce(sum(l.credit) - sum(l.debit), 0) FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id WHERE e.status = 'posted' AND l.account_code = '2110' AND e.entry_date <= ?",
        (end,)).fetchone()[0]
    return Table("supplier_aging", TITLES["supplier_aging"],
                 f"حتى {end} · حسب تاريخ استحقاق الفاتورة · {_tie(ledger_2110, totals[-1], '2110')}",
                 ("المورد", *(t for _l, t in BUCKETS), "رصيد لنا", "الإجمالي"),
                 (TEXT, *([MONEY] * (len(BUCKETS) + 2))), tuple(rows), 0)


ITEM_CARD_LABELS = {"receipt": "استلام شراء", "issue": "صرف", "return": "مرتجع بيع", "restock": "عكس مرتجع مورد"}


def item_card(con, item_id: str, start: str, end: str) -> Table:
    """Every stock movement of one item with its lot and running quantity, opening balance first."""
    start, end = _range(start, end)
    try:
        item = con.execute("SELECT id, code, name FROM items WHERE id = ? AND kind IN ('tire', 'battery')",
                           (int(item_id),)).fetchone()
    except ValueError:
        item = None
    if item is None:
        raise ReportError("اختر صنفًا مخزّنًا")
    opening = con.execute("SELECT coalesce(sum(qty), 0) FROM stock_movements WHERE item_id = ? AND date(at) < ?",
                          (item[0], start)).fetchone()[0]
    rows = [("head", ("", "رصيد أول الفترة", "", "", str(opening), ""))]
    running = opening
    for at, kind, qty, ref, dot, src_type, src_no, pr_no in con.execute(
            "SELECT m.at, m.kind, m.qty, l.ref_no, coalesce(l.dot, ''), si.source_type, "
            "coalesce(s.number, ''), coalesce(prt.number, '') FROM stock_movements m "
            "JOIN stock_lots l ON l.id = m.lot_id LEFT JOIN stock_issues si ON si.id = m.issue_id "
            "LEFT JOIN sales s ON si.source_type = 'sale' AND s.id = si.source_id "
            "LEFT JOIN purchase_return_lines prl ON prl.id = m.purchase_return_line_id "
            "LEFT JOIN purchase_returns prt ON prt.id = prl.return_id "
            "WHERE m.item_id = ? AND date(m.at) BETWEEN ? AND ? ORDER BY m.at, m.id", (item[0], start, end)):
        running += qty
        if kind == "issue" and src_type == "purchase_return":
            label = "مرتجع للمورد"
        else:
            label = ITEM_CARD_LABELS[kind]
        rows.append(("line", (at[:10], label, f"{ref}" + (f" · DOT {dot}" if dot else ""), src_no or pr_no,
                              str(qty), str(running))))
    closing = running
    rows.append(("grand", ("", "رصيد آخر الفترة", "", "", str(closing), "")))
    on_hand = con.execute("SELECT coalesce(sum(qty_on_hand), 0) FROM stock_lots WHERE item_id = ?",
                          (item[0],)).fetchone()[0]
    after = con.execute("SELECT coalesce(sum(qty), 0) FROM stock_movements WHERE item_id = ? AND date(at) > ?",
                        (item[0], end)).fetchone()[0]
    tie = "يطابق رصيد الدفعات" if closing + after == on_hand else f"تحذير: رصيد الدفعات الحالي = {on_hand}"
    return Table("item_card", TITLES["item_card"], f"{item[1]} · {item[2]} · من {start} إلى {end} · {tie}",
                 ("التاريخ", "الحركة", "الدفعة", "المرجع", "الكمية", "الرصيد"),
                 (DATE, TEXT, CODE, CODE, CODE, CODE), tuple(rows), 1)


def build(con, key: str, start: str, end: str, account: str = "", compare: bool = False,
          full: bool = False) -> Table:
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
    if key == "credit_aging":
        return credit_aging(con, end)
    if key == "partner":
        return partner_statement(con, account, start, end)
    if key == "daily_sales":
        return daily_sales(con, start, end)
    if key == "top_items":
        return top_items(con, start, end)
    if key == "slow_moving":
        return slow_moving(con, end, account)
    if key == "journal":
        return journal(con, start, end, full)
    if key == "suppliers":
        return supplier_balances(con, end)
    if key == "suspended":
        return suspended(con)
    if key == "stock_valuation":
        return stock_valuation(con, end)
    if key == "expenses":
        return expenses(con, start, end)
    if key == "collections":
        return collections(con, start, end)
    if key == "purchases":
        return purchases(con, start, end)
    if key == "returns":
        return returns(con, start, end)
    if key == "supplier_aging":
        return supplier_aging(con, end)
    if key == "item_card":
        return item_card(con, account, start, end)
    raise ReportError("تقرير غير معروف")


def _cell(value, kind: str) -> str:
    return fmt(value) if kind == MONEY and type(value) is int else ("" if value is None else str(value))


_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _safe(text: str) -> str:
    """Neutralise spreadsheet formulas: a text cell starting with =+-@ would run when opened in Excel."""
    return "'" + text if text.startswith(_FORMULA_LEAD) else text


def to_csv(table: Table, path: str) -> None:
    """UTF-8 with BOM so Excel opens Arabic correctly; money as plain pounds with two decimals."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([_safe(table.title)])
        w.writerow([_safe(table.subtitle)])
        w.writerow([_safe(h) for h in table.headers])
        for _kind, cells in table.rows:
            w.writerow([_cell(v, k).replace(",", "") if k == MONEY else _safe(_cell(v, k))
                        for v, k in zip(cells, table.kinds, strict=True)])
