"""Stock tabs: lots with their movements, and stocktake. Read-only."""
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import inventory, stock_view
from morv.ui.listpage import ListPage
from morv.ui.widgets import info

MOVE_LABELS = {"receipt": "استلام", "issue": "صرف", "return": "مرتجع"}


def _cost_ok(session) -> bool:
    return session.can("purchases.post") or session.can("items.cost_view")


class LotsPage(ListPage):
    title = "الدفعات (DOT)"
    search_placeholder = "الصنف، الكود، رمز DOT، أو مرجع الدفعة (F2)"
    filters = [("stock", "بها رصيد"), ("all", "كل الدفعات")]
    columns = [
        ("الصنف", lambda r: r[2]),
        ("الدفعة", lambda r: ltr(r[1])),
        ("DOT", lambda r: ltr(r[4]) if r[4] else "—"),
        ("الاستلام", lambda r: ltr(r[5])),
        ("المستلم", lambda r: ltr(str(r[6]))),
        ("المتبقي", lambda r: ltr(str(r[7]))),
        ("تكلفة الوحدة", lambda r: ltr(fmt(r[8])) if r[8] is not None else "—"),
    ]

    def __init__(self, session):
        super().__init__(session)
        self.add_btn.hide()

    def can_edit(self) -> bool:
        return False

    def fetch(self, query, filter_key, offset):
        rows = stock_view.lots(self.session.con, query, in_stock_only=filter_key == "stock", offset=offset)
        if _cost_ok(self.session):
            return rows
        return [r[:8] + (None,) for r in rows]

    def total(self) -> str:
        qty, _value = stock_view.totals(self.session.con)
        return f"إجمالي الرصيد على الرف: {qty}"

    def build_preview(self, row_id: int) -> None:
        row = next(r for r in self.model.rows if r[0] == row_id)
        self.heading(row[2], f"الدفعة {ltr(row[1])}")
        self.summary_metrics([
            ("DOT", ltr(row[4]) if row[4] else "—"),
            ("المستلم", ltr(str(row[6]))),
            ("المتبقي", ltr(str(row[7]))),
        ])
        self.summary_section("بيانات الدفعة", "tire")
        details = [("الدفعة", ltr(row[1])), ("تاريخ الاستلام", ltr(row[5]))]
        if row[8] is not None:
            details.append(("تكلفة الوحدة", ltr(fmt(row[8]))))
        self.summary_details(details)
        self.summary_section("حركة الدفعة", "truck")
        moves = stock_view.lot_movements(self.session.con, row_id)
        if moves:
            self.summary_table(["الحركة", "الكمية", "التاريخ"],
                               [(MOVE_LABELS.get(kind, kind), ltr(str(qty)), ltr(at))
                                for kind, qty, at, _non_oldest, _reason in moves])
        else:
            self.summary_note("لا توجد حركات مسجلة لهذه الدفعة.")
        for kind, _qty, _at, non_oldest, reason in moves:
            if kind == "issue" and non_oldest:
                self.summary_note(f"صرف من غير الأقدم: {reason}")



class StockPage(ListPage):
    title = "الجرد والتسويات"
    add_label = "تشغيل المطابقة"
    add_icon = "check"
    search_placeholder = "الصنف أو الكود (F2)"
    filters = [("all", "الكل"), ("stock", "بها رصيد"), ("low", "تحت حد الطلب")]
    columns = [
        ("الصنف", lambda r: r[1]),
        ("الكود", lambda r: ltr(r[2])),
        ("الرصيد", lambda r: ltr(str(r[4]))),
        ("الدفعات", lambda r: ltr(str(r[5]))),
        ("حد الطلب", lambda r: ltr(str(r[7])) if r[7] else "—"),
        ("القيمة الدفترية", lambda r: ltr(fmt(r[6])) if r[6] is not None else "—"),
    ]

    def can_edit(self) -> bool:
        return _cost_ok(self.session)

    def fetch(self, query, filter_key, offset):
        rows = stock_view.summary(self.session.con, query, filter_key, offset=offset)
        if _cost_ok(self.session):
            return rows
        return [r[:6] + (None,) + r[7:] for r in rows]

    def total(self) -> str:
        qty, value = stock_view.totals(self.session.con)
        text = f"إجمالي الرصيد: {qty}"
        return text + f" · القيمة الدفترية: {fmt(value)} جنيه" if _cost_ok(self.session) else text

    def build_preview(self, row_id: int) -> None:
        row = next(r for r in self.model.rows if r[0] == row_id)
        self.heading(row[1], f"{ltr(row[2])} · الرصيد {ltr(str(row[4]))}")
        metrics = [("الرصيد", ltr(str(row[4]))), ("الدفعات", ltr(str(row[5])))]
        if row[6] is not None:
            metrics.append(("القيمة الدفترية", ltr(fmt(row[6]))))
        self.summary_metrics(metrics)
        self.summary_section("تفاصيل الدفعات (LOT)", "tire")
        lots = [lot for lot in inventory.list_lots(self.session.con, row_id) if lot[5] > 0]
        if lots:
            if _cost_ok(self.session):
                self.summary_table(
                    ["DOT", "المتبقي", "تكلفة الشراء"],
                    [(ltr(dot) if dot else "—", ltr(str(on_hand)), ltr(fmt(unit_cost)))
                     for _id, _ref, dot, _received, _qty_in, on_hand, unit_cost in lots])
                self.summary_note("تكلفة الشراء التاريخية للدفعات مستقلة عن تقييم FIFO والقيمة الدفترية.")
            else:
                self.summary_table(
                    ["DOT", "المتبقي"],
                    [(ltr(dot) if dot else "—", ltr(str(on_hand)))
                     for _id, _ref, dot, _received, _qty_in, on_hand, _unit_cost in lots])
            self.summary_details([("مرجع الدفعة", ltr(ref))
                                  for _id, ref, _dot, _received, _qty_in, _on_hand, _unit_cost in lots])
        else:
            self.summary_note("لا رصيد لهذا الصنف.")
        self.summary_note("تسويات الجرد (زيادة أو نقص أو تلف) غير مفعّلة بعد؛ تُضاف مع دفتر التسويات.")

    def open_dialog(self, row_id):
        if row_id is not None or not self.can_edit():
            return
        problems = inventory.reconcile(self.session.con)
        if problems:
            info(self, "المطابقة", f"وُجدت {len(problems)} فروق:\n" + "\n".join(problems[:15]), error=True)
        else:
            info(self, "المطابقة", "متطابق: الدفعات والحركات وطبقات التكلفة والقيود بلا أي فرق.")
