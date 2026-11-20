"""Report catalog: each report builds one uniform read-only Table from posted ledger data."""
import csv
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core.money import fmt
from morv.infra import income_statement, suspended_balances

TEXT, CODE, MONEY, DATE = "text", "code", "money", "date"
KEYS = ("income", "trial", "ledger", "suspended")
TITLES = {"income": "قائمة الدخل", "trial": "ميزان المراجعة", "ledger": "كشف حساب", "suspended": "الأرصدة المعلّقة"}
NEEDS_ACCOUNT = {"ledger"}
NEEDS_RANGE = {"income", "ledger"}      # trial balance and suspended balances are as of the end date


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


def _range(start: str, end: str) -> tuple[str, str]:
    try:
        start, end = date.fromisoformat((start or "").strip()).isoformat(), date.fromisoformat((end or "").strip()).isoformat()
    except ValueError as e:
        raise ReportError("تاريخ غير صالح؛ اكتبه بصيغة YYYY-MM-DD") from e
    if start > end:
        raise ReportError("تاريخ البداية بعد تاريخ النهاية")
    return start, end


def income(con, start: str, end: str) -> Table:
    start, end = _range(start, end)
    rows = tuple((kind, (code, name, amount)) for _i, kind, code, name, amount
                 in income_statement.table_rows(income_statement.build(con, start, end)))
    return Table("income", TITLES["income"], f"من {start} إلى {end}", ("الحساب", "البند", "المبلغ"),
                 (CODE, TEXT, MONEY), rows, 1)


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


def build(con, key: str, start: str, end: str, account: str = "") -> Table:
    if key == "income":
        return income(con, start, end)
    if key == "trial":
        return trial_balance(con, end)
    if key == "ledger":
        return account_statement(con, account, start, end)
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
