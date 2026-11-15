"""شاشة نقطة البيع: نافذة فاتورة بيع (عميل، أسطر، تحصيل مختلط) وترحيلها عبر محرك البيع، وملخص آخر فاتورة."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableView, QVBoxLayout, QWidget)

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import customers, items, selling
from morv.infra.selling import SaleError, SaleLine
from morv.ui.listpage import TupleModel
from morv.ui.modal import Modal
from morv.ui.widgets import form, ltr_edit, muted, primary_button

ITEM_CHOICES = 50
KIND_NAMES = {"tire": "إطار", "battery": "بطارية", "service": "خدمة"}


class PosPage(QWidget):
    """نقطة البيع: زر فاتورة بيع جديدة وملخص آخر فاتورة رُحِّلت في هذه الجلسة بتنبيهاتها."""

    def __init__(self, session):
        """بناء الصفحة."""
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        card = QFrame()
        card.setObjectName("card")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 24, 24, 24)
        inner.setSpacing(12)
        self.new_btn = primary_button("فاتورة بيع جديدة")
        self.new_btn.setEnabled(session.can("sales.post"))
        self.new_btn.clicked.connect(self.new_sale)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.hint = muted("" if session.can("sales.post") else "لا تملك صلاحية ترحيل فواتير البيع.")
        inner.addWidget(self.new_btn, 0, Qt.AlignmentFlag.AlignLeading)
        inner.addWidget(self.hint)
        inner.addWidget(self.summary)
        inner.addStretch(1)
        lay.addWidget(card)

    def new_sale(self) -> None:
        """فتح نافذة الفاتورة؛ عند الترحيل يُعرض ملخصها وأي تنبيهات."""
        if not self.session.can("sales.post"):
            return
        dlg = SaleDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            r = dlg.posted
            text = f"تم ترحيل الفاتورة {ltr(r.number)} · الإجمالي {ltr(fmt(r.total))} جنيه"
            if r.credit:
                text += f" · آجل {ltr(fmt(r.credit))}"
            if r.warnings:
                text += "\nتنبيهات: " + " · ".join(r.warnings)
            self.summary.setText(text)


class SaleDialog(Modal):
    """نافذة فاتورة بيع: العميل والتاريخ، إدخال الأسطر بسعر افتراضي حسب الفئة، التحصيل المختلط، ثم الترحيل."""

    def __init__(self, session, parent=None):
        """بناء الرأس وإدخال الأسطر والجدول والتحصيل."""
        super().__init__("فاتورة بيع جديدة", parent, width=980)
        self.session = session
        self.posted = None
        self.lines: list[SaleLine] = []
        self._names: list[str] = []
        self._busy = False
        self._request_key = uuid.uuid4().hex     # مفتاح واحد للنموذج: إعادة الضغط لا تكرر الفاتورة
        self._can_price = session.can("sales.price_override")

        f = form()
        self.customer = QComboBox()
        self.customer.addItem("بدون عميل (بيع نقدي)", None)
        for row in customers.search(session.con, "", limit=300):
            self.customer.addItem(row[1], row[0])
        self.customer.currentIndexChanged.connect(self._on_customer_changed)
        self.sale_date = ltr_edit("YYYY-MM-DD")
        self.sale_date.setText(date.today().isoformat())
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("ملاحظات (اختيارية)")
        self.notes.setFixedHeight(40)
        f.addRow("العميل", self.customer)
        f.addRow("تاريخ الفاتورة *", self.sale_date)
        f.addRow("ملاحظات", self.notes)
        self.body.addLayout(f)

        entry = QFrame()
        entry.setObjectName("card")
        col = QVBoxLayout(entry)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(8)
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("ابحث عن صنف: الاسم أو الكود أو المقاس")
        self.item_search.textChanged.connect(self.refresh_items)
        self.item_combo = QComboBox()
        self.item_combo.currentIndexChanged.connect(self._on_item_changed)
        col.addWidget(self.item_search)
        col.addWidget(self.item_combo)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.qty = QSpinBox()
        self.qty.setRange(1, 100000)
        self.qty.setValue(1)
        self.qty.valueChanged.connect(self._on_item_changed)
        self.price = ltr_edit("0.00")
        self.price.setReadOnly(not self._can_price)
        self.add_btn = QPushButton("إضافة السطر")
        self.add_btn.clicked.connect(self.add_line)
        for title, w in (("الكمية", self.qty), ("سعر الوحدة (جنيه)", self.price)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            row.addLayout(cell, 1)
        row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(row)
        self.stock_hint = muted("")
        col.addWidget(self.stock_hint)
        self.body.addWidget(entry)

        self.lines_model = TupleModel([
            ("#", lambda r: ltr(str(r[0]))),
            ("الصنف", lambda r: r[1]),
            ("الكمية", lambda r: ltr(str(r[2]))),
            ("سعر الوحدة", lambda r: ltr(fmt(r[3]))),
            ("إجمالي السطر", lambda r: ltr(fmt(r[2] * r[3]))),
        ])
        self.view = QTableView()
        self.view.setModel(self.lines_model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.view.setMinimumHeight(150)
        self.body.addWidget(self.view)
        bar = QHBoxLayout()
        self.remove_btn = QPushButton("حذف السطر المختار")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.total_label = QLabel()
        self.total_label.setObjectName("cardTitle")
        bar.addWidget(self.remove_btn)
        bar.addStretch(1)
        bar.addWidget(self.total_label)
        self.body.addLayout(bar)

        pay = QHBoxLayout()
        pay.setSpacing(10)
        self.cash = ltr_edit("0.00")
        self.transfer = ltr_edit("0.00")
        self.card = ltr_edit("0.00")
        for title, w in (("نقدي (الصندوق)", self.cash), ("تحويل بنكي", self.transfer), ("بطاقة", self.card)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            w.textChanged.connect(self._refresh_credit)
            pay.addLayout(cell, 1)
        self.credit_label = QLabel()
        self.credit_label.setObjectName("cardTitle")
        pay.addWidget(self.credit_label, 1, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(pay)

        self.post_btn = self.add_footer("ترحيل الفاتورة", self.post)
        self.refresh_items()
        self._refresh_total()

    # ---------- الأصناف والأسعار ----------
    def _customer_id(self):
        """رقم العميل المختار أو None."""
        return self.customer.currentData()

    def refresh_items(self, *_args) -> None:
        """تحديث قائمة الأصناف حسب البحث (كل الأنواع)."""
        q = self.item_search.text()
        rows = []
        for kind in ("tire", "battery", "service"):
            rows += items.search(self.session.con, q, kind=kind, limit=ITEM_CHOICES)
        self.item_combo.clear()
        for r in rows[:ITEM_CHOICES]:
            spec = f" · {ltr(r[4])}" if r[4] else ""
            self.item_combo.addItem(f"{r[3]} ({ltr(r[2])}){spec}", (r[0], r[1], r[3]))
        self._on_item_changed()

    def _on_item_changed(self, *_args) -> None:
        """السعر الافتراضي لفئة العميل وتلميح الدفعة المقترحة والرصيد."""
        data = self.item_combo.currentData()
        if data is None:
            self.price.setText("")
            self.stock_hint.setText("لا أصناف مطابقة.")
            return
        self.price.setText(fmt(selling.default_price(self.session.con, data[0], self._customer_id())))
        if data[1] == "service":
            self.stock_hint.setText("خدمة: لا تُصرف من المخزون.")
            return
        dots = selling.suggested_dots(self.session.con, data[0], self.qty.value())
        if dots is None:
            self.stock_hint.setText("المخزون لا يكفي الكمية المطلوبة.")
        else:
            self.stock_hint.setText("الدفعة المقترحة: " + " · ".join(
                f"DOT {ltr(d) if d else '—'} × {ltr(str(q))}" for d, q in dots))

    def _on_customer_changed(self, *_args) -> None:
        """تغيير العميل يغيّر فئة السعر: يُحدَّث السعر الافتراضي والجدول والإجمالي."""
        self._on_item_changed()
        self._refresh_lines()

    # ---------- الأسطر ----------
    def add_line(self) -> bool:
        """التحقق من السطر ثم إضافته؛ السعر المطابق للافتراضي يُحفظ None فيتبع فئة العميل."""
        data = self.item_combo.currentData()
        if data is None:
            return self._fail("اختر صنفًا للسطر.")
        try:
            price = parse_money(self.price.text())
        except MoneyError as e:
            return self._fail(f"سعر الوحدة: {e}")
        if price <= 0:
            return self._fail("سعر الوحدة يجب أن يكون أكبر من صفر.")
        listed = selling.default_price(self.session.con, data[0], self._customer_id())
        self.lines.append(SaleLine(data[0], self.qty.value(), None if price == listed else price))
        self._names.append(data[2])
        self.notice.show_message("")
        self.qty.setValue(1)
        self._refresh_lines()
        return True

    def remove_selected(self) -> None:
        """حذف السطر المختار من المسودة (قبل الترحيل فقط)."""
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر سطرًا من الجدول أولًا.", "warn")
            return
        del self.lines[idx.row()]
        del self._names[idx.row()]
        self._refresh_lines()

    def _price_of(self, ln: SaleLine) -> int:
        """سعر السطر الفعلي: المُدخل، أو الافتراضي لفئة العميل الحالي."""
        if ln.unit_price is not None:
            return ln.unit_price
        return selling.default_price(self.session.con, ln.item_id, self._customer_id())

    def _refresh_lines(self) -> None:
        """إعادة عرض الأسطر والإجمالي والآجل."""
        self.lines_model.reset_rows([(i, self._names[i - 1], ln.qty, self._price_of(ln))
                                     for i, ln in enumerate(self.lines, start=1)])
        self._refresh_total()

    def total_minor(self) -> int:
        """إجمالي المسودة بالقرش."""
        return sum(ln.qty * self._price_of(ln) for ln in self.lines)

    def _refresh_total(self) -> None:
        """نص الإجمالي ثم الآجل."""
        self.total_label.setText(f"الإجمالي: {ltr(fmt(self.total_minor()))} جنيه")
        self._refresh_credit()

    def _payments(self) -> dict:
        """المبالغ المُدخلة غير الصفرية؛ نص غير صالح يرفع MoneyError."""
        out = {}
        for key, edit in (("cash", self.cash), ("transfer", self.transfer), ("card", self.card)):
            text = edit.text().strip()
            amount = parse_money(text) if text else 0
            if amount:
                out[key] = amount
        return out

    def _refresh_credit(self, *_args) -> None:
        """الآجل = الإجمالي − المحصَّل، أو رسالة إن كان النص غير صالح أو المحصَّل أكبر."""
        try:
            left = self.total_minor() - sum(self._payments().values())
        except MoneyError:
            self.credit_label.setText("مبلغ تحصيل غير صالح")
            return
        self.credit_label.setText(f"الآجل على العميل: {ltr(fmt(left))}" if left >= 0 else "المحصَّل أكبر من الإجمالي")

    def _fail(self, text: str) -> bool:
        """عرض خطأ في شريط الرسائل؛ يعيد False."""
        self.notice.show_message(text)
        return False

    # ---------- الترحيل ----------
    def post(self) -> None:
        """فحص الشكل ثم الترحيل عبر المحرك؛ أي رفض يُعرض بنصه ولا يُحفظ شيء، وتبقى المدخلات."""
        if self._busy:
            return
        if not self.lines:
            self._fail("أضف سطرًا واحدًا على الأقل قبل الترحيل.")
            return
        try:
            payments = self._payments()
        except MoneyError as e:
            self._fail(f"مبلغ التحصيل: {e}")
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = selling.post_sale(
                self.session.con, self.session.user, request_key=self._request_key, customer_id=self._customer_id(),
                sale_date=self.sale_date.text().strip(), lines=list(self.lines), payments=payments,
                notes=self.notes.toPlainText())
        except SaleError as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # أي عطل غير متوقع: المحرك ذري فلا يبقى شيء ناقص
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self.accept()
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
