"""شاشة الموردين: قائمة ومعاينة ونافذة إضافة/تعديل. الرصيد وكشف الحساب في المرحلة 3."""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout

from morv.core.text import ltr
from morv.infra import suppliers
from morv.infra.suppliers import Supplier
from morv.ui.listpage import ListPage
from morv.ui.widgets import Notice, ask, danger_button, form, ltr_edit, primary_button


class SuppliersPage(ListPage):
    """قائمة الموردين."""
    title = "الموردون"
    add_label = "مورد جديد"
    search_placeholder = "الاسم أو الموبايل (F2)"
    filters = [("all", "الكل"), ("inactive", "الموقوفون")]
    columns = [
        ("المورد", lambda r: r[1]),
        ("الموبايل", lambda r: ltr(r[2])),
        ("جهة الاتصال", lambda r: r[3] or "—"),
        ("الحالة", lambda r: "نشط" if r[4] else "موقوف"),
    ]

    def can_edit(self) -> bool:
        """صلاحية الموردين."""
        return self.session.can("suppliers.manage")

    def fetch(self, query, filter_key, offset):
        """صفحة من البحث."""
        return suppliers.search(self.session.con, query, include_inactive=filter_key == "inactive", offset=offset)

    def total(self) -> str:
        """العدّاد."""
        return f"{suppliers.count(self.session.con)} مورد نشط"

    def build_preview(self, row_id: int) -> None:
        """تفاصيل المورد."""
        s = suppliers.get(self.session.con, row_id)
        self.heading(s.name, "" if s.active else "موقوف")
        self.kv("الموبايل", ltr(s.mobile))
        if s.phone2:
            self.kv("هاتف إضافي", ltr(s.phone2))
        self.kv("جهة الاتصال", s.contact_person)
        self.kv("العنوان", s.address)
        self.kv("الرقم الضريبي", ltr(s.tax_id) if s.tax_id else "")
        self.kv("ملاحظات", s.notes)
        if self.can_edit():
            edit = primary_button("تعديل")
            edit.clicked.connect(lambda: self.open_dialog(s.id))
            toggle = danger_button("إيقاف" if s.active else "تفعيل")
            toggle.clicked.connect(lambda: self.toggle_active(s))
            self.actions(edit, toggle)

    def toggle_active(self, s: Supplier) -> None:
        """إيقاف أو تفعيل مع تأكيد."""
        verb = "إيقاف" if s.active else "تفعيل"
        if ask(self, verb, f"{verb} المورد «{s.name}»؟"):
            suppliers.set_active(self.session.con, self.session.user, s.id, not s.active)
            self.reload(keep_id=s.id)

    def open_dialog(self, row_id):
        """نافذة إضافة أو تعديل."""
        if not self.can_edit():
            return
        dlg = SupplierDialog(self.session, suppliers.get(self.session.con, row_id) if row_id else None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.saved.id)


class SupplierDialog(QDialog):
    """نافذة بيانات المورد."""

    def __init__(self, session, s: Supplier | None, parent=None):
        """بناء الحقول."""
        super().__init__(parent)
        self.session, self.s, self.saved = session, s, None
        self.setWindowTitle("تعديل مورد" if s else "مورد جديد")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        f = form()
        self.name = QLineEdit(s.name if s else "")
        self.mobile = ltr_edit("01xxxxxxxxx")
        self.mobile.setText(s.mobile if s else "")
        self.phone2 = ltr_edit("01xxxxxxxxx")
        self.phone2.setText(s.phone2 if s else "")
        self.contact = QLineEdit(s.contact_person if s else "")
        self.address = QLineEdit(s.address if s else "")
        self.tax = ltr_edit("")
        self.tax.setText(s.tax_id if s else "")
        self.notes = QPlainTextEdit(s.notes if s else "")
        self.notes.setFixedHeight(64)
        f.addRow("اسم المورد *", self.name)
        f.addRow("رقم الموبايل *", self.mobile)
        f.addRow("هاتف إضافي", self.phone2)
        f.addRow("جهة الاتصال", self.contact)
        f.addRow("العنوان", self.address)
        f.addRow("الرقم الضريبي", self.tax)
        f.addRow("ملاحظات", self.notes)
        lay.addLayout(f)
        self.notice = Notice()
        lay.addWidget(self.notice)
        btns = QHBoxLayout()
        save = primary_button("حفظ")
        save.setShortcut("Ctrl+S")
        save.clicked.connect(self.save)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        btns.addWidget(save)
        btns.addWidget(cancel)
        btns.addStretch(1)
        lay.addLayout(btns)

    def save(self) -> None:
        """الحفظ."""
        s = Supplier(self.s.id if self.s else None, self.name.text(), self.mobile.text(), self.phone2.text(),
                     self.contact.text(), self.address.text(), self.tax.text(), self.notes.toPlainText())
        try:
            self.saved = (suppliers.update if self.s else suppliers.create)(self.session.con, self.session.user, s)
        except suppliers.SupplierError as e:
            self.notice.show_message(str(e))
            return
        self.accept()
