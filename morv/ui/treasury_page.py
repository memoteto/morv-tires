"""Treasury screen: cash, bank and wallet balances, their ledger movements, and manual movements."""
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import cash_closing, safe_count, treasury, treasury_view
from morv.ui.listpage import ListPage
from morv.ui.treasury_dialogs import CloseDialog, MovementDialog, ReverseDialog, SafeCountDialog
from morv.ui.widgets import danger_button, muted, primary_button

ACCOUNT_NAMES = {"1110": "الصندوق", "1130": "الخزنة", "1210": "بنك التحويلات", "1220": "بنك البطاقات", "1230": "المحافظ الإلكترونية"}
SOURCE_LABELS = {"sale": "فاتورة بيع", "sale_return": "مرتجع بيع", "purchase_invoice": "فاتورة شراء",
                 "installment_payment": "تحصيل قسط", "installment_contract": "عقد تقسيط",
                 "treasury_movement": "حركة خزينة", "cash_closing": "فرق إقفال الصندوق", "safe_count": "فرق جرد الخزنة"}


class _RowsWithBalances(list):
    """Rows plus the account balances read in the same snapshot."""
    balances = None
    closing = None
    unclosed = 0
    safe = None


class TreasuryPage(ListPage):
    title = "الخزينة وحركة الأموال"
    add_label = "حركة جديدة"
    search_placeholder = "البيان أو التاريخ (F2)"
    empty_message = "لا توجد حركات مطابقة"
    filters_as_dropdown = True
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
        self.close_btn = None
        self.safe_btn = None
        self.close_note = None
        super().__init__(session)

    def can_edit(self) -> bool:
        return self.session.can("treasury.manage")

    def open_dialog(self, row_id) -> None:
        if not self.can_edit():
            return
        if MovementDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.reload(self.current_id)

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
        if self.session.can("treasury.close"):
            self.close_btn = primary_button("إقفال الصندوق")
            self.close_btn.clicked.connect(self.close_day)
            grid.addWidget(self.close_btn, 1, 0)
            self.safe_btn = primary_button("جرد الخزنة")
            self.safe_btn.clicked.connect(self.count_safe)
            grid.addWidget(self.safe_btn, 2, 0)
            self.close_note = muted("")
            grid.addWidget(self.close_note, 1, 1, 2, len(tiles) - 1)
        return grid_host

    def count_safe(self) -> None:
        if SafeCountDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.reload(self.current_id)

    def close_day(self) -> None:
        if CloseDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.reload(self.current_id)

    def fetch(self, con, query, filter_key, offset):
        if not self.can_view(con):
            return []
        rows = _RowsWithBalances(treasury_view.movements(con, filter_key, query, offset=offset))
        rows.balances = treasury_view.balances(con)
        rows.closing = cash_closing.latest_closing(con)
        rows.unclosed = cash_closing.unclosed_days(con)
        rows.safe = safe_count.latest_count(con)
        return rows

    def on_loaded(self, rows: list) -> None:
        balances = getattr(rows, "balances", None)
        if balances is None:
            return
        for code, amount in balances:
            self._balance_values[code].setText(ltr(fmt(amount)))
        self._balance_values["total"].setText(ltr(fmt(sum(amount for _, amount in balances))))
        if self.close_note is not None:
            last = rows.closing
            text = f"آخر إقفال {ltr(last[2])} · {ltr(last[3])}" if last else "لم يُقفل الصندوق بعد"
            text += f" · {rows.unclosed} يوم بلا إقفال" if rows.unclosed else ""
            safe = f"آخر جرد للخزنة {ltr(rows.safe[2])}" if rows.safe else "لم تُجرد الخزنة بعد"
            self.close_note.setText(text + "\n" + safe)

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
        info = treasury.movement_info(self.session.con, entry.source_id) if entry.source_type == "treasury_movement" else None
        if info:
            details = [("رقم الحركة", ltr(info[0])), ("نوع الحركة", treasury.LABELS[info[1]])] + details
            if info[3]:
                details.append(("الطرف", info[3]))
            if info[2]:
                details.append(("المرجع", info[2]))
            if info[4]:
                details.append(("معكوسة بالحركة", ltr(info[4])))
            if info[5]:
                details.append(("تعكس الحركة", ltr(info[5])))
        self.summary_section("بيانات القيد", "calendar")
        self.summary_details(details)
        self.summary_section("سطور القيد", "cash")
        self.summary_table(
            ["الحساب", "مدين", "دائن"],
            [(f"{ltr(code)} {name}", ltr(fmt(debit)) if debit else "—", ltr(fmt(credit)) if credit else "—")
             for code, name, debit, credit in entry.lines])
        if info and info[1] != "reversal" and info[4] is None and self.can_edit():
            reverse = danger_button("عكس الحركة")
            reverse.clicked.connect(lambda: self._reverse(entry.source_id, row_id))
            self.actions(reverse)

    def _reverse(self, movement_id: int, row_id: int) -> None:
        if ReverseDialog(self.session, movement_id, self).exec() == QDialog.DialogCode.Accepted:
            self.reload(row_id)
