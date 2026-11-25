"""Settings partner table, adaptive details, and existing profit controls."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QLineEdit, QPushButton,
                               QSizePolicy, QTableView, QVBoxLayout, QWidget)

from morv.core import profit_rules
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import profit, treasury
from morv.ui.adaptive_layout import AdaptiveFields, AdaptiveSplit, AdaptiveTableColumns, scroll_content
from morv.ui.listpage import SummaryDetailRow, TupleModel
from morv.ui.modal import Modal
from morv.ui.profit_dialogs import ShareSetDialog
from morv.ui.tokens import LAYOUT
from morv.ui.widgets import Notice, action_row, card, danger_button, field, ltr_edit, muted, primary_button


class PartnersTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.partners: list[tuple] = []
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(LAYOUT["pane_gap"])
        self.new_btn = primary_button("شريك جديد", "plus")
        self.new_btn.clicked.connect(self.new_partner)
        self.details_btn = QPushButton("التفاصيل والإعدادات")
        lay.addLayout(action_row(self.new_btn, self.details_btn))

        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.model = TupleModel([
            ("الشريك", lambda p: p[1]),
            ("الحساب", lambda p: ltr(p[2])),
            ("رصيد الجاري", lambda p: ltr(fmt(p[4]))),
            ("الحالة", lambda p: "نشط" if p[3] else "موقوف"),
        ])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
        self.columns = AdaptiveTableColumns(self.table, {0: 4, 1: 2, 2: 3, 3: 2}, {})
        table_layout.addWidget(self.table, 1)
        self.empty = muted("لا يوجد شركاء")
        table_layout.addWidget(self.empty)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, LAYOUT["preview_scroll_gutter"], 0)
        detail_layout.setSpacing(LAYOUT["pane_gap"])
        self.back_btn = QPushButton("رجوع إلى الشركاء")
        detail_layout.addLayout(action_row(self.back_btn))
        content = QWidget()
        cards = QVBoxLayout(content)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(LAYOUT["pane_gap"])
        identity, inner = card("تفاصيل الشريك", compact=True)
        self.identity_card = identity
        self.name_row, self.account_row, self.balance_row, self.status_row = (
            self._detail_row(inner, label) for label in
            ("اسم الشريك", "الحساب الجاري", "رصيد الجاري", "الحالة")
        )
        self.name_label = self.name_row.value_label
        self.account_label = self.account_row.value_label
        self.balance_label = self.balance_row.value_label
        self.status_label = self.status_row.value_label
        self.active_btn = danger_button("إيقاف الشريك")
        self.active_btn.clicked.connect(self.toggle_active)
        inner.addLayout(action_row(self.active_btn))
        detail_layout.addWidget(identity)
        self.shares_btn = None
        self.share_row = None
        self.reserve_edit = None
        if session.can("profit.manage"):
            shares, inner = card("حصص الأرباح", compact=True)
            self.share_row = self._detail_row(inner, "الحصة السارية")
            self.shares_btn = primary_button("اعتماد الحصص")
            self.shares_btn.clicked.connect(self.edit_shares)
            inner.addLayout(action_row(self.shares_btn))
            cards.addWidget(shares)
            reserve, inner = card("الاحتياطي القانوني", compact=True)
            self.reserve_edit = ltr_edit("0")
            self.reserve_edit.setMaximumWidth(120)
            self.reserve_edit.setText(profit_rules.fmt_percent(profit.reserve_bp(session.con)) or "0")
            save = primary_button("حفظ النسبة", "save")
            save.clicked.connect(self.save_reserve)
            save.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            controls = AdaptiveFields([self.reserve_edit, save], minimum_width=0)
            inner.addWidget(field("نسبة الاحتياطي من ربح الفترة %", controls))
            cards.addWidget(reserve)
        cards.addStretch(1)
        self.detail_scroll = scroll_content(content)
        detail_layout.addWidget(self.detail_scroll, 1)
        self.detail_scroll.setVisible(self.share_row is not None)
        if self.share_row is None:
            detail_layout.addStretch(1)
        self.split = AdaptiveSplit(table_panel, detail, detail_max=LAYOUT["settings_partner_preview_max"],
                                   detail_first=True)
        lay.addWidget(self.split, 1)
        self.split.mode_changed.connect(self._sync_mode)
        self.details_btn.clicked.connect(self.split.show_detail)
        self.back_btn.clicked.connect(self.split.show_primary)
        self.table.selectionModel().currentRowChanged.connect(self._selection_changed)
        self.table.clicked.connect(lambda _index: self.split.show_detail())
        self.notice = Notice()
        lay.addWidget(self.notice)
        self.reload()
        self._sync_mode(self.split.compact)

    @staticmethod
    def _detail_row(layout, title):
        row = SummaryDetailRow(title, "")
        row.value_label.setTextFormat(Qt.TextFormat.PlainText)
        row.value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
                                     | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(row)
        return row

    def _sync_mode(self, compact):
        self.details_btn.setVisible(compact)
        self.back_btn.setVisible(compact)

    def _selection_changed(self, current, _previous):
        if not self._loading:
            self._update_detail()
            if current.isValid():
                self.split.show_detail()

    def _update_detail(self):
        cur = self._current()
        self.name_row.set_value(cur[1] if cur else "—")
        self.account_row.set_value(ltr(cur[2]) if cur else "—")
        self.balance_row.set_value(ltr(fmt(cur[4])) if cur else "—")
        self.status_row.set_value(("نشط" if cur[3] else "موقوف") if cur else "—")
        if self.share_row is not None:
            effective = (profit.shares_effective(self.session.con, date.today().isoformat())
                         if cur and self.session.can("profit.manage") else None)
            bp = next((bp for pid, _, bp in effective[2] if pid == cur[0]), None) if effective else None
            self.share_row.set_value(ltr(profit_rules.fmt_percent(bp) + "%") if bp is not None
                                     else "غير معتمدة" if cur else "—")
        active = cur is None or cur[3]
        self.active_btn.setText("إيقاف الشريك" if active else "تفعيل الشريك")
        self.active_btn.setObjectName("danger" if active else "success")
        self.active_btn.style().unpolish(self.active_btn)
        self.active_btn.style().polish(self.active_btn)
        self.active_btn.setEnabled(cur is not None)

    def reload(self, select_id: int | None = None) -> None:
        cur = self._current()
        if select_id is None and cur:
            select_id = cur[0]
        self._loading = True
        try:
            self.partners = treasury.list_partners(self.session.con)
            self.model.reset_rows(self.partners)
            if self.partners:
                idx = next((i for i, p in enumerate(self.partners) if p[0] == select_id), 0)
                self.table.setCurrentIndex(self.model.index(idx, 0))
            self.empty.setVisible(not self.partners)
        finally:
            self._loading = False
        self._update_detail()
        self.columns.schedule()

    def _current(self):
        row = self.table.currentIndex().row()
        return self.partners[row] if 0 <= row < len(self.partners) else None

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
        self.name = QLineEdit()
        self.body.addWidget(field("اسم الشريك *", self.name))
        self.add_footer("حفظ", self.save)

    def save(self) -> None:
        try:
            self.created = treasury.add_partner(self.session.con, self.session.user, self.name.text())
        except treasury.TreasuryError as e:
            self.notice.show_message(str(e))
            return
        self.accept()
