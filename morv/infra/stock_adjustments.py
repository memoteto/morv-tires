"""Stock count adjustments: gains create lots and layers, losses issue FIFO cost to 5270."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from morv.core import costing
from morv.core import dot as dotmod
from morv.infra import inventory, ledger, purchasing
from morv.infra.db.connection import transaction
from morv.infra.users import audit, require

INVENTORY_ACCOUNT = "1410"
SHORTAGE_ACCOUNT = "5270"     # loss and damage
SURPLUS_ACCOUNT = "4220"      # gains, kept apart so a shortage is never hidden behind a surplus


class StockAdjustError(ValueError):
    """Adjustment refused, with a message fit to show."""


@dataclass(frozen=True)
class AdjustLine:
    item_id: int
    direction: str                      # "gain" or "loss"
    qty: int
    picks: dict | None = None           # loss: physical lots; default is the oldest DOT first
    dot: str | None = None              # gain: DOT of the found units
    source_lot_id: int | None = None    # gain: lot whose recorded cost applies
    manual_unit_cost: int | None = None # gain: only when no recorded cost exists


@dataclass(frozen=True)
class Priced:
    line: AdjustLine
    value: int
    cost_source: str
    source_lot_id: int | None
    unit_cost: int | None               # gain only
    alloc: tuple                        # loss only: (layer_id, qty, cost)
    lots: dict                          # loss only: lot_id -> qty
    dot: str | None


@dataclass(frozen=True)
class PostedAdjustment:
    id: int
    number: str
    gain_total: int
    loss_total: int
    replayed: bool


def _shape(lines) -> list:
    if not lines:
        raise StockAdjustError("التسوية بلا أسطر")
    seen = set()
    for i, ln in enumerate(lines, start=1):
        if ln.direction not in ("gain", "loss"):
            raise StockAdjustError(f"السطر {i}: النوع زيادة أو عجز")
        if type(ln.qty) is not int or ln.qty <= 0:
            raise StockAdjustError(f"السطر {i}: الكمية عدد صحيح موجب")
        if ln.item_id in seen:
            raise StockAdjustError(f"السطر {i}: الصنف مكرر في التسوية نفسها")
        seen.add(ln.item_id)
    return list(lines)


def _gain_cost(con, i: int, ln: AdjustLine) -> tuple:
    """Recorded cost first (source lot, then last purchase); a manual cost only when no record exists."""
    ref = None
    if ln.source_lot_id is not None:
        ref = con.execute("SELECT purchase_unit_cost FROM stock_lots WHERE id=? AND item_id=?",
                          (ln.source_lot_id, ln.item_id)).fetchone()
        if not ref:
            raise StockAdjustError(f"السطر {i}: الدفعة المصدر لا تتبع هذا الصنف")
        source = ("source_lot", ln.source_lot_id)
    elif ln.dot:
        ref = con.execute("SELECT purchase_unit_cost, id FROM stock_lots WHERE item_id=? AND dot=? "
                          "AND adjustment_line_id IS NULL ORDER BY id DESC LIMIT 1", (ln.item_id, ln.dot)).fetchone()
        source = ("source_lot", ref[1] if ref else None)
    if ref:
        if ln.manual_unit_cost is not None:
            raise StockAdjustError(f"السطر {i}: يوجد مرجع تكلفة مسجّل؛ لا تُدخل تكلفة يدوية")
        return ref[0], source[0], source[1]
    last = purchasing.last_cost(con, ln.item_id)
    if last is not None:
        if ln.manual_unit_cost is not None:
            raise StockAdjustError(f"السطر {i}: يوجد مرجع تكلفة مسجّل؛ لا تُدخل تكلفة يدوية")
        return last, "last_purchase", None
    if type(ln.manual_unit_cost) is not int or ln.manual_unit_cost <= 0:
        raise StockAdjustError(f"السطر {i}: لا مرجع لتكلفة الصنف؛ أدخل تكلفة يدوية أكبر من صفر")
    return ln.manual_unit_cost, "manual", None


def _price(con, i: int, ln: AdjustLine) -> Priced:
    kind = con.execute("SELECT kind FROM items WHERE id=?", (ln.item_id,)).fetchone()
    if not kind or kind[0] not in inventory.COGS_ACCOUNTS:
        raise StockAdjustError(f"السطر {i}: صنف غير موجود أو غير مخزّن")
    if ln.direction == "gain":
        d = None
        if ln.dot:
            try:
                d = dotmod.parse_dot(ln.dot)
            except dotmod.DotError as e:
                raise StockAdjustError(f"السطر {i}: {e}") from e
        unit, source, lot = _gain_cost(con, i, AdjustLine(ln.item_id, ln.direction, ln.qty, None, d,
                                                          ln.source_lot_id, ln.manual_unit_cost))
        return Priced(ln, unit * ln.qty, source, lot, unit, (), {}, d)
    try:
        suggested = inventory.suggest_lots(con, ln.item_id, ln.qty)
        lots = inventory._check_picks(con, ln.item_id, ln.qty, ln.picks) if ln.picks else suggested
        layers = con.execute("SELECT id, qty_remaining, cost_remaining FROM cost_layers "
                             "WHERE item_id=? AND qty_remaining > 0 ORDER BY id", (ln.item_id,)).fetchall()
        alloc = costing.plan_fifo(layers, ln.qty)
    except (inventory.StockError, costing.InsufficientStock) as e:
        raise StockAdjustError(f"السطر {i}: {e}") from e
    return Priced(ln, sum(c for _, _, c in alloc), "fifo", None, None, tuple(alloc), lots, None)


def preview(con: sqlite3.Connection, by, lines: list) -> list:
    """What a post would book, with no write: one Priced per line, for review before approval."""
    require(con, by, "stock.adjust", StockAdjustError)
    return [_price(con, i, ln) for i, ln in enumerate(_shape(lines), start=1)]


def _digest(kind, day, reason, lines) -> str:
    body = [kind, day, reason, [[x.item_id, x.direction, x.qty, sorted((x.picks or {}).items()), x.dot,
                                 x.source_lot_id, x.manual_unit_cost] for x in lines]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def _entry_lines(gain: int, loss: int) -> list:
    out = []
    if loss:
        out += [(SHORTAGE_ACCOUNT, loss, 0), (INVENTORY_ACCOUNT, 0, loss)]
    if gain:
        out += [(INVENTORY_ACCOUNT, gain, 0), (SURPLUS_ACCOUNT, 0, gain)]
    return out


def _reversal_lines(restored: int, removed: int) -> list:
    """Mirror entry: a reversed loss credits 5270, a reversed gain debits 4220."""
    out = []
    if restored:
        out += [(INVENTORY_ACCOUNT, restored, 0), (SHORTAGE_ACCOUNT, 0, restored)]
    if removed:
        out += [(SURPLUS_ACCOUNT, removed, 0), (INVENTORY_ACCOUNT, 0, removed)]
    return out


def _new_header(con, by, key, digest, kind, day, reason, reverses_id, memo, gain, loss):
    adj_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM stock_adjustments").fetchone()[0]
    number = f"SA-{adj_id:06d}"
    try:
        entry_id = ledger.post_entry(con, by, day, "stock_adjustment", adj_id, f"{memo} {number}: {reason}",
                                     _reversal_lines(gain, loss) if kind == "reversal" else _entry_lines(gain, loss))
    except ledger.LedgerError as e:
        raise StockAdjustError(str(e)) from e
    con.execute("INSERT INTO stock_adjustments (id, number, request_key, payload_hash, kind, adjust_date, reason, "
                "reverses_id, journal_entry_id, user_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (adj_id, number, key, digest, kind, day, reason, reverses_id, entry_id, by.id))
    return adj_id, number


def _write_gain(con, by, adj_id: int, number: str, no: int, p: Priced, day: str) -> None:
    ln = p.line
    line_id = con.execute("INSERT INTO stock_adjustment_lines (adjustment_id, line_no, item_id, direction, qty, "
                          "value, cost_source, source_lot_id) VALUES (?,?,?,'gain',?,?,?,?)",
                          (adj_id, no, ln.item_id, ln.qty, p.value, p.cost_source, p.source_lot_id)).lastrowid
    lot_id = con.execute(
        "INSERT INTO stock_lots (ref_no, item_id, adjustment_line_id, dot, mfg_key, received_date, qty_received, "
        "qty_on_hand, purchase_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"{number}/{no}", ln.item_id, line_id, p.dot, dotmod.mfg_key(p.dot) if p.dot else None, day,
         ln.qty, ln.qty, p.unit_cost)).lastrowid
    con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, adjustment_line_id, user_id) "
                "VALUES (?,?,'adjust_in',?,?,?)", (ln.item_id, lot_id, ln.qty, line_id, by.id))
    con.execute("INSERT INTO cost_layers (item_id, adjustment_line_id, received_date, qty_received, cost_total, "
                "qty_remaining, cost_remaining) VALUES (?,?,?,?,?,?,?)",
                (ln.item_id, line_id, day, ln.qty, p.value, ln.qty, p.value))


def _write_loss(con, by, adj_id: int, no: int, p: Priced) -> None:
    ln = p.line
    line_id = con.execute("INSERT INTO stock_adjustment_lines (adjustment_id, line_no, item_id, direction, qty, "
                          "value, cost_source) VALUES (?,?,?,'loss',?,?,'fifo')",
                          (adj_id, no, ln.item_id, ln.qty, p.value)).lastrowid
    for lot_id, q in p.lots.items():
        con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - ? WHERE id=?", (q, lot_id))
        con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, adjustment_line_id, user_id) "
                    "VALUES (?,?,'adjust_out',?,?,?)", (ln.item_id, lot_id, -q, line_id, by.id))
    for layer_id, q, c in p.alloc:
        con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining - ?, cost_remaining = cost_remaining - ? "
                    "WHERE id=?", (q, c, layer_id))
        con.execute("INSERT INTO cost_allocations (adjustment_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                    (line_id, layer_id, q, c))


def post_adjustment(con: sqlite3.Connection, by, *, request_key: str, adjust_date: str | None, reason: str,
                    lines: list) -> PostedAdjustment:
    require(con, by, "stock.adjust", StockAdjustError)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise StockAdjustError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise StockAdjustError("سبب التسوية مطلوب")
    try:
        day = ledger.valid_date(adjust_date or date.today().isoformat(), "تاريخ التسوية")
    except ledger.LedgerError as e:
        raise StockAdjustError(str(e)) from e
    shaped = _shape(lines)
    digest = _digest("adjustment", day, reason, shaped)
    with transaction(con):
        old = con.execute("SELECT id, number, payload_hash FROM stock_adjustments WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[2] != digest:
                raise StockAdjustError("مفتاح الطلب سبق استخدامه لتسوية بمحتوى مختلف")
            g, lo = _totals(con, old[0])
            return PostedAdjustment(old[0], old[1], g, lo, True)
        priced = [_price(con, i, ln) for i, ln in enumerate(shaped, start=1)]
        gain = sum(p.value for p in priced if p.line.direction == "gain")
        loss = sum(p.value for p in priced if p.line.direction == "loss")
        adj_id, number = _new_header(con, by, key, digest, "adjustment", day, reason, None, "تسوية جرد", gain, loss)
        for no, p in enumerate(priced, start=1):
            if p.line.direction == "gain":
                _write_gain(con, by, adj_id, number, no, p, day)
            else:
                _write_loss(con, by, adj_id, no, p)
        audit(con, by, "stock.adjust", "stock_adjustment", adj_id, f"{number} · زيادة {gain} · عجز {loss}")
    return PostedAdjustment(adj_id, number, gain, loss, False)


def _totals(con, adj_id: int) -> tuple:
    rows = dict(con.execute("SELECT direction, sum(value) FROM stock_adjustment_lines WHERE adjustment_id=? "
                            "GROUP BY direction", (adj_id,)).fetchall())
    return rows.get("gain", 0), rows.get("loss", 0)


def reverse_adjustment(con: sqlite3.Connection, by, *, request_key: str, adjustment_id: int,
                       reason: str) -> PostedAdjustment:
    """Restores a loss to its original lots and layers; removes a gain only while untouched."""
    require(con, by, "stock.adjust", StockAdjustError)
    key = (request_key or "").strip()
    if len(key) < 8:
        raise StockAdjustError("مفتاح الطلب مطلوب (8 أحرف على الأقل)")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise StockAdjustError("سبب العكس مطلوب")
    digest = _digest("reversal", adjustment_id, reason, [])
    with transaction(con):
        old = con.execute("SELECT id, number, payload_hash FROM stock_adjustments WHERE request_key=?",
                          (key,)).fetchone()
        if old:
            if old[2] != digest:
                raise StockAdjustError("مفتاح الطلب سبق استخدامه لعكس بمحتوى مختلف")
            g, lo = _totals(con, old[0])
            return PostedAdjustment(old[0], old[1], g, lo, True)
        src = con.execute("SELECT number, kind FROM stock_adjustments WHERE id=?", (adjustment_id,)).fetchone()
        if not src or src[1] != "adjustment":
            raise StockAdjustError("تسوية الجرد غير موجودة")
        if con.execute("SELECT 1 FROM stock_adjustments WHERE reverses_id=?", (adjustment_id,)).fetchone():
            raise StockAdjustError("تسوية الجرد معكوسة سابقًا")
        originals = con.execute("SELECT id, item_id, direction, qty, value FROM stock_adjustment_lines "
                                "WHERE adjustment_id=? ORDER BY line_no", (adjustment_id,)).fetchall()
        gain = sum(o[4] for o in originals if o[2] == "loss")      # restoring a loss is a gain, and the reverse
        loss = sum(o[4] for o in originals if o[2] == "gain")
        today = date.today().isoformat()
        new_id, number = _new_header(con, by, key, digest, "reversal", today, reason, adjustment_id,
                                     f"عكس تسوية جرد {src[0]}", gain, loss)
        for no, (line_id, item_id, direction, qty, value) in enumerate(originals, start=1):
            if direction == "loss":
                _restore_loss(con, by, new_id, no, line_id, item_id, qty, value)
            else:
                _remove_gain(con, by, new_id, no, line_id, item_id, qty, value)
        audit(con, by, "stock.adjust.reverse", "stock_adjustment", new_id, f"{number} عكس {src[0]}")
    return PostedAdjustment(new_id, number, gain, loss, False)


def _restore_loss(con, by, new_id: int, no: int, orig_line: int, item_id: int, qty: int, value: int) -> None:
    line_id = con.execute("INSERT INTO stock_adjustment_lines (adjustment_id, line_no, item_id, direction, qty, "
                          "value, cost_source) VALUES (?,?,?,'gain',?,?,'restore')",
                          (new_id, no, item_id, qty, value)).lastrowid
    for lot_id, q in con.execute("SELECT lot_id, -qty FROM stock_movements WHERE adjustment_line_id=? "
                                 "AND kind='adjust_out'", (orig_line,)).fetchall():
        con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + ? WHERE id=?", (q, lot_id))
        con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, adjustment_line_id, user_id) "
                    "VALUES (?,?,'adjust_in',?,?,?)", (item_id, lot_id, q, line_id, by.id))
    for layer_id, q, c in con.execute("SELECT layer_id, qty, cost FROM cost_allocations WHERE adjustment_line_id=?",
                                      (orig_line,)).fetchall():
        con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining + ?, cost_remaining = cost_remaining + ? "
                    "WHERE id=?", (q, c, layer_id))
        con.execute("INSERT INTO cost_allocations (adjustment_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                    (line_id, layer_id, -q, -c))


def _remove_gain(con, by, new_id: int, no: int, orig_line: int, item_id: int, qty: int, value: int) -> None:
    lot = con.execute("SELECT id, qty_on_hand FROM stock_lots WHERE adjustment_line_id=?", (orig_line,)).fetchone()
    layer = con.execute("SELECT id, qty_remaining, cost_remaining FROM cost_layers WHERE adjustment_line_id=?",
                        (orig_line,)).fetchone()
    if lot[1] != qty or layer[1] != qty or layer[2] != value:
        raise StockAdjustError("لا يمكن عكس الزيادة: بعض وحداتها صُرفت أو بيعت")
    line_id = con.execute("INSERT INTO stock_adjustment_lines (adjustment_id, line_no, item_id, direction, qty, "
                          "value, cost_source) VALUES (?,?,?,'loss',?,?,'fifo')",
                          (new_id, no, item_id, qty, value)).lastrowid
    con.execute("UPDATE stock_lots SET qty_on_hand = 0 WHERE id=?", (lot[0],))
    con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, adjustment_line_id, user_id) "
                "VALUES (?,?,'adjust_out',?,?,?)", (item_id, lot[0], -qty, line_id, by.id))
    con.execute("UPDATE cost_layers SET qty_remaining = 0, cost_remaining = 0 WHERE id=?", (layer[0],))
    con.execute("INSERT INTO cost_allocations (adjustment_line_id, layer_id, qty, cost) VALUES (?,?,?,?)",
                (line_id, layer[0], qty, value))


def stocked_items(con: sqlite3.Connection) -> list[tuple]:
    """Active stocked items for the adjustment screen: (id, code, name, on hand)."""
    return con.execute(
        "SELECT i.id, i.code, i.name, coalesce(sum(l.qty_on_hand), 0) FROM items i "
        "LEFT JOIN stock_lots l ON l.item_id = i.id WHERE i.kind IN ('tire', 'battery') AND i.active = 1 "
        "GROUP BY i.id ORDER BY i.name_norm").fetchall()


def history(con: sqlite3.Connection) -> list[tuple]:
    """Adjustments and reversals, newest first: (id, number, kind, date, gain, loss, reason, reversed)."""
    return con.execute(
        "SELECT a.id, a.number, a.kind, a.adjust_date, "
        "coalesce((SELECT sum(value) FROM stock_adjustment_lines WHERE adjustment_id = a.id AND direction='gain'), 0), "
        "coalesce((SELECT sum(value) FROM stock_adjustment_lines WHERE adjustment_id = a.id AND direction='loss'), 0), "
        "a.reason, EXISTS (SELECT 1 FROM stock_adjustments x WHERE x.reverses_id = a.id) "
        "FROM stock_adjustments a ORDER BY a.id DESC").fetchall()
