"""Sales return dialog with direct invoice-line editing and fixed totals and approval."""
import uuid
from datetime import date

from PySide6.QtCore import QAbstractTableModel, Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QSpinBox

from morv.core import return_rules
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra.sale_returns import ReturnError, ReturnLineIn, post_return, preview_installment_refund
from morv.ui.document_table import DocumentEditor, DocumentTableDialog
from morv.ui.widgets import DateEdit, ltr_edit, muted

CONDITIONS = (("sound", "سليم"), ("defective", "معيب"))
REFUND_LABELS = {"cash": "نقدي", "card": "بطاقة / ماكينة POS", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                 "credit": "على حساب العميل", "installment": "تقسيط — رد المدفوع وإلغاء المتبقي"}
REASONS = ("عيب في الصنف", "مقاس خاطئ", "لم يُركَّب", "طلب العميل")


class ReturnTableModel(QAbstractTableModel):
    NAME, AVAILABLE, QTY, CONDITION, TAX, TOTAL = range(6)
    HEADERS = ("الصنف", "المتاح", "كمية المرتجع", "الحالة", "الضريبة", "الإجمالي")

    def __init__(self, returnable, parent=None):
        super().__init__(parent)
        self.rows = tuple(returnable)
        self.quantities = [0] * len(self.rows)
        self.conditions = ["sound"] * len(self.rows)

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def amounts(self, row):
        source, qty = self.rows[row], self.quantities[row]
        return (return_rules.portion(source.net_total, source.returned, qty, source.sold),
                return_rules.tax_portion(source.net_total, source.tax_amount, source.returned, qty, source.sold))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        source, qty, condition = self.rows[row], self.quantities[row], self.conditions[row]
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == self.NAME:
                return (f"{source.item_name} — المباع {source.sold}، سبق إرجاع {source.returned}")
            if col == self.CONDITION:
                return "السليم يعود للمخزون، والمعيب يذهب لمخزون التالف"
        if role == Qt.ItemDataRole.TextAlignmentRole and col != self.NAME:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.EditRole:
            return qty if col == self.QTY else condition if col == self.CONDITION else None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        total, tax = self.amounts(row)
        return (source.item_name, ltr(str(source.available)), ltr(str(qty)),
                dict(CONDITIONS)[condition] if qty else "—", ltr(fmt(tax)), ltr(fmt(total)))[col]

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self.rows[index.row()].available > 0 and (
                index.column() == self.QTY or
                (index.column() == self.CONDITION and self.quantities[index.row()] > 0)):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not self.flags(index) & Qt.ItemFlag.ItemIsEditable:
            return False
        row, col = index.row(), index.column()
        if col == self.QTY:
            if type(value) is not int or not 0 <= value <= self.rows[row].available:
                return False
            self.quantities[row] = value
        elif col == self.CONDITION:
            if value not in dict(CONDITIONS):
                return False
            self.conditions[row] = value
        else:
            return False
        self.dataChanged.emit(self.index(row, self.QTY), self.index(row, self.TOTAL), [])
        return True

    def select_all(self):
        self.quantities = [r.available for r in self.rows]
        if self.rows:
            self.dataChanged.emit(self.index(0, self.QTY), self.index(len(self.rows) - 1, self.TOTAL), [])

    def clear_quantities(self):
        self.quantities = [0] * len(self.rows)
        if self.rows:
            self.dataChanged.emit(self.index(0, self.QTY), self.index(len(self.rows) - 1, self.TOTAL), [])

    def selected_lines(self):
        return [ReturnLineIn(r.sale_line_id, qty, condition)
                for r, qty, condition in zip(self.rows, self.quantities, self.conditions, strict=True) if qty]

    def expected_amounts(self):
        return {r.sale_line_id: (r.returned, *self.amounts(i))
                for i, r in enumerate(self.rows) if self.quantities[i]}


class ReturnEditor(DocumentEditor):
    def createEditor(self, parent, option, index):
        if index.column() == ReturnTableModel.QTY:
            editor = self.quantity_editor(parent, index.model().rows[index.row()].available)
        else:
            editor = QComboBox(parent)
            for key, label in CONDITIONS:
                editor.addItem(label, key)
        self.active_editor = editor
        return editor

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QSpinBox):
            editor.setValue(value)
        else:
            editor.setCurrentIndex(editor.findData(value))

    def setModelData(self, editor, model, index):
        if isinstance(editor, QSpinBox):
            editor.interpretText()
            value = editor.value()
        else:
            value = editor.currentData()
        model.setData(index, value)


class SaleReturnDialog(DocumentTableDialog):
    def __init__(self, session, sale, returnable, methods, parent=None):
        super().__init__(f"مرتجع بيع — {ltr(sale.number)}", parent)
        self.session = session
        self.sale = sale
        self.posted = None
        self._busy = False
        self._request_key = uuid.uuid4().hex
        self.metrics = self.add_document_metrics([
            ("العميل", sale.customer_name), ("إجمالي الفاتورة", ltr(fmt(sale.total))),
            ("المتاح للإرجاع", ltr(fmt(sale.total - sale.returned))),
        ])

        self.return_date = DateEdit()
        self.return_date.setText(max(date.today().isoformat(), sale.sale_date))
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems(REASONS)
        self.reason.setCurrentText("")
        self.refund = QComboBox()
        for method in methods:
            self.refund.addItem(REFUND_LABELS.get(method, method), method)
        self.refund.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.refund.setMinimumContentsLength(12)
        self.note = ltr_edit("ملاحظة (اختيارية)")
        self.body.addWidget(self.fields([
            ("تاريخ المرتجع *", self.return_date), ("سبب المرتجع *", self.reason),
            ("طريقة الرد", self.refund), ("ملاحظة", self.note),
        ]))

        self.add_quantity_toolbar("أصناف الفاتورة", "حدّد الكمية مباشرة في الجدول")

        self.model = ReturnTableModel(returnable, self)
        self.view = self.add_document_table(self.model, ReturnEditor, stretch_column=self.model.NAME)
        self.view.setColumnHidden(self.model.TAX, not sale.tax_enabled)

        bottom = QHBoxLayout()
        settlement, info = self.document_card()
        title = QLabel("تسوية المرتجع")
        title.setObjectName("cardTitle")
        info.addWidget(title)
        self.refund_breakdown = muted("")
        self.refund_breakdown.setWordWrap(True)
        self.refund_breakdown.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        info.addWidget(self.refund_breakdown)
        bottom.addWidget(settlement, 1)
        totals, totals_layout = self.document_card()
        self.before_tax_label = self.amount_row(totals_layout, "الإجمالي قبل الضريبة")
        self.tax_label = self.amount_row(totals_layout, "ضريبة القيمة المضافة")
        self.total_label = self.amount_row(
            totals_layout, "إجمالي المرتجع شامل الضريبة" if sale.tax_enabled else "إجمالي المرتجع",
            strong=True)
        self.before_tax_label.parentWidget().setVisible(sale.tax_enabled)
        self.tax_label.parentWidget().setVisible(sale.tax_enabled)
        bottom.addWidget(totals, 1)
        self.body.addLayout(bottom)

        self.post_btn = self.add_footer("اعتماد المرتجع", self.post)
        self.model.dataChanged.connect(self._refresh_total)
        self.refund.currentIndexChanged.connect(self._refresh_total)
        self.return_date.textChanged.connect(self._refresh_total)
        self._refresh_total()

    @property
    def lines(self):
        return self.model.selected_lines()

    def _refresh_total(self, *_args):
        self.refresh_quantity_actions()
        amounts = self.model.expected_amounts()
        total = sum(a[1] for a in amounts.values())
        tax = sum(a[2] for a in amounts.values())
        self.before_tax_label.setText(ltr(fmt(total - tax)))
        self.tax_label.setText(ltr(fmt(tax)))
        self.total_label.setText(ltr(fmt(total)) + " جنيه")
        if self.refund.currentData() != "installment":
            self.refund_breakdown.setText(
                f"عدد الأصناف المرتجعة: {ltr(str(len(amounts)))}\nطريقة الرد: {self.refund.currentText()}")
            self.post_btn.setEnabled(bool(amounts) and not self._busy)
            return
        if not amounts:
            self.refund_breakdown.setText("يلزم إرجاع كل الأصناف؛ المرتجع الجزئي للتقسيط غير متاح.")
            self.post_btn.setEnabled(False)
            return
        try:
            preview = preview_installment_refund(
                self.session.con, self.sale.id, self.lines, self.return_date.text().strip())
        except ReturnError as exc:
            self.refund_breakdown.setText(str(exc))
            self.post_btn.setEnabled(False)
            return
        methods = "، ".join(f"{REFUND_LABELS[method]}: {ltr(fmt(amount))}" for method, amount in preview.refunds)
        self.refund_breakdown.setText(
            f"سيُرد للعميل: {ltr(fmt(preview.refunded))} ({methods or 'لا مبالغ محصّلة'}). "
            f"تُلغى المديونية: {ltr(fmt(preview.receivable_reduced))}. "
            f"الفوائد المؤجلة: {ltr(fmt(preview.deferred_reversal))}، "
            f"الفوائد المعترف بها سابقًا: {ltr(fmt(preview.recognized_reversal))} جنيه.")
        self.post_btn.setEnabled(not self._busy)

    def post(self):
        if self._busy:
            return
        self.editor.finish()
        self._refresh_total()
        if not self.lines:
            self.notice.show_message("حدّد كمية مرتجع لصنف واحد على الأقل.")
            return
        if not self.post_btn.isEnabled():
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = post_return(
                self.session.con, self.session.user, request_key=self._request_key, sale_id=self.sale.id,
                lines=self.lines, reason=self.reason.currentText(), refund_method=self.refund.currentData(),
                return_date=self.return_date.text().strip(), notes=self.note.text().strip() or None,
                expected_amounts=self.model.expected_amounts())
        except ReturnError as exc:
            self.notice.show_message(f"لم يُرحَّل المرتجع: {exc}")
        except Exception as exc:
            self.notice.show_message(f"تعذّر الترحيل ولم يُحفظ شيء ({type(exc).__name__}).")
        else:
            self.accept()
        finally:
            self._busy = False
            if self.posted is None:
                self._refresh_total()
