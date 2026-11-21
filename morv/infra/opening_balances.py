"""Opening balances: one entry per line against transitional account 3500, which must net to zero."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date

from morv.core import dot as dotmod
from morv.infra import ledger, stock_adjustments
from morv.infra.db.connection import transaction
from morv.infra.users import audit, require

CLEARING = "3500"
ASSET_ACCOUNTS = ("1110", "1120", "1210", "1220", "1230", "1510", "1610")
LIABILITY_ACCOUNTS = ("2310",)
RETAINED = "3200"
CUSTOMERS, SUPPLIERS, INVENTORY = "1310", "2110", "1410"


class OpeningError(ValueError):
    """Opening balances refused, with a message fit to show."""


@dataclass(frozen=True)
class OpeningLine:
    kind: str            # account, partner_capital, partner_current, retained, customer, supplier, inventory
    amount: int = 0      # natural-side balance; partner_current and retained may be negative; inventory is derived
    account: str | None = None
    partner_id: int | None = None
    customer_id: int | None = None
    supplier_id: int | None = None
    item_id: int | None = None       # inventory: one lot per line
    qty: int | None = None
    unit_cost: int | None = None
    dot: str | None = None


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


def _plan_inventory(con, i: int, ln: OpeningLine) -> Planned:
    if type(ln.qty) is not int or ln.qty <= 0:
        raise OpeningError(f"السطر {i}: الكمية عدد صحيح موجب")
    if type(ln.unit_cost) is not int or ln.unit_cost <= 0:
        raise OpeningError(f"السطر {i}: تكلفة الوحدة بالقرش عدد صحيح أكبر من صفر")
    row = con.execute("SELECT name, kind, active FROM items WHERE id=?", (ln.item_id,)).fetchone()
    if not row or not row[2] or row[1] not in ("tire", "battery"):
        raise OpeningError(f"السطر {i}: الصنف غير موجود أو موقوف أو غير مخزّن")
    d = None
    if ln.dot:
        try:
            d = dotmod.parse_dot(ln.dot)
        except dotmod.DotError as e:
            raise OpeningError(f"السطر {i}: {e}") from e
    amount = ln.qty * ln.unit_cost
    return Planned(replace(ln, amount=amount, dot=d), f"مخزون {row[0]}", "asset", _entry(INVENTORY, amount, "debit"))


def _plan_line(con, i: int, ln: OpeningLine) -> Planned:
    if ln.kind == "inventory":
        return _plan_inventory(con, i, ln)
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
        key = (ln.kind, ln.account, ln.partner_id, ln.customer_id, ln.supplier_id, ln.item_id, ln.dot, ln.unit_cost)
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
    body = [kind, day, note, [[x.kind, x.amount, x.account, x.partner_id, x.customer_id, x.supplier_id, x.item_id,
                               x.qty, x.unit_cost, x.dot] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _clearing_zero(con) -> None:
    if ledger.account_balance(con, CLEARING) != 0:
        raise OpeningError("الحساب الانتقالي 3500 لم يُصفَّر؛ الأرصدة الافتتاحية غير مكتملة")


def _write(con, by, op_id: int, number: str, day: str, planned, mirror: bool) -> list:
    """Writes lines and entries; returns the new line ids in order. Inventory lots only on a real opening."""
    ids = []
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
                    "customer_id, supplier_id, item_id, qty, unit_cost, dot, amount, journal_entry_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (line_id, op_id, no, ln.kind, ln.account, ln.partner_id, ln.customer_id, ln.supplier_id,
                     ln.item_id, ln.qty, ln.unit_cost, ln.dot, ln.amount, entry_id))
        if ln.kind == "inventory" and not mirror:
            _receive(con, by, number, no, line_id, ln, day)
        ids.append(line_id)
    return ids


def _receive(con, by, number: str, no: int, line_id: int, ln: OpeningLine, day: str) -> None:
    lot_id = con.execute(
        "INSERT INTO stock_lots (ref_no, item_id, opening_line_id, dot, mfg_key, received_date, qty_received, "
        "qty_on_hand, purchase_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"{number}/{no}", ln.item_id, line_id, ln.dot, dotmod.mfg_key(ln.dot) if ln.dot else None, day,
         ln.qty, ln.qty, ln.unit_cost)).lastrowid
    con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, opening_line_id, user_id) "
                "VALUES (?,?,'opening_in',?,?,?)", (ln.item_id, lot_id, ln.qty, line_id, by.id))
    con.execute("INSERT INTO cost_layers (item_id, opening_line_id, received_date, qty_received, cost_total, "
                "qty_remaining, cost_remaining) VALUES (?,?,?,?,?,?,?)",
                (ln.item_id, line_id, day, ln.qty, ln.amount, ln.qty, ln.amount))


def _unreceive(con, by, orig_line: int, new_line: int, ln: OpeningLine) -> None:
    lot = con.execute("SELECT id, qty_on_hand FROM stock_lots WHERE opening_line_id=?", (orig_line,)).fetchone()
    layer = con.execute("SELECT id, qty_remaining, cost_remaining FROM cost_layers WHERE opening_line_id=?",
                        (orig_line,)).fetchone()
    if lot[1] != ln.qty or layer[1] != ln.qty or layer[2] != ln.amount:
        raise OpeningError("لا يمكن عكس المخزون الافتتاحي: بعض وحداته صُرفت")
    con.execute("UPDATE stock_lots SET qty_on_hand = 0 WHERE id=?", (lot[0],))
    con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, opening_line_id, user_id) "
                "VALUES (?,?,'opening_out',?,?,?)", (ln.item_id, lot[0], -ln.qty, new_line, by.id))
    con.execute("UPDATE cost_layers SET qty_remaining = 0, cost_remaining = 0 WHERE id=?", (layer[0],))
    con.execute("INSERT INTO cost_allocations (opening_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                (new_line, layer[0], ln.qty, ln.amount))


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
        rows = con.execute("SELECT id, kind, amount, account, partner_id, customer_id, supplier_id, item_id, qty, "
                           "unit_cost, dot FROM opening_balance_lines WHERE opening_id=? ORDER BY line_no",
                           (opening_id,)).fetchall()
        originals = [OpeningLine(*r[1:]) for r in rows]
        today = date.today().isoformat()
        new_id, number = _header(con, by, key, digest, "reversal", today, reason, opening_id)
        planned = plan(con, originals).planned
        new_ids = _write(con, by, new_id, number, today, planned, mirror=True)
        for row, new_line, p in zip(rows, new_ids, planned, strict=True):
            if p.line.kind == "inventory":
                _unreceive(con, by, row[0], new_line, p.line)
        _clearing_zero(con)
        audit(con, by, "opening.reverse", "opening_balance", new_id, f"{number} عكس {src[0]}")
    return PostedOpening(new_id, number, False)


def choices(con: sqlite3.Connection, kind: str) -> list[tuple]:
    """Targets the opening screen offers for a line kind: (key, label)."""
    if kind == "account":
        marks = ",".join("?" * len(ASSET_ACCOUNTS + LIABILITY_ACCOUNTS))
        return [(code, f"{name} ({code})") for code, name in con.execute(
            f"SELECT code, name FROM accounts WHERE code IN ({marks}) ORDER BY code", ASSET_ACCOUNTS + LIABILITY_ACCOUNTS)]
    if kind == "customer":
        return con.execute("SELECT id, name FROM customers ORDER BY name_norm").fetchall()
    if kind == "supplier":
        return con.execute("SELECT id, name FROM suppliers WHERE active=1 ORDER BY name_norm").fetchall()
    if kind in ("partner_capital", "partner_current"):
        return con.execute("SELECT id, name FROM partners WHERE active=1 ORDER BY id").fetchall()
    if kind == "inventory":
        return [(iid, f"{name} · {code}") for iid, code, name, _on_hand in stock_adjustments.stocked_items(con)]
    return []


def current(con: sqlite3.Connection) -> tuple | None:
    """The live opening document: (id, number, date, line count), or None when none is live."""
    return con.execute(
        "SELECT o.id, o.number, o.opening_date, (SELECT count(*) FROM opening_balance_lines WHERE opening_id = o.id) "
        "FROM opening_balances o WHERE o.kind = 'opening' "
        "AND NOT EXISTS (SELECT 1 FROM opening_balances r WHERE r.reverses_id = o.id)").fetchone()


def opening_of(con: sqlite3.Connection, kind: str, party_id: int) -> int:
    """The live opening balance of one customer or supplier, zero when none or reversed."""
    column = "customer_id" if kind == "customer" else "supplier_id"
    return con.execute(f"SELECT coalesce(sum(amount), 0) FROM active_opening_lines WHERE {column}=?",
                       (party_id,)).fetchone()[0]
