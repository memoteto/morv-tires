"""Monthly interest entry dialog: shows what a month still owes, then posts the one entry."""
from datetime import date

from PySide6.QtWidgets import QLabel

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import interest_recognition as rec
from morv.ui.modal import Modal
from morv.ui.widgets import form, ltr_edit, muted


def last_month(today: str | None = None) -> str:
    now = date.fromisoformat(today) if today else date.today()
    first = now.replace(day=1)
    return str(date.fromordinal(first.toordinal() - 1))[:7]


class InterestDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("اعتراف فوائد التقسيط", parent, width=560)
        self.session = session
        self.posted = None
        self._busy = False
        f = form()
        self.period = ltr_edit("YYYY-MM")
        self.period.setText(last_month())
        self.period.textChanged.connect(self.refresh)
        self.total = QLabel()
        self.rows = QLabel()
        f.addRow("الشهر *", self.period)
        f.addRow("فوائد لم يُعترف بها", self.total)
        f.addRow("عدد الأقساط", self.rows)
        self.body.addLayout(f)
        self.body.addWidget(muted("الفائدة موزّعة بالتساوي كما في جدول العقد الموقّع؛ القيد ينقلها من "
                                  "الفوائد المؤجلة إلى إيراد الفوائد."))
        self.post_btn = self.add_footer("ترحيل قيد الشهر", self.post)
        self.refresh()

    def refresh(self, *_args) -> None:
        try:
            rows = rec.pending(self.session.con, self.period.text().strip())
        except Exception:      # noqa: BLE001 - a half-typed month must not break the view
            rows = []
        self.total.setText(f"{ltr(fmt(sum(r[1] for r in rows)))} جنيه")
        self.rows.setText(ltr(str(len(rows))))
        self.post_btn.setEnabled(bool(rows))

    def post(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = rec.recognise(self.session.con, self.session.user, period=self.period.text().strip())
        except rec.RecognitionError as e:
            self.notice.show_message(str(e))
        except Exception as e:      # the engine is atomic, so nothing half-written remains
            self.notice.show_message(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}).")
        else:
            self.accept()
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
