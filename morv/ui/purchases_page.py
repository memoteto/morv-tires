"""Purchases screen: posted invoice list with preview, and a full-page new-invoice form."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QTableView,
                               QVBoxLayout, QWidget)

from morv.core import dot as dotmod
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import items, purchase_view, purchasing, suppliers
from morv.infra.purchasing import PurchaseError, PurchaseLine
from morv.ui.listpage import TupleModel, ListPage
from morv.ui.widgets import Notice, form, ltr_edit, muted, primary_button

ITEM_CHOICES = 50          # most items in the picker; search narrows it


class PurchasesPage(QStackedWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.list_page = PurchasesListPage(session, self)
        self.form_page = None
        self.addWidget(self.list_page)

    def open_form(self) -> None:
        if self.form_page is not None:
            self.removeWidget(self.form_page)
            self.form_page.deleteLater()
        self.form_page = PurchaseForm(self.session, self)
        self.addWidget(self.form_page)
        self.setCurrentWidget(self.form_page)

    def back_to_list(self, keep_id: int | None = None) -> None:
        self.setCurrentWidget(self.list_page)
        self.list_page.reload(keep_id=keep_id)


class PurchasesListPage(ListPage):
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

    def __init__(self, session, stack=None):
        super().__init__(session)       # Qt refuses attributes set before its own init
        self.stack = stack

    def can_edit(self) -> bool:
        return self.session.can("purchases.post")

    def can_view(self) -> bool:
        return self.session.can("purchases.post") or self.session.can("items.cost_view")

    def fetch(self, query, filter_key, offset):
        if not self.can_view():
            return []
        return purchase_view.list_invoices(self.session.con, query, offset=offset)

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير الشراء"
        return f"{purchase_view.count(self.session.con)} فاتورة شراء"

    def build_preview(self, row_id: int) -> None:
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
        if row_id is not None:
            self.show_preview_id(row_id)
            return
        if not self.can_edit() or self.stack is None:
            return
        self.stack.open_form()


class PurchaseForm(QWidget):
    def __init__(self, session, stack, parent=None):
        super().__init__(parent)
        self.session = session
        self.stack = stack
        self.posted = None
        self.lines: list[PurchaseLine] = []
        self._names: list[str] = []
        self._busy = False
        self._request_key = uuid.uuid4().hex     # One key per form: pressing again cannot duplicate the invoice

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        self.notice = Notice()
        root.addWidget(self._header())
        root.addWidget(self.notice)

        # ---- Invoice header ----
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
        attach = QPushButton("إرفاق صورة فاتورة المورد — قريبًا")
        attach.setEnabled(False)
        f.addRow("المورد *", self.supplier)
        f.addRow("رقم فاتورة المورد *", self.inv_no)
        f.addRow("تاريخ الفاتورة *", self.inv_date)
        f.addRow("ملاحظات", self.notes)
        f.addRow("", attach)
        root.addLayout(f)

        # ---- Line entry ----
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
        root.addWidget(entry)

        # ---- Line table ----
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
        root.addWidget(self.view, 1)
        bar = QHBoxLayout()
        self.remove_btn = QPushButton("حذف السطر المختار")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.total_label = QLabel()
        self.total_label.setObjectName("cardTitle")
        bar.addWidget(self.remove_btn)
        bar.addStretch(1)
        bar.addWidget(self.total_label)
        root.addLayout(bar)

        footer = QHBoxLayout()
        self.post_btn = primary_button("ترحيل الفاتورة")
        self.post_btn.clicked.connect(self.post)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(lambda: self.stack.back_to_list())
        footer.addWidget(self.post_btn)
        footer.addWidget(cancel)
        footer.addStretch(1)
        root.addLayout(footer)

        self.refresh_items()
        self._refresh_total()
        if not self.supplier.count():
            self.notice.show_message("لا يوجد موردون نشطون؛ أضف مورّدًا من شاشة الموردين أولًا.", "warn")

    def _header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("فاتورة شراء جديدة")
        title.setObjectName("cardTitle")
        back = QPushButton("رجوع للقائمة")
        back.clicked.connect(lambda: self.stack.back_to_list())
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(back)
        return w

    # ---------- Items ----------
    def refresh_items(self, *_args) -> None:
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
        data = self.item_combo.currentData()
        if data is None:
            self.dot_hint.setText("لا أصناف مطابقة.")
        elif data[1] == "tire":
            self.dot_hint.setText("رمز DOT إلزامي للإطارات، مثل 2324 (الأسبوع 23 من سنة 2024).")
        else:
            self.dot_hint.setText("رمز DOT اختياري للبطاريات.")

    # ---------- Lines ----------
    def add_line(self) -> bool:
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
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر سطرًا من الجدول أولًا.", "warn")
            return
        del self.lines[idx.row()]
        del self._names[idx.row()]
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        self.lines_model.reset_rows([(i, self._names[i - 1], ln.dot, ln.qty, ln.unit_cost)
                                     for i, ln in enumerate(self.lines, start=1)])
        self._refresh_total()

    def total_minor(self) -> int:
        return sum(ln.qty * ln.unit_cost for ln in self.lines)

    def _refresh_total(self) -> None:
        self.total_label.setText(f"الإجمالي: {ltr(fmt(self.total_minor()))} جنيه")

    def _fail(self, text: str) -> bool:
        self.notice.show_message(text)
        return False

    # ---------- Posting ----------
    def post(self) -> None:
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
        except Exception as e:      # Any unexpected failure: the engine is atomic, so nothing half-written remains
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self.stack.back_to_list(keep_id=self.posted.id)
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
