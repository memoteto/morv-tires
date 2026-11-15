"""شاشة المبيعات: قائمة فواتير البيع المرحّلة ومعاينتها، ونافذة فاتورة بيع جديدة وترحيلها."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QTableView, QVBoxLayout)

from morv.core import sales_rules
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import customers, items, sale_view, sales
from morv.infra.sales import SaleError, SaleLineIn
from morv.ui.listpage import ListPage, TupleModel
from morv.ui.modal import Modal
from morv.ui.widgets import form, ltr_edit, muted

ITEM_CHOICES = 50
PRICE_COLUMN = {"retail": 5, "wholesale": 6, "super": 7}      # موضع سعر الشريحة في صف items.search
PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل بنكي", "wallet": "محفظة", "credit": "على الحساب"}


class SalesPage(ListPage):
    """قائمة فواتير البيع المرحّلة. الفاتورة لا تُعدَّل بعد الترحيل؛ التصحيح بمستند عكسي في إصدار لاحق."""
    title = "المبيعات"
    add_label = "فاتورة بيع جديدة"
    search_placeholder = "رقم الفاتورة، اسم العميل، أو الموبايل (F2)"
    columns = [
        ("الفاتورة", lambda r: ltr(r[1])),
        ("العميل", lambda r: r[2]),
        ("التاريخ", lambda r: ltr(r[3])),
        ("الإجمالي", lambda r: ltr(fmt(r[4]))),
        ("الدفع", lambda r: r[5] or "—"),
    ]

    def can_edit(self) -> bool:
        """صلاحية ترحيل فواتير البيع."""
        return self.session.can("sales.post")

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
        """صف موجود: معاينته. بلا صف: نافذة فاتورة جديدة."""
        if row_id is not None:
            self.show_preview_id(row_id)
            return
        if not self.can_edit():
            return
        dlg = SaleDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.posted.id)


class SaleDialog(Modal):
    """نافذة فاتورة بيع: العميل والعداد، ثم الأسطر وخصومها، ثم وسيلتا الدفع، ثم الترحيل عبر محرك المبيعات."""

    def __init__(self, session, parent=None):
        """بناء الرأس وإدخال الأسطر والدفع وجدول الأسطر والإجمالي."""
        super().__init__("فاتورة بيع جديدة", parent, width=980)
        self.session = session
        self.posted = None
        self.lines: list[SaleLineIn] = []
        self._names: list[str] = []
        self._busy = False
        self._request_key = uuid.uuid4().hex     # مفتاح واحد للنموذج: إعادة الضغط لا تكرر الفاتورة

        # ---- رأس الفاتورة ----
        f = form()
        self.customer = QComboBox()
        self.customer.addItem("عميل نقدي", ("", None))
        for row in customers.search(session.con, "", limit=300):
            self.customer.addItem(row[1], (row[0], row[3]))
        self.customer.currentIndexChanged.connect(self._on_customer_changed)
        self.odometer = ltr_edit("قراءة العداد (اختياري)")
        self.plate = ltr_edit("رقم اللوحة (اختياري)")
        self.sale_date = ltr_edit("YYYY-MM-DD")
        self.sale_date.setText(date.today().isoformat())
        f.addRow("العميل", self.customer)
        f.addRow("قراءة العداد", self.odometer)
        f.addRow("رقم اللوحة", self.plate)
        f.addRow("تاريخ الفاتورة *", self.sale_date)
        self.body.addLayout(f)

        # ---- إدخال سطر ----
        entry = QFrame()
        entry.setObjectName("card")
        col = QVBoxLayout(entry)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(8)
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("ابحث عن صنف أو خدمة: الاسم أو الكود أو المقاس")
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
        self.price = ltr_edit("0.00")
        self.line_disc = ltr_edit("0.00")
        self.add_btn = QPushButton("إضافة السطر")
        self.add_btn.clicked.connect(self.add_line)
        for title, w in (("الكمية", self.qty), ("سعر الوحدة (جنيه)", self.price), ("خصم السطر (جنيه)", self.line_disc)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            row.addLayout(cell, 1)
        row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(row)
        self.body.addWidget(entry)

        # ---- جدول الأسطر ----
        self.lines_model = TupleModel([
            ("#", lambda r: ltr(str(r[0]))),
            ("الصنف", lambda r: r[1]),
            ("الكمية", lambda r: ltr(str(r[2]))),
            ("سعر الوحدة", lambda r: ltr(fmt(r[3]))),
            ("خصم السطر", lambda r: ltr(fmt(r[4])) if r[4] else "—"),
            ("صافي السطر", lambda r: ltr(fmt(r[2] * r[3] - r[4]))),
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

        # ---- الخصم والإجمالي ----
        bar = QHBoxLayout()
        self.remove_btn = QPushButton("حذف السطر المختار")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.invoice_disc = ltr_edit("0.00")
        self.invoice_disc.setPlaceholderText("خصم على الفاتورة كلها")
        self.invoice_disc.textChanged.connect(self._refresh_total)
        self.total_label = QLabel()
        self.total_label.setObjectName("cardTitle")
        bar.addWidget(self.remove_btn)
        bar.addStretch(1)
        bar.addWidget(muted("خصم الفاتورة (جنيه)"))
        bar.addWidget(self.invoice_disc)
        bar.addWidget(self.total_label)
        self.body.addLayout(bar)

        # ---- وسائل الدفع: حتى وسيلتين ----
        pay = form()
        self.pay1_method = self._method_combo(first_blank=False)
        self.pay1_amount = ltr_edit("فارغ = كامل الإجمالي")
        self.pay1_received = ltr_edit("المستلم نقدًا (لحساب الباقي)")
        self.pay2_method = self._method_combo(first_blank=True)
        self.pay2_amount = ltr_edit("فارغ = المتبقي")
        pay.addRow("الدفعة الأولى", self._pair(self.pay1_method, self.pay1_amount, self.pay1_received))
        pay.addRow("الدفعة الثانية (اختيارية)", self._pair(self.pay2_method, self.pay2_amount))
        self.body.addLayout(pay)
        self.change_label = muted("")
        self.body.addWidget(self.change_label)

        self.post_btn = self.add_footer("ترحيل الفاتورة", self.post)
        self.refresh_items()
        self._refresh_total()
        if not items.search(self.session.con, "", limit=1):
            self.notice.show_message("لا توجد أصناف نشطة؛ أضف أصنافًا من شاشة المخزون أولًا.", "warn")

    # ---------- أدوات بناء ----------
    @staticmethod
    def _method_combo(first_blank: bool) -> QComboBox:
        """قائمة وسيلة دفع؛ الثانية تبدأ بخانة فارغة تعني «لا وسيلة ثانية»."""
        c = QComboBox()
        if first_blank:
            c.addItem("— لا —", None)
        for key, label in PAYMENT_LABELS.items():
            c.addItem(label, key)
        return c

    @staticmethod
    def _pair(*widgets) -> QFrame:
        """صف أفقي لعناصر الدفعة الواحدة."""
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for x in widgets:
            h.addWidget(x, 1)
        return w

    # ---------- العميل والأصناف ----------
    def _on_customer_changed(self, *_args) -> None:
        """تغيير شريحة السعر حسب العميل ثم تحديث أسعار الأسطر المعروضة في الاختيار."""
        self._on_item_changed()

    def _tier_key(self) -> str:
        """شريحة السعر للعميل الحالي؛ العميل النقدي قطاعي."""
        data = self.customer.currentData()
        return (data[1] if data and data[1] else "retail")

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
        """يملأ سعر الوحدة من شريحة العميل للصنف المختار."""
        r = self.item_combo.currentData()
        if r is None:
            self.price.clear()
            return
        cents = r[PRICE_COLUMN[self._tier_key()]] or r[5]
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
        """حذف السطر المختار من المسودة قبل الترحيل."""
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر سطرًا من الجدول أولًا.", "warn")
            return
        del self.lines[idx.row()]
        del self._names[idx.row()]
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        """إعادة عرض الأسطر والإجمالي."""
        self.lines_model.reset_rows([(i, self._names[i - 1], ln.qty, ln.unit_price, ln.line_discount)
                                     for i, ln in enumerate(self.lines, start=1)])
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
        """نص الإجمالي وحساب الباقي المعروض."""
        self.total_label.setText(f"الإجمالي: {ltr(fmt(self.total_minor()))} جنيه")

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
            received=parse_money(self.pay1_received.text()) if self.pay1_received.text().strip() else None)
        out = [first]
        if self.pay2_method.currentData() is not None:
            rest = total - first.amount
            amount = parse_money(self.pay2_amount.text()) if self.pay2_amount.text().strip() else rest
            out.append(sales_rules.Payment(method=self.pay2_method.currentData(), amount=amount))
        return out

    def post(self) -> None:
        """فحص الرأس والأسطر ثم الترحيل عبر المحرك؛ أي رفض يُعرض بنصه ولا يُحفظ شيء، وتبقى المدخلات."""
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
                odometer=odometer, vehicle_plate=self.plate.text().strip() or None)
        except (SaleError, sales_rules.SaleRuleError) as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # أي عطل غير متوقع: المحرك ذري فلا يبقى شيء ناقص
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self.accept()
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
