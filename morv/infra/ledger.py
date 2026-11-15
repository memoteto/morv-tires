"""ترحيل القيود المحاسبية المتوازنة وقراءة أرصدة الحسابات (يُستدعى من داخل معاملة المتصل)."""
import sqlite3
from datetime import date

from morv.infra.users import audit


class LedgerError(ValueError):
    """قيد مرفوض: غير متوازن أو بمبلغ أو حساب غير صالح."""


def valid_date(text, what: str = "التاريخ") -> str:
    """التحقق من تاريخ بصيغة YYYY-MM-DD وأنه ليس في المستقبل."""
    try:
        d = date.fromisoformat(str(text))
    except ValueError as e:
        raise LedgerError(f"{what} غير صالح (المطلوب YYYY-MM-DD): {text!r}") from e
    if d > date.today():
        raise LedgerError(f"{what} في المستقبل: {text}")
    return d.isoformat()


def post_entry(con: sqlite3.Connection, by, entry_date: str, source_type: str, source_id: int,
               memo: str, lines: list) -> int:
    """ترحيل قيد؛ lines = [(حساب, مدين, دائن)] بالقرش. يشترط معاملة مفتوحة وتوازن المدين والدائن."""
    if not con.in_transaction:
        raise LedgerError("ترحيل القيد يجب أن يكون داخل معاملة")
    if len(lines) < 2:
        raise LedgerError("القيد يحتاج سطرين على الأقل")
    for _, d, c in lines:
        if type(d) is not int or type(c) is not int:
            raise LedgerError("المبالغ أعداد صحيحة بالقرش فقط")
    if sum(d for _, d, _ in lines) != sum(c for _, _, c in lines):
        raise LedgerError("القيد غير متوازن")
    cur = con.execute(
        "INSERT INTO journal_entries (entry_date, source_type, source_id, memo, user_id) VALUES (?,?,?,?,?)",
        (entry_date, source_type, source_id, memo, by.id if by else None))
    entry_id = cur.lastrowid
    try:
        for no, (acc, d, c) in enumerate(lines, start=1):
            con.execute("INSERT INTO journal_lines (entry_id, line_no, account_code, debit, credit) VALUES (?,?,?,?,?)",
                        (entry_id, no, acc, d, c))
    except sqlite3.IntegrityError as e:
        raise LedgerError(f"سطر قيد مرفوض: {e}") from e
    except sqlite3.DatabaseError as e:
        raise LedgerError(str(e)) from e
    audit(con, by, "journal.post", "journal_entry", entry_id, f"{source_type}#{source_id}")
    return entry_id


def account_balance(con: sqlite3.Connection, code: str) -> int:
    """رصيد حساب بالقرش = مجموع المدين - مجموع الدائن."""
    return con.execute("SELECT coalesce(sum(debit) - sum(credit), 0) FROM journal_lines WHERE account_code=?",
                       (code,)).fetchone()[0]


def unbalanced_entries(con: sqlite3.Connection) -> list:
    """أرقام القيود التي لا يتساوى مدينها ودائنها (يجب أن تكون فارغة دائمًا)."""
    rows = con.execute("SELECT entry_id FROM journal_lines GROUP BY entry_id HAVING sum(debit) <> sum(credit)")
    return [r[0] for r in rows]
