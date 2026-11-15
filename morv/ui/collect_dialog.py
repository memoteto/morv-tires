"""Collection dialog: takes money against a contract and shows what the receipt will clear."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QLabel

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra.installment_collections import CollectionError, collect
from morv.ui.modal import Modal
from morv.ui.widgets import form, ltr_edit, muted

METHODS = (("cash", "نقدي"), ("card", "بطاقة / ماكينة POS"), ("transfer", "تحويل / محفظة"), ("wallet", "محفظة"))


class CollectDialog(Modal):
    def __init__(self, session, contract, due_row, remaining, parent=None):
        super().__init__(f"تحصيل قسط — {ltr(contract[0])}", parent, width=620)
        self.session = session
        self.contract_id = contract[1]
        self.remaining = remaining
        self.posted = None
        self._busy = False
        self._request_key = uuid.uuid4().hex

        f = form()
        self.amount = ltr_edit("0.00")
        self.amount.setText(f"{due_row[1] // 100}.{due_row[1] % 100:02d}")
        self.method = QComboBox()
        for key, label in METHODS:
            self.method.addItem(label, key)
        self.reference = ltr_edit("رقم الإيصال / المرجع (لغير النقدي)")
        self.collect_date = ltr_edit("YYYY-MM-DD")
        self.collect_date.setText(date.today().isoformat())
        self.notes = ltr_edit("ملاحظة (اختيارية)")
        f.addRow("العميل", QLabel(contract[2]))
        f.addRow("القسط", QLabel(f"{ltr(str(due_row[0]))} · استحقاق {ltr(due_row[2])}"))
        f.addRow("المتبقي على القسط", QLabel(f"{ltr(fmt(due_row[1]))} جنيه"))
        f.addRow("المتبقي على العقد", QLabel(f"{ltr(fmt(remaining))} جنيه"))
        f.addRow("المبلغ المحصَّل *", self.amount)
        f.addRow("طريقة التحصيل", self.method)
        f.addRow("رقم العملية / المرجع", self.reference)
        f.addRow("تاريخ التحصيل *", self.collect_date)
        f.addRow("ملاحظة", self.notes)
        self.body.addLayout(f)
        self.body.addWidget(muted("التحصيل يذهب لأقدم قسط، والتحصيل الزائد ممنوع."))
        self.post_btn = self.add_footer("اعتماد التحصيل", self.post)

    def post(self) -> None:
        if self._busy:
            return
        try:
            amount = parse_money(self.amount.text())
        except MoneyError as e:
            self.notice.show_message(f"المبلغ: {e}")
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = collect(
                self.session.con, self.session.user, request_key=self._request_key, contract_id=self.contract_id,
                amount=amount, method=self.method.currentData(),
                collect_date=self.collect_date.text().strip(), reference=self.reference.text(),
                notes=self.notes.text())
        except CollectionError as e:
            self.notice.show_message(f"لم يُعتمد التحصيل: {e}")
        except Exception as e:      # the engine is atomic, so nothing half-written remains
            self.notice.show_message(f"تعذّر التحصيل ولم يُحفظ شيء ({type(e).__name__}).")
        else:
            self.accept()
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
