"""قراءة المخزون للعرض فقط: الدفعات وحركاتها وملخص الرصيد والقيمة؛ لا كتابة هنا."""
from morv.core.money import to_latin
from morv.core.text import norm_ar


def _like(q: str) -> tuple[str, str]:
    """نمطا البحث: الاسم الموحّد، والنص اللاتيني الكبير للكود وDOT والمرجع."""
    q = (q or "").strip()
    return f"%{norm_ar(q)}%", f"%{to_latin(q).upper().replace(' ', '')}%"


def lots(con, query: str = "", in_stock_only: bool = True, limit: int = 300, offset: int = 0) -> list[tuple]:
    """الدفعات بالأقدم تصنيعًا أولًا لكل صنف.

    الصف: (id, ref_no, item_name, item_code, dot, received_date, qty_received, qty_on_hand, unit_cost).
    """
    where, params = [], []
    if in_stock_only:
        where.append("l.qty_on_hand > 0")
    if (query or "").strip():
        name_like, lat = _like(query)
        where.append("(i.name_norm LIKE ? OR i.code LIKE ? OR l.dot LIKE ? OR l.ref_no LIKE ?)")
        params += [name_like, lat, lat, lat]
    sql = ("SELECT l.id, l.ref_no, i.name, i.code, l.dot, l.received_date, l.qty_received, l.qty_on_hand, "
           "l.purchase_unit_cost FROM stock_lots l JOIN items i ON i.id = l.item_id" +
           (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY i.name_norm, l.mfg_key IS NULL, l.mfg_key, l.id LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def lot_movements(con, lot_id: int) -> list[tuple]:
    """حركات دفعة بالترتيب: (kind, qty, at, picked_non_oldest, override_reason)."""
    return con.execute("SELECT kind, qty, at, picked_non_oldest, override_reason FROM stock_movements "
                       "WHERE lot_id = ? ORDER BY id", (lot_id,)).fetchall()


def summary(con, query: str = "", filter_key: str = "all", limit: int = 300, offset: int = 0) -> list[tuple]:
    """ملخص الأصناف المخزَّنة: (item_id, name, code, kind, on_hand, lots_count, book_value, min_qty).

    filter_key: all، أو stock (بها رصيد)، أو low (الرصيد تحت حد الطلب). الموقوف يظهر فقط إن بقي له رصيد.
    """
    where, params = ["i.kind IN ('tire', 'battery')", "(i.active = 1 OR coalesce(l.q, 0) > 0)"], []
    if filter_key == "stock":
        where.append("coalesce(l.q, 0) > 0")
    elif filter_key == "low":
        where.append("i.min_qty > 0 AND coalesce(l.q, 0) < i.min_qty")
    if (query or "").strip():
        name_like, lat = _like(query)
        where.append("(i.name_norm LIKE ? OR i.code LIKE ?)")
        params += [name_like, lat]
    sql = ("SELECT i.id, i.name, i.code, i.kind, coalesce(l.q, 0), coalesce(l.n, 0), coalesce(c.v, 0), i.min_qty "
           "FROM items i "
           "LEFT JOIN (SELECT item_id, sum(qty_on_hand) q, count(*) n FROM stock_lots WHERE qty_on_hand > 0 "
           "GROUP BY item_id) l ON l.item_id = i.id "
           "LEFT JOIN (SELECT item_id, sum(cost_remaining) v FROM cost_layers GROUP BY item_id) c ON c.item_id = i.id "
           "WHERE " + " AND ".join(where) + " ORDER BY i.name_norm LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def totals(con) -> tuple[int, int]:
    """(إجمالي الكمية على الرف، إجمالي القيمة الدفترية بالقرش)."""
    q = con.execute("SELECT coalesce(sum(qty_on_hand), 0) FROM stock_lots").fetchone()[0]
    v = con.execute("SELECT coalesce(sum(cost_remaining), 0) FROM cost_layers").fetchone()[0]
    return q, v
