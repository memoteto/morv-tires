"""Treasury dialogs: record a manual movement and reverse a posted one."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import ledger, suppliers, treasury, treasury_view
from morv.infra.treasury import NeedsConfirmation, TreasuryError
from morv.ui.modal import Modal
from morv.ui.widgets import ask, form, ltr_edit, muted

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
