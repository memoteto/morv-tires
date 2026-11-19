"""Treasury screen: cash, bank and wallet balances with their ledger movements. Read-only."""
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import treasury_view
from morv.ui.listpage import ListPage
from morv.ui.widgets import muted

ACCOUNT_NAMES = {"1110": "الصندوق", "1210": "بنك التحويلات", "1220": "بنك البطاقات", "1230": "المحافظ الإلكترونية"}
SOURCE_LABELS = {"sale": "فاتورة بيع", "sale_return": "مرتجع بيع", "purchase_invoice": "فاتورة شراء",
                 "installment_payment": "تحصيل قسط", "installment_contract": "عقد تقسيط"}


class _RowsWithBalances(list):
    """Rows plus the account balances read in the same snapshot."""
    balances = None


class TreasuryPage(ListPage):
    title = "الخزينة وحركة الأموال"
    search_placeholder = "البيان أو التاريخ (F2)"
    empty_message = "لا توجد حركات مطابقة"
    filters = [("all", "كل الحسابات")] + [(code, name) for code, name in ACCOUNT_NAMES.items()]
    columns = [
        ("التاريخ", lambda r: ltr(r[1])),
        ("الحساب", lambda r: ACCOUNT_NAMES.get(r[2], r[2])),
        ("البيان", lambda r: r[3] or "—"),
        ("النوع", lambda r: SOURCE_LABELS.get(r[4], r[4])),
        ("وارد", lambda r: ltr(fmt(r[5])) if r[5] else "—"),
        ("صادر", lambda r: ltr(fmt(r[6])) if r[6] else "—"),
    ]

    def __init__(self, session):
        self._balance_values: dict[str, QLabel] = {}
        super().__init__(session)
        self.add_btn.hide()

    def can_edit(self) -> bool:
        return False

    def can_view(self, con=None) -> bool:
        return self.session.can("treasury.view", con)

    def extra_header(self) -> QWidget:
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        tiles = [("total", "إجمالي الأموال")] + list(ACCOUNT_NAMES.items())
        for i, (key, title) in enumerate(tiles):
            box = QFrame()
            box.setObjectName("card")
            inner = QVBoxLayout(box)
            inner.setContentsMargins(14, 10, 14, 10)
            inner.setSpacing(4)
            inner.addWidget(muted(title))
            value = QLabel("0")
            value.setObjectName("cardTitle")
            inner.addWidget(value)
            self._balance_values[key] = value
            grid.addWidget(box, 0, i)
        return grid_host

    def fetch(self, con, query, filter_key, offset):
        if not self.can_view(con):
            return []
        rows = _RowsWithBalances(treasury_view.movements(con, filter_key, query, offset=offset))
        rows.balances = treasury_view.balances(con)
        return rows

    def on_loaded(self, rows: list) -> None:
        balances = getattr(rows, "balances", None)
        if balances is None:
            return
        for code, amount in balances:
            self._balance_values[code].setText(ltr(fmt(amount)))
        self._balance_values["total"].setText(ltr(fmt(sum(amount for _, amount in balances))))

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على الخزينة"
        return f"{treasury_view.count(self.session.con)} حركة · اضغط صفًا لمعاينة القيد كاملًا"

    def build_preview(self, row_id: int) -> None:
        entry = treasury_view.get_entry(self.session.con, row_id)
        self.heading(SOURCE_LABELS.get(entry.source_type, entry.source_type), entry.memo)
        details = [("تاريخ القيد", ltr(entry.entry_date)), ("وقت الترحيل", ltr(entry.posted_at))]
        if entry.user_name:
            details.append(("المستخدم", entry.user_name))
        self.summary_section("بيانات القيد", "calendar")
        self.summary_details(details)
        self.summary_section("سطور القيد", "cash")
        self.summary_table(
            ["الحساب", "مدين", "دائن"],
            [(f"{ltr(code)} {name}", ltr(fmt(debit)) if debit else "—", ltr(fmt(credit)) if credit else "—")
             for code, name, debit, credit in entry.lines])
