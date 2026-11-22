"""Suppliers screen: list, preview and add or edit dialog."""
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QWidget,
)

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import purchase_view, suppliers
from morv.infra.suppliers import Supplier
from morv.ui.listpage import ListPage
from morv.ui.modal import Modal
from morv.ui.purchase_return_dialogs import PurchaseReturnDialog, SupplierReturnsDialog
from morv.ui.widgets import ask, danger_button, field, form, ltr_edit, muted, primary_button


class SuppliersPage(ListPage):
    title = "الموردون"
    add_label = "مورد جديد"
    search_placeholder = "اسم المورد أو الموبايل (F2)"
    filters = [("all", "الكل"), ("inactive", "الموقوفون")]
    row_icon = staticmethod(lambda r: "truck")
    columns = [
        ("المورد", lambda r: r[1]),
        ("الموبايل", lambda r: ltr(r[2])),
        ("العنوان", lambda r: r[7] or "—"),
        ("السداد", lambda r: f"آجل {r[5]} يومًا" if r[5] else "نقدي"),
        ("يورّد", lambda r: suppliers.SUPPLIES.get(r[6], "")),
        ("الحالة", lambda r: "نشط" if r[4] else "موقوف"),
    ]

    def can_edit(self) -> bool:
        return self.session.can("suppliers.manage")

    def fetch(self, con, query, filter_key, offset):
        return suppliers.search(con, query, include_inactive=filter_key == "inactive", offset=offset)

    def total(self) -> str:
        return f"{suppliers.count(self.session.con)} مورد نشط"

    def build_preview(self, row_id: int) -> None:
        s = suppliers.get(self.session.con, row_id)
        self.heading(s.name, suppliers.SUPPLIES[s.supplies] + ("" if s.active else " · موقوف"))
        contact = [("الموبايل", ltr(s.mobile)), ("جهة الاتصال", s.contact_person),
                   ("العنوان", s.address)]
        if s.phone2:
            contact.insert(1, ("هاتف إضافي", ltr(s.phone2)))
        self.summary_section("بيانات التواصل", "phone")
        self.summary_details(contact)
        self.summary_section("بيانات السداد", "cash")
        payment = [("السداد", (f"آجل {s.payment_days} يومًا" if s.payment_days else "نقدي") +
                    f" · {suppliers.PAY_METHODS[s.pay_method]}")]
        if s.credit_limit:
            payment.append(("حد الائتمان لنا", ltr(fmt(s.credit_limit))))
        self.summary_details(payment)
        extra = []
        if s.commercial_reg:
            extra.append(("السجل التجاري", ltr(s.commercial_reg)))
        if s.tax_id:
            extra.append(("الرقم الضريبي", ltr(s.tax_id)))
        if s.bank_info:
            extra.append(("بيانات التحويل", ltr(s.bank_info)))
        if s.notes:
            extra.append(("ملاحظات", s.notes))
        if extra:
            self.summary_section("بيانات إضافية", "truck")
            self.summary_details(extra)
        if self.session.can("purchases.post") or self.session.can("items.cost_view"):
            count, total = purchase_view.supplier_totals(self.session.con, s.id)
            self.summary_metrics([("فواتير الشراء", ltr(str(count))),
                                  ("إجمالي الفواتير", f"{ltr(fmt(total))} جنيه")])
        self.summary_note("رصيد المورد وسداده يأتيان مع مرحلة الخزينة.")
        if self.can_edit():
            edit = primary_button("تعديل", "edit")
            edit.clicked.connect(lambda: self.open_dialog(s.id))
            toggle = danger_button("إيقاف" if s.active else "تفعيل", "pause" if s.active else "play")
            toggle.clicked.connect(lambda: self.toggle_active(s))
            self.actions(edit, toggle)
        if self.session.can("purchases.post"):
            send_back = QPushButton("مرتجع للمورد")
            send_back.clicked.connect(lambda: self.open_return(s))
            returns = QPushButton("مرتجعات المورد")
            returns.clicked.connect(lambda: self.open_returns(s))
            self.actions(send_back, returns)

    def open_return(self, s: Supplier) -> None:
        dlg = PurchaseReturnDialog(self.session, s.id, s.name, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=s.id)

    def open_returns(self, s: Supplier) -> None:
        SupplierReturnsDialog(self.session, s.id, s.name, self).exec()
        self.reload(keep_id=s.id)

    def toggle_active(self, s: Supplier) -> None:
        verb = "إيقاف" if s.active else "تفعيل"
        if ask(self, verb, f"{verb} المورد «{s.name}»؟", danger=s.active):
            suppliers.set_active(self.session.con, self.session.user, s.id, not s.active)
            self.reload(keep_id=s.id)

    def open_dialog(self, row_id):
        if not self.can_edit():
            return
        dlg = SupplierDialog(self.session, suppliers.get(self.session.con, row_id) if row_id else None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.saved.id)


class SupplierDialog(Modal):
    def __init__(self, session, s: Supplier | None, parent=None):
        super().__init__("تعديل مورد" if s else "مورد جديد", parent, width=600)
        self.session, self.s, self.saved = session, s, None
        f = form()
        self.name = QLineEdit(s.name if s else "")
        self.name.setPlaceholderText("مثال: الشركة المتحدة لتجارة الإطارات")
        self.mobile = ltr_edit("01xxxxxxxxx")
        self.mobile.setText(s.mobile if s else "")
        self.address = QLineEdit(s.address if s else "")
        self.address.setPlaceholderText("المدينة — المنطقة")
        self.notes = QPlainTextEdit(s.notes if s else "")
        self.notes.setPlaceholderText("أي معلومة تفيد عند التعامل مع المورد")
        self.notes.setFixedHeight(64)
        f.addRow(field("اسم المورد *", self.name))
        f.addRow(field("رقم الموبايل *", self.mobile))
        f.addRow(field("العنوان", self.address))
        f.addRow(field("ملاحظات", self.notes))
        self.body.addLayout(f)
        self.body.addWidget(muted("الرصيد السابق مع المورد يُدخل مع مرحلة الخزينة."))

        self.more = QCheckBox("بيانات إضافية (اختيارية)")
        self.more.toggled.connect(lambda on: self.more_box.setVisible(on))
        self.body.addWidget(self.more)
        self.more_box = QWidget()
        mf = form()
        self.contact = QLineEdit(s.contact_person if s else "")
        self.contact.setPlaceholderText("مثال: أ/ مصطفى")
        self.phone2 = ltr_edit("02xxxxxxxx")
        self.phone2.setText(s.phone2 if s else "")
        self.days = QSpinBox()
        self.days.setRange(0, 365)
        self.days.setValue(s.payment_days if s else 0)
        self.limit = ltr_edit("0.00")
        self.limit.setText(fmt(s.credit_limit) if s and s.credit_limit else "")
        self.supplies = QComboBox()
        for k, v in suppliers.SUPPLIES.items():
            self.supplies.addItem(v, k)
        self.pay = QComboBox()
        for k, v in suppliers.PAY_METHODS.items():
            self.pay.addItem(v, k)
        if s:
            self.supplies.setCurrentIndex(list(suppliers.SUPPLIES).index(s.supplies))
            self.pay.setCurrentIndex(list(suppliers.PAY_METHODS).index(s.pay_method))
        self.creg = ltr_edit("")
        self.creg.setText(s.commercial_reg if s else "")
        self.tax = ltr_edit("")
        self.tax.setText(s.tax_id if s else "")
        self.bank = ltr_edit("اسم البنك ورقم الحساب أو رقم المحفظة")
        self.bank.setText(s.bank_info if s else "")
        mf.addRow(field("اسم المسؤول / المندوب", self.contact))
        mf.addRow(field("هاتف إضافي", self.phone2))
        mf.addRow(field("مدة السداد (أيام)", self.days))
        mf.addRow(muted("تحسب تاريخ استحقاق فواتير الشراء الآجلة تلقائيًا"))
        mf.addRow(field("حد الائتمان الممنوح لنا", self.limit))
        mf.addRow(field("يورّد لنا", self.supplies))
        mf.addRow(field("طريقة السداد المفضلة", self.pay))
        mf.addRow(field("السجل التجاري", self.creg))
        mf.addRow(field("رقم التسجيل الضريبي", self.tax))
        mf.addRow(field("بيانات التحويل", self.bank))
        self.more_box.setLayout(mf)
        self.more_box.setVisible(False)
        self.body.addWidget(self.more_box)
        if s and any((s.contact_person, s.phone2, s.payment_days, s.credit_limit, s.commercial_reg, s.tax_id, s.bank_info)):
            self.more.setChecked(True)
        self.add_footer("حفظ", self.save)
        self.name.setFocus()

    def save(self) -> None:
        try:
            limit = parse_money(self.limit.text()) if self.limit.text().strip() else 0
        except MoneyError as e:
            self.notice.show_message(f"حد الائتمان: {e}")
            return
        s = Supplier(self.s.id if self.s else None, self.name.text(), self.mobile.text(), self.phone2.text(),
                     self.contact.text(), self.address.text(), self.tax.text(), self.notes.toPlainText(), True,
                     self.days.value(), limit, self.supplies.currentData(), self.pay.currentData(),
                     self.creg.text(), self.bank.text())
        try:
            self.saved = (suppliers.update if self.s else suppliers.create)(self.session.con, self.session.user, s)
        except suppliers.SupplierError as e:
            self.notice.show_message(str(e))
            return
        self.accept()
