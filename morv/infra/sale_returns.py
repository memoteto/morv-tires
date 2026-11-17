"""Sales return: one atomic reversing document; the invoice is never touched."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import costing
from morv.core import return_rules as rules
from morv.core.sales_rules import PAYMENT_ACCOUNT
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.inventory import COGS_ACCOUNTS
from morv.infra.users import audit, has_permission


class ReturnError(ValueError):
    """Sales return refused, with a message fit to show."""


@dataclass(frozen=True)
class ReturnLineIn:
    sale_line_id: int
    qty: int
    condition: str = "sound"


@dataclass(frozen=True)
class PostedReturn:
    id: int
    number: str
    total: int
    replayed: bool


def _payload_hash(sale_id, day, lines, refund_method, reason) -> str:
    body = [sale_id, day, refund_method, reason,
            [[x.sale_line_id, x.qty, x.condition] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _refund_method(con, sale_id: int, asked) -> str:
    methods = [r[0] for r in con.execute(
        "SELECT method FROM sale_payments WHERE sale_id=? ORDER BY id", (sale_id,))]
    if asked:
        if asked not in methods:
            raise ReturnError("طريقة الرد يجب أن تكون من وسائل دفع الفاتورة الأصلية")
        return asked
    if len(set(methods)) != 1:
        raise ReturnError("الفاتورة دُفعت بوسيلتين؛ حدّد طريقة الرد")
    return methods[0]


def _sale_line(con, sale_id: int, line_id: int) -> tuple:
    row = con.execute(
        "SELECT l.id, l.line_no, l.item_id, l.qty, l.net_total, i.kind FROM sale_lines l "
        "JOIN items i ON i.id = l.item_id WHERE l.id=? AND l.sale_id=?", (line_id, sale_id)).fetchone()
    if not row:
        raise ReturnError("سطر غير موجود في هذه الفاتورة")
    already = con.execute("SELECT coalesce(sum(qty), 0) FROM sale_return_lines WHERE sale_line_id=?",
                          (line_id,)).fetchone()[0]
    return (*row, already)


def _issue_parts(con, sale_id: int, line_id: int) -> tuple:
    issue = con.execute("SELECT id FROM stock_issues WHERE source_type='sale' AND source_id=? AND source_line_id=?",
                        (sale_id, line_id)).fetchone()
    if not issue:
        raise ReturnError("لا يوجد صرف مخزون لهذا السطر")
    layers = [(r[0], r[1], r[2]) for r in con.execute(
        "SELECT layer_id, qty, cost FROM cost_allocations WHERE issue_id=? ORDER BY id", (issue[0],))]
    lots = [(r[0], -r[1], 0) for r in con.execute(
        "SELECT lot_id, qty FROM stock_movements WHERE issue_id=? ORDER BY id", (issue[0],))]
    return layers, lots


def post_return(con: sqlite3.Connection, by, *, request_key: str, sale_id: int, lines: list, reason: str,
                refund_method: str | None = None, return_date: str | None = None,
                notes: str | None = None) -> PostedReturn:
    if by is None or not has_permission(con, by, "sales.return"):
        raise ReturnError("لا تملك صلاحية ترحيل مرتجعات البيع")
    key = (request_key or "").strip()
    if len(key) < 8:
        raise ReturnError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    if not lines:
        raise ReturnError("المرتجع بلا أسطر")
    try:
        why = rules.check_reason(reason)
        for ln in lines:
            rules.check_condition(ln.condition)
        day = ledger.valid_date(return_date or date.today().isoformat(), "تاريخ المرتجع")
    except (rules.ReturnRuleError, ledger.LedgerError) as e:
        raise ReturnError(str(e)) from e

    with transaction(con):
        sale = con.execute("SELECT id, number, sale_date FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise ReturnError("الفاتورة غير موجودة")
        if day < sale[2]:
            raise ReturnError("تاريخ المرتجع قبل تاريخ الفاتورة")
        # Installment returns need a joint receivable/deferred-interest settlement.
        # Refuse every installment sale, regardless of the requested refund method.
        if con.execute(
            "SELECT 1 FROM sale_payments WHERE sale_id=? AND method='installment' LIMIT 1",
            (sale_id,),
        ).fetchone():
            raise ReturnError("مرتجع التقسيط موقوف مؤقتًا حتى اكتمال تسويته المحاسبية")
        method = _refund_method(con, sale_id, refund_method)
        h = _payload_hash(sale_id, day, lines, method, why)
        old = con.execute("SELECT id, number, total, payload_hash FROM sale_returns WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[3] != h:
                raise ReturnError("مفتاح الطلب سبق استخدامه لمرتجع بمحتوى مختلف")
            return PostedReturn(old[0], old[1], old[2], True)
        if len({ln.sale_line_id for ln in lines}) != len(lines):
            raise ReturnError("سطر الفاتورة مكرر في المرتجع؛ اجمعه في سطر واحد")

        shaped = []
        for i, ln in enumerate(lines, start=1):
            _, _, item_id, sold, net_total, kind, already = _sale_line(con, sale_id, ln.sale_line_id)
            try:
                qty = rules.returnable(sold, already, ln.qty)
            except rules.ReturnRuleError as e:
                raise ReturnError(f"السطر {i}: {e}") from e
            value = rules.portion(net_total, already, qty, sold)
            layer_take, lot_take = [], []
            if kind in COGS_ACCOUNTS:
                layers, lots = _issue_parts(con, sale_id, ln.sale_line_id)
                try:
                    layer_take = costing.take_units(layers, already, qty)
                    lot_take = costing.take_units(lots, already, qty)
                except costing.InsufficientStock as e:
                    raise ReturnError(f"السطر {i}: {e}") from e
            cost = sum(c for _, _, c in layer_take)
            shaped.append((ln, item_id, kind, qty, value, cost, layer_take, lot_take))

        total = sum(x[4] for x in shaped)
        cost_total = sum(x[5] for x in shaped)
        if total <= 0 and cost_total <= 0:
            raise ReturnError("المرتجع بلا قيمة ولا تكلفة")
        return_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM sale_returns").fetchone()[0]
        number = f"SR-{return_id:06d}"

        entry = []
        if total:
            entry += [(rules.RETURNS_ACCOUNT, total, 0), (PAYMENT_ACCOUNT[method], 0, total)]
        stock_side: dict[str, int] = {}
        cogs_side: dict[str, int] = {}
        for ln, _item, kind, _qty, _value, cost, _layers, _lots in shaped:
            if not cost:
                continue
            acc = rules.stock_account(ln.condition)
            stock_side[acc] = stock_side.get(acc, 0) + cost
            cogs_side[COGS_ACCOUNTS[kind]] = cogs_side.get(COGS_ACCOUNTS[kind], 0) + cost
        entry += [(a, v, 0) for a, v in sorted(stock_side.items())]
        entry += [(a, 0, v) for a, v in sorted(cogs_side.items())]
        try:
            entry_id = ledger.post_entry(con, by, day, "sale_return", return_id,
                                         f"مرتجع بيع {number} على {sale[1]}", entry)
        except ledger.LedgerError as e:
            raise ReturnError(str(e)) from e

        con.execute(
            "INSERT INTO sale_returns (id, number, request_key, payload_hash, sale_id, return_date, total, "
            "cost_total, reason, refund_method, notes, journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (return_id, number, key, h, sale_id, day, total, cost_total, why, method,
             (notes or "").strip() or None, entry_id, by.id))
        for no, (ln, item_id, _kind, qty, value, cost, layer_take, lot_take) in enumerate(shaped, start=1):
            cur = con.execute(
                "INSERT INTO sale_return_lines (return_id, line_no, sale_line_id, qty, condition, value, cost) "
                "VALUES (?,?,?,?,?,?,?)", (return_id, no, ln.sale_line_id, qty, ln.condition, value, cost))
            if ln.condition != "sound":
                continue                                  # defective returns neither to sellable stock nor to its layer
            for lot_id, q, _ in lot_take:
                con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + ? WHERE id=?", (q, lot_id))
                con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, return_line_id, user_id) "
                            "VALUES (?,?,'return',?,?,?)", (item_id, lot_id, q, cur.lastrowid, by.id))
            for layer_id, q, c in layer_take:
                con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining + ?, "
                            "cost_remaining = cost_remaining + ? WHERE id=?", (q, c, layer_id))
                con.execute("INSERT INTO cost_allocations (return_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                            (cur.lastrowid, layer_id, -q, -c))
        audit(con, by, "sale.return", "sale_return", return_id, f"{number} · {total} · {why}")
    return PostedReturn(return_id, number, total, False)
