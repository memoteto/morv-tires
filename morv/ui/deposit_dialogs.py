"""Customer deposit dialog and the customer documents list with receipt and deposit reversal."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit

from morv.core.money import MoneyError, fmt, parse_money
from morv.infra import customer_deposits, customer_receipts, ledger
from morv.infra.customer_deposits import DepositError
from morv.ui.modal import Modal
from morv.ui.receipt_dialogs import METHOD_LABELS, ReverseReceiptDialog
from morv.ui.widgets import DateEdit, danger_button, form, ltr_edit, muted

KIND_LABELS = {"deposit": "إيداع عربون", "refund": "رد عربون", "apply": "تسوية العربون على الدين"}


class DepositDialog(Modal):
    def __init__(self, session, customer_id: int, customer_name: str, parent=None):
        super().__init__("عربون العميل", parent, width=540)
        self.session, self.customer_id, self.saved = session, customer_id, None
        self._key = uuid.uuid4().hex     # one key per dialog: a double click or a retry cannot post twice
        con = session.con
        held = customer_receipts.deposit_balance(con, customer_id)
        debt = customer_receipts.customer_balance(con, customer_id)
        self.body.addWidget(muted(f"العميل: {customer_name} · العربون المتاح: {fmt(held)} · الدين الآجل: {fmt(debt)}"))
        f = form()
        self.kind = QComboBox()
        for key, label in KIND_LABELS.items():
            self.kind.addItem(label, key)
        self.method = QComboBox()
        for key in customer_receipts.METHODS:
            self.method.addItem(METHOD_LABELS[key], key)
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        self.amount = ltr_edit("0.00")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("ملاحظات (اختياري)")
        self.method_label = muted("طريقة الدفع *")
        f.addRow("العملية *", self.kind)
        f.addRow(self.method_label, self.method)
        f.addRow("التاريخ *", self.date)
        f.addRow("المبلغ *", self.amount)
        f.addRow("ملاحظات", self.notes)
        self.body.addLayout(f)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.add_footer("تسجيل", self.save)
        self.amount.setFocus()

    def _kind_changed(self) -> None:
        money = self.kind.currentData() != "apply"
        self.method.setVisible(money)
        self.method_label.setVisible(money)

    def save(self) -> None:
        try:
            amount = parse_money(self.amount.text())
        except MoneyError as e:
            self.notice.show_message(f"المبلغ: {e}")
            return
        try:
            self.saved = customer_deposits.post_document(
                self.session.con, self.session.user, request_key=self._key, customer_id=self.customer_id,
                kind=self.kind.currentData(), amount=amount, method=self.method.currentData(),
                doc_date=self.date.text().strip(), notes=self.notes.text())
        except (DepositError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ReverseDepositDialog(Modal):
    def __init__(self, session, document_id: int, parent=None):
        super().__init__("عكس مستند عربون", parent, width=520)
        self.session, self.document_id, self.saved = session, document_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل مستند عكسي بتاريخ اليوم ويبقى المستند الأصلي كما هو في السجل."))
        f = form()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow("سبب العكس *", self.reason)
        self.body.addLayout(f)
        self.add_footer("عكس المستند", self.save)
        self.reason.setFocus()

    def save(self) -> None:
        try:
            self.saved = customer_deposits.reverse_document(
                self.session.con, self.session.user, request_key=self._key, document_id=self.document_id,
                reason=self.reason.text())
        except (DepositError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class CustomerDocumentsDialog(Modal):
    """Receipts and deposit documents of one customer, newest first; the selected one can be reversed."""

    def __init__(self, session, customer_id: int, customer_name: str, parent=None):
        super().__init__(f"مستندات العميل {customer_name}", parent, width=640)
        self.session, self.customer_id, self.changed = session, customer_id, False
        self.docs = QComboBox()
        self.body.addWidget(self.docs)
        self.note = muted("")
        self.body.addWidget(self.note)
        self.reverse_btn = danger_button("عكس المستند المحدد")
        self.reverse_btn.clicked.connect(self.reverse_selected)
        self.body.addWidget(self.reverse_btn)
        self.docs.currentIndexChanged.connect(self._selected)
        self.add_footer("إغلاق", self.accept)
        self._fill()

    def _fill(self) -> None:
        con = self.session.con
        items = []
        for rid, number, kind, day, method, amount, _notes, reversed_ in customer_receipts.history(con, self.customer_id):
            label = "سند قبض" if kind == "receipt" else "عكس سند قبض"
            items.append((day, rid, "receipt", f"{label} {number} · {day} · {fmt(amount)}"
                          + (f" · {METHOD_LABELS[method]}" if method else ""), kind == "receipt" and not reversed_))
        for did, number, kind, day, method, amount, _notes, reversed_ in customer_deposits.history(con, self.customer_id):
            label = customer_deposits.LABELS[kind]
            items.append((day, did, "deposit", f"{label} {number} · {day} · {fmt(amount)}"
                          + (f" · {METHOD_LABELS[method]}" if method else ""), kind != "reversal" and not reversed_))
        self.docs.blockSignals(True)
        self.docs.clear()
        for _day, doc_id, source, label, reversible in sorted(items, key=lambda x: (x[0], x[1]), reverse=True):
            self.docs.addItem(label + ("" if reversible else " — لا يُعكس"), (source, doc_id, reversible))
        self.docs.blockSignals(False)
        self.note.setText(f"عدد المستندات: {self.docs.count()}" if self.docs.count() else "لا توجد مستندات لهذا العميل.")
        self._selected()

    def _selected(self) -> None:
        data = self.docs.currentData()
        self.reverse_btn.setEnabled(bool(data and data[2]))

    def reverse_selected(self) -> None:
        source, doc_id, _ = self.docs.currentData()
        dlg = (ReverseReceiptDialog(self.session, doc_id, self) if source == "receipt"
               else ReverseDepositDialog(self.session, doc_id, self))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.changed = True
            self._fill()
