"""Customers screen: searchable list, row preview, add and edit dialog."""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QSpinBox, QWidget)

from morv.core.money import fmt, parse_money, MoneyError
from morv.core.text import ltr
from morv.core import validate
from morv.infra import customer_statement, customers
from morv.infra.customers import Customer, Vehicle
from morv.ui.customer_statement_dialog import CustomerStatementDialog
from morv.ui.listpage import ListPage
from morv.ui.modal import Modal
from morv.ui.deposit_dialogs import CustomerDocumentsDialog, DepositDialog
from morv.ui.receipt_dialogs import ReceiptDialog
from morv.ui.widgets import ask, danger_button, field, form, ltr_edit, muted, Notice, primary_button


class CustomersPage(ListPage):
    title = "العملاء"
    add_label = "عميل جديد"
    search_placeholder = "الاسم، الموبايل، أو رقم اللوحة (F2)"
    filters = [("all", "الكل"), ("wholesale", "جملة"), ("super", "جملة الجملة"), ("inactive", "الموقوفون")]
    row_icon = staticmethod(lambda r: "user")
    columns = [
        ("العميل", lambda r: r[1]),
        ("الموبايل", lambda r: ltr(r[2])),
        ("فئة السعر", lambda r: customers.LEVELS[r[3]]),
        ("النوع", lambda r: customers.TYPES[r[4]]),
        ("المركبة", lambda r: r[5] or "—"),
        ("الحالة", lambda r: "نشط" if r[6] else "موقوف"),
    ]

    def can_edit(self) -> bool:
        return self.session.can("customers.manage")

    def fetch(self, con, query, filter_key, offset):
        level = filter_key if filter_key in ("wholesale", "super") else None
        return customers.search(con, query, level=level, include_inactive=filter_key == "inactive",
                                offset=offset)

    def total(self) -> str:
        return f"{customers.count(self.session.con)} عميل نشط · اضغط صفًا لمعاينته وكشف حسابه"

    def build_preview(self, row_id: int) -> None:
        c = customers.get(self.session.con, row_id)
        self.heading(c.name, f"{customers.TYPES[c.customer_type]} · {customers.LEVELS[c.price_level]}" + ("" if c.active else " · موقوف"))
        if c.notes:
            n = Notice()
            n.show_message(c.notes, "warn")
            self.preview_layout.addWidget(n)
        contact = [("الموبايل", ltr(c.mobile))]
        if c.mobile2:
            contact.append(("هاتف إضافي", ltr(c.mobile2)))
        if c.national_id:
            contact.append(("الرقم القومي", ltr(validate.mask_national_id(c.national_id))))
        contact.append(("العنوان", c.address))
        if c.work_place:
            contact.append(("جهة العمل", c.work_place))
        self.summary_section("بيانات التواصل", "phone")
        self.summary_details(contact)
        self.summary_section("بيانات الائتمان", "cash")
        self.summary_details([
            ("حد الآجل", fmt(c.credit_limit) if c.credit_limit else "لا يُسمح بالآجل"),
            ("مدة الائتمان", f"{c.credit_days} يوم"),
        ])
        self.summary_section("المركبات", "car")
        if c.vehicles:
            self.summary_details([
                (v.title or "مركبة", " · ".join(x for x in
                 (v.plate, ltr(v.tire_front) if v.tire_front else "", v.battery) if x))
                for v in c.vehicles])
        else:
            self.summary_note("لا توجد مركبات مسجلة.")
        self.summary_section("موافقة واتساب", "phone")
        self.summary_details([("تذكيرات واتساب", "موافق" if c.whatsapp_ok else "لم يوافق")])
        self.summary_note("آخر زيارة ومواعيد الصيانة تظهر هنا في إصدار لاحق.")
        statement = QPushButton("كشف الحساب")
        statement.clicked.connect(lambda: self.open_statement(c))
        receipt = QPushButton("سند قبض")
        receipt.clicked.connect(lambda: self.open_receipt(c))
        receipt.setEnabled(self.session.can("receipts.post"))
        deposit = QPushButton("عربون")
        deposit.clicked.connect(lambda: self.open_deposit(c))
        deposit.setEnabled(self.session.can("receipts.post"))
        documents = QPushButton("مستندات القبض والعربون")
        documents.clicked.connect(lambda: self.open_documents(c))
        if self.can_edit():
            edit = primary_button("تعديل", "edit")
            edit.clicked.connect(lambda: self.open_dialog(c.id))
            toggle = danger_button("إيقاف" if c.active else "تفعيل", "pause" if c.active else "play")
            toggle.clicked.connect(lambda: self.toggle_active(c))
            self.actions(edit, toggle, receipt, deposit, documents, statement)
        else:
            self.actions(receipt, deposit, documents, statement)

    def open_statement(self, c: Customer) -> None:
        con = self.session.con
        dlg = CustomerStatementDialog(
            c.name, lambda date_from, date_to: customer_statement.statement(con, c.id, date_from, date_to), self)
        dlg.exec()

    def open_receipt(self, c: Customer) -> None:
        dlg = ReceiptDialog(self.session, c.id, c.name, self)
        if dlg.exec() == QDialog.Accepted:
            self.reload(keep_id=c.id)

    def open_deposit(self, c: Customer) -> None:
        if DepositDialog(self.session, c.id, c.name, self).exec() == QDialog.Accepted:
            self.reload(keep_id=c.id)

    def open_documents(self, c: Customer) -> None:
        dlg = CustomerDocumentsDialog(self.session, c.id, c.name, self)
        dlg.exec()
        if dlg.changed:
            self.reload(keep_id=c.id)

    def toggle_active(self, c: Customer) -> None:
        verb = "إيقاف" if c.active else "تفعيل"
        if ask(self, verb, f"{verb} العميل «{c.name}»؟" + (" لن يظهر في البيع." if c.active else ""), danger=c.active):
            customers.set_active(self.session.con, self.session.user, c.id, not c.active)
            self.reload(keep_id=c.id)

    def open_dialog(self, row_id):
        if not self.can_edit():
            return
        dlg = CustomerDialog(self.session, customers.get(self.session.con, row_id) if row_id else None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.saved.id)


class CustomerDialog(Modal):
    def __init__(self, session, c: Customer | None, parent=None):
        super().__init__("تعديل عميل" if c else "عميل جديد", parent, width=640)
        self.session = session
        self.c = c
        self.saved = None
        self.vehicles: list[Vehicle] = list(c.vehicles) if c else []
        lay = self.body
        f = form()
        self.name = QLineEdit(c.name if c else "")
        self.name.setPlaceholderText("الاسم ثلاثي على الأقل")
        self.mobile = ltr_edit("01xxxxxxxxx")
        self.mobile.setText(c.mobile if c else "")
        self.level = QComboBox()
        for k, v in customers.LEVELS.items():
            self.level.addItem(v, k)
        self.level.setEnabled(session.user.is_manager or session.can("items.prices"))
        self.ctype = QComboBox()
        for k, v in customers.TYPES.items():
            self.ctype.addItem(v, k)
        if c:
            self.level.setCurrentIndex(list(customers.LEVELS).index(c.price_level))
            self.ctype.setCurrentIndex(list(customers.TYPES).index(c.customer_type))
        self.address = QLineEdit(c.address if c else "")
        self.notes = QPlainTextEdit(c.notes if c else "")
        self.notes.setPlaceholderText("تظهر للكاشير تلقائيًا عند اختيار العميل في الفاتورة")
        self.notes.setFixedHeight(64)
        f.addRow(field("اسم العميل *", self.name))
        f.addRow(field("رقم الموبايل *", self.mobile))
        f.addRow(field("فئة السعر", self.level))
        f.addRow(field("نوع العميل", self.ctype))
        f.addRow(field("العنوان", self.address))
        f.addRow(field("ملاحظات", self.notes))
        lay.addLayout(f)

        vh = QHBoxLayout()
        vh.addWidget(muted("المركبات"), 1)
        add_v = QPushButton("إضافة مركبة")
        add_v.clicked.connect(self.add_vehicle)
        rm_v = QPushButton("حذف المختارة")
        rm_v.clicked.connect(self.remove_vehicle)
        vh.addWidget(add_v)
        vh.addWidget(rm_v)
        lay.addLayout(vh)
        self.vlist = QListWidget()
        self.vlist.setFixedHeight(80)
        lay.addWidget(self.vlist)
        self._refresh_vehicles()

        self.more = QCheckBox("بيانات التقسيط والآجل")
        self.more.toggled.connect(lambda on: self.more_box.setVisible(on))
        lay.addWidget(self.more)
        self.more_box = QWidget()
        mf = form()
        self.national_id = ltr_edit("14 رقمًا")
        self.national_id.setText(c.national_id if c else "")
        self.work = QLineEdit(c.work_place if c else "")
        self.mobile2 = ltr_edit("01xxxxxxxxx")
        self.mobile2.setText(c.mobile2 if c else "")
        self.limit = ltr_edit("0 = لا يُسمح بالآجل")
        self.limit.setText(fmt(c.credit_limit) if c and c.credit_limit else "")
        self.days = QSpinBox()
        self.days.setRange(0, 365)
        self.days.setValue(c.credit_days if c else 0)
        self.tax = ltr_edit("للشركات فقط")
        self.tax.setText(c.tax_id if c else "")
        self.wa = QCheckBox("يوافق العميل على تلقي تذكيرات الأقساط عبر واتساب")
        self.wa.setChecked(bool(c and c.whatsapp_ok))
        mf.addRow(field("الرقم القومي", self.national_id))
        mf.addRow(muted("إلزامي عند إنشاء عقد تقسيط، ويُعرض مخفيًا بعد الحفظ"))
        mf.addRow(field("جهة العمل", self.work))
        mf.addRow(field("هاتف إضافي", self.mobile2))
        mf.addRow(field("حد البيع الآجل", self.limit))
        mf.addRow(field("مدة السداد (أيام)", self.days))
        mf.addRow(field("الرقم الضريبي / السجل", self.tax))
        mf.addRow(self.wa)
        self.more_box.setLayout(mf)
        self.more_box.setVisible(False)
        lay.addWidget(self.more_box)
        if c and any((c.national_id, c.work_place, c.mobile2, c.credit_limit, c.tax_id, c.whatsapp_ok)):
            self.more.setChecked(True)

        self.add_footer("حفظ", self.save)
        self.name.setFocus()

    def _refresh_vehicles(self) -> None:
        self.vlist.clear()
        for v in self.vehicles:
            self.vlist.addItem(" · ".join(x for x in (v.title, v.plate, ltr(v.tire_front) if v.tire_front else "") if x) or "مركبة")

    def add_vehicle(self) -> None:
        dlg = VehicleDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.vehicles.append(dlg.vehicle)
            self._refresh_vehicles()

    def remove_vehicle(self) -> None:
        i = self.vlist.currentRow()
        if i >= 0:
            del self.vehicles[i]
            self._refresh_vehicles()

    def save(self) -> None:
        try:
            limit = parse_money(self.limit.text()) if self.limit.text().strip() else 0
        except MoneyError as e:
            self.notice.show_message(f"حد الآجل: {e}")
            return
        c = Customer(self.c.id if self.c else None, self.name.text(), self.mobile.text(), self.mobile2.text(),
                     self.national_id.text(), self.ctype.currentData(), self.level.currentData(), limit,
                     self.days.value(), self.address.text(), self.work.text(), self.tax.text(),
                     self.notes.toPlainText(), self.wa.isChecked(), True, self.vehicles)
        try:
            self.saved = (customers.update if self.c else customers.create)(self.session.con, self.session.user, c)
        except customers.CustomerError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class VehicleDialog(Modal):
    def __init__(self, parent=None):
        super().__init__("مركبة", parent, width=460)
        self.vehicle = None
        lay = self.body
        f = form()
        self.make = QLineEdit()
        self.make.setPlaceholderText("تويوتا")
        self.model = QLineEdit()
        self.model.setPlaceholderText("كورولا")
        self.year = ltr_edit("2019")
        self.plate = QLineEdit()
        self.plate.setPlaceholderText("ق س ط 4826")
        self.tire_f = ltr_edit("205/55R16")
        self.tire_r = ltr_edit("نفس الأمامي")
        self.battery = ltr_edit("70Ah")
        self.odo = ltr_edit("84500")
        for lbl, w in (("الماركة", self.make), ("الموديل", self.model), ("السنة", self.year), ("رقم اللوحة", self.plate),
                       ("مقاس الإطار الأمامي", self.tire_f), ("مقاس الإطار الخلفي", self.tire_r),
                       ("مقاس البطارية", self.battery), ("آخر عداد كم", self.odo)):
            f.addRow(field(lbl, w))
        lay.addLayout(f)
        self.add_footer("إضافة", self.go)

    def go(self) -> None:
        if not any((self.make.text().strip(), self.model.text().strip(), self.plate.text().strip())):
            self.notice.show_message("اكتب الماركة أو الموديل أو رقم اللوحة على الأقل")
            return
        year = validate.to_latin(self.year.text().strip())
        odo = validate.to_latin(self.odo.text().strip()).replace(",", "")
        if (year and not year.isdigit()) or (odo and not odo.isdigit()):
            self.notice.show_message("السنة والعداد أرقام فقط")
            return
        self.vehicle = Vehicle(self.make.text(), self.model.text(), int(year) if year else None, self.plate.text(),
                               self.tire_f.text(), self.tire_r.text(), self.battery.text(), int(odo) if odo else None)
        self.accept()
