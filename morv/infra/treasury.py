"""Manual treasury movements and partners: validated, idempotent, always a balanced two-line entry."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core.money import fmt
from morv.infra import cash_closing, ledger
from morv.infra.db.connection import transaction
from morv.infra.treasury_view import ACCOUNTS as MONEY_ACCOUNTS
from morv.infra.users import audit, has_permission

SUPPLIERS_ACCOUNT = "2110"
EXPENSE_ACCOUNTS = ("5210", "5220", "5230", "5240", "5250", "5260", "5290")
KINDS = ("transfer", "expense", "partner_withdrawal", "partner_deposit", "partner_capital", "supplier_payment")
LABELS = {"transfer": "تحويل داخلي", "expense": "مصروف", "partner_withdrawal": "سحب شريك",
          "partner_deposit": "إيداع تمويل من شريك", "partner_capital": "زيادة رأس مال شريك",
          "supplier_payment": "سداد مورد", "reversal": "عكس حركة"}
PARTNER_KINDS = ("partner_withdrawal", "partner_deposit", "partner_capital")
MAX_PARTNERS = 99


class TreasuryError(ValueError):
    pass


class NeedsConfirmation(TreasuryError):
    """The movement is valid but looks unusual; repeat the call with confirmed=True to post it."""

    def __init__(self, warnings: list[str]):
        super().__init__("؛ ".join(warnings))
        self.warnings = warnings


@dataclass(frozen=True)
class PostedMovement:
    id: int
    number: str
    amount: int
    replayed: bool


@dataclass(frozen=True)
class Partner:
    id: int
    name: str
    account: str
    capital_account: str
    active: bool


def _require_manage(con, by) -> None:
    if by is None or not has_permission(con, by, "treasury.manage"):
        raise TreasuryError("لا تملك صلاحية تسجيل حركات الخزينة")


def _key(request_key) -> str:
    key = (request_key or "").strip()
    if len(key) < 8:
        raise TreasuryError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    return key


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------- Partners ----------

def add_partner(con: sqlite3.Connection, by, name: str) -> Partner:
    _require_manage(con, by)
    name = (name or "").strip()
    if not name:
        raise TreasuryError("اسم الشريك مطلوب")
    with transaction(con):
        if con.execute("SELECT 1 FROM partners WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
            raise TreasuryError("يوجد شريك بهذا الاسم")
        used = {int(r[0]) - 3300 for r in con.execute("SELECT current_account FROM partners")}
        n = next((i for i in range(1, MAX_PARTNERS + 1) if i not in used), None)
        if n is None:
            raise TreasuryError("استُنفدت حسابات الشركاء")
        capital, current = str(3100 + n), str(3300 + n)
        for code, title in ((capital, f"رأس مال {name}"), (current, f"جاري {name}")):
            con.execute("INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable, system, active) "
                        "VALUES (?, ?, '3', 'equity', 'credit', 1, 0, 1)", (code, title))
        pid = con.execute("INSERT INTO partners (name, current_account, capital_account) VALUES (?, ?, ?)",
                          (name, current, capital)).lastrowid
        audit(con, by, "partner.add", "partner", pid, f"{name} · {capital} · {current}")
    return Partner(pid, name, current, capital, True)


def set_partner_active(con: sqlite3.Connection, by, partner_id: int, active: bool) -> None:
    _require_manage(con, by)
    with transaction(con):
        row = con.execute("SELECT current_account, capital_account FROM partners WHERE id=?", (partner_id,)).fetchone()
        if row is None:
            raise TreasuryError("الشريك غير موجود")
        con.execute("UPDATE partners SET active=? WHERE id=?", (int(active), partner_id))
        con.execute("UPDATE accounts SET active=? WHERE code IN (?, ?)", (int(active), row[0], row[1]))
        audit(con, by, "partner.activate" if active else "partner.deactivate", "partner", partner_id)


def list_partners(con: sqlite3.Connection, active_only: bool = False) -> list[tuple]:
    """(id, name, current account, active, balance) where a positive balance is owed to the partner."""
    rows = con.execute("SELECT id, name, current_account, active FROM partners "
                       + ("WHERE active = 1 " if active_only else "") + "ORDER BY id").fetchall()
    return [(i, n, a, bool(act), partner_balance(con, i)) for i, n, a, act in rows]


def partner_balance(con: sqlite3.Connection, partner_id: int) -> int:
    row = con.execute("SELECT current_account FROM partners WHERE id=?", (partner_id,)).fetchone()
    if row is None:
        raise TreasuryError("الشريك غير موجود")
    return -ledger.account_balance(con, row[0])


def supplier_owed(con: sqlite3.Connection, supplier_id: int) -> int:
    invoiced = con.execute("SELECT coalesce(sum(total), 0) FROM purchase_invoices WHERE supplier_id=?",
                           (supplier_id,)).fetchone()[0]
    paid = con.execute(
        "SELECT coalesce(sum(CASE kind WHEN 'supplier_payment' THEN amount ELSE -amount END), 0) "
        "FROM treasury_movements WHERE supplier_id=?", (supplier_id,)).fetchone()[0]
    return invoiced - paid


def movement_info(con: sqlite3.Connection, movement_id: int) -> tuple | None:
    """(number, kind, reference, party name, number of its reversal, number it reverses)."""
    return con.execute(
        "SELECT m.number, m.kind, m.reference, coalesce(p.name, s.name, ''), "
        "(SELECT r.number FROM treasury_movements r WHERE r.reverses_id = m.id), "
        "(SELECT o.number FROM treasury_movements o WHERE o.id = m.reverses_id) "
        "FROM treasury_movements m LEFT JOIN partners p ON p.id = m.partner_id "
        "LEFT JOIN suppliers s ON s.id = m.supplier_id WHERE m.id = ?", (movement_id,)).fetchone()


def expense_accounts(con: sqlite3.Connection) -> list[tuple]:
    marks = ",".join("?" * len(EXPENSE_ACCOUNTS))
    return con.execute(f"SELECT code, name FROM accounts WHERE code IN ({marks}) AND active = 1 ORDER BY code",
                       EXPENSE_ACCOUNTS).fetchall()


# ---------- Movements ----------

def _sides(kind, money, other, partner_account, capital_account):
    """(debit, credit) accounts; money is the chosen cash or bank account, other the counterpart."""
    if kind == "transfer":
        return other, money
    if kind == "expense":
        return other, money
    if kind == "partner_withdrawal":
        return partner_account, money
    if kind == "partner_deposit":
        return money, partner_account
    if kind == "partner_capital":
        return money, capital_account
    return SUPPLIERS_ACCOUNT, money


def _account_ok(con, code, what) -> None:
    row = con.execute("SELECT active, postable FROM accounts WHERE code=?", (code,)).fetchone()
    if row is None or not row[0] or not row[1]:
        raise TreasuryError(f"{what} غير صالح")


def _post_locked(con, by, key, kind, amount, memo, money_account, other_account, partner_id, supplier_id, day,
                 reference, confirmed, skip_closed) -> PostedMovement:
    """Body of post_movement; the caller owns the transaction."""
    h = _digest(kind, day, amount, money_account, other_account, partner_id, supplier_id, memo, reference)
    old = con.execute("SELECT id, number, amount, payload_hash FROM treasury_movements WHERE request_key=?",
                      (key,)).fetchone()
    if old:
        if old[3] != h:
            raise TreasuryError("مفتاح الطلب سبق استخدامه لحركة بمحتوى مختلف")
        return PostedMovement(old[0], old[1], old[2], True)
    partner_account = capital_account = None
    if partner_id:
        row = con.execute("SELECT current_account, capital_account, active FROM partners WHERE id=?",
                          (partner_id,)).fetchone()
        if row is None or not row[2]:
            raise TreasuryError("الشريك غير موجود أو معطّل")
        partner_account, capital_account = row[0], row[1]
    if supplier_id:
        row = con.execute("SELECT active FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if row is None or not row[0]:
            raise TreasuryError("المورد غير موجود أو موقوف")
    debit, credit = _sides(kind, money_account, other_account, partner_account, capital_account)
    _account_ok(con, debit, "الحساب المدين")
    _account_ok(con, credit, "الحساب الدائن")

    warnings = _warnings(con, kind, day, amount, debit, credit, partner_id, supplier_id, skip_closed)
    if warnings and not confirmed:
        raise NeedsConfirmation(warnings)

    movement_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM treasury_movements").fetchone()[0]
    number = f"TM-{day[:4]}-{movement_id:06d}"
    entry_id = ledger.post_entry(con, by, day, "treasury_movement", movement_id,
                                 f"{LABELS[kind]} {number}: {memo}", [(debit, amount, 0), (credit, 0, amount)])
    con.execute(
        "INSERT INTO treasury_movements (id, number, request_key, payload_hash, kind, movement_date, amount, "
        "debit_account, credit_account, partner_id, supplier_id, reference, memo, journal_entry_id, user_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (movement_id, number, key, h, kind, day, amount, debit, credit, partner_id, supplier_id, reference,
         memo, entry_id, by.id))
    audit(con, by, "treasury.movement", "treasury_movement", movement_id, f"{number} · {kind} · {amount}")
    return PostedMovement(movement_id, number, amount, False)


def _prepare(con, by, request_key, kind, amount, memo, money_account, other_account, partner_id, supplier_id,
             movement_date, reference) -> tuple:
    """Validates a movement without writing; returns (key, memo, reference, day, partner, supplier)."""
    _require_manage(con, by)
    key = _key(request_key)
    if kind not in KINDS:
        raise TreasuryError("نوع الحركة غير صالح")
    if type(amount) is not int or amount <= 0:
        raise TreasuryError("المبلغ يجب أن يكون عددًا صحيحًا موجبًا بالقرش")
    memo = (memo or "").strip()
    if not memo:
        raise TreasuryError("البيان مطلوب")
    reference = (reference or "").strip() or None
    try:
        day = ledger.valid_date(movement_date or date.today().isoformat(), "تاريخ الحركة")
    except ledger.LedgerError as e:
        raise TreasuryError(str(e)) from e
    locked = ledger.locked_through(con)
    if locked and day <= locked:
        raise TreasuryError(f"التاريخ داخل فترة مالية مقفلة حتى {locked}")
    if money_account not in MONEY_ACCOUNTS:
        raise TreasuryError("حساب الخزينة يجب أن يكون الصندوق أو أحد حسابات البنوك والمحافظ")
    if kind == "transfer" and (other_account not in MONEY_ACCOUNTS or other_account == money_account):
        raise TreasuryError("التحويل يحتاج حسابين مختلفين من الصندوق والبنوك والمحافظ")
    if kind == "expense" and other_account not in EXPENSE_ACCOUNTS:
        raise TreasuryError("بند المصروف غير مسموح")
    if kind in PARTNER_KINDS and not partner_id:
        raise TreasuryError("الشريك مطلوب")
    if kind == "supplier_payment" and not supplier_id:
        raise TreasuryError("المورد مطلوب")
    if kind not in PARTNER_KINDS:
        partner_id = None
    if kind != "supplier_payment":
        supplier_id = None
    return key, memo, reference, day, partner_id, supplier_id


def post_movement(con: sqlite3.Connection, by, *, request_key: str, kind: str, amount: int, memo: str,
                  money_account: str, other_account: str | None = None, partner_id: int | None = None,
                  supplier_id: int | None = None, movement_date: str | None = None,
                  reference: str | None = None, confirmed: bool = False) -> PostedMovement:
    """Posts one manual movement or writes nothing; unusual-but-valid ones need confirmed=True."""
    key, memo, reference, day, partner_id, supplier_id = _prepare(
        con, by, request_key, kind, amount, memo, money_account, other_account, partner_id, supplier_id,
        movement_date, reference)
    with transaction(con):
        return _post_locked(con, by, key, kind, amount, memo, money_account, other_account, partner_id, supplier_id,
                            day, reference, confirmed, False)


def _warnings(con, kind, day, amount, debit, credit, partner_id, supplier_id, skip_closed=False) -> list[str]:
    out = []
    if credit in MONEY_ACCOUNTS and ledger.account_balance(con, credit) < amount:
        out.append("رصيد الحساب الدافع لا يكفي، وسيصبح سالبًا")
    closed = cash_closing.last_closed_date(con)
    if closed and day < closed and not skip_closed:
        out.append(f"التاريخ يقع في فترة مُقفلة (آخر إقفال {closed})")
    if kind == "supplier_payment":
        owed = supplier_owed(con, supplier_id)
        if amount > owed:
            out.append(f"المبلغ أكبر من مستحقات المورد ({fmt(max(owed, 0))})؛ سيُسجَّل الفرق دفعة مقدمة")
    if kind == "partner_withdrawal" and amount > partner_balance(con, partner_id):
        out.append("السحب يتجاوز رصيد جاري الشريك؛ سيصبح مدينًا للشركة")
    same = con.execute(
        "SELECT 1 FROM treasury_movements m WHERE m.kind=? AND m.movement_date=? AND m.amount=? "
        "AND m.debit_account=? AND m.credit_account=? AND coalesce(m.partner_id,0)=? AND coalesce(m.supplier_id,0)=? "
        "AND NOT EXISTS (SELECT 1 FROM treasury_movements r WHERE r.reverses_id = m.id) LIMIT 1",
        (kind, day, amount, debit, credit, partner_id or 0, supplier_id or 0)).fetchone()
    if same:
        out.append("سُجّلت حركة مماثلة (النوع والمبلغ والتاريخ والطرف) من قبل")
    return out


def reverse_movement(con: sqlite3.Connection, by, *, request_key: str, movement_id: int, memo: str) -> PostedMovement:
    """Mirrors a movement with a new entry dated today; the original stays untouched."""
    _require_manage(con, by)
    key = _key(request_key)
    memo = (memo or "").strip()
    if not memo:
        raise TreasuryError("سبب العكس مطلوب")
    with transaction(con):
        h = _digest("reversal", movement_id, memo)
        old = con.execute("SELECT id, number, amount, payload_hash FROM treasury_movements WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise TreasuryError("مفتاح الطلب سبق استخدامه لحركة بمحتوى مختلف")
            return PostedMovement(old[0], old[1], old[2], True)
        src = con.execute("SELECT kind, number, amount, debit_account, credit_account, partner_id, supplier_id "
                          "FROM treasury_movements WHERE id=?", (movement_id,)).fetchone()
        if src is None:
            raise TreasuryError("الحركة غير موجودة")
        if src[0] == "reversal":
            raise TreasuryError("لا يُعكس مستند عكسي")
        if con.execute("SELECT 1 FROM treasury_movements WHERE reverses_id=?", (movement_id,)).fetchone():
            raise TreasuryError("الحركة معكوسة من قبل")
        day = date.today().isoformat()
        new_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM treasury_movements").fetchone()[0]
        number = f"TM-{day[:4]}-{new_id:06d}"
        entry_id = ledger.post_entry(con, by, day, "treasury_movement", new_id,
                                     f"{LABELS['reversal']} {src[1]} {number}: {memo}",
                                     [(src[4], src[2], 0), (src[3], 0, src[2])])
        con.execute(
            "INSERT INTO treasury_movements (id, number, request_key, payload_hash, kind, movement_date, amount, "
            "debit_account, credit_account, partner_id, supplier_id, memo, reverses_id, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,'reversal',?,?,?,?,?,?,?,?,?,?)",
            (new_id, number, key, h, day, src[2], src[4], src[3], src[5], src[6], memo, movement_id, entry_id, by.id))
        audit(con, by, "treasury.reverse", "treasury_movement", new_id, f"{number} عكس {src[1]}")
    return PostedMovement(new_id, number, src[2], False)


def close_and_hand_over(con: sqlite3.Connection, by, *, request_key: str, closing_date: str, counted: int,
                        expected_book: int, memo: str | None, handover: int, partner_id: int | None = None,
                        bank_account: str | None = None, confirmed: bool = False) -> cash_closing.Closed:
    """Closes the till and moves the surplus out in one transaction; a failed handover undoes the closing."""
    _require_manage(con, by)
    if type(handover) is not int or not 0 < handover <= counted:
        raise TreasuryError("مبلغ التسليم يجب أن يكون موجبًا ولا يزيد على العد الفعلي")
    if bool(partner_id) == bool(bank_account):
        raise TreasuryError("اختر شريكًا أو حسابًا بنكيًا للتسليم")
    kind = "partner_withdrawal" if partner_id else "transfer"
    today = date.today().isoformat()
    other = bank_account if kind == "transfer" else None
    ckey, cday = cash_closing.prepare_close(con, by, request_key, closing_date, counted)
    # The closing key also derives the movement key, so a replay of the pair returns both unchanged.
    key, memo_h, reference, day, pid, sid = _prepare(
        con, by, ckey + ":h", kind, handover, "تسليم فائض الصندوق بعد الإقفال", "1110", other, partner_id, None,
        today, None)
    memo = (memo or "").strip() or None
    with transaction(con):
        closed = cash_closing.close_in_tx(con, by, ckey, cday, counted, expected_book, memo)
        _post_locked(con, by, key, kind, handover, memo_h, "1110", other, pid, sid, day, reference, confirmed, True)
    return closed
