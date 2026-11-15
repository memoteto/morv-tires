"""تبويبا المخزون الأخيران: الدفعات (DOT) وحركاتها، والجرد والمطابقة؛ عرض فقط، والتسويات مؤجلة بقرار السياسة."""
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import inventory, stock_view
from morv.ui.listpage import ListPage
from morv.ui.widgets import info, muted

MOVE_LABELS = {"receipt": "استلام", "issue": "صرف"}


def _cost_ok(session) -> bool:
    """التكلفة والقيمة لمن يرحّل المشتريات أو يرى التكلفة فقط."""
    return session.can("purchases.post") or session.can("items.cost_view")


class LotsPage(ListPage):
    """دفعات الإطارات والبطاريات: DOT والمتبقي والتكلفة الأصلية، مع حركات كل دفعة في المعاينة."""
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
        """بناء الصفحة وإخفاء زر الإضافة (الدفعات تُنشأ بالاستلام فقط)."""
        super().__init__(session)
        self.add_btn.hide()

    def can_edit(self) -> bool:
        """لا إضافة ولا تعديل: الدفعة تنشأ من فاتورة الشراء."""
        return False

    def fetch(self, query, filter_key, offset):
        """صفحة من الدفعات؛ التكلفة تُحجب عمن لا يملك صلاحيتها."""
        rows = stock_view.lots(self.session.con, query, in_stock_only=filter_key == "stock", offset=offset)
        if _cost_ok(self.session):
            return rows
        return [r[:8] + (None,) for r in rows]

    def total(self) -> str:
        """العدّاد."""
        qty, _value = stock_view.totals(self.session.con)
        return f"إجمالي الرصيد على الرف: {qty}"

    def build_preview(self, row_id: int) -> None:
        """تفاصيل الدفعة وحركاتها."""
        row = next(r for r in self.model.rows if r[0] == row_id)
        self.heading(row[2], f"الدفعة {ltr(row[1])}")
        self.kv("رمز DOT", ltr(row[4]) if row[4] else "—")
        self.kv("تاريخ الاستلام", ltr(row[5]))
        self.kv("الكمية المستلمة", ltr(str(row[6])))
        self.kv("المتبقي", ltr(str(row[7])))
        if row[8] is not None:
            self.kv("تكلفة الوحدة", ltr(fmt(row[8])))
        for kind, qty, at, non_oldest, reason in stock_view.lot_movements(self.session.con, row_id):
            note = f" · صرف من غير الأقدم: {reason}" if non_oldest else ""
            self.kv(MOVE_LABELS.get(kind, kind), f"{ltr(str(qty))} · {ltr(at)}{note}")


class StockPage(ListPage):
    """الجرد والمطابقة: رصيد كل صنف وقيمته الدفترية، وزر مطابقة الدفعات مع التكلفة والقيود. التسويات غير مفعّلة بعد."""
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
        """زر المطابقة لمن يرى التكلفة (نتائجها تكشف القيم)."""
        return _cost_ok(self.session)

    def fetch(self, query, filter_key, offset):
        """صفحة من الملخص؛ القيمة تُحجب عمن لا يملك صلاحية التكلفة."""
        rows = stock_view.summary(self.session.con, query, filter_key, offset=offset)
        if _cost_ok(self.session):
            return rows
        return [r[:6] + (None,) + r[7:] for r in rows]

    def total(self) -> str:
        """العدّاد: الكمية، والقيمة لمن يملك صلاحيتها."""
        qty, value = stock_view.totals(self.session.con)
        text = f"إجمالي الرصيد: {qty}"
        return text + f" · القيمة الدفترية: {fmt(value)} جنيه" if _cost_ok(self.session) else text

    def build_preview(self, row_id: int) -> None:
        """دفعات الصنف التي بها رصيد، بالأقدم تصنيعًا أولًا (ترتيب الصرف المقترح)."""
        row = next(r for r in self.model.rows if r[0] == row_id)
        self.heading(row[1], f"{ltr(row[2])} · الرصيد {ltr(str(row[4]))}")
        lots = [lot for lot in inventory.list_lots(self.session.con, row_id) if lot[5] > 0]
        if not lots:
            self.preview_layout.addWidget(muted("لا رصيد لهذا الصنف."))
        for _id, ref, dot, _received, _qty_in, on_hand, unit_cost in lots:
            cost = f" × {ltr(fmt(unit_cost))}" if _cost_ok(self.session) else ""
            self.kv(f"DOT {ltr(dot)}" if dot else "بلا DOT", f"{ltr(str(on_hand))}{cost} · {ltr(ref)}")
        self.preview_layout.addWidget(muted("تسويات الجرد (زيادة أو نقص أو تلف) غير مفعّلة بعد؛ تُضاف مع دفتر التسويات."))

    def open_dialog(self, row_id):
        """زر «تشغيل المطابقة»: يعرض نتيجتها (متطابق أو قائمة الفروق)."""
        if row_id is not None or not self.can_edit():
            return
        problems = inventory.reconcile(self.session.con)
        if problems:
            info(self, "المطابقة", f"وُجدت {len(problems)} فروق:\n" + "\n".join(problems[:15]), error=True)
        else:
            info(self, "المطابقة", "متطابق: الدفعات والحركات وطبقات التكلفة والقيود بلا أي فرق.")
