"""Dialogs that post unrecognised installment interest at month end and reverse a recognised month."""
import uuid

from PySide6.QtWidgets import QComboBox, QLineEdit

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import interest_review as review
from morv.infra import interest_reversal, ledger
from morv.infra.interest_reversal import ReversalError
from morv.ui.modal import Modal
from morv.ui.widgets import muted


class InterestRecognitionDialog(Modal):
    def __init__(self, session, parent=None, through: str | None = None):
        super().__init__("اعتراف بفوائد الأشهر المنتهية", parent, width=620)
        self.session, self.saved = session, None
        self.through = through or review.default_through()
        self.items = review.review(session.con, self.through)
        ready = [i for i in self.items if i.status == review.READY]
        blocked = [i for i in self.items if i.status == review.BLOCKED]
        self.body.addWidget(muted(f"أشهر منتهية حتى {ltr(self.through)}. يُرحَّل كل شهر بتاريخ نهايته."))
        self.body.addWidget(muted(f"جاهز للاعتراف: {len(ready)} شهر بإجمالي {ltr(fmt(sum(i.amount for i in ready)))}"))
        self.body.addWidget(muted(f"خارج نطاق الاعتراف الآلي: {len(blocked)} شهر"))
        for it in (ready + blocked)[:12]:
            amount = ltr(fmt(it.amount)) if it.amount is not None else "—"
            tail = "" if it.status == review.READY else f" — {it.reason}"
            self.body.addWidget(muted(f"{ltr(it.number)} · {ltr(it.period)} · {amount}{tail}"))
        self.body.addWidget(muted("تنبيه: سيتغيّر دخل الأشهر الماضية في قائمة الدخل. العقود غير الجاهزة لا تُرحَّل."))
        if ready:
            self.add_footer("اعتراف بالأشهر الجاهزة", self.save)

    def save(self) -> None:
        result = review.post_ready(self.session.con, self.session.user, self.items)
        self.saved = result
        if result.failed:
            self.notice.show_message(f"رُحِّل {result.posted} شهر؛ تعذّر: " + "؛ ".join(result.failed))
            return
        self.accept()


class ReverseInterestDialog(Modal):
    """Reverses the latest recognised month of a contract so it can be posted again."""

    def __init__(self, session, parent=None):
        super().__init__("عكس اعتراف بفائدة", parent, width=620)
        self.session, self.saved = session, None
        self._key = uuid.uuid4().hex     # one key per dialog: a double click or a retry cannot post twice
        self.body.addWidget(muted("يُعكس أحدث شهر معترف به لكل عقد؛ يُسجَّل قيد عكسي بتاريخ اليوم ثم يظهر الشهر جاهزًا للاعتراف من جديد."))
        self.choice = QComboBox()
        for rec_id, number, period, amount, entry_date in interest_reversal.reversible(session.con):
            self.choice.addItem(f"{number} · {period} · {fmt(amount)} · مرحّل بتاريخ {entry_date}", rec_id)
        self.body.addWidget(self.choice)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        self.body.addWidget(self.reason)
        if self.choice.count():
            self.add_footer("عكس الاعتراف", self.save)
        else:
            self.body.addWidget(muted("لا توجد أشهر معترف بها يمكن عكسها."))

    def save(self) -> None:
        try:
            self.saved = interest_reversal.reverse(
                self.session.con, self.session.user, request_key=self._key,
                recognition_id=self.choice.currentData(), reason=self.reason.text())
        except (ReversalError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()
