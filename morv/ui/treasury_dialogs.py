"""Treasury dialogs: record a manual movement and reverse a posted one."""
import uuid
from datetime import date

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QWidget

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import cash_closing, ledger, suppliers, treasury, treasury_view
from morv.infra.cash_closing import ClosingError
from morv.infra.treasury import NeedsConfirmation, TreasuryError
from morv.ui.modal import Modal
from morv.ui.widgets import ask, danger_button, form, ltr_edit, muted

# kind -> (label of the money account row, label of the counterpart row or None)
_ROWS = {
    "transfer": ("من حساب", "إلى حساب"),
    "expense": ("يُدفع من", "بند المصروف"),
    "partner_withdrawal": ("يُدفع من", None),
    "partner_deposit": ("يُستلم في", None),
    "partner_capital": ("يُستلم في", None),
    "supplier_payment": ("يُدفع من", None),
}


class MovementDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("حركة خزينة جديدة", parent, width=600)
        self.session, self.saved = session, None
        self._key = uuid.uuid4().hex     # one key per dialog: a double click or a retry cannot post twice
        con = session.con
        self._names = treasury_view.account_names(con, treasury_view.ACCOUNTS)
        self._balances = dict(treasury_view.balances(con))
        self._partners = treasury.list_partners(con, active_only=True)
        self._suppliers = suppliers.search(con, "")

        f = form()
        self.kind = QComboBox()
        for k in treasury.KINDS:
            self.kind.addItem(treasury.LABELS[k], k)
        self.date = ltr_edit("YYYY-MM-DD")
        self.date.setText(date.today().isoformat())
        self.money = QComboBox()
        for code in treasury_view.ACCOUNTS:
            self.money.addItem(f"{self._names[code]} ({fmt(self._balances[code])})", code)
        self.other = QComboBox()
        self.partner = QComboBox()
        for pid, name, _acc, _active, _bal in self._partners:
            self.partner.addItem(name, pid)
        self.supplier = QComboBox()
        for row in self._suppliers:
            self.supplier.addItem(row[1], row[0])
        self.supplier_info = muted("")
        self.partner_info = muted("")
        self.amount = ltr_edit("0.00")
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("سبب الحركة (إلزامي)")
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("رقم إيصال أو عقد (اختياري)")

        self._labels: dict[str, QLabel] = {}
        for key, text, widget in (
            ("kind", "نوع الحركة *", self.kind), ("date", "التاريخ *", self.date),
            ("money", "", self.money), ("other", "", self.other),
            ("partner", "الشريك *", self.partner), ("partner_info", "", self.partner_info),
            ("supplier", "المورد *", self.supplier), ("supplier_info", "", self.supplier_info),
            ("amount", "المبلغ *", self.amount), ("memo", "البيان *", self.memo),
            ("reference", "المرجع", self.reference),
        ):
            label = QLabel(text)
            self._labels[key] = label
            f.addRow(label, widget)
        self._widgets = {"other": self.other, "partner": self.partner, "partner_info": self.partner_info,
                         "supplier": self.supplier, "supplier_info": self.supplier_info}
        self.body.addLayout(f)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.supplier.currentIndexChanged.connect(self._party_info)
        self.partner.currentIndexChanged.connect(self._party_info)
        self._kind_changed()
        self.add_footer("تسجيل الحركة", self.save)
        self.amount.setFocus()

    def _show(self, key: str, on: bool) -> None:
        self._labels[key].setVisible(on)
        self._widgets[key].setVisible(on)

    def _kind_changed(self) -> None:
        kind = self.kind.currentData()
        money_label, other_label = _ROWS[kind]
        self._labels["money"].setText(money_label)
        self._labels["other"].setText(other_label or "")
        self.other.clear()
        if kind == "transfer":
            for code in treasury_view.ACCOUNTS:
                self.other.addItem(self._names[code], code)
        elif kind == "expense":
            for code, name in treasury.expense_accounts(self.session.con):
                self.other.addItem(name, code)
        self._show("other", other_label is not None)
        partner_kind = kind in treasury.PARTNER_KINDS
        self._show("partner", partner_kind)
        self._show("partner_info", partner_kind)
        self._show("supplier", kind == "supplier_payment")
        self._show("supplier_info", kind == "supplier_payment")
        self._party_info()

    def _party_info(self) -> None:
        con = self.session.con
        if self.supplier.currentData() is not None:
            owed = treasury.supplier_owed(con, self.supplier.currentData())
            self.supplier_info.setText(f"المستحق للمورد الآن: {ltr(fmt(owed))}" if owed >= 0
                                       else f"دفعة مقدمة للمورد: {ltr(fmt(-owed))}")
        if self.partner.currentData() is not None:
            bal = treasury.partner_balance(con, self.partner.currentData())
            self.partner_info.setText(f"رصيد جاري الشريك: {ltr(fmt(bal))}" + ("" if bal >= 0 else " (مدين للشركة)"))

    def save(self) -> None:
        kind = self.kind.currentData()
        if kind in treasury.PARTNER_KINDS and self.partner.currentData() is None:
            self.notice.show_message("أضف المالك أو الشركاء أولًا من الإعدادات")
            return
        try:
            amount = parse_money(self.amount.text())
        except MoneyError as e:
            self.notice.show_message(f"المبلغ: {e}")
            return
        args = dict(request_key=self._key, kind=kind, amount=amount, memo=self.memo.text(),
                    money_account=self.money.currentData(), other_account=self.other.currentData(),
                    partner_id=self.partner.currentData(), supplier_id=self.supplier.currentData(),
                    movement_date=self.date.text().strip(), reference=self.reference.text())
        try:
            try:
                self.saved = treasury.post_movement(self.session.con, self.session.user, **args)
            except NeedsConfirmation as e:
                if not ask(self, "تأكيد الحركة", "\n".join(f"• {w}" for w in e.warnings) + "\n\nهل تريد المتابعة؟",
                           danger=True):
                    return
                self.saved = treasury.post_movement(self.session.con, self.session.user, confirmed=True, **args)
        except (TreasuryError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ReverseDialog(Modal):
    def __init__(self, session, movement_id: int, parent=None):
        super().__init__("عكس حركة خزينة", parent, width=520)
        self.session, self.movement_id, self.saved = session, movement_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("تُسجَّل حركة عكسية بتاريخ اليوم وتبقى الحركة الأصلية كما هي في السجل."))
        f = form()
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow("سبب العكس *", self.memo)
        self.body.addLayout(f)
        self.add_footer("عكس الحركة", self.save)
        self.memo.setFocus()

    def save(self) -> None:
        try:
            self.saved = treasury.reverse_movement(self.session.con, self.session.user, request_key=self._key,
                                                   movement_id=self.movement_id, memo=self.memo.text())
        except (TreasuryError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class CloseDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("إقفال الصندوق اليومي", parent, width=520)
        self.session, self.saved = session, None
        self._key = uuid.uuid4().hex
        con = session.con
        _, self._book = cash_closing.book_snapshot(con)
        last = cash_closing.latest_closing(con)
        self._last = last
        self.body.addWidget(muted("عُدّ النقد في الدُّرج وأدخل المجموع. الفرق يحتاج سببًا، والبيع بعد الإقفال يدخل الدورة التالية."))
        self.body.addWidget(muted("آخر إقفال: " + (f"{ltr(last[1])} · {ltr(last[2])} · {ltr(last[3])}" if last else "لا يوجد")))
        f = form()
        self.date = ltr_edit("YYYY-MM-DD")
        self.date.setText(date.today().isoformat())
        self.book_label = QLabel(ltr(fmt(self._book)))
        self.counted = ltr_edit("0.00")
        self.diff_label = QLabel("—")
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("سبب الفرق (إلزامي إن وُجد فرق)")
        f.addRow("تاريخ الإقفال", self.date)
        f.addRow("رصيد الصندوق الدفتري", self.book_label)
        f.addRow("العد الفعلي *", self.counted)
        f.addRow("الفرق", self.diff_label)
        f.addRow("سبب الفرق", self.memo)
        self.body.addLayout(f)
        self.hand_check = QCheckBox("تسليم الفائض بعد الإقفال")
        self.body.addWidget(self.hand_check)
        hf = form()
        self.hand_to = QComboBox()
        names = treasury_view.account_names(con, treasury_view.ACCOUNTS)
        for code in treasury_view.ACCOUNTS[1:]:
            self.hand_to.addItem(f"تحويل إلى: {names[code]}", ("bank", code))
        for pid, name, _acc, _active, _bal in treasury.list_partners(con, active_only=True):
            self.hand_to.addItem(f"سحب شريك: {name}", ("partner", pid))
        self.keep = ltr_edit("0.00")
        self.keep.setText("0.00")
        hf.addRow("الوجهة", self.hand_to)
        hf.addRow("يبقى في الدُّرج", self.keep)
        self.hand_box = QWidget()
        self.hand_box.setLayout(hf)
        self.hand_box.setVisible(False)
        self.body.addWidget(self.hand_box)
        self.hand_check.toggled.connect(self.hand_box.setVisible)
        self.counted.textChanged.connect(self._diff)
        self.cancel_btn = danger_button("إلغاء آخر إقفال")
        self.cancel_btn.setVisible(last is not None)
        self.cancel_btn.clicked.connect(self.cancel_last)
        self.body.addWidget(self.cancel_btn)
        self.add_footer("إقفال", self.save)
        self.counted.setFocus()

    def _diff(self) -> None:
        try:
            diff = parse_money(self.counted.text()) - self._book
        except MoneyError:
            self.diff_label.setText("—")
            return
        self.diff_label.setText(ltr(fmt(diff)) + (" (عجز)" if diff < 0 else " (زيادة)" if diff > 0 else " (مطابق)"))

    def save(self) -> None:
        try:
            counted = parse_money(self.counted.text())
        except MoneyError as e:
            self.notice.show_message(f"العد الفعلي: {e}")
            return
        diff = counted - self._book
        if diff and not ask(self, "تأكيد الفرق", f"الفرق {fmt(diff)} سيُرحَّل على حساب "
                            + ("عجز الصندوق" if diff < 0 else "زيادة الصندوق قيد التحقيق") + ". المتابعة؟", danger=True):
            return
        try:
            handover = self._handover(counted)
        except MoneyError as e:
            self.notice.show_message(f"المبلغ المتروك: {e}")
            return
        args = dict(request_key=self._key, closing_date=self.date.text().strip(), counted=counted,
                    expected_book=self._book, memo=self.memo.text())
        try:
            if handover is None:
                self.saved = cash_closing.close_day(self.session.con, self.session.user, **args)
            else:
                self.saved = self._close_and_hand_over(args, handover)
        except (ClosingError, TreasuryError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        if self.saved is not None:
            self.accept()

    def _handover(self, counted: int):
        """(amount, destination) or None when no handover was asked for or nothing is left to hand over."""
        if not self.hand_check.isChecked():
            return None
        amount = counted - parse_money(self.keep.text())
        return (amount, self.hand_to.currentData()) if amount > 0 else None

    def _close_and_hand_over(self, args: dict, handover: tuple):
        amount, (kind, target) = handover
        extra = dict(handover=amount, partner_id=target if kind == "partner" else None,
                     bank_account=target if kind == "bank" else None)
        try:
            return treasury.close_and_hand_over(self.session.con, self.session.user, **args, **extra)
        except NeedsConfirmation as e:
            if not ask(self, "تأكيد التسليم", "\n".join(f"• {w}" for w in e.warnings) + "\n\nهل تريد المتابعة؟",
                       danger=True):
                return None
            return treasury.close_and_hand_over(self.session.con, self.session.user, confirmed=True, **args, **extra)

    def cancel_last(self) -> None:
        if self._last is None:
            return
        dlg = CancelClosingDialog(self.session, self._last[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.saved = dlg.saved
            self.accept()


class CancelClosingDialog(Modal):
    def __init__(self, session, closing_id: int, parent=None):
        super().__init__("إلغاء إقفال", parent, width=480)
        self.session, self.closing_id, self.saved = session, closing_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل مستند إلغاء ويُعكس قيد الفرق إن وُجد، ثم يمكن إقفال التاريخ من جديد."))
        f = form()
        self.memo = QLineEdit()
        f.addRow("سبب الإلغاء *", self.memo)
        self.body.addLayout(f)
        self.add_footer("إلغاء الإقفال", self.save)

    def save(self) -> None:
        try:
            self.saved = cash_closing.cancel_closing(self.session.con, self.session.user, request_key=self._key,
                                                     closing_id=self.closing_id, memo=self.memo.text())
        except (ClosingError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()
