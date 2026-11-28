"""Sales invoices and returns: list and preview; new invoices come from the POS."""
from PySide6.QtWidgets import QDialog, QPushButton

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import sale_view
from morv.ui.listpage import ListPage
from morv.ui.sale_return_dialog import SaleReturnDialog

PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                  "credit": "آجل", "installment": "تقسيط"}


class SalesPage(ListPage):
    title = "فواتير البيع والمرتجعات"
    add_label = "فاتورة جديدة من نقطة البيع"
    search_placeholder = "رقم الفاتورة، اسم العميل، أو الموبايل (F2)"
    columns = [
        ("الفاتورة", lambda r: ltr(r[1])),
        ("التاريخ", lambda r: ltr(r[3])),
        ("العميل", lambda r: r[2]),
        ("الدفع", lambda r: "، ".join(PAYMENT_LABELS.get(m, m) for m in r[5].split("، ")) if r[5] else "—"),
        ("الإجمالي", lambda r: ltr(fmt(r[4]))),
        ("المرتجع", lambda r: ltr(fmt(r[6])) if r[6] else "—"),
    ]

    def can_edit(self) -> bool:
        return False

    def __init__(self, session):
        super().__init__(session)
        self.add_btn.setVisible(False)      # starting a sale from here is not built; unbuilt actions are hidden

    def can_view(self, con=None) -> bool:
        return self.session.can("sales.post", con)

    def fetch(self, con, query, filter_key, offset):
        if not self.can_view(con):
            return []
        return sale_view.list_sales(con, query, offset=offset)

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير البيع"
        return f"{sale_view.count(self.session.con)} فاتورة بيع · اضغط صفًا لمعاينته وعمل مرتجع منه"

    def build_preview(self, row_id: int) -> None:
        inv = sale_view.get_sale(self.session.con, row_id)
        self.heading(f"فاتورة {inv.number}", f"العميل: {inv.customer_name}")
        self.summary_metrics([
            ("الإجمالي", ltr(fmt(inv.total))),
            ("المرتجعات", ltr(fmt(inv.returned))),
            ("الصافي", ltr(fmt(inv.total - inv.returned))),
        ])
        self.summary_section("بيانات الفاتورة", "calendar")
        details = [("تاريخ الفاتورة", ltr(inv.sale_date))]
        if inv.vehicle_plate:
            details.append(("رقم اللوحة", ltr(inv.vehicle_plate)))
        if inv.odometer is not None:
            details.append(("قراءة العداد", ltr(str(inv.odometer))))
        if inv.invoice_discount:
            details.append(("خصم على الفاتورة", ltr(fmt(inv.invoice_discount))))
        if inv.tax_enabled:
            if inv.line_discount:
                details.append(("خصم الأصناف", ltr(fmt(inv.line_discount))))
            details.extend([
                ("أساس السعر", inv.tax_basis_label),
                ("الإجمالي قبل الضريبة", ltr(fmt(inv.value_before_tax))),
                ("ضريبة القيمة المضافة", ltr(fmt(inv.tax_total))),
            ])
        for label, value in (("الموبايل", inv.customer_mobile), ("عنوان العميل", inv.customer_address),
                             ("رقم العميل الضريبي", inv.customer_tax_id), ("الكاشير", inv.cashier_name),
                             ("وقت التسجيل", inv.recorded_at), ("ملاحظات", inv.notes)):
            if value:
                details.append((label, value))
        self.summary_details(details)
        self.summary_section("وسائل الدفع", "cash")
        self.summary_details([(PAYMENT_LABELS.get(p[0], p[0]), ltr(fmt(p[1]))) for p in inv.payments])
        self.summary_section("تفاصيل الأصناف", "tire")
        headers = ["الصنف", "الكمية", "سعر الوحدة", "الإجمالي"]
        rows = [(f"{ln.line_no}. {ln.item_name}", ltr(str(ln.qty)),
                 ltr(fmt(ln.unit_price)), ltr(fmt(ln.net_total))) for ln in inv.lines]
        if inv.tax_enabled:
            headers[2] = "سعر شامل" if inv.prices_tax_inclusive else "سعر غير شامل"
            headers.extend(["الخصم", "المعاملة الضريبية", "مبلغ الضريبة"])
            rows = [row + (ltr(fmt(ln.line_discount + ln.invoice_share)),
                           ln.tax_label, ltr(fmt(ln.tax_amount)))
                    for row, ln in zip(rows, inv.lines, strict=True)]
        self.summary_records(headers, rows)
        if inv.returned:
            self.summary_section("المرتجعات", "alert")
            self.summary_table(
                ["المستند", "التاريخ", "الإجمالي"],
                [(ltr(r[0]), ltr(r[1]), ltr(fmt(r[2]))) for r in inv.returns])
            reasons = [(ltr(r[0]), r[3]) for r in inv.returns if r[3]]
            if reasons:
                self.summary_details(reasons)
        buttons = []
        for label, handler in (("طباعة", self.print_sale), ("معاينة الطباعة", self.preview_sale)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, h=handler: h(inv))
            buttons.append(btn)
        if self.can_return():
            btn = QPushButton("مرتجع من الفاتورة")
            btn.clicked.connect(lambda: self.open_return(row_id))
            buttons.append(btn)
        self.actions(*buttons)

    def _shop(self):
        from morv.infra import shop
        return shop.load(self.session.con)

    def print_sale(self, inv) -> None:
        from morv.ui import printing
        from morv.ui.widgets import info
        shop_data = self._shop()
        try:
            where = printing.send(shop_data, printing.sale_document(inv, shop_data))
        except printing.PrintError as e:
            info(self, "الطباعة", str(e), error=True)
            return
        info(self, "الطباعة", f"أُرسلت الفاتورة {inv.number} إلى {where}")

    def preview_sale(self, inv) -> None:
        from morv.ui import printing
        shop_data = self._shop()
        printing.PrintPreview(self, shop_data, printing.sale_document(inv, shop_data)).exec()

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
