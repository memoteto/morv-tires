"""Customer receipt dialogs: collect a credit debt and reverse a posted receipt."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QLineEdit

from morv.core.money import MoneyError, fmt, parse_money
from morv.infra import customer_receipts, ledger
from morv.infra.customer_receipts import ReceiptError
from morv.ui.modal import Modal
from morv.ui.widgets import DateEdit, field, form, ltr_edit, muted

METHOD_LABELS = {"cash": "نقدًا", "card": "بطاقة", "transfer": "تحويل", "wallet": "محفظة إلكترونية"}


class ReceiptDialog(Modal):
    def __init__(self, session, customer_id: int, customer_name: str, parent=None):
        super().__init__("سند قبض", parent, width=520)
        self.session, self.customer_id, self.saved = session, customer_id, None
        self._key = uuid.uuid4().hex     # one key per dialog: a double click or a retry cannot post twice
        owed = customer_receipts.customer_balance(session.con, customer_id)
        self.body.addWidget(muted(f"العميل: {customer_name} · الدين الآجل المتبقي: {fmt(owed)}"))
        f = form()
        self.method = QComboBox()
        for key in customer_receipts.METHODS:
            self.method.addItem(METHOD_LABELS[key], key)
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        self.amount = ltr_edit("0.00")
        if owed > 0:
            self.amount.setText(fmt(owed).replace(",", ""))
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("ملاحظات (اختياري)")
        f.addRow(field("طريقة القبض *", self.method))
        f.addRow(field("التاريخ *", self.date))
        f.addRow(field("المبلغ *", self.amount))
        f.addRow(field("ملاحظات", self.notes))
        self.body.addLayout(f)
        self.add_footer("تسجيل السند", self.save)
        self.amount.setFocus()

    def save(self) -> None:
        try:
            amount = parse_money(self.amount.text())
        except MoneyError as e:
            self.notice.show_message(f"المبلغ: {e}")
            return
        try:
            self.saved = customer_receipts.post_receipt(
                self.session.con, self.session.user, request_key=self._key, customer_id=self.customer_id,
                amount=amount, method=self.method.currentData(), receipt_date=self.date.text().strip(),
                notes=self.notes.text())
        except (ReceiptError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ReverseReceiptDialog(Modal):
    def __init__(self, session, receipt_id: int, parent=None):
        super().__init__("عكس سند قبض", parent, width=520)
        self.session, self.receipt_id, self.saved = session, receipt_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل سند عكسي بتاريخ اليوم ويبقى السند الأصلي كما هو في السجل."))
        f = form()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow(field("سبب العكس *", self.reason))
        self.body.addLayout(f)
        self.add_footer("عكس السند", self.save)
        self.reason.setFocus()

    def save(self) -> None:
        try:
            self.saved = customer_receipts.reverse_receipt(
                self.session.con, self.session.user, request_key=self._key, receipt_id=self.receipt_id,
                reason=self.reason.text())
        except (ReceiptError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()
