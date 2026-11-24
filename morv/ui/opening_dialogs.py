"""Opening balances screen: line builder with review and balance check, status tab, reversal."""
import uuid
from datetime import date

from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QLabel, QLineEdit, QPushButton,
                               QTableView, QVBoxLayout, QWidget)

from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import ledger, opening_balances
from morv.infra.opening_balances import OpeningError, OpeningLine
from morv.ui.adaptive_layout import AdaptiveCards, AdaptiveFields, AdaptiveTableColumns
from morv.ui.listpage import TupleModel
from morv.ui.modal import Modal
from morv.ui.tokens import LAYOUT
from morv.ui.widgets import (DateEdit, Notice, action_row, card, danger_button, field, ltr_edit, muted,
                             primary_button, section_card)

TYPES = [("account", "نقدية أو بنك أو أصل أو التزام"), ("customer", "رصيد عميل"), ("supplier", "رصيد مورد"),
         ("partner_capital", "رأس مال شريك"), ("partner_current", "جاري شريك (الموجب له، والسالب عليه)"),
         ("retained", "أرباح مرحّلة"), ("inventory", "مخزون (دفعة بتكلفتها وDOT)")]
SIDE_TEXT = {"asset": "أصل", "liability": "التزام", "equity": "حقوق ملكية"}


class OpeningBalancesDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("الأرصدة الافتتاحية", parent, width=700)
        self.session, self.saved, self.previewed = session, None, False
        self._key = uuid.uuid4().hex
        self.lines: list[OpeningLine] = []
        self._draft_rows = []
        self.body.addWidget(muted("الأصول − الالتزامات = حقوق الملكية"))
        self.kind = QComboBox()
        for key, label in TYPES:
            self.kind.addItem(label, key)
        self.target = QComboBox()
        self.amount = ltr_edit("0.00")
        self.qty = ltr_edit("الكمية للمخزون")
        self.unit_cost = ltr_edit("تكلفة الوحدة للمخزون")
        self.dot = ltr_edit("DOT مثل 2024")
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        self.note = QLineEdit()
        self.note.setPlaceholderText("ملاحظة (اختياري)")
        self.body.addWidget(AdaptiveFields([
            field("تاريخ بدء التشغيل *", self.date), field("ملاحظة", self.note),
        ]))
        self.target_field = field("الحساب أو الطرف", self.target)
        self.body.addWidget(AdaptiveFields([field("النوع *", self.kind), self.target_field]))
        self.amount_field = field("المبلغ", self.amount)
        self.body.addWidget(self.amount_field)
        self.stock_fields = AdaptiveFields([
            field("الكمية", self.qty), field("تكلفة الوحدة", self.unit_cost), field("DOT", self.dot),
        ])
        self.body.addWidget(self.stock_fields)
        self.line_model = TupleModel([
            ("النوع", lambda r: r[0]), ("الحساب أو الطرف", lambda r: r[1]),
            ("التفاصيل", lambda r: r[2]), ("المبلغ", lambda r: r[3]),
        ])
        self.line_table = QTableView()
        self.line_table.setObjectName("summaryTable")
        self.line_table.setModel(self.line_model)
        self.line_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.line_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.line_table.setAlternatingRowColors(True)
        self.line_table.setShowGrid(False)
        self.line_table.verticalHeader().hide()
        self.line_table.setFixedHeight(5 * LAYOUT["summary_table_row_height"])
        self.columns = AdaptiveTableColumns(self.line_table, {0: 3, 1: 4, 2: 4, 3: 3}, {})
        self.body.addWidget(self.line_table)
        self.summary = muted("لم تُضف أسطر بعد.")
        self.body.addWidget(self.summary)
        self.total_labels = [QLabel("—") for _ in range(4)]
        self.totals = AdaptiveFields([
            field(title, label) for title, label in zip(
                ("الأصول", "الالتزامات", "حقوق الملكية", "الفرق"), self.total_labels, strict=True)
        ])
        self.body.addWidget(self.totals)
        self.totals.hide()
        self.kind.currentIndexChanged.connect(self._fill_targets)
        add = QPushButton("إضافة السطر")
        add.clicked.connect(self.add_line)
        undo = QPushButton("حذف آخر سطر")
        undo.clicked.connect(self.remove_last)
        review = QPushButton("معاينة القيود")
        review.clicked.connect(self.review)
        for w in (add, undo, review):
            self.add_footer_widget(w)
        self.post_btn = self.add_footer("اعتماد الأرصدة", self.save)
        self.post_btn.setEnabled(False)
        self._fill_targets()

    def _fill_targets(self) -> None:
        kind = self.kind.currentData()
        self.target.clear()
        for key, label in opening_balances.choices(self.session.con, kind):
            self.target.addItem(label, key)
        self.target_field.setVisible(kind != "retained")
        self.amount_field.setVisible(kind != "inventory")
        self.stock_fields.setVisible(kind == "inventory")

    def _invalidate(self) -> None:
        self.previewed = False
        self.post_btn.setEnabled(False)
        self.totals.hide()
        self.line_model.reset_rows(self._draft_rows)
        self.columns.schedule()
        self.summary.setText(f"أسطر الأرصدة: {len(self.lines)}" if self.lines else "لم تُضف أسطر بعد.")

    def add_line(self) -> None:
        kind, key = self.kind.currentData(), self.target.currentData()
        if key is None and kind != "retained":
            self.notice.show_message("اختر الحساب أو الطرف")
            return
        try:
            if kind == "inventory":
                line = OpeningLine("inventory", item_id=key, qty=int(self.qty.text().strip()),
                                   unit_cost=parse_money(self.unit_cost.text()), dot=self.dot.text().strip() or None)
            else:
                amount = parse_money(self.amount.text())
                line = OpeningLine(kind, amount, account=key if kind == "account" else None,
                                   partner_id=key if kind.startswith("partner") else None,
                                   customer_id=key if kind == "customer" else None,
                                   supplier_id=key if kind == "supplier" else None)
        except (MoneyError, ValueError) as e:
            self.notice.show_message(str(e) or "الكمية عدد صحيح")
            return
        self.lines.append(line)
        amount = line.qty * line.unit_cost if kind == "inventory" else line.amount
        detail = (f"{line.qty} × {fmt(line.unit_cost)} · DOT {line.dot or '—'}"
                  if kind == "inventory" else "—")
        self._draft_rows.append((
            self.kind.currentText(), self.target.currentText() if key is not None else "أرباح مرحّلة",
            detail, ltr(fmt(amount)),
        ))
        self._invalidate()

    def remove_last(self) -> None:
        if self.lines:
            self.lines.pop()
            self._draft_rows.pop()
            self._invalidate()

    def review(self) -> None:
        if not self.lines:
            self.add_line()
        if not self.lines:
            return
        self._invalidate()
        try:
            view = opening_balances.preview(self.session.con, self.session.user, self.lines)
        except (OpeningError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.line_model.reset_rows([
            (SIDE_TEXT[p.side], row[1], row[2], ltr(fmt(p.line.amount)))
            for p, row in zip(view.planned, self._draft_rows, strict=True)
        ])
        self.columns.schedule()
        for label, amount in zip(self.total_labels,
                                 (view.assets, view.liabilities, view.equity, view.difference), strict=True):
            label.setText(ltr(fmt(amount)))
        self.totals.show()
        self.summary.setText("غير متوازن: راجع الفرق" if view.difference else "متوازن — جاهز للاعتماد")
        self.previewed = not view.difference
        self.post_btn.setEnabled(self.previewed)

    def save(self) -> None:
        if not self.previewed:
            self.notice.show_message("عاين القيود وتأكد من التوازن أولًا")
            return
        try:
            self.saved = opening_balances.post_opening(
                self.session.con, self.session.user, request_key=self._key, opening_date=self.date.text().strip(),
                lines=self.lines, note=self.note.text())
        except (OpeningError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ReverseOpeningDialog(Modal):
    def __init__(self, session, opening_id: int, parent=None):
        super().__init__("عكس الأرصدة الافتتاحية", parent, width=520)
        self.session, self.opening_id, self.saved = session, opening_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل مستند عكسي، ولا يُقبل إلا قبل بدء أي مستند آخر."))
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        self.body.addWidget(field("سبب العكس *", self.reason))
        self.add_footer("عكس الأرصدة", self.save)

    def save(self) -> None:
        try:
            self.saved = opening_balances.reverse_opening(
                self.session.con, self.session.user, request_key=self._key, opening_id=self.opening_id,
                reason=self.reason.text())
        except (OpeningError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class OpeningTab(QWidget):
    """Settings tab: whether an opening is live, and the buttons to enter or reverse it."""

    def __init__(self, session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(LAYOUT["pane_gap"])
        status_card, inner = card("حالة الأرصدة الافتتاحية")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("cardTitle")
        inner.addWidget(self.status)
        self.document_labels = [QLabel("—") for _ in range(3)]
        self.document_fields = AdaptiveFields([
            field(title, label) for title, label in zip(
                ("رقم المستند", "تاريخ بدء التشغيل", "عدد الأسطر"), self.document_labels, strict=True)
        ])
        inner.addWidget(self.document_fields)
        lay.addWidget(status_card)
        entry, body, _ = section_card("إدخال الأرصدة", "cash")
        body.addWidget(muted("تسجيل الأصول والالتزامات وحقوق الملكية عند بدء التشغيل."))
        self.enter_btn = primary_button("إدخال الأرصدة الافتتاحية", "plus")
        self.enter_btn.clicked.connect(self.enter)
        body.addStretch(1)
        body.addLayout(action_row(self.enter_btn))
        reverse, body, _ = section_card("عكس الأرصدة", "reports")
        body.addWidget(muted("يُسمح بالعكس قبل وجود مستندات أخرى فقط."))
        self.reverse_btn = danger_button("عكس الأرصدة الافتتاحية")
        self.reverse_btn.clicked.connect(self.reverse)
        body.addStretch(1)
        body.addLayout(action_row(self.reverse_btn))
        self.actions = AdaptiveCards([entry, reverse], minimum_width=LAYOUT["settings_card_min"])
        lay.addWidget(self.actions, 1)
        note, inner = card()
        inner.addWidget(muted("الأصول − الالتزامات = حقوق الملكية"))
        lay.addWidget(note)
        self.notice = Notice()
        lay.addWidget(self.notice)
        self.refresh()

    def refresh(self) -> None:
        live = opening_balances.current(self.session.con)
        self.live_id = live[0] if live else None
        self.status.setText("مستند ساري" if live else "لم تُدخل أرصدة افتتاحية بعد.")
        self.document_fields.setVisible(live is not None)
        for label, value in zip(self.document_labels, live[1:] if live else ("—", "—", "—"), strict=True):
            label.setText(ltr(str(value)))
        self.enter_btn.setEnabled(live is None)
        self.reverse_btn.setEnabled(live is not None)

    def enter(self) -> None:
        if OpeningBalancesDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def reverse(self) -> None:
        if self.live_id is not None and ReverseOpeningDialog(self.session, self.live_id, self).exec() \
                == QDialog.DialogCode.Accepted:
            self.refresh()
