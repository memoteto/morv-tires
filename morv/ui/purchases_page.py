"""شاشة المشتريات: قائمة فواتير الشراء المرحّلة ومعاينتها، ونافذة إنشاء فاتورة شراء وترحيلها."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableView, QVBoxLayout)

from morv.core import dot as dotmod
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import items, purchase_view, purchasing, suppliers
from morv.infra.purchasing import PurchaseError, PurchaseLine
from morv.ui.listpage import TupleModel, ListPage
from morv.ui.modal import Modal
from morv.ui.widgets import form, ltr_edit, muted

ITEM_CHOICES = 50          # أقصى عدد أصناف في قائمة الاختيار (يضيّقه البحث)


class PurchasesPage(ListPage):
    """قائمة فواتير الشراء المرحّلة. الفاتورة لا تُعدَّل بعد الترحيل؛ التصحيح بمستند عكسي في إصدار لاحق."""
    title = "المشتريات"
    add_label = "فاتورة شراء جديدة"
    search_placeholder = "رقم الفاتورة، المورد، أو رقم فاتورة المورد (F2)"
    columns = [
        ("الفاتورة", lambda r: ltr(r[1])),
        ("المورد", lambda r: r[2]),
        ("رقم فاتورة المورد", lambda r: ltr(r[3])),
        ("التاريخ", lambda r: ltr(r[4])),
        ("الإجمالي", lambda r: ltr(fmt(r[5]))),
    ]

    def can_edit(self) -> bool:
        """صلاحية ترحيل فواتير الشراء."""
        return self.session.can("purchases.post")

    def can_view(self) -> bool:
        """التكلفة بيانات حساسة: تظهر لمن يرحّل الفواتير أو يرى التكلفة فقط."""
        return self.session.can("purchases.post") or self.session.can("items.cost_view")

    def fetch(self, query, filter_key, offset):
        """صفحة من القائمة؛ فارغة لمن لا يملك صلاحية الاطلاع."""
        if not self.can_view():
            return []
        return purchase_view.list_invoices(self.session.con, query, offset=offset)

    def total(self) -> str:
        """العدّاد، أو سبب غياب القائمة."""
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير الشراء"
        return f"{purchase_view.count(self.session.con)} فاتورة شراء"

    def build_preview(self, row_id: int) -> None:
        """تفاصيل الفاتورة وأسطرها."""
        inv = purchase_view.get_invoice(self.session.con, row_id)
        self.heading(inv.number, f"المورد: {inv.supplier_name}")
        self.kv("رقم فاتورة المورد", ltr(inv.supplier_invoice_no))
        self.kv("تاريخ الفاتورة", ltr(inv.invoice_date))
        self.kv("تاريخ الاستلام", ltr(inv.received_date))
        self.kv("الإجمالي", ltr(fmt(inv.total)))
        if inv.notes:
            self.kv("ملاحظات", inv.notes)
        for ln in inv.lines:
            dot = f" · DOT {ltr(ln.dot)}" if ln.dot else ""
            self.kv(f"{ln.line_no}. {ln.item_name}",
                    f"{ltr(str(ln.qty))} × {ltr(fmt(ln.unit_cost))} = {ltr(fmt(ln.line_total))}{dot}")

    def open_dialog(self, row_id):
        """صف موجود: معاينته (لا تعديل بعد الترحيل). بلا صف: نافذة فاتورة جديدة."""
        if row_id is not None:
            self.show_preview_id(row_id)
            return
        if not self.can_edit():
            return
        dlg = PurchaseDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=dlg.posted.id)


class PurchaseDialog(Modal):
    """نافذة فاتورة شراء: المورد والرأس، ثم إدخال الأسطر، ثم الترحيل بزر واحد عبر محرك المشتريات."""

    def __init__(self, session, parent=None):
        """بناء الرأس وإدخال الأسطر وجدول الأسطر والإجمالي."""
        super().__init__("فاتورة شراء جديدة", parent, width=960)
        self.session = session
        self.posted = None
        self.lines: list[PurchaseLine] = []
        self._names: list[str] = []
        self._busy = False
        self._request_key = uuid.uuid4().hex     # مفتاح واحد للنموذج: إعادة الضغط لا تكرر الفاتورة

        # ---- رأس الفاتورة ----
        f = form()
        self.supplier = QComboBox()
        for row in suppliers.search(session.con, "", limit=300):
            self.supplier.addItem(row[1], row[0])
        self.inv_no = ltr_edit("رقم الفاتورة عند المورد")
        self.inv_date = ltr_edit("YYYY-MM-DD")
        self.inv_date.setText(date.today().isoformat())
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("ملاحظات (اختيارية)")
        self.notes.setFixedHeight(48)
        f.addRow("المورد *", self.supplier)
        f.addRow("رقم فاتورة المورد *", self.inv_no)
        f.addRow("تاريخ الفاتورة *", self.inv_date)
        f.addRow("ملاحظات", self.notes)
        self.body.addLayout(f)

        # ---- إدخال سطر ----
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
        self.cost = ltr_edit("0.00")
        self.dot = ltr_edit("WWYY")
        self.add_btn = QPushButton("إضافة السطر")
        self.add_btn.clicked.connect(self.add_line)
        for title, w in (("الكمية", self.qty), ("تكلفة الوحدة (جنيه)", self.cost), ("رمز DOT (أسبوع وسنة)", self.dot)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            row.addLayout(cell, 1)
        row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(row)
        self.dot_hint = muted("")
        col.addWidget(self.dot_hint)
        self.body.addWidget(entry)

        # ---- جدول الأسطر ----
        self.lines_model = TupleModel([
            ("#", lambda r: ltr(str(r[0]))),
            ("الصنف", lambda r: r[1]),
            ("DOT", lambda r: ltr(r[2]) if r[2] else "—"),
            ("الكمية", lambda r: ltr(str(r[3]))),
            ("تكلفة الوحدة", lambda r: ltr(fmt(r[4]))),
            ("إجمالي السطر", lambda r: ltr(fmt(r[3] * r[4]))),
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
        self.view.setMinimumHeight(170)
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

        self.post_btn = self.add_footer("ترحيل الفاتورة", self.post)
        self.refresh_items()
        self._refresh_total()
        if not self.supplier.count():
            self.notice.show_message("لا يوجد موردون نشطون؛ أضف مورّدًا من شاشة الموردين أولًا.", "warn")

    # ---------- الأصناف ----------
    def refresh_items(self, *_args) -> None:
        """تحديث قائمة اختيار الصنف حسب نص البحث (إطارات وبطاريات فقط؛ الخدمات لا تُشترى)."""
        q = self.item_search.text()
        rows = []
        for kind in ("tire", "battery"):
            rows += items.search(self.session.con, q, kind=kind, limit=ITEM_CHOICES)
        self.item_combo.clear()
        for r in rows[:ITEM_CHOICES]:
            spec = f" · {ltr(r[4])}" if r[4] else ""
            self.item_combo.addItem(f"{r[3]} ({ltr(r[2])}){spec}", (r[0], r[1], r[3]))
        self._on_item_changed()

    def _on_item_changed(self, *_args) -> None:
        """تلميح DOT حسب نوع الصنف المختار."""
        data = self.item_combo.currentData()
        if data is None:
            self.dot_hint.setText("لا أصناف مطابقة.")
        elif data[1] == "tire":
            self.dot_hint.setText("رمز DOT إلزامي للإطارات، مثل 2324 (الأسبوع 23 من سنة 2024).")
        else:
            self.dot_hint.setText("رمز DOT اختياري للبطاريات.")

    # ---------- الأسطر ----------
    def add_line(self) -> bool:
        """التحقق من مدخلات السطر ثم إضافته؛ يعيد True عند الإضافة، وإلا يعرض الخطأ ويترك المدخلات."""
        data = self.item_combo.currentData()
        if data is None:
            return self._fail("اختر صنفًا للسطر.")
        try:
            cost = parse_money(self.cost.text())
        except MoneyError as e:
            return self._fail(f"تكلفة الوحدة: {e}")
        if cost <= 0:
            return self._fail("تكلفة الوحدة يجب أن تكون أكبر من صفر.")
        dot_text = self.dot.text().strip()
        dot = None
        if dot_text:
            try:
                dot = dotmod.parse_dot(dot_text)
            except dotmod.DotError as e:
                return self._fail(str(e))
        elif data[1] == "tire":
            return self._fail(f"رمز DOT إلزامي للإطار «{data[2]}».")
        self.lines.append(PurchaseLine(data[0], self.qty.value(), cost, dot))
        self._names.append(data[2])
        self.notice.show_message("")
        self.cost.clear()
        self.dot.clear()
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

    def _refresh_lines(self) -> None:
        """إعادة عرض الأسطر والإجمالي."""
        self.lines_model.reset_rows([(i, self._names[i - 1], ln.dot, ln.qty, ln.unit_cost)
                                     for i, ln in enumerate(self.lines, start=1)])
        self._refresh_total()

    def total_minor(self) -> int:
        """إجمالي المسودة بالقرش."""
        return sum(ln.qty * ln.unit_cost for ln in self.lines)

    def _refresh_total(self) -> None:
        """نص الإجمالي."""
        self.total_label.setText(f"الإجمالي: {ltr(fmt(self.total_minor()))} جنيه")

    def _fail(self, text: str) -> bool:
        """عرض خطأ في شريط الرسائل؛ يعيد False ليسهل الخروج."""
        self.notice.show_message(text)
        return False

    # ---------- الترحيل ----------
    def post(self) -> None:
        """فحص الرأس ثم الترحيل عبر المحرك؛ أي رفض يُعرض بنصه ولا يُحفظ شيء، وتبقى المدخلات."""
        if self._busy:
            return
        supplier_id = self.supplier.currentData()
        if supplier_id is None:
            self._fail("اختر المورد.")
            return
        if not self.inv_no.text().strip():
            self._fail("رقم فاتورة المورد مطلوب.")
            return
        if not self.lines:
            self._fail("أضف سطرًا واحدًا على الأقل قبل الترحيل.")
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = purchasing.post_invoice(
                self.session.con, self.session.user, request_key=self._request_key, supplier_id=supplier_id,
                supplier_invoice_no=self.inv_no.text(), invoice_date=self.inv_date.text().strip(),
                lines=list(self.lines), notes=self.notes.toPlainText())
        except PurchaseError as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # أي عطل غير متوقع: المحرك ذري فلا يبقى شيء ناقص
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self.accept()
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)

