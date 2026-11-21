"""Opening balances: one entry per line against transitional account 3500, which must net to zero."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, require

CLEARING = "3500"
ASSET_ACCOUNTS = ("1110", "1120", "1210", "1220", "1230", "1510", "1610")
LIABILITY_ACCOUNTS = ("2310",)
RETAINED = "3200"
CUSTOMERS, SUPPLIERS = "1310", "2110"


class OpeningError(ValueError):
    """Opening balances refused, with a message fit to show."""


@dataclass(frozen=True)
class OpeningLine:
    kind: str            # account, partner_capital, partner_current, retained, customer, supplier
    amount: int          # natural-side balance; partner_current and retained may be negative
    account: str | None = None
    partner_id: int | None = None
    customer_id: int | None = None
    supplier_id: int | None = None


@dataclass(frozen=True)
class Planned:
    line: OpeningLine
    label: str
    side: str            # "asset", "liability" or "equity"
    entry: tuple         # ledger lines against the clearing account


@dataclass(frozen=True)
class Preview:
    planned: tuple
    assets: int
    liabilities: int
    equity: int

    @property
    def difference(self) -> int:
        return self.assets - self.liabilities - self.equity


@dataclass(frozen=True)
class PostedOpening:
    id: int
    number: str
    replayed: bool


def _entry(account: str, amount: int, natural: str) -> tuple:
    """Lines for one balance: the account on its natural side (flipped when negative), the rest on 3500."""
    debit_side = (natural == "debit") == (amount > 0)
    n = abs(amount)
    return ((account, n, 0), (CLEARING, 0, n)) if debit_side else ((account, 0, n), (CLEARING, n, 0))


def _plan_line(con, i: int, ln: OpeningLine) -> Planned:
    if type(ln.amount) is not int or ln.amount == 0:
        raise OpeningError(f"السطر {i}: المبلغ عدد صحيح بالقرش لا يساوي صفرًا")
    if ln.kind == "account":
        if ln.account in ASSET_ACCOUNTS:
            return Planned(ln, f"حساب {ln.account}", "asset", _entry(ln.account, ln.amount, "debit"))
        if ln.account in LIABILITY_ACCOUNTS:
            return Planned(ln, f"حساب {ln.account}", "liability", _entry(ln.account, ln.amount, "credit"))
        raise OpeningError(f"السطر {i}: الحساب غير مسموح في الرصيد الافتتاحي")
    if ln.kind in ("customer", "supplier"):
        if ln.amount < 0:
            raise OpeningError(f"السطر {i}: رصيد العميل أو المورد الافتتاحي موجب")
        table, pid = ("customers", ln.customer_id) if ln.kind == "customer" else ("suppliers", ln.supplier_id)
        row = con.execute(f"SELECT name, active FROM {table} WHERE id=?", (pid,)).fetchone()
        if not row or not row[1]:
            raise OpeningError(f"السطر {i}: العميل أو المورد غير موجود أو موقوف")
        if ln.kind == "customer":
            return Planned(ln, f"عميل {row[0]}", "asset", _entry(CUSTOMERS, ln.amount, "debit"))
        return Planned(ln, f"مورد {row[0]}", "liability", _entry(SUPPLIERS, ln.amount, "credit"))
    if ln.kind == "retained":
        return Planned(ln, "أرباح مرحّلة", "equity", _entry(RETAINED, ln.amount, "credit"))
    if ln.kind in ("partner_capital", "partner_current"):
        if ln.kind == "partner_capital" and ln.amount < 0:
            raise OpeningError(f"السطر {i}: رأس المال موجب")
        row = con.execute("SELECT name, active, capital_account, current_account FROM partners WHERE id=?",
                          (ln.partner_id,)).fetchone()
        if not row or not row[1]:
            raise OpeningError(f"السطر {i}: الشريك غير موجود أو موقوف")
        if ln.kind == "partner_capital":
            return Planned(ln, f"رأس مال {row[0]}", "equity", _entry(row[2], ln.amount, "credit"))
        natural = con.execute("SELECT normal_side FROM accounts WHERE code=?", (row[3],)).fetchone()[0]
        # Positive means the shop owes the partner: a credit balance, whatever the account's usual side
        return Planned(ln, f"جاري {row[0]}", "equity", _entry(row[3], -ln.amount if natural == "debit" else ln.amount,
                                                              natural))
    raise OpeningError(f"السطر {i}: نوع غير معروف")


def _shape(lines) -> list:
    if not lines:
        raise OpeningError("لا أسطر في الرصيد الافتتاحي")
    seen = set()
    for i, ln in enumerate(lines, start=1):
        key = (ln.kind, ln.account, ln.partner_id, ln.customer_id, ln.supplier_id)
        if key in seen:
            raise OpeningError(f"السطر {i}: مكرر؛ سطر واحد لكل حساب أو طرف")
        seen.add(key)
    return list(lines)


def plan(con: sqlite3.Connection, lines: list) -> Preview:
    """Entries and the balance check, with no write; the difference must be zero before it can post."""
    planned = tuple(_plan_line(con, i, ln) for i, ln in enumerate(_shape(lines), start=1))

    def total(side):
        return sum(p.line.amount for p in planned if p.side == side)
    return Preview(planned, total("asset"), total("liability"), total("equity"))


def preview(con: sqlite3.Connection, by, lines: list) -> Preview:
    require(con, by, "opening.post", OpeningError)
    return plan(con, lines)


def _digest(kind, day, note, lines) -> str:
    body = [kind, day, note, [[x.kind, x.amount, x.account, x.partner_id, x.customer_id, x.supplier_id]
                              for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _clearing_zero(con) -> None:
    if ledger.account_balance(con, CLEARING) != 0:
        raise OpeningError("الحساب الانتقالي 3500 لم يُصفَّر؛ الأرصدة الافتتاحية غير مكتملة")


def _write(con, by, op_id: int, number: str, day: str, planned, mirror: bool) -> None:
    for no, p in enumerate(planned, start=1):
        line_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM opening_balance_lines").fetchone()[0]
        lines = [(a, c, d) for a, d, c in p.entry] if mirror else list(p.entry)
        try:
            entry_id = ledger.post_entry(con, by, day, "opening_balance", line_id,
                                         f"{'عكس ' if mirror else ''}رصيد افتتاحي {number} · {p.label}", lines)
        except ledger.LedgerError as e:
            raise OpeningError(str(e)) from e
        ln = p.line
        con.execute("INSERT INTO opening_balance_lines (id, opening_id, line_no, kind, account, partner_id, "
                    "customer_id, supplier_id, amount, journal_entry_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (line_id, op_id, no, ln.kind, ln.account, ln.partner_id, ln.customer_id, ln.supplier_id,
                     ln.amount, entry_id))


def _header(con, by, key, digest, kind, day, note, reverses_id) -> tuple:
    op_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM opening_balances").fetchone()[0]
    number = f"OB-{op_id:06d}"
    con.execute("INSERT INTO opening_balances (id, number, request_key, payload_hash, kind, opening_date, note, "
                "reverses_id, user_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (op_id, number, key, digest, kind, day, note, reverses_id, by.id))
    return op_id, number


def _check_key(key) -> str:
    key = (key or "").strip()
    if len(key) < 8:
        raise OpeningError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def post_opening(con: sqlite3.Connection, by, *, request_key: str, opening_date: str, lines: list,
                 note: str = "") -> PostedOpening:
    require(con, by, "opening.post", OpeningError)
    key = _check_key(request_key)
    try:
        day = ledger.valid_date(opening_date, "تاريخ بدء التشغيل")
    except ledger.LedgerError as e:
        raise OpeningError(str(e)) from e
    shaped = _shape(lines)
    digest = _digest("opening", day, note.strip(), shaped)
    with transaction(con):
        old = con.execute("SELECT id, number, payload_hash FROM opening_balances WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[2] != digest:
                raise OpeningError("مفتاح الطلب سبق استخدامه لرصيد افتتاحي بمحتوى مختلف")
            return PostedOpening(old[0], old[1], True)
        if con.execute("SELECT 1 FROM active_opening_lines LIMIT 1").fetchone():
            raise OpeningError("يوجد رصيد افتتاحي ساري؛ اعكسه أولًا قبل إدخال غيره")
        first = con.execute("SELECT min(entry_date) FROM journal_entries WHERE source_type <> 'opening_balance'"
                            ).fetchone()[0]
        if first and first < day:
            raise OpeningError(f"يوجد مستند بتاريخ {first} أسبق من تاريخ بدء التشغيل")
        view = plan(con, shaped)
        if view.difference:
            raise OpeningError(f"الأرصدة غير متوازنة: الأصول ناقص الالتزامات تزيد عن حقوق الملكية بمقدار {view.difference}")
        op_id, number = _header(con, by, key, digest, "opening", day, note.strip(), None)
        _write(con, by, op_id, number, day, view.planned, mirror=False)
        _clearing_zero(con)
        audit(con, by, "opening.post", "opening_balance", op_id, f"{number} · {len(view.planned)} سطر")
    return PostedOpening(op_id, number, False)


def reverse_opening(con: sqlite3.Connection, by, *, request_key: str, opening_id: int, reason: str) -> PostedOpening:
    """Allowed only while nothing but opening entries exist, so a mistake before go-live can be redone."""
    require(con, by, "opening.post", OpeningError)
    key = _check_key(request_key)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise OpeningError("سبب العكس مطلوب")
    digest = _digest("reversal", opening_id, reason, [])
    with transaction(con):
        old = con.execute("SELECT id, number, payload_hash FROM opening_balances WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[2] != digest:
                raise OpeningError("مفتاح الطلب سبق استخدامه لعكس بمحتوى مختلف")
            return PostedOpening(old[0], old[1], True)
        src = con.execute("SELECT number, kind FROM opening_balances WHERE id=?", (opening_id,)).fetchone()
        if not src or src[1] != "opening":
            raise OpeningError("الرصيد الافتتاحي غير موجود")
        if con.execute("SELECT 1 FROM opening_balances WHERE reverses_id=?", (opening_id,)).fetchone():
            raise OpeningError("الرصيد الافتتاحي معكوس سابقًا")
        if con.execute("SELECT 1 FROM journal_entries WHERE source_type <> 'opening_balance' LIMIT 1").fetchone():
            raise OpeningError("بدأت مستندات أخرى بعد الرصيد الافتتاحي؛ لا يُعكس")
        originals = [OpeningLine(k, a, acc, pid, cid, sid) for k, a, acc, pid, cid, sid in con.execute(
            "SELECT kind, amount, account, partner_id, customer_id, supplier_id FROM opening_balance_lines "
            "WHERE opening_id=? ORDER BY line_no", (opening_id,))]
        today = date.today().isoformat()
        new_id, number = _header(con, by, key, digest, "reversal", today, reason, opening_id)
        _write(con, by, new_id, number, today, plan(con, originals).planned, mirror=True)
        _clearing_zero(con)
        audit(con, by, "opening.reverse", "opening_balance", new_id, f"{number} عكس {src[0]}")
    return PostedOpening(new_id, number, False)


def opening_of(con: sqlite3.Connection, kind: str, party_id: int) -> int:
    """The live opening balance of one customer or supplier, zero when none or reversed."""
    column = "customer_id" if kind == "customer" else "supplier_id"
    return con.execute(f"SELECT coalesce(sum(amount), 0) FROM active_opening_lines WHERE {column}=?",
                       (party_id,)).fetchone()[0]
