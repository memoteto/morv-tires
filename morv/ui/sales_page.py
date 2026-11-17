"""Sales invoices and returns: list and preview; new invoices come from the POS."""
from PySide6.QtWidgets import QDialog, QPushButton

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import sale_view
from morv.ui.listpage import ListPage
from morv.ui.sale_return_dialog import SaleReturnDialog
from morv.ui.widgets import muted

PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                  "installment": "تقسيط"}


class SalesPage(ListPage):
    title = "فواتير البيع والمرتجعات"
    add_label = "فاتورة جديدة من نقطة البيع"
    search_placeholder = "رقم الفاتورة، اسم العميل، أو الموبايل (F2)"
    columns = [
        ("الرقم", lambda r: ltr(r[1])),
        ("التاريخ", lambda r: ltr(r[3])),
        ("العميل", lambda r: r[2]),
        ("الدفع", lambda r: r[5] or "—"),
        ("الإجمالي", lambda r: ltr(fmt(r[4]))),
        ("المرتجع", lambda r: ltr(fmt(r[6])) if r[6] else "—"),
    ]

    def can_edit(self) -> bool:
        return False

    def can_view(self) -> bool:
        return self.session.can("sales.post")

    def fetch(self, query, filter_key, offset):
        if not self.can_view():
            return []
        return sale_view.list_sales(self.session.con, query, offset=offset)

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير البيع"
        return f"{sale_view.count(self.session.con)} فاتورة بيع · اضغط صفًا لمعاينته وعمل مرتجع منه"

    def build_preview(self, row_id: int) -> None:
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
        if inv.returned:
            self.kv("المرتجعات", ltr(fmt(inv.returned)))
            self.kv("الصافي بعد المرتجع", ltr(fmt(inv.total - inv.returned)))
            for r in inv.returns:
                self.kv(ltr(r[0]), f"{ltr(r[1])} · {ltr(fmt(r[2]))} · {r[3]}")
        if self.can_return():
            if any(line.available > 0 for line in sale_view.returnable_lines(self.session.con, row_id)):
                btn = QPushButton("مرتجع من الفاتورة")
                btn.clicked.connect(lambda: self.open_return(row_id))
                self.actions(btn)
            else:
                self.actions(muted("الفاتورة مرتجعة بالكامل"))

    def can_return(self) -> bool:
        return self.session.can("sales.return")

    def open_return(self, sale_id: int) -> None:
        con = self.session.con
        dlg = SaleReturnDialog(self.session, sale_view.get_sale(con, sale_id),
                               sale_view.returnable_lines(con, sale_id),
                               sale_view.refund_methods(con, sale_id), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=sale_id)

    def open_dialog(self, row_id):
        if row_id is not None:
            self.show_preview_id(row_id)
