"""Reports screen: read-only income statement for a chosen period."""
from PySide6.QtWidgets import QDialog

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import income_statement, ledger
from morv.ui.listpage import ListPage
from morv.ui.period_dialogs import PeriodLockDialog

_KIND_PREFIX = {"head": "", "line": "    ", "total": "", "grand": ""}


class _Rows(list):
    period = ("", "")


class ReportsPage(ListPage):
    title = "قائمة الدخل"
    add_label = "قفل الفترة"
    add_icon = "lock"
    search_placeholder = "بحث في البنود (F2)"
    empty_message = "لا توجد قيود في هذه الفترة"
    filters = list(income_statement.PERIODS)
    columns = [
        ("الحساب", lambda r: ltr(r[2])),
        ("البند", lambda r: _KIND_PREFIX[r[1]] + r[3]),
        ("المبلغ", lambda r: ltr(fmt(r[4])) if r[4] is not None else ""),
    ]

    def __init__(self, session):
        super().__init__(session)

    def can_edit(self) -> bool:
        return self.session.can("period.lock")

    def open_dialog(self, row_id) -> None:
        if self.can_edit() and PeriodLockDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.reload(self.current_id)

    def can_view(self, con=None) -> bool:
        return self.session.can("reports.view", con)

    def fetch(self, con, query, filter_key, offset):
        if offset or not self.can_view(con):
            return []
        start, end = income_statement.period_range(filter_key)
        rows = _Rows(income_statement.table_rows(income_statement.build(con, start, end)))
        rows.period = (start, end)
        text = (query or "").strip()
        if text:
            rows = _Rows(r for r in rows if r[1] != "line" or text in r[3] or text in r[2])
            rows.period = (start, end)
        return rows

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على التقارير"
        locked = ledger.locked_through(self.session.con)
        note = f"الفترة مقفلة حتى {ltr(locked)}" if locked else "لا توجد فترة مقفلة"
        return f"{note} · من القيود المرحّلة فقط · الزيادة النقدية قيد التحقيق لا تدخل النتيجة"

    def build_preview(self, row_id: int) -> None:
        row = next((r for r in self.model.rows if r[0] == row_id), None)
        if row is None:
            return
        self.heading(row[3], ltr(row[2]) if row[2] else "")
        if row[4] is not None:
            self.summary_section("المبلغ", "cash")
            self.summary_details([("القيمة", ltr(fmt(row[4])))])
