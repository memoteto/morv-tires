"""Bank reconciliation dialogs: record a statement balance and cancel the last record."""
import uuid
from datetime import date

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QLabel, QLineEdit

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import bank_reconciliation as br
from morv.infra import ledger, treasury_view
from morv.infra.bank_reconciliation import ReconciliationError
from morv.ui.modal import Modal
from morv.ui.widgets import DateEdit, danger_button, form, ltr_edit, muted


class ReconciliationDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("مطابقة البنك والمحافظ", parent, width=560)
        self.session, self.saved, self._last = session, None, None
        self._key = uuid.uuid4().hex
        self._names = treasury_view.account_names(session.con, br.ACCOUNTS)
        self.body.addWidget(muted("أدخل رصيد الكشف في تاريخ معيّن؛ يُحسب رصيد البرنامج حتى التاريخ نفسه ويُحفظ معه. "
                                  "المحضر لا يُنشئ أي قيد."))
        f = form()
        self.account = QComboBox()
        for code in br.ACCOUNTS:
            self.account.addItem(self._names[code], code)
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        self.program = QLabel("—")
        self.statement = ltr_edit("0.00")
        self.diff_label = QLabel("—")
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("سبب الفرق (إلزامي إن وُجد فرق)")
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("مرجع الكشف (اختياري)")
        f.addRow("الحساب *", self.account)
        f.addRow("تاريخ الرصيد *", self.date)
        f.addRow("رصيد البرنامج في التاريخ", self.program)
        f.addRow("رصيد الكشف *", self.statement)
        f.addRow("الفرق", self.diff_label)
        f.addRow("سبب الفرق", self.memo)
        f.addRow("مرجع الكشف", self.reference)
        self.body.addLayout(f)
        self.fee_box = QCheckBox("سجّل الفرق كمصروف بنكي (رسوم) بتاريخ الرصيد ثم احفظ محضرًا مطابقًا")
        self.fee_box.setVisible(False)
        self.body.addWidget(self.fee_box)
        self.last_note = muted("")
        self.body.addWidget(self.last_note)
        self.cancel_btn = danger_button("إلغاء آخر محضر لهذا الحساب")
        self.cancel_btn.clicked.connect(self.cancel_last)
        self.body.addWidget(self.cancel_btn)
        self.account.currentIndexChanged.connect(self._refresh)
        self.date.textChanged.connect(self._refresh)
        self.statement.textChanged.connect(self._diff)
        self.add_footer("حفظ المحضر", self.save)
        self._refresh()
        self.statement.setFocus()

    def _program(self):
        try:
            day = ledger.valid_date(self.date.text().strip(), "تاريخ الرصيد")
        except ledger.LedgerError:
            return None
        return br.program_balance(self.session.con, self.account.currentData(), day)

    def _refresh(self) -> None:
        con, code = self.session.con, self.account.currentData()
        book = self._program()
        self.program.setText(ltr(fmt(book)) if book is not None else "—")
        self._last = br.latest(con, code)
        if self._last:
            note = f"آخر محضر: {ltr(self._last[1])} · {ltr(self._last[2])} · الفرق {ltr(fmt(self._last[5]))}"
            if br.drifted(con, code):
                note += "\nتنبيه: رصيد البرنامج في تاريخ ذلك المحضر تغيّر بعد حفظه."
        else:
            note = "لا يوجد محضر مطابقة لهذا الحساب بعد."
        self.last_note.setText(note)
        self.cancel_btn.setVisible(self._last is not None)
        self._diff()

    def _diff(self) -> None:
        book = self._program()
        try:
            diff = parse_money(self.statement.text()) - book
        except (MoneyError, TypeError):
            self.diff_label.setText("—")
            return
        side = "الكشف أعلى من البرنامج" if diff > 0 else "الكشف أقل من البرنامج" if diff < 0 else "متطابق"
        self.fee_box.setVisible(diff < 0)
        if diff >= 0:
            self.fee_box.setChecked(False)
        self.diff_label.setText(f"{ltr(fmt(diff))} ({side})")

    def save(self) -> None:
        try:
            statement = parse_money(self.statement.text())
        except MoneyError as e:
            self.notice.show_message(f"رصيد الكشف: {e}")
            return
        book = self._program()
        if book is None:
            self.notice.show_message("تاريخ الرصيد غير صالح")
            return
        try:
            if self.fee_box.isChecked():
                self.saved = br.reconcile_fee(
                    self.session.con, self.session.user, request_key=self._key, account=self.account.currentData(),
                    statement_date=self.date.text().strip(), statement_balance=statement,
                    reference=self.reference.text())
            else:
                self.saved = br.reconcile(
                    self.session.con, self.session.user, request_key=self._key, account=self.account.currentData(),
                    statement_date=self.date.text().strip(), statement_balance=statement, expected_program=book,
                    reference=self.reference.text(), memo=self.memo.text())
        except (ReconciliationError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()

    def cancel_last(self) -> None:
        dlg = CancelReconciliationDialog(self.session, self._last[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.saved = dlg.saved
            self.accept()


class CancelReconciliationDialog(Modal):
    def __init__(self, session, reconciliation_id: int, parent=None):
        super().__init__("إلغاء محضر مطابقة", parent, width=480)
        self.session, self.reconciliation_id, self.saved = session, reconciliation_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل مستند إلغاء ويبقى المحضر الأصلي في السجل، ثم يمكن المطابقة من جديد."))
        f = form()
        self.memo = QLineEdit()
        f.addRow("سبب الإلغاء *", self.memo)
        self.body.addLayout(f)
        self.add_footer("إلغاء المحضر", self.save)

    def save(self) -> None:
        try:
            self.saved = br.cancel(self.session.con, self.session.user, request_key=self._key,
                                   reconciliation_id=self.reconciliation_id, memo=self.memo.text())
        except ReconciliationError as e:
            self.notice.show_message(str(e))
            return
        self.accept()
