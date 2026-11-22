"""Settings tab listing the shop partners with their balances, and adding or stopping partners."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from morv.core import profit_rules
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import profit, treasury
from morv.ui.modal import Modal
from morv.ui.profit_dialogs import ShareSetDialog
from morv.ui.widgets import action_row, danger_button, field, form, ltr_edit, muted, Notice, primary_button


class PartnersTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.partners: list[tuple] = []
        lay = QVBoxLayout(self)
        self.new_btn = primary_button("شريك جديد", "plus")
        self.new_btn.clicked.connect(self.new_partner)
        lay.addLayout(action_row(self.new_btn))
        self.list = QListWidget()
        self.list.setObjectName("userList")
        self.list.setSpacing(2)
        lay.addWidget(self.list, 1)
        self.active_btn = danger_button("إيقاف الشريك")
        self.active_btn.clicked.connect(self.toggle_active)
        self.shares_btn = None
        self.reserve_edit = None
        if session.can("profit.manage"):
            self.shares_btn = primary_button("حصص الأرباح")
            self.shares_btn.clicked.connect(self.edit_shares)
            lay.addLayout(action_row(self.shares_btn, self.active_btn))
            row = QHBoxLayout()
            self.reserve_edit = ltr_edit("0")
            self.reserve_edit.setMaximumWidth(120)
            self.reserve_edit.setText(profit_rules.fmt_percent(profit.reserve_bp(session.con)) or "0")
            save = primary_button("حفظ نسبة الاحتياطي")
            save.clicked.connect(self.save_reserve)
            row.addWidget(muted("نسبة الاحتياطي القانوني من ربح الفترة %"))
            row.addWidget(self.reserve_edit)
            row.addWidget(save)
            row.addStretch(1)
            lay.addLayout(row)
        else:
            lay.addLayout(action_row(self.active_btn))
        lay.addWidget(muted("للمحل الذي له مالك واحد: أضِفه كشريك واحد. الرصيد الموجب مستحق للشريك على المحل."))
        self.notice = Notice()
        lay.addWidget(self.notice)
        self.reload()

    def reload(self, select_id: int | None = None) -> None:
        self.partners = treasury.list_partners(self.session.con)
        self.list.clear()
        for pid, name, account, active, balance in self.partners:
            text = f"{name} — {ltr(account)} — {ltr(fmt(balance))}" + ("" if active else " (موقوف)")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            item.setSizeHint(QSize(0, 44))
            self.list.addItem(item)
        idx = next((i for i, p in enumerate(self.partners) if p[0] == select_id), 0)
        if self.partners:
            self.list.setCurrentRow(idx)
        self.active_btn.setEnabled(bool(self.partners))

    def edit_shares(self) -> None:
        if ShareSetDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.reload()
            self.notice.show_message("اعتُمدت الحصص", "success")

    def save_reserve(self) -> None:
        try:
            profit.set_reserve_bp(self.session.con, self.session.user, profit_rules.parse_percent(self.reserve_edit.text()))
        except (profit.ProfitError, profit_rules.ShareError) as e:
            self.notice.show_message(str(e))
            return
        self.notice.show_message("حُفظت نسبة الاحتياطي", "success")

    def _current(self):
        row = self.list.currentRow()
        return self.partners[row] if 0 <= row < len(self.partners) else None

    def new_partner(self) -> None:
        dlg = NewPartnerDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(dlg.created.id)

    def toggle_active(self) -> None:
        cur = self._current()
        if cur is None:
            return
        try:
            treasury.set_partner_active(self.session.con, self.session.user, cur[0], not cur[3])
        except treasury.TreasuryError as e:
            self.notice.show_message(str(e))
            return
        self.reload(cur[0])


class NewPartnerDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("شريك جديد", parent, width=420)
        self.session = session
        self.created = None
        f = form()
        self.name = QLineEdit()
        f.addRow(field("اسم الشريك *", self.name))
        self.body.addLayout(f)
        self.add_footer("حفظ", self.save)

    def save(self) -> None:
        try:
            self.created = treasury.add_partner(self.session.con, self.session.user, self.name.text())
        except treasury.TreasuryError as e:
            self.notice.show_message(str(e))
            return
        self.accept()
