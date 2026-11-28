"""Supplier return dialogs using the shared table-document layout and engine quote."""
import uuid
from datetime import date

from PySide6.QtCore import QAbstractTableModel, Qt, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLineEdit, QSpinBox

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import ledger, purchase_returns, purchase_view
from morv.infra.purchase_returns import PurchaseReturnError, ReturnLine
from morv.ui.adaptive_layout import AdaptiveFields
from morv.ui.document_table import DocumentEditor, DocumentTableDialog
from morv.ui.modal import Modal
from morv.ui.widgets import DateEdit, danger_button, field, form, ltr_edit, muted


class PurchaseReturnModel(QAbstractTableModel):
    NAME, INVOICE, LOT, AVAILABLE, QTY, BOOK, TAX = range(7)
    HEADERS = ("الصنف", "الفاتورة", "الدفعة / DOT", "المتاح", "المرتجع", "القيمة الدفترية", "الضريبة")
    draftChanged = Signal()

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = tuple(rows)
        self.quantities = [0] * len(rows)
        self.tax_inputs = [None] * len(rows)
        self.amounts = {}

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        source = self.rows[row]
        qty = self.quantities[row]
        actual = self.amounts.get(source.purchase_line_id)
        override = self.tax_inputs[row]
        if role == Qt.ItemDataRole.EditRole:
            if col == self.QTY:
                return qty
            if col == self.TAX:
                return override if override is not None else ""
            return None
        if role == Qt.ItemDataRole.TextAlignmentRole and col != self.NAME:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == self.NAME:
                return source.name
            if col == self.INVOICE:
                return f"{source.invoice_number} · {source.supplier_invoice_no} · {source.invoice_date}"
            if col == self.TAX and qty:
                if actual:
                    return (f"حصة الضريبة الأصلية {fmt(actual.allocated)}؛ "
                            f"القابلة للخصم {fmt(actual.deductible_amount)}. الفراغ يعيد الحساب التلقائي.")
                return "أدخل ضريبة مستند المورد أو اترك الحقل فارغًا للحساب التلقائي."
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        tax = ("—" if not qty else
               (ltr(fmt(actual.tax_amount)) + (" *" if actual.manual else "") if actual else
                (override or "—")))
        return (source.name, ltr(source.invoice_number), ltr(source.ref + (" / " + source.dot if source.dot else "")),
                ltr(str(source.available)), ltr(str(qty)),
                ltr(fmt(actual.book_value)) if actual else "—", tax)[col]

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        row, col = index.row(), index.column()
        if col == self.QTY or (col == self.TAX and self.quantities[row] and self.rows[row].original_tax):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not self.flags(index) & Qt.ItemFlag.ItemIsEditable:
            return False
        row, col = index.row(), index.column()
        if col == self.QTY:
            if type(value) is not int or not 0 <= value <= self.rows[row].available:
                return False
            if value == self.quantities[row]:
                return True
            self.tax_inputs[row] = None
            self.quantities[row] = value
        elif col == self.TAX:
            raw = str(value).strip() or None
            if raw == self.tax_inputs[row]:
                return True
            self.tax_inputs[row] = raw
        else:
            return False
        self.draftChanged.emit()
        self.dataChanged.emit(self.index(row, self.QTY), self.index(row, self.TAX), [])
        return True

    def selected_lines(self):
        lines = []
        for source, qty, raw in zip(self.rows, self.quantities, self.tax_inputs, strict=True):
            if not qty:
                continue
            try:
                tax = None if raw is None else parse_money(raw)
            except MoneyError as exc:
                raise PurchaseReturnError("ضريبة مستند المورد غير صالحة") from exc
            lines.append(ReturnLine(source.purchase_line_id, qty, tax))
        return lines

    def set_quote(self, quote):
        self.amounts = {r.purchase_line_id: r for r in quote.lines} if quote else {}
        if self.rows:
            self.dataChanged.emit(self.index(0, self.BOOK), self.index(len(self.rows) - 1, self.TAX), [])

    def select_all(self):
        for i, source in enumerate(self.rows):
            if self.quantities[i] != source.available:
                self.tax_inputs[i] = None
            self.quantities[i] = source.available
        self.draftChanged.emit()
        if self.rows:
            self.dataChanged.emit(self.index(0, self.QTY), self.index(len(self.rows) - 1, self.TAX), [])

    def clear_quantities(self):
        self.quantities = [0] * len(self.rows)
        self.tax_inputs = [None] * len(self.rows)
        self.draftChanged.emit()
        if self.rows:
            self.dataChanged.emit(self.index(0, self.QTY), self.index(len(self.rows) - 1, self.TAX), [])


class PurchaseReturnEditor(DocumentEditor):
    def createEditor(self, parent, option, index):
        if index.column() == PurchaseReturnModel.QTY:
            editor = self.quantity_editor(parent, index.model().rows[index.row()].available)
        else:
            editor = ltr_edit("تلقائي")
            editor.setParent(parent)
        self.active_editor = editor
        return editor

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, QSpinBox):
            editor.setValue(value)
        else:
            editor.setText(value)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QSpinBox):
            editor.interpretText()
            value = editor.value()
        else:
            value = editor.text()
        model.setData(index, value)


class PurchaseReturnDialog(DocumentTableDialog):
    def __init__(self, session, supplier_id: int, supplier_name: str, parent=None, *, invoice_id=None):
        super().__init__("مرتجع مشتريات", parent)
        self.session, self.supplier_id, self.saved = session, supplier_id, None
        self._key = uuid.uuid4().hex
        self._busy = False
        self.quote = None
        rows = purchase_returns.returnable_details(session.con, supplier_id, invoice_id)
        self.model = PurchaseReturnModel(rows, self)
        self.taxable_source = any(r.original_tax for r in rows)
        invoices = {r.invoice_id: r for r in rows}
        source_id = invoice_id if invoice_id is not None else next(iter(invoices)) if len(invoices) == 1 else None
        invoice = purchase_view.get_invoice(session.con, source_id) if source_id is not None else None
        if invoice and invoice.supplier_id != supplier_id:
            raise PurchaseReturnError("الفاتورة لا تخص هذا المورد")
        number = invoice.number if invoice else f"{len(invoices)} فواتير" if invoices else "—"
        self.metrics = self.add_document_metrics([
            ("اسم المورد", supplier_name), ("رقم الفاتورة", ltr(number) if invoice else number),
            ("تاريخ الفاتورة", ltr(invoice.invoice_date) if invoice else "متعددة" if invoices else "—"),
            ("فاتورة المورد", ltr(invoice.supplier_invoice_no) if invoice else "متعددة" if invoices else "—"),
            ("إجمالي الفاتورة" if invoice else "إجمالي الفواتير",
             ltr(fmt(invoice.total if invoice else sum(r.invoice_total for r in invoices.values())))),
            ("المتاح للإرجاع", f"{len(rows)} دفعة · {sum(r.available for r in rows)} وحدة"),
        ])
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب المرتجع (إلزامي)")
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        self.body.addWidget(self.fields([("تاريخ المرتجع *", self.date), ("سبب المرتجع *", self.reason)]))
        self.add_quantity_toolbar("أصناف الفاتورة" if invoice else "دفعات المورد", "حدّد الكمية في الجدول")
        self.view = self.add_document_table(self.model, PurchaseReturnEditor, stretch_column=self.model.NAME)
        self.view.setColumnHidden(self.model.INVOICE, invoice is not None)
        self.view.setColumnHidden(self.model.TAX, not self.taxable_source)

        bottom = QHBoxLayout()
        settlement, info = self.document_card()
        self.settlement_mode = QComboBox()
        self.settlement_mode.addItem("اختر طريقة التسوية", None)
        self.settlement_mode.addItem("التسوية الكاملة", "book")
        self.settlement_mode.addItem("مبلغ متفق عليه مع المورد", "manual")
        self.settlement_edit = ltr_edit("0.00")
        self.settlement_reason = QLineEdit()
        self.credit_note = QLineEdit()
        self.credit_note.setPlaceholderText("رقم مستند المورد")
        mode_field = field("طريقة التسوية *", self.settlement_mode)
        self.amount_field = field("مبلغ التسوية *", self.settlement_edit)
        self.reason_field = field("سبب اختلاف التسوية *", self.settlement_reason)
        self.credit_field = field("مرجع مستند المورد *", self.credit_note)
        info.addWidget(AdaptiveFields([mode_field, self.amount_field, self.reason_field, self.credit_field]))
        self.hint = muted("")
        self.hint.setWordWrap(True)
        info.addWidget(self.hint)
        bottom.addWidget(settlement, 1)
        totals, amounts = self.document_card()
        self.book_label = self.amount_row(amounts, "القيمة الدفترية")
        self.tax_label = self.amount_row(amounts, "إجمالي الضريبة المرتجعة")
        self.deductible_label = self.amount_row(amounts, "الضريبة القابلة للخصم")
        self.difference_label = self.amount_row(amounts, "فرق التسوية")
        self.total_label = self.amount_row(amounts, "تخفيض رصيد المورد", strong=True)
        bottom.addWidget(totals, 1)
        self.body.addLayout(bottom)
        self.post_btn = self.add_footer("تسجيل المرتجع", self.save)

        self.model.draftChanged.connect(self._refresh_preview)
        self.date.textChanged.connect(self._refresh_preview)
        self.reason.textChanged.connect(self._refresh_settlement)
        self.settlement_mode.currentIndexChanged.connect(self._refresh_settlement)
        self.settlement_edit.textChanged.connect(self._refresh_settlement)
        self.settlement_reason.textChanged.connect(self._refresh_settlement)
        self.credit_note.textChanged.connect(self._refresh_settlement)
        self._refresh_preview()
        if not rows:
            self.notice.show_message("لا توجد دفعات متاحة للإرجاع لهذا المورد.")

    @property
    def book_total(self):
        return self.quote.book_total if self.quote else None

    def _refresh_preview(self, *_args):
        self.refresh_quantity_actions()
        self.quote = None
        try:
            lines = self.model.selected_lines()
            if lines:
                self.quote = purchase_returns.preview_return(
                    self.session.con, self.supplier_id, lines, self.date.text().strip())
        except PurchaseReturnError as exc:
            self.notice.show_message(str(exc))
        else:
            self.notice.show_message("")
        self.model.set_quote(self.quote)
        self._refresh_settlement()

    def _settlement_amount(self):
        mode = self.settlement_mode.currentData()
        if mode == "book" and self.quote:
            return self.quote.default_settlement
        if mode == "manual":
            try:
                amount = parse_money(self.settlement_edit.text())
                return amount if amount >= 0 else None
            except MoneyError:
                return None
        return None

    def _refresh_settlement(self, *_args):
        quote = self.quote
        amount = self._settlement_amount()
        diff = None if amount is None or quote is None else amount - quote.default_settlement
        manual = any(raw is not None for raw in self.model.tax_inputs)
        special = bool(manual or diff)
        has_tax = quote.taxable_source if quote else self.taxable_source
        self.amount_field.setVisible(self.settlement_mode.currentData() == "manual")
        self.reason_field.setVisible(special)
        self.credit_field.setVisible(has_tax and special)
        for label in (self.tax_label, self.deductible_label):
            label.parentWidget().setVisible(has_tax)
        for label, value in (
            (self.book_label, quote.book_total if quote else None),
            (self.tax_label, quote.tax_total if quote else None),
            (self.deductible_label, quote.deductible_tax_total if quote else None),
            (self.difference_label, diff), (self.total_label, amount),
        ):
            label.setText("—" if value is None else ltr(fmt(value)) + " ج.م")
        needs_tax = bool(quote and quote.taxable_source and special and any(
            qty and row.original_tax and raw is None
            for row, qty, raw in zip(self.model.rows, self.model.quantities, self.model.tax_inputs, strict=True)))
        if needs_tax:
            hint = "أكّد ضريبة كل سطر خاضع في الجدول وفق مستند المورد."
        elif manual:
            hint = "* مبلغ ضريبة محدد يدويًا."
        else:
            hint = ""
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))
        self.post_btn.setEnabled(bool(quote and amount is not None and not self._busy and not needs_tax
                                      and len(self.reason.text().strip()) >= 3
                                      and (not special or len(self.settlement_reason.text().strip()) >= 3)
                                      and (not (has_tax and special) or self.credit_note.text().strip())
                                      and amount >= quote.tax_total))

    def save(self):
        if self._busy:
            return
        self.editor.finish()
        if not self.post_btn.isEnabled() or not self.quote:
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.saved = purchase_returns.post_return(
                self.session.con, self.session.user, request_key=self._key, supplier_id=self.supplier_id,
                lines=self.model.selected_lines(), reason=self.reason.text(), return_date=self.date.text().strip(),
                settlement_amount=self._settlement_amount(), settlement_reason=self.settlement_reason.text(),
                credit_note_ref=self.credit_note.text(), expected_quote=self.quote)
        except (PurchaseReturnError, ledger.LedgerError) as exc:
            self._refresh_preview()
            self.notice.show_message(str(exc))
        else:
            self.accept()
        finally:
            self._busy = False
            if self.saved is None:
                self._refresh_settlement()


class ReversePurchaseReturnDialog(Modal):
    def __init__(self, session, return_id: int, parent=None):
        super().__init__("عكس مرتجع مورد", parent, width=520)
        self.session, self.return_id, self.saved = session, return_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يعود الصنف إلى المخزون ويُسجَّل مستند عكسي بتاريخ اليوم."))
        f = form()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow("سبب العكس *", self.reason)
        self.body.addLayout(f)
        self.add_footer("عكس المرتجع", self.save)

    def save(self) -> None:
        try:
            self.saved = purchase_returns.reverse_return(
                self.session.con, self.session.user, request_key=self._key, return_id=self.return_id,
                reason=self.reason.text())
        except (PurchaseReturnError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class SupplierReturnsDialog(Modal):
    """Purchase returns newest first, of one supplier or of all of them; the selected one can be reversed."""

    def __init__(self, session, supplier_id: int | None = None, supplier_name: str = "", parent=None):
        title = f"مرتجعات المورد {supplier_name}" if supplier_id else "مرتجعات الشراء"
        super().__init__(title, parent, width=640)
        self.session, self.supplier_id, self.changed = session, supplier_id, False
        self.docs = QComboBox()
        self.body.addWidget(self.docs)
        self.note = muted("")
        self.body.addWidget(self.note)
        self.reverse_btn = danger_button("عكس المرتجع المحدد")
        self.reverse_btn.clicked.connect(self.reverse_selected)
        self.body.addWidget(self.reverse_btn)
        self.docs.currentIndexChanged.connect(self._selected)
        self.add_footer("إغلاق", self.accept)
        self._fill()

    def _fill(self) -> None:
        rows = purchase_returns.history(self.session.con, self.supplier_id)
        self.docs.blockSignals(True)
        self.docs.clear()
        for rid, number, kind, day, name, total, reason, reversed_ in rows:
            label = "مرتجع" if kind == "return" else "عكس مرتجع"
            reversible = kind == "return" and not reversed_
            # listing every supplier, the supplier's name is what tells two documents apart
            who = "" if self.supplier_id else f" · {name}"
            self.docs.addItem(
                f"{label} {number}{who} · {day} · {fmt(total)} · {reason}" + ("" if reversible else " — لا يُعكس"),
                (rid, reversible))
        self.docs.blockSignals(False)
        empty = "لا توجد مرتجعات لهذا المورد." if self.supplier_id else "لا توجد مرتجعات شراء بعد."
        self.note.setText(f"عدد المستندات: {self.docs.count()}" if self.docs.count() else empty)
        self._selected()

    def _selected(self) -> None:
        data = self.docs.currentData()
        self.reverse_btn.setEnabled(bool(data and data[1]))

    def reverse_selected(self) -> None:
        dlg = ReversePurchaseReturnDialog(self.session, self.docs.currentData()[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.changed = True
            self._fill()
