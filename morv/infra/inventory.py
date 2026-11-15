"""رصيد الدفعات الفعلية وطبقات FIFO: الصرف، والقيمة الدفترية، ومطابقة الدفاتر.

الدفعات (ما على الرف وDOT) منفصلة عن الطبقات (التكلفة المحاسبية): تكلفة الصرف من أقدم طبقة
مهما كانت الدفعة المختارة. صرف الدفعة غير الأقدم تصنيعًا يحتاج صلاحية وسببًا مسجلًا.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import costing
from morv.infra import ledger
from morv.infra.db.connection import transaction
from morv.infra.users import audit, has_permission

COGS_ACCOUNTS = {"tire": "5110", "battery": "5120"}
INVENTORY_ACCOUNT = "1410"


class StockError(ValueError):
    """صرف مرفوض: رصيد غير كافٍ أو دفعة غير صالحة أو سبب ناقص."""


@dataclass(frozen=True)
class IssueResult:
    """نتيجة الصرف: رقمه، وتكلفته FIFO، وما صُرف من الدفعات والطبقات."""
    issue_id: int
    cost_total: int
    lot_picks: tuple
    allocations: tuple


def _lots(con, item_id: int) -> list:
    """دفعات الصنف التي فيها رصيد، من الأقدم تصنيعًا (بلا DOT آخرًا) ثم الأقدم استلامًا."""
    return con.execute("SELECT id, qty_on_hand FROM stock_lots WHERE item_id=? AND qty_on_hand > 0 "
                       "ORDER BY mfg_key IS NULL, mfg_key, received_date, id", (item_id,)).fetchall()


def suggest_lots(con, item_id: int, qty: int) -> dict:
    """الصرف المقترح {دفعة: كمية} من الأقدم تصنيعًا."""
    try:
        return costing.plan_lots(_lots(con, item_id), qty)
    except costing.InsufficientStock as e:
        raise StockError(str(e)) from e


def issue(con: sqlite3.Connection, by, item_id: int, qty: int, *, source_type: str, source_id: int,
          source_line_id: int, picks: dict | None = None, reason: str | None = None, issue_date: str | None = None) -> IssueResult:
    """صرف في معاملة مستقلة (انظر issue_in_tx للاستدعاء من معاملة مستند أكبر)."""
    with transaction(con):
        return issue_in_tx(con, by, item_id, qty, source_type=source_type, source_id=source_id,
                           source_line_id=source_line_id, picks=picks, reason=reason, issue_date=issue_date)


def issue_in_tx(con: sqlite3.Connection, by, item_id: int, qty: int, *, source_type: str, source_id: int,
                source_line_id: int, picks: dict | None = None, reason: str | None = None, issue_date: str | None = None) -> IssueResult:
    """صرف qty من صنف داخل معاملة المتصل: حركات الدفعات + تخصيص FIFO + قيد التكلفة معًا.

    picks={دفعة: كمية} اختياري؛ غيابه يعني اتباع المقترح. أي اختيار يخالف المقترح يلزمه
    صلاحية stock.pick_lot وسبب.
    """
    if not con.in_transaction:
        raise StockError("الصرف يجب أن يكون داخل معاملة")
    if by is None:
        raise StockError("الصرف يحتاج مستخدمًا")
    if type(qty) is not int or qty <= 0:
        raise StockError("الكمية عدد صحيح موجب")
    if type(source_id) is not int or type(source_line_id) is not int or not source_type:
        raise StockError("مرجع المصدر (المستند والسطر) مطلوب")
    if con.execute("SELECT 1 FROM stock_issues WHERE source_type=? AND source_id=? AND source_line_id=?",
                   (source_type, source_id, source_line_id)).fetchone():
        raise StockError("سطر المصدر هذا صُرف من قبل؛ لا يُصرف مرتين")
    item = con.execute("SELECT kind FROM items WHERE id=?", (item_id,)).fetchone()
    if not item or item[0] not in COGS_ACCOUNTS:
        raise StockError("صنف غير موجود أو غير مخزّن")
    try:
        day = ledger.valid_date(issue_date or date.today().isoformat(), "تاريخ الصرف")
    except ledger.LedgerError as e:
        raise StockError(str(e)) from e
    suggested = suggest_lots(con, item_id, qty)
    chosen = _check_picks(con, item_id, qty, picks) if picks else suggested
    override = chosen != suggested
    if override:
        if not has_permission(con, by, "stock.pick_lot"):
            raise StockError("لا تملك صلاحية اختيار دفعة غير الأقدم")
        if len((reason or "").strip()) < 3:
            raise StockError("اختيار دفعة غير الأقدم يحتاج سببًا مسجلًا")
    layers = con.execute("SELECT id, qty_remaining, cost_remaining FROM cost_layers "
                         "WHERE item_id=? AND qty_remaining > 0 ORDER BY id", (item_id,)).fetchall()
    try:
        alloc = costing.plan_fifo(layers, qty)
    except costing.InsufficientStock as e:
        raise StockError(str(e)) from e
    cost_total = sum(c for _, _, c in alloc)
    issue_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM stock_issues").fetchone()[0]
    entry_id = ledger.post_entry(con, by, day, "stock_issue", issue_id, f"تكلفة صرف #{issue_id}",
                                 [(COGS_ACCOUNTS[item[0]], cost_total, 0), (INVENTORY_ACCOUNT, 0, cost_total)])
    con.execute("INSERT INTO stock_issues (id, item_id, qty, cost_total, source_type, source_id, source_line_id, "
                "journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (issue_id, item_id, qty, cost_total, source_type, source_id, source_line_id, entry_id, by.id))
    for lot_id, q in chosen.items():
        con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - ? WHERE id=?", (q, lot_id))
        con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, issue_id, picked_non_oldest, "
                    "override_reason, user_id) VALUES (?,?,?,?,?,?,?,?)",
                    (item_id, lot_id, "issue", -q, issue_id, int(override), (reason or "").strip() or None, by.id))
    for layer_id, q, c in alloc:
        con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining - ?, cost_remaining = cost_remaining - ? "
                    "WHERE id=?", (q, c, layer_id))
        con.execute("INSERT INTO cost_allocations (issue_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                    (issue_id, layer_id, q, c))
    audit(con, by, "stock.issue", "stock_issue", issue_id,
          f"صنف {item_id} · {qty} · {cost_total}" + (f" · دفعة غير الأقدم: {reason.strip()}" if override else ""))
    return IssueResult(issue_id, cost_total, tuple(chosen.items()), tuple(alloc))


def _check_picks(con, item_id: int, qty: int, picks: dict) -> dict:
    """التحقق من اختيار الدفعات: تتبع الصنف، وكل كمية موجبة ضمن رصيدها، ومجموعها = qty."""
    out = {}
    for lot_id, q in picks.items():
        if type(q) is not int or q <= 0:
            raise StockError("كمية الدفعة المختارة عدد صحيح موجب")
        row = con.execute("SELECT qty_on_hand FROM stock_lots WHERE id=? AND item_id=?", (lot_id, item_id)).fetchone()
        if not row:
            raise StockError("دفعة لا تتبع هذا الصنف")
        if q > row[0]:
            raise StockError("الكمية المختارة أكبر من رصيد الدفعة")
        out[lot_id] = q
    if sum(out.values()) != qty:
        raise StockError("مجموع كميات الدفعات المختارة لا يساوي الكمية المطلوبة")
    return out


def on_hand(con, item_id: int) -> int:
    """الرصيد الفعلي للصنف من دفعاته."""
    return con.execute("SELECT coalesce(sum(qty_on_hand), 0) FROM stock_lots WHERE item_id=?", (item_id,)).fetchone()[0]


def book_value(con, item_id: int | None = None) -> int:
    """القيمة الدفترية بالقرش من طبقات التكلفة (لصنف أو للمخزون كله)."""
    if item_id is None:
        return con.execute("SELECT coalesce(sum(cost_remaining), 0) FROM cost_layers").fetchone()[0]
    return con.execute("SELECT coalesce(sum(cost_remaining), 0) FROM cost_layers WHERE item_id=?",
                       (item_id,)).fetchone()[0]


def list_lots(con, item_id: int) -> list:
    """دفعات الصنف: المرجع وDOT والمتبقي وتكلفة الشراء التاريخية (وليست قيمة دفترية)."""
    return con.execute("SELECT id, ref_no, dot, received_date, qty_received, qty_on_hand, purchase_unit_cost "
                       "FROM stock_lots WHERE item_id=? ORDER BY mfg_key IS NULL, mfg_key, id", (item_id,)).fetchall()


def reconcile(con: sqlite3.Connection) -> list:
    """مطابقة كاملة بين الدفعات والحركات والطبقات والتخصيصات والقيود؛ قائمة فارغة = متطابق."""
    bad = []
    q = con.execute
    # مجموعة الأصناف من الجانبين، فلا يفلت صنف موجود في جانب واحد فقط
    for r in q("SELECT x.item_id, coalesce(l.q, 0), coalesce(c.q, 0) FROM "
               "(SELECT item_id FROM stock_lots UNION SELECT item_id FROM cost_layers) x "
               "LEFT JOIN (SELECT item_id, sum(qty_on_hand) q FROM stock_lots GROUP BY item_id) l USING (item_id) "
               "LEFT JOIN (SELECT item_id, sum(qty_remaining) q FROM cost_layers GROUP BY item_id) c USING (item_id) "
               "WHERE coalesce(l.q, 0) <> coalesce(c.q, 0)"):
        bad.append(f"صنف {r[0]}: رصيد الدفعات {r[1]} ≠ رصيد الطبقات {r[2]}")
    for r in q("SELECT p.id FROM purchase_lines p WHERE "
               "(SELECT count(*) FROM stock_lots WHERE purchase_line_id = p.id) <> 1 "
               "OR (SELECT count(*) FROM cost_layers WHERE purchase_line_id = p.id) <> 1"):
        bad.append(f"سطر شراء {r[0]}: ليس له دفعة وطبقة واحدة بالضبط")
    for table, label in (("stock_lots", "دفعة"), ("cost_layers", "طبقة")):
        for r in q(f"SELECT t.id, t.item_id, p.item_id FROM {table} t JOIN purchase_lines p ON p.id = t.purchase_line_id "
                   "WHERE t.item_id <> p.item_id"):
            bad.append(f"{label} {r[0]}: الصنف {r[1]} ≠ صنف سطر الشراء {r[2]}")
    for r in q("SELECT m.id FROM stock_movements m LEFT JOIN stock_lots l ON l.id = m.lot_id WHERE l.id IS NULL"):
        bad.append(f"حركة {r[0]}: دفعتها غير موجودة")
    for r in q("SELECT a.id FROM cost_allocations a LEFT JOIN cost_layers c ON c.id = a.layer_id WHERE c.id IS NULL"):
        bad.append(f"تخصيص {r[0]}: طبقته غير موجودة")
    for r in q("SELECT l.id, l.qty_on_hand, coalesce(sum(m.qty), 0) FROM stock_lots l "
               "LEFT JOIN stock_movements m ON m.lot_id = l.id GROUP BY l.id "
               "HAVING l.qty_on_hand <> coalesce(sum(m.qty), 0)"):
        bad.append(f"دفعة {r[0]}: الرصيد {r[1]} ≠ مجموع الحركات {r[2]}")
    for r in q("SELECT c.id, c.qty_remaining, c.cost_remaining, c.qty_received - coalesce(sum(a.qty), 0), "
               "c.cost_total - coalesce(sum(a.cost), 0) FROM cost_layers c LEFT JOIN cost_allocations a "
               "ON a.layer_id = c.id GROUP BY c.id "
               "HAVING c.qty_remaining <> c.qty_received - coalesce(sum(a.qty), 0) "
               "OR c.cost_remaining <> c.cost_total - coalesce(sum(a.cost), 0)"):
        bad.append(f"طبقة {r[0]}: ({r[1]}, {r[2]}) ≠ المتوقع من التخصيصات ({r[3]}, {r[4]})")
    for r in q("SELECT s.id FROM stock_issues s "
               "LEFT JOIN (SELECT issue_id, sum(qty) q, sum(cost) c FROM cost_allocations GROUP BY issue_id) a "
               "ON a.issue_id = s.id "
               "LEFT JOIN (SELECT issue_id, -sum(qty) q FROM stock_movements WHERE issue_id IS NOT NULL "
               "GROUP BY issue_id) m ON m.issue_id = s.id "
               "WHERE s.qty <> coalesce(a.q, 0) OR s.cost_total <> coalesce(a.c, 0) OR s.qty <> coalesce(m.q, 0)"):
        bad.append(f"صرف {r[0]}: الرأس لا يطابق الحركات والتخصيصات")
    ledger_value = ledger.account_balance(con, INVENTORY_ACCOUNT)
    if ledger_value != book_value(con):
        bad.append(f"حساب المخزون {INVENTORY_ACCOUNT} = {ledger_value} ≠ قيمة الطبقات {book_value(con)}")
    for e in ledger.unbalanced_entries(con):
        bad.append(f"قيد {e} غير متوازن")
    for e in ledger.open_entries(con):
        bad.append(f"قيد {e}: عالق مفتوحًا لم يُرحَّل")
    for e in ledger.incomplete_entries(con):
        bad.append(f"قيد {e}: عدد السطور لا يطابق المعلن")
    return bad
