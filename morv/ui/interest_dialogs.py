"""Dialog that previews unrecognised installment interest and posts it at month end after confirmation."""
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import interest_review as review
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
