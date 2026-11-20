"""Dialogs for locking and unlocking the financial period."""
import uuid
from datetime import date, timedelta

from PySide6.QtWidgets import QDialog, QLineEdit

from morv.core.text import ltr
from morv.infra import ledger, period_lock
from morv.infra.period_lock import LockError
from morv.ui.modal import Modal
from morv.ui.widgets import danger_button, form, ltr_edit, muted


class PeriodLockDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("قفل الفترة المالية", parent, width=520)
        self.session, self.saved = session, None
        self._key = uuid.uuid4().hex
        self._latest = latest = period_lock.latest_lock(session.con)
        self.body.addWidget(muted("بعد القفل لا يُرحَّل أي قيد بتاريخ داخل الفترة، وتصير قائمة الدخل لها نهائية."))
        self.body.addWidget(muted("الفترة مقفلة حتى: " + (ltr(latest[2]) if latest else "لا يوجد قفل")))
        first_of_month = date.today().replace(day=1)
        f = form()
        self.through = ltr_edit("YYYY-MM-DD")
        self.through.setText((first_of_month - timedelta(days=1)).isoformat())
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("ملاحظة (اختياري)")
        f.addRow("قفل حتى تاريخ *", self.through)
        f.addRow("ملاحظة", self.memo)
        self.body.addLayout(f)
        self.blockers = muted("")
        self.body.addWidget(self.blockers)
        self.through.editingFinished.connect(self._check)
        self.unlock_btn = danger_button("فتح آخر قفل")
        self.unlock_btn.setVisible(latest is not None)
        self.unlock_btn.clicked.connect(self.unlock_last)
        self.body.addWidget(self.unlock_btn)
        self.add_footer("قفل الفترة", self.save)
        self._check()

    def _check(self) -> None:
        try:
            day = ledger.valid_date(self.through.text().strip())
        except ledger.LedgerError:
            self.blockers.setText("")
            return
        self.blockers.setText("\n".join(period_lock.blockers(self.session.con, day)))

    def save(self) -> None:
        try:
            self.saved = period_lock.lock_period(self.session.con, self.session.user, request_key=self._key,
                                                 through_date=self.through.text().strip(), memo=self.memo.text())
        except (LockError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()

    def unlock_last(self) -> None:
        dlg = UnlockDialog(self.session, self._latest[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.saved = dlg.saved
            self.accept()


class UnlockDialog(Modal):
    def __init__(self, session, lock_id: int, parent=None):
        super().__init__("فتح الفترة المالية", parent, width=480)
        self.session, self.lock_id, self.saved = session, lock_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل مستند فتح بسببه، ويُفتح آخر قفل فقط؛ وما قبله يبقى مقفلًا."))
        f = form()
        self.memo = QLineEdit()
        f.addRow("سبب الفتح *", self.memo)
        self.body.addLayout(f)
        self.add_footer("فتح الفترة", self.save)

    def save(self) -> None:
        try:
            self.saved = period_lock.unlock_period(self.session.con, self.session.user, request_key=self._key,
                                                   lock_id=self.lock_id, memo=self.memo.text())
        except (LockError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()
