"""Dialogs for partner profit shares and the distribution of profit of locked periods."""
import uuid
from datetime import date

from PySide6.QtWidgets import QDialog, QLabel, QLineEdit

from morv.core import profit_rules
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import ledger, profit, treasury
from morv.infra.profit import ProfitError
from morv.ui.modal import Modal
from morv.ui.widgets import DateEdit, ask, danger_button, form, ltr_edit, muted


class ShareSetDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("حصص الشركاء في الأرباح", parent, width=520)
        self.session, self.saved = session, None
        self._key = uuid.uuid4().hex
        con = session.con
        self._partners = treasury.list_partners(con, active_only=True)
        current = profit.shares_effective(con, date.today().isoformat())
        known = {pid: bp for pid, _, bp in current[2]} if current else {}
        self.body.addWidget(muted("اعتمد نسب الشركاء النشطين؛ المجموع 100%. تسري من التاريخ المحدد على الفترات القادمة فقط."))
        f = form()
        self.edits: dict[int, QLineEdit] = {}
        for pid, name, _acc, _active, _bal in self._partners:
            edit = ltr_edit("0")
            default = known.get(pid) or (profit_rules.TOTAL_BP if len(self._partners) == 1 else 0)
            edit.setText(profit_rules.fmt_percent(default) if default else "")
            edit.textChanged.connect(self._sum)
            self.edits[pid] = edit
            f.addRow(f"{name} %", edit)
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("مرجع الاعتماد (محضر أو عقد)")
        self.total_label = QLabel("")
        f.addRow("المجموع", self.total_label)
        f.addRow("تاريخ السريان", self.date)
        f.addRow("مرجع الاعتماد *", self.memo)
        self.body.addLayout(f)
        self.add_footer("اعتماد الحصص", self.save)
        self._sum()

    def _read(self) -> list[tuple[int, int]]:
        out = []
        for pid, edit in self.edits.items():
            if edit.text().strip():
                out.append((pid, profit_rules.parse_percent(edit.text())))
        return [(p, b) for p, b in out if b]

    def _sum(self) -> None:
        try:
            total = sum(b for _, b in self._read())
        except profit_rules.ShareError:
            self.total_label.setText("—")
            return
        self.total_label.setText(ltr(profit_rules.fmt_percent(total) + "%"))

    def save(self) -> None:
        try:
            shares = self._read()
            self.saved = profit.set_shares(self.session.con, self.session.user, request_key=self._key,
                                           effective_from=self.date.text().strip(), shares=shares,
                                           memo=self.memo.text())
        except (ProfitError, profit_rules.ShareError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class DistributionDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("توزيع أرباح فترة مقفلة", parent, width=560)
        self.session, self.saved = session, None
        self._key = uuid.uuid4().hex
        self._preview = None
        con = session.con
        locked = ledger.locked_through(con)
        last = profit.latest_distribution(con)
        self.body.addWidget(muted("يُوزَّع الربح القابل للتوزيع فقط، بعد خسائر الفترات السابقة والتوزيعات والاحتياطي. القيد لا يمس النقد."))
        self.body.addWidget(muted("آخر توزيع حتى: " + (ltr(last[2]) if last else "لا يوجد")))
        f = form()
        self.through = DateEdit(optional=True)
        self.through.setText(locked or "")
        self.amount = ltr_edit("0.00")
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("بيان التوزيع (قرار الشركاء)")
        f.addRow("حتى نهاية", self.through)
        self.info = muted("")
        self.info.setWordWrap(True)
        f.addRow("الحساب", self.info)
        f.addRow("المبلغ الموزَّع *", self.amount)
        f.addRow("البيان *", self.memo)
        self.body.addLayout(f)
        self.through.textChanged.connect(self._refresh)
        self.reverse_btn = danger_button("عكس آخر توزيع")
        self.reverse_btn.setVisible(last is not None)
        self.reverse_btn.clicked.connect(self.reverse_last)
        self.body.addWidget(self.reverse_btn)
        self._last = last
        self.add_footer("توزيع", self.save)
        self._refresh()

    def _refresh(self) -> None:
        self._preview = None
        if not self.through.text().strip():      # nothing chosen yet is a prompt, not an error
            self.info.setText("اختر تاريخ نهاية فترة مقفلة لعرض الربح القابل للتوزيع.")
            return
        try:
            p = profit.preview(self.session.con, self.through.text().strip())
        except ProfitError as e:
            self.info.setText(str(e))
            return
        self._preview = p
        reserve = f"\nالاحتياطي {profit_rules.fmt_percent(p.reserve_bp)}%: {ltr(fmt(p.reserve_amount))}" if p.reserve_bp else ""
        shares = "، ".join(f"{name} {profit_rules.fmt_percent(bp)}%" for _, name, bp in p.shares)
        self.info.setText(f"الفترة من {ltr(p.from_date)} · نتيجة الفترة {ltr(fmt(p.period_net))}\n"
                          f"الحد الأقصى القابل للتوزيع {ltr(fmt(p.max_amount))}{reserve}\nالحصص: {shares}")
        self.amount.setText(fmt(p.max_amount).replace(",", ""))

    def save(self) -> None:
        if self._preview is None:
            self.notice.show_message("لا يمكن التوزيع قبل زوال المانع الظاهر في الحساب")
            return
        try:
            amount = parse_money(self.amount.text())
        except MoneyError as e:
            self.notice.show_message(f"المبلغ: {e}")
            return
        if not ask(self, "تأكيد التوزيع", f"سيُسجَّل توزيع {fmt(amount)} على الشركاء بنسبهم في حساباتهم الجارية. المتابعة؟",
                   danger=True):
            return
        try:
            self.saved = profit.distribute(self.session.con, self.session.user, request_key=self._key,
                                           through_date=self.through.text().strip(), amount=amount,
                                           memo=self.memo.text())
        except (ProfitError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()

    def reverse_last(self) -> None:
        dlg = ReverseDistributionDialog(self.session, self._last[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.saved = dlg.saved
            self.accept()


class ReverseDistributionDialog(Modal):
    def __init__(self, session, distribution_id: int, parent=None):
        super().__init__("عكس توزيع الأرباح", parent, width=480)
        self.session, self.distribution_id, self.saved = session, distribution_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل قيد عكسي بتاريخ اليوم، ويعود المبلغ قابلًا للتوزيع."))
        f = form()
        self.memo = QLineEdit()
        f.addRow("سبب العكس *", self.memo)
        self.body.addLayout(f)
        self.add_footer("عكس التوزيع", self.save)

    def save(self) -> None:
        try:
            self.saved = profit.reverse_distribution(self.session.con, self.session.user, request_key=self._key,
                                                     distribution_id=self.distribution_id, memo=self.memo.text())
        except (ProfitError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()
