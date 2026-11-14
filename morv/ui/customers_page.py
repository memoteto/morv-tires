"""شاشة العملاء: قائمة ببحث (الاسم/الموبايل/اللوحة)، ومعاينة بالضغط على الصف، ونافذة إضافة/تعديل.

الرصيد وكشف الحساب والفاتورة تأتي في مراحلها؛ هنا بيانات العميل ومركباته فقط.
الرقم القومي يُعرض مخفيًا إلا آخر 4 أرقام.
"""
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QSpinBox, QWidget)

from morv.core.money import fmt, parse_money, MoneyError
from morv.core.text import ltr
from morv.core import validate
from morv.infra import customers
from morv.infra.customers import Customer, Vehicle
from morv.ui.listpage import ListPage
from morv.ui.modal import Modal
from morv.ui.widgets import Notice, ask, danger_button, form, ltr_edit, muted, primary_button


class CustomersPage(ListPage):
    """قائمة العملاء."""
    title = "العملاء"
    add_label = "عميل جديد"
    search_placeholder = "الاسم، الموبايل، أو رقم اللوحة (F2)"
    filters = [("all", "الكل"), ("wholesale", "جملة"), ("super", "جملة الجملة"), ("inactive", "الموقوفون")]
    columns = [
        ("العميل", lambda r: r[1]),
        ("الموبايل", lambda r: ltr(r[2])),
        ("فئة السعر", lambda r: customers.LEVELS[r[3]]),
        ("النوع", lambda r: customers.TYPES[r[4]]),
        ("المركبة", lambda r: r[5] or "—"),
        ("الحالة", lambda r: "نشط" if r[6] else "موقوف"),
    ]

    def can_edit(self) -> bool:
        """صلاحية العملاء."""
        return self.session.can("customers.manage")

    def fetch(self, query, filter_key, offset):
        """صفحة من البحث حسب الفلتر."""
        level = filter_key if filter_key in ("wholesale", "super") else None
        return customers.search(self.session.con, query, level=level, include_inactive=filter_key == "inactive",
                                offset=offset)

    def total(self) -> str:
        """العدّاد."""
        return f"{customers.count(self.session.con)} عميل نشط"

    def build_preview(self, row_id: int) -> None:
        """تفاصيل العميل ومركباته وأزراره."""
        c = customers.get(self.session.con, row_id)
        self.heading(c.name, f"{customers.TYPES[c.customer_type]} · {customers.LEVELS[c.price_level]}" + ("" if c.active else " · موقوف"))
        if c.notes:
            n = Notice()
            n.show_message(c.notes, "warn")
            self.preview_layout.addWidget(n)
        self.kv("الموبايل", ltr(c.mobile))
        if c.mobile2:
            self.kv("هاتف إضافي", ltr(c.mobile2))
        if c.national_id:
            self.kv("الرقم القومي", ltr(validate.mask_national_id(c.national_id)))
        self.kv("العنوان", c.address)
        if c.work_place:
            self.kv("جهة العمل", c.work_place)
        self.kv("حد الآجل", (fmt(c.credit_limit) + f" · {c.credit_days} يوم") if c.credit_limit else "لا يُسمح بالآجل")
        self.kv("تذكيرات واتساب", "موافق" if c.whatsapp_ok else "لم يوافق")
        self.preview_layout.addWidget(muted("المركبات"))
        if not c.vehicles:
            self.preview_layout.addWidget(muted("لا توجد مركبات مسجلة."))
        for v in c.vehicles:
            self.kv(v.title or "مركبة", " · ".join(x for x in (v.plate, ltr(v.tire_front) if v.tire_front else "", v.battery) if x))
        if self.can_edit():
            edit = primary_button("تعديل")
            edit.clicked.connect(lambda: self.open_dialog(c.id))
            toggle = danger_button("إيقاف" if c.active else "تفعيل")
            toggle.clicked.connect(lambda: self.toggle_active(c))
            self.actions(edit, toggle)

    def toggle_active(self, c: Customer) -> None:
        """إيقاف أو تفعيل مع تأكيد."""
        verb = "إيقاف" if c.active else "تفعيل"
        if ask(self, verb, f"{verb} العميل «{c.name}»؟" + (" لن يظهر في البيع." if c.active else ""), danger=c.active):
            customers.set_active(self.session.con, self.session.user, c.id, not c.active)
            self.reload(keep_id=c.id)

    def open_dialog(self, row_id):
        """نافذة إضافة أو تعديل."""
        if not self.can_edit():
            return
        dlg = CustomerDialog(self.session, customers.get(self.session.con, row_id) if row_id else None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.saved.id)


class CustomerDialog(Modal):
    """نافذة بيانات العميل. حقول التقسيط والآجل تحت مفتاح «بيانات إضافية»."""

    def __init__(self, session, c: Customer | None, parent=None):
        """بناء النافذة وملء الحقول عند التعديل."""
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
        self.level.setEnabled(session.user.is_manager or session.can("items.prices"))   # فئة السعر بصلاحية
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
        f.addRow("اسم العميل *", self.name)
        f.addRow("رقم الموبايل *", self.mobile)
        f.addRow("فئة السعر", self.level)
        f.addRow("نوع العميل", self.ctype)
        f.addRow("العنوان", self.address)
        f.addRow("ملاحظات", self.notes)
        lay.addLayout(f)

        # المركبات
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

        # بيانات إضافية
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
        mf.addRow("الرقم القومي", self.national_id)
        mf.addRow("", muted("إلزامي عند إنشاء عقد تقسيط، ويُعرض مخفيًا بعد الحفظ"))
        mf.addRow("جهة العمل", self.work)
        mf.addRow("هاتف إضافي", self.mobile2)
        mf.addRow("حد البيع الآجل", self.limit)
        mf.addRow("مدة السداد (أيام)", self.days)
        mf.addRow("الرقم الضريبي / السجل", self.tax)
        mf.addRow("", self.wa)
        self.more_box.setLayout(mf)
        self.more_box.setVisible(False)
        lay.addWidget(self.more_box)
        if c and any((c.national_id, c.work_place, c.mobile2, c.credit_limit, c.tax_id, c.whatsapp_ok)):
            self.more.setChecked(True)

        self.add_footer("حفظ", self.save)
        self.name.setFocus()

    def _refresh_vehicles(self) -> None:
        """إعادة رسم قائمة المركبات."""
        self.vlist.clear()
        for v in self.vehicles:
            self.vlist.addItem(" · ".join(x for x in (v.title, v.plate, ltr(v.tire_front) if v.tire_front else "") if x) or "مركبة")

    def add_vehicle(self) -> None:
        """حوار مركبة جديدة."""
        dlg = VehicleDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.vehicles.append(dlg.vehicle)
            self._refresh_vehicles()

    def remove_vehicle(self) -> None:
        """حذف المركبة المختارة من القائمة (يُحفظ مع العميل)."""
        i = self.vlist.currentRow()
        if i >= 0:
            del self.vehicles[i]
            self._refresh_vehicles()

    def save(self) -> None:
        """جمع الحقول والحفظ."""
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
    """حوار مركبة واحدة."""

    def __init__(self, parent=None):
        """الحقول."""
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
            f.addRow(lbl, w)
        lay.addLayout(f)
        self.add_footer("إضافة", self.go)

    def go(self) -> None:
        """تحقق بسيط ثم القبول."""
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
