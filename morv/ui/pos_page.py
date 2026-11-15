"""شاشة نقطة البيع: فاتورة بيع جديدة داخل الشاشة نفسها (لا نافذة منبثقة)، ترحّل عبر محرك المبيعات."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableView, QVBoxLayout, QWidget)

from morv.core import sales_rules
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import customers, items, sales
from morv.infra.sales import SaleError, SaleLineIn
from morv.ui.listpage import TupleModel
from morv.ui.widgets import Notice, form, ltr_edit, muted, primary_button

ITEM_CHOICES = 50
PRICE_COLUMN = {"retail": 5, "wholesale": 6, "super": 7}      # موضع سعر الشريحة في صف items.search
TIER_LABELS = {"retail": "قطاعي (تجزئة)", "wholesale": "جملة", "super": "جملة الجملة"}
PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة / ماكينة POS", "transfer": "تحويل / محفظة", "wallet": "محفظة"}


class PosPage(QWidget):
    """نقطة البيع: بيانات العميل والمركبة والأصناف والملخص والدفع في شاشة واحدة، والعناصر غير المبنية معطّلة."""

    def __init__(self, session, parent=None):
        """بناء الشاشة وتحميل الأصناف والعملاء."""
        super().__init__(parent)
        self.session = session
        self.posted = None
        self.lines: list[SaleLineIn] = []
        self._names: list[str] = []
        self._busy = False
        self._request_key = uuid.uuid4().hex     # مفتاح واحد لكل فاتورة: إعادة الضغط لا تكرر الفاتورة
        self.tier = "retail"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        self.notice = Notice()
        root.addWidget(self._build_header())
        root.addWidget(self.notice)
        main = QHBoxLayout()
        main.setSpacing(14)
        main.addLayout(self._build_right_column(), 3)
        main.addLayout(self._build_left_column(), 2)
        root.addLayout(main, 1)
        root.addLayout(self._build_footer())

        self.refresh_items()
        self._refresh_total()
        if not items.search(self.session.con, "", limit=1):
            self.notice.show_message("لا توجد أصناف نشطة؛ أضف أصنافًا من شاشة المخزون أولًا.", "warn")

    # ---------- الهيكل ----------
    def _build_header(self) -> QFrame:
        """عنوان الشاشة وسطر المخزن."""
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("فاتورة بيع جديدة")
        title.setObjectName("cardTitle")
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(muted("نقطة البيع · المخزن الرئيسي"))
        return w

    def _build_footer(self) -> QHBoxLayout:
        """أزرار الترحيل والعناصر المؤجلة."""
        bar = QHBoxLayout()
        for label in ("تعليق الفاتورة", "معاينة الطباعة", "حفظ كمسودة"):
            bar.addWidget(self._deferred_button(label))
        bar.addStretch(1)
        self.post_btn = primary_button("اعتماد الفاتورة")
        self.post_btn.clicked.connect(self.post)
        bar.addWidget(self.post_btn)
        return bar

    def _build_right_column(self) -> QVBoxLayout:
        """الجهة اليمنى: بيانات الفاتورة والعميل، ثم الأصناف والأسطر."""
        col = QVBoxLayout()
        col.setSpacing(10)

        head = form()
        self.customer = QComboBox()
        self.customer.addItem("عميل نقدي", ("", "retail"))
        for row in customers.search(self.session.con, "", limit=300):
            self.customer.addItem(row[1], (row[0], row[3]))
        self.customer.currentIndexChanged.connect(self._on_customer_changed)
        self.odometer = ltr_edit("قراءة العداد (اختياري)")
        self.plate = ltr_edit("رقم اللوحة (اختياري)")
        self.sale_date = ltr_edit("YYYY-MM-DD")
        self.sale_date.setText(date.today().isoformat())
        self.note = ltr_edit("ملاحظة على الفاتورة (اختيارية)")
        head.addRow("العميل", self._row(self.customer, self._deferred_button("عميل جديد")))
        head.addRow("فئة السعر", self._tier_row())
        head.addRow("عداد الكيلومترات", self.odometer)
        head.addRow("المركبة / اللوحة", self.plate)
        head.addRow("تاريخ الفاتورة *", self.sale_date)
        head.addRow("ملاحظة", self.note)
        col.addLayout(head)

        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("ابحث عن صنف أو خدمة: الاسم أو الكود أو المقاس")
        self.item_search.textChanged.connect(self.refresh_items)
        self.item_combo = QComboBox()
        self.item_combo.currentIndexChanged.connect(self._on_item_changed)
        col.addWidget(self._row(self.item_search, self._deferred_button("إضافة سريعة")))
        col.addWidget(self.item_combo)
        cells = QHBoxLayout()
        cells.setSpacing(10)
        self.qty = QSpinBox()
        self.qty.setRange(1, 100000)
        self.qty.setValue(1)
        self.price = ltr_edit("0.00")
        self.line_disc = ltr_edit("0.00")
        for title, w in (("الكمية", self.qty), ("السعر (جنيه)", self.price), ("الخصم (جنيه)", self.line_disc)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            cells.addLayout(cell, 1)
        self.add_btn = QPushButton("إضافة السطر")
        self.add_btn.clicked.connect(self.add_line)
        cells.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(cells)

        self.lines_model = TupleModel([
            ("م", lambda r: ltr(str(r[0]))),
            ("الصنف / الخدمة", lambda r: r[1]),
            ("الكمية", lambda r: ltr(str(r[2]))),
            ("السعر", lambda r: ltr(fmt(r[3]))),
            ("الخصم", lambda r: ltr(fmt(r[4])) if r[4] else "—"),
            ("الإجمالي", lambda r: ltr(fmt(r[2] * r[3] - r[4]))),
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
        self.view.setMinimumHeight(180)
        col.addWidget(self.view)
        self.empty_label = muted("الفاتورة فارغة. ابحث عن صنف أو امسح الباركود للبدء.")
        col.addWidget(self.empty_label)
        bar = QHBoxLayout()
        self.remove_btn = QPushButton("حذف السطر المختار")
        self.remove_btn.clicked.connect(self.remove_selected)
        bar.addWidget(self.remove_btn)
        bar.addStretch(1)
        col.addLayout(bar)
        return col

    def _build_left_column(self) -> QVBoxLayout:
        """الجهة اليسرى: ملخص الفاتورة ثم طرق الدفع."""
        col = QVBoxLayout()
        col.setSpacing(10)

        summary = QFrame()
        summary.setObjectName("card")
        sc = form()
        self.subtotal_label = QLabel()
        self.invoice_disc = ltr_edit("0.00")
        self.invoice_disc.textChanged.connect(self._refresh_total)
        self.total_label = QLabel()
        self.total_label.setObjectName("cardTitle")
        sc.addRow("الإجمالي قبل الخصم", self.subtotal_label)
        sc.addRow("خصم على الفاتورة (جنيه)", self.invoice_disc)
        sc.addRow("الضريبة", muted("المحل غير مسجّل — بدون ضريبة"))
        sc.addRow("الإجمالي النهائي (جنيه)", self.total_label)
        wrap = QVBoxLayout(summary)
        wrap.setContentsMargins(12, 10, 12, 10)
        wrap.addLayout(sc)
        col.addWidget(summary)

        pay = QFrame()
        pay.setObjectName("card")
        pc = QVBoxLayout(pay)
        pc.setContentsMargins(12, 10, 12, 10)
        pc.setSpacing(8)
        pc.addWidget(muted("طريقة الدفع"))
        pc.addWidget(self._deferred_button("آجل"))
        pc.addWidget(self._deferred_button("تقسيط"))
        self.pay1_method = self._method_combo(first_blank=False)
        self.pay1_amount = ltr_edit("فارغ = كامل الإجمالي")
        self.pay1_received = ltr_edit("المبلغ المستلم نقدًا (لحساب الباقي)")
        self.pay1_ref = ltr_edit("رقم الإيصال / المرجع (للبطاقة والتحويل)")
        self.pay2_method = self._method_combo(first_blank=True)
        self.pay2_amount = ltr_edit("فارغ = المتبقي")
        pc.addWidget(muted("الدفعة الأولى"))
        pc.addWidget(self.pay1_method)
        pc.addWidget(self.pay1_amount)
        pc.addWidget(self.pay1_received)
        pc.addWidget(self.pay1_ref)
        pc.addWidget(muted("الدفعة الثانية (اختيارية، وسيلتان كحد أقصى)"))
        pc.addWidget(self.pay2_method)
        pc.addWidget(self.pay2_amount)
        col.addWidget(pay)
        col.addStretch(1)
        return col

    # ---------- أدوات بناء ----------
    def _row(self, *widgets) -> QFrame:
        """صف أفقي لعناصر متجاورة."""
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for x in widgets:
            h.addWidget(x, 1)
        return w

    def _tier_row(self) -> QFrame:
        """فئة السعر كأزرار متبادلة؛ الاختيار يحدّد سعر الوحدة للصنف المختار."""
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.tier_buttons = {}
        for key, label in TIER_LABELS.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(key == self.tier)
            b.clicked.connect(lambda _checked=False, k=key: self._set_tier(k))
            self.tier_buttons[key] = b
            h.addWidget(b, 1)
        return w

    def _set_tier(self, key: str) -> None:
        """تغيير فئة السعر ثم تحديث سعر الصنف المختار."""
        self.tier = key
        for k, b in self.tier_buttons.items():
            b.setChecked(k == key)
        self._on_item_changed()

    @staticmethod
    def _deferred_button(label: str) -> QPushButton:
        """زر لعنصر غير مبني بعد: معطّل مع عبارة «قريبًا» كما في التصميم."""
        b = QPushButton(f"{label} — قريبًا")
        b.setEnabled(False)
        return b

    @staticmethod
    def _method_combo(first_blank: bool) -> QComboBox:
        """قائمة وسيلة دفع مدعومة في المحرك فقط؛ الثانية تبدأ بخانة «لا» تعني عدم وجود وسيلة ثانية."""
        c = QComboBox()
        if first_blank:
            c.addItem("— لا —", None)
        for key, label in PAYMENT_LABELS.items():
            c.addItem(label, key)
        return c

    # ---------- العميل والأصناف ----------
    def _on_customer_changed(self, *_args) -> None:
        """تغيير العميل يضبط فئة السعر حسب شريحته."""
        data = self.customer.currentData()
        self._set_tier(data[1] if data and data[1] else "retail")

    def refresh_items(self, *_args) -> None:
        """تحديث قائمة اختيار الصنف حسب نص البحث."""
        q = self.item_search.text()
        rows = []
        for kind in ("tire", "battery", "service"):
            rows += items.search(self.session.con, q, kind=kind, limit=ITEM_CHOICES)
        self.item_combo.blockSignals(True)
        self.item_combo.clear()
        for r in rows[:ITEM_CHOICES]:
            spec = f" · {ltr(r[4])}" if r[4] else ""
            self.item_combo.addItem(f"{r[3]} ({ltr(r[2])}){spec}", r)
        self.item_combo.blockSignals(False)
        self._on_item_changed()

    def _on_item_changed(self, *_args) -> None:
        """يملأ سعر الوحدة من فئة السعر المختارة للصنف."""
        r = self.item_combo.currentData()
        if r is None:
            self.price.clear()
            return
        cents = r[PRICE_COLUMN[self.tier]] or r[5]
        self.price.setText(f"{cents // 100}.{cents % 100:02d}")

    # ---------- الأسطر ----------
    def add_line(self) -> bool:
        """التحقق من مدخلات السطر ثم إضافته؛ يعيد True عند الإضافة، وإلا يعرض الخطأ ويترك المدخلات."""
        r = self.item_combo.currentData()
        if r is None:
            return self._fail("اختر صنفًا للسطر.")
        try:
            unit = parse_money(self.price.text())
            disc = parse_money(self.line_disc.text() or "0")
        except MoneyError as e:
            return self._fail(str(e))
        if unit <= 0:
            return self._fail("سعر الوحدة يجب أن يكون أكبر من صفر.")
        if disc < 0 or disc > self.qty.value() * unit:
            return self._fail("خصم السطر أكبر من قيمة السطر.")
        self.lines.append(SaleLineIn(item_id=r[0], qty=self.qty.value(), unit_price=unit, line_discount=disc))
        self._names.append(r[3])
        self.notice.show_message("")
        self.line_disc.clear()
        self.qty.setValue(1)
        self._refresh_lines()
        return True

    def remove_selected(self) -> None:
        """حذف السطر المختار من الفاتورة قبل الترحيل."""
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر سطرًا من الجدول أولًا.", "warn")
            return
        del self.lines[idx.row()]
        del self._names[idx.row()]
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        """إعادة عرض الأسطر والإجمالي ورسالة الفاتورة الفارغة."""
        self.lines_model.reset_rows([(i, self._names[i - 1], ln.qty, ln.unit_price, ln.line_discount)
                                     for i, ln in enumerate(self.lines, start=1)])
        self.empty_label.setVisible(not self.lines)
        self._refresh_total()

    def subtotal_minor(self) -> int:
        """مجموع الأسطر قبل خصومها بالقرش."""
        return sum(ln.qty * ln.unit_price for ln in self.lines)

    def total_minor(self) -> int:
        """الإجمالي بعد خصم الأسطر وخصم الفاتورة، بالقرش، كما يحسبه المحرك."""
        line_disc = sum(ln.line_discount for ln in self.lines)
        try:
            inv_disc = parse_money(self.invoice_disc.text() or "0")
        except MoneyError:
            inv_disc = 0
        return self.subtotal_minor() - line_disc - inv_disc

    def _refresh_total(self, *_args) -> None:
        """نصوص الملخص: قبل الخصم، والنهائي."""
        self.subtotal_label.setText(f"{ltr(fmt(self.subtotal_minor()))} جنيه")
        self.total_label.setText(f"{ltr(fmt(self.total_minor()))} جنيه")

    def _fail(self, text: str) -> bool:
        """عرض خطأ في شريط الرسائل؛ يعيد False ليسهل الخروج."""
        self.notice.show_message(text)
        return False

    # ---------- الدفع والترحيل ----------
    def _payments(self, total: int) -> list:
        """وسائل الدفع من النموذج؛ الفارغ من المبلغ يأخذ الباقي."""
        first = sales_rules.Payment(
            method=self.pay1_method.currentData(),
            amount=parse_money(self.pay1_amount.text()) if self.pay1_amount.text().strip() else total,
            received=parse_money(self.pay1_received.text()) if self.pay1_received.text().strip() else None,
            reference=self.pay1_ref.text().strip() or None)
        out = [first]
        if self.pay2_method.currentData() is not None:
            rest = total - first.amount
            amount = parse_money(self.pay2_amount.text()) if self.pay2_amount.text().strip() else rest
            out.append(sales_rules.Payment(method=self.pay2_method.currentData(), amount=amount))
        return out

    def post(self) -> None:
        """فحص الفاتورة ثم الترحيل عبر المحرك؛ أي رفض يُعرض بنصه ولا يُحفظ شيء، وتبقى المدخلات."""
        if self._busy:
            return
        if not self.lines:
            self._fail("أضف سطرًا واحدًا على الأقل قبل الترحيل.")
            return
        try:
            total = self.total_minor()
            payments = self._payments(total)
            inv_disc = parse_money(self.invoice_disc.text() or "0")
            odometer = int(self.odometer.text()) if self.odometer.text().strip() else None
        except (MoneyError, ValueError, sales_rules.SaleRuleError) as e:
            self._fail(f"تحقق من المدخلات: {e}")
            return
        customer = self.customer.currentData()
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = sales.post_sale(
                self.session.con, self.session.user, request_key=self._request_key,
                customer_id=customer[0] or None, lines=list(self.lines), payments=payments,
                sale_date=self.sale_date.text().strip(), invoice_discount=inv_disc,
                odometer=odometer, vehicle_plate=self.plate.text().strip() or None,
                notes=self.note.text().strip() or None)
        except (SaleError, sales_rules.SaleRuleError) as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # أي عطل غير متوقع: المحرك ذري فلا يبقى شيء ناقص
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self._reset_after_post()
            self.notice.show_message(f"تم ترحيل الفاتورة رقم {self.posted.number}", "success")
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)

    def _reset_after_post(self) -> None:
        """فاتورة جديدة فارغة بعد الترحيل، بمفتاح طلب جديد."""
        self._request_key = uuid.uuid4().hex
        self.lines.clear()
        self._names.clear()
        self.odometer.clear()
        self.plate.clear()
        self.note.clear()
        self.invoice_disc.clear()
        self.pay1_amount.clear()
        self.pay1_received.clear()
        self.pay1_ref.clear()
        self.pay2_method.setCurrentIndex(0)
        self.pay2_amount.clear()
        self.customer.setCurrentIndex(0)
        self._refresh_lines()
