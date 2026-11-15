"""شاشة فواتير البيع والمرتجعات: قائمة الفواتير المرحّلة ومعاينتها. الفواتير الجديدة تُنشأ من نقطة البيع."""
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import sale_view
from morv.ui.listpage import ListPage

PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل / محفظة", "wallet": "محفظة"}


class SalesPage(ListPage):
    """قائمة فواتير البيع المرحّلة. الفاتورة لا تُعدَّل بعد الترحيل؛ التصحيح بمستند عكسي في إصدار لاحق."""
    title = "فواتير البيع والمرتجعات"
    add_label = "فاتورة جديدة من نقطة البيع"
    search_placeholder = "رقم الفاتورة، اسم العميل، أو الموبايل (F2)"
    columns = [
        ("الفاتورة", lambda r: ltr(r[1])),
        ("العميل", lambda r: r[2]),
        ("التاريخ", lambda r: ltr(r[3])),
        ("الإجمالي", lambda r: ltr(fmt(r[4]))),
        ("الدفع", lambda r: r[5] or "—"),
    ]

    def can_edit(self) -> bool:
        """لا تُنشأ فواتير من هذه الشاشة؛ الإنشاء من نقطة البيع."""
        return False

    def can_view(self) -> bool:
        """القائمة لمن يرحّل فواتير البيع فقط."""
        return self.session.can("sales.post")

    def fetch(self, query, filter_key, offset):
        """صفحة من القائمة؛ فارغة لمن لا يملك صلاحية الاطلاع."""
        if not self.can_view():
            return []
        return sale_view.list_sales(self.session.con, query, offset=offset)

    def total(self) -> str:
        """العدّاد، أو سبب غياب القائمة."""
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير البيع"
        return f"{sale_view.count(self.session.con)} فاتورة بيع"

    def build_preview(self, row_id: int) -> None:
        """تفاصيل الفاتورة وأسطرها ووسائل دفعها."""
        inv = sale_view.get_sale(self.session.con, row_id)
        self.heading(inv.number, f"العميل: {inv.customer_name}")
        self.kv("تاريخ الفاتورة", ltr(inv.sale_date))
        if inv.vehicle_plate:
            self.kv("رقم اللوحة", ltr(inv.vehicle_plate))
        if inv.odometer is not None:
            self.kv("قراءة العداد", ltr(str(inv.odometer)))
        self.kv("الإجمالي", ltr(fmt(inv.total)))
        if inv.invoice_discount:
            self.kv("خصم على الفاتورة", ltr(fmt(inv.invoice_discount)))
        for p in inv.payments:
            self.kv(PAYMENT_LABELS.get(p[0], p[0]), ltr(fmt(p[1])))
        for ln in inv.lines:
            self.kv(f"{ln.line_no}. {ln.item_name}",
                    f"{ltr(str(ln.qty))} × {ltr(fmt(ln.unit_price))} = {ltr(fmt(ln.net_total))}")

    def open_dialog(self, row_id):
        """صف موجود: معاينته فقط."""
        if row_id is not None:
            self.show_preview_id(row_id)
