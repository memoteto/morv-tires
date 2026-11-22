"""Stock adjustment dialogs: build lines, review the entry, approve; and a history with reversal."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton

from morv.core.money import MoneyError, fmt, parse_money
from morv.infra import ledger, stock_adjustments
from morv.infra.stock_adjustments import AdjustLine, StockAdjustError
from morv.ui.modal import Modal
from morv.ui.widgets import danger_button, DateEdit, field, form, ltr_edit, muted

SOURCE_TEXT = {"source_lot": "تكلفة الدفعة المصدر", "last_purchase": "آخر تكلفة شراء", "manual": "تكلفة يدوية",
               "fifo": "تكلفة FIFO"}


class StockAdjustmentDialog(Modal):
    def __init__(self, session, item_id: int | None = None, parent=None):
        super().__init__("تسوية جرد", parent, width=640)
        self.session, self.saved, self.previewed = session, None, False
        self._key = uuid.uuid4().hex     # one key per dialog: a double click or a retry cannot post twice
        self.lines: list[AdjustLine] = []
        self.names: dict[int, str] = {}
        self.body.addWidget(muted("العجز والتلف يُصرفان بتكلفة FIFO على «فروق الجرد»، والزيادة تُدخل دفعة جديدة "
                                  "وتُقيَّد على «زيادة الجرد». راجع القيد قبل الاعتماد."))
        f = form()
        self.item = QComboBox()
        for iid, code, name, on_hand in stock_adjustments.stocked_items(session.con):
            self.names[iid] = name
            self.item.addItem(f"{name} · {code} · الرصيد {on_hand}", iid)
        if item_id is not None and self.item.findData(item_id) >= 0:
            self.item.setCurrentIndex(self.item.findData(item_id))
        self.direction = QComboBox()
        self.direction.addItem("عجز أو تلف", "loss")
        self.direction.addItem("زيادة", "gain")
        self.qty = ltr_edit("1")
        self.qty.setText("1")
        self.dot = ltr_edit("DOT للزيادة مثل 2024")
        self.cost = ltr_edit("0.00")
        self.cost.setPlaceholderText("تكلفة الوحدة: تُطلب فقط إن لم يوجد مرجع تكلفة للصنف")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب التسوية (إلزامي)")
        self.date = DateEdit()
        self.date.setText(date.today().isoformat())
        f.addRow(field("الصنف *", self.item))
        f.addRow(field("النوع *", self.direction))
        f.addRow(field("الكمية *", self.qty))
        f.addRow(field("DOT (زيادة)", self.dot))
        f.addRow(field("التكلفة اليدوية (زيادة)", self.cost))
        f.addRow(field("السبب *", self.reason))
        f.addRow(field("التاريخ *", self.date))
        self.body.addLayout(f)
        self.summary = muted("لم تُضف أسطر بعد.")
        self.body.addWidget(self.summary)
        add = QPushButton("إضافة السطر")
        add.clicked.connect(self.add_line)
        review = QPushButton("معاينة القيد")
        review.clicked.connect(self.review)
        self.add_footer_widget(add)
        self.add_footer_widget(review)
        self.post_btn = self.add_footer("اعتماد التسوية", self.save)
        self.post_btn.setEnabled(False)
        if not self.item.count():
            self.notice.show_message("لا توجد أصناف مخزّنة.")

    def _invalidate(self) -> None:
        self.previewed = False
        self.post_btn.setEnabled(False)

    def add_line(self) -> None:
        iid = self.item.currentData()
        if iid is None:
            return
        try:
            qty = int(self.qty.text().strip())
        except ValueError:
            self.notice.show_message("الكمية عدد صحيح")
            return
        manual = None
        if self.cost.text().strip():
            try:
                manual = parse_money(self.cost.text())
            except MoneyError as e:
                self.notice.show_message(str(e))
                return
        if any(x.item_id == iid for x in self.lines):
            self.notice.show_message("الصنف مضاف سابقًا؛ سطر واحد لكل صنف في التسوية الواحدة")
            return
        kind = self.direction.currentData()
        self.lines.append(AdjustLine(iid, kind, qty, dot=self.dot.text().strip() or None if kind == "gain" else None,
                                     manual_unit_cost=manual if kind == "gain" else None))
        self.summary.setText(f"أسطر التسوية: {len(self.lines)}")
        self._invalidate()

    def review(self) -> None:
        if not self.lines:
            self.add_line()
        if not self.lines:
            return
        try:
            priced = stock_adjustments.preview(self.session.con, self.session.user, self.lines)
        except (StockAdjustError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        gain = sum(p.value for p in priced if p.line.direction == "gain")
        loss = sum(p.value for p in priced if p.line.direction == "loss")
        rows = [f"{'زيادة' if p.line.direction == 'gain' else 'عجز'} · {self.names[p.line.item_id]} × {p.line.qty}"
                f" = {fmt(p.value)} ({SOURCE_TEXT[p.cost_source]})" for p in priced]
        if loss:
            rows.append(f"قيد العجز: مدين 5270 فروق الجرد {fmt(loss)} / دائن 1410 المخزون {fmt(loss)}")
        if gain:
            rows.append(f"قيد الزيادة: مدين 1410 المخزون {fmt(gain)} / دائن 4220 زيادة الجرد {fmt(gain)}")
        self.summary.setText("\n".join(rows))
        self.previewed = True
        self.post_btn.setEnabled(True)

    def save(self) -> None:
        if not self.previewed:
            self.notice.show_message("عاين القيد أولًا ثم اعتمد")
            return
        try:
            self.saved = stock_adjustments.post_adjustment(
                self.session.con, self.session.user, request_key=self._key, adjust_date=self.date.text().strip(),
                reason=self.reason.text(), lines=self.lines)
        except (StockAdjustError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ReverseAdjustmentDialog(Modal):
    def __init__(self, session, adjustment_id: int, parent=None):
        super().__init__("عكس تسوية جرد", parent, width=520)
        self.session, self.adjustment_id, self.saved = session, adjustment_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يُسجَّل مستند عكسي بتاريخ اليوم؛ عكس الزيادة لا يُقبل إن بيعت وحداتها."))
        f = form()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow(field("سبب العكس *", self.reason))
        self.body.addLayout(f)
        self.add_footer("عكس التسوية", self.save)

    def save(self) -> None:
        try:
            self.saved = stock_adjustments.reverse_adjustment(
                self.session.con, self.session.user, request_key=self._key, adjustment_id=self.adjustment_id,
                reason=self.reason.text())
        except (StockAdjustError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class AdjustmentsHistoryDialog(Modal):
    """Adjustments newest first; the selected one can be reversed."""

    def __init__(self, session, parent=None):
        super().__init__("سجل تسويات الجرد", parent, width=700)
        self.session, self.changed = session, False
        self.docs = QComboBox()
        self.body.addWidget(self.docs)
        self.note = muted("")
        self.body.addWidget(self.note)
        self.reverse_btn = danger_button("عكس التسوية المحددة")
        self.reverse_btn.clicked.connect(self.reverse_selected)
        self.body.addWidget(self.reverse_btn)
        self.docs.currentIndexChanged.connect(self._selected)
        self.add_footer("إغلاق", self.accept)
        self._fill()

    def _fill(self) -> None:
        rows = stock_adjustments.history(self.session.con)
        self.docs.blockSignals(True)
        self.docs.clear()
        for aid, number, kind, day, gain, loss, reason, reversed_ in rows:
            label = "تسوية" if kind == "adjustment" else "عكس تسوية"
            reversible = kind == "adjustment" and not reversed_ and self.session.can("stock.adjust")
            self.docs.addItem(f"{label} {number} · {day} · زيادة {fmt(gain)} · عجز {fmt(loss)} · {reason}"
                              + ("" if reversible else " — لا يُعكس"), (aid, reversible))
        self.docs.blockSignals(False)
        self.note.setText(f"عدد المستندات: {self.docs.count()}" if self.docs.count() else "لا توجد تسويات.")
        self._selected()

    def _selected(self) -> None:
        data = self.docs.currentData()
        self.reverse_btn.setEnabled(bool(data and data[1]))

    def reverse_selected(self) -> None:
        dlg = ReverseAdjustmentDialog(self.session, self.docs.currentData()[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.changed = True
            self._fill()
