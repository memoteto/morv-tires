"""Opening balances screen: line builder with review and balance check, status tab, reversal."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QVBoxLayout, QWidget

from morv.core.money import MoneyError, fmt, parse_money
from morv.infra import ledger, opening_balances
from morv.infra.opening_balances import OpeningError, OpeningLine
from morv.ui.modal import Modal
from morv.ui.widgets import danger_button, DateEdit, field, form, ltr_edit, muted, Notice, primary_button

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
        self.body.addWidget(muted("تُدخل مرة واحدة بتاريخ بدء التشغيل. لا يُعتمد المستند إلا إذا تساوت الأصول ناقص "
                                  "الالتزامات مع حقوق الملكية، وبعد معاينة القيود."))
        f = form()
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
        f.addRow(field("النوع *", self.kind))
        f.addRow(field("الحساب أو الطرف", self.target))
        f.addRow(field("المبلغ", self.amount))
        f.addRow(field("الكمية (مخزون)", self.qty))
        f.addRow(field("تكلفة الوحدة (مخزون)", self.unit_cost))
        f.addRow(field("DOT (مخزون)", self.dot))
        f.addRow(field("تاريخ بدء التشغيل *", self.date))
        f.addRow(field("ملاحظة", self.note))
        self.body.addLayout(f)
        self.summary = muted("لم تُضف أسطر بعد.")
        self.body.addWidget(self.summary)
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
        self.target.clear()
        for key, label in opening_balances.choices(self.session.con, self.kind.currentData()):
            self.target.addItem(label, key)

    def _invalidate(self) -> None:
        self.previewed = False
        self.post_btn.setEnabled(False)

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
        self.summary.setText(f"أسطر الأرصدة: {len(self.lines)}")
        self._invalidate()

    def remove_last(self) -> None:
        if self.lines:
            self.lines.pop()
            self.summary.setText(f"أسطر الأرصدة: {len(self.lines)}")
            self._invalidate()

    def review(self) -> None:
        if not self.lines:
            self.add_line()
        if not self.lines:
            return
        try:
            view = opening_balances.preview(self.session.con, self.session.user, self.lines)
        except (OpeningError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        rows = [f"{SIDE_TEXT[p.side]} · {p.label} · {fmt(p.line.amount)}" for p in view.planned]
        rows.append(f"الأصول {fmt(view.assets)} · الالتزامات {fmt(view.liabilities)} · حقوق الملكية {fmt(view.equity)}")
        if view.difference:
            rows.append(f"غير متوازن: الفرق {fmt(view.difference)} (الأصول ناقص الالتزامات ناقص حقوق الملكية)")
        else:
            rows.append("متوازن: كل سطر يُرحَّل بقيد مقابل حساب انتقالي 3500 يُصفَّر عند الاعتماد.")
        self.summary.setText("\n".join(rows))
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
        f = form()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow(field("سبب العكس *", self.reason))
        self.body.addLayout(f)
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
        self.status = muted("")
        lay.addWidget(self.status)
        self.enter_btn = primary_button("إدخال الأرصدة الافتتاحية")
        self.enter_btn.clicked.connect(self.enter)
        self.reverse_btn = danger_button("عكس الأرصدة الافتتاحية")
        self.reverse_btn.clicked.connect(self.reverse)
        lay.addWidget(self.enter_btn)
        lay.addWidget(self.reverse_btn)
        self.notice = Notice()
        lay.addWidget(self.notice)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        live = opening_balances.current(self.session.con)
        self.live_id = live[0] if live else None
        self.status.setText(f"مستند ساري: {live[1]} بتاريخ {live[2]} ({live[3]} سطر)" if live
                            else "لم تُدخل أرصدة افتتاحية بعد.")
        self.enter_btn.setEnabled(live is None)
        self.reverse_btn.setEnabled(live is not None)

    def enter(self) -> None:
        if OpeningBalancesDialog(self.session, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def reverse(self) -> None:
        if self.live_id is not None and ReverseOpeningDialog(self.session, self.live_id, self).exec() \
                == QDialog.DialogCode.Accepted:
            self.refresh()
