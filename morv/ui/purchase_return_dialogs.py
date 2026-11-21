"""Supplier return dialogs: send lot stock back at invoice cost and reverse a posted return."""
import uuid
from datetime import date

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton

from morv.core.money import fmt
from morv.infra import ledger, purchase_returns
from morv.infra.purchase_returns import PurchaseReturnError, ReturnLine
from morv.ui.modal import Modal
from morv.ui.widgets import danger_button, form, ltr_edit, muted


class PurchaseReturnDialog(Modal):
    def __init__(self, session, supplier_id: int, supplier_name: str, parent=None):
        super().__init__("مرتجع للمورد", parent, width=560)
        self.session, self.supplier_id, self.saved = session, supplier_id, None
        self._key = uuid.uuid4().hex     # one key per dialog: a double click or a retry cannot post twice
        self.lines: list[ReturnLine] = []
        self.costs: dict[int, int] = {}
        self.body.addWidget(muted(f"المورد: {supplier_name} · يُرجَع الصنف بتكلفة فاتورة الشراء من الدفعة المختارة."))
        f = form()
        self.lot = QComboBox()
        for line_id, ref, name, dot, avail, cost in purchase_returns.returnable(session.con, supplier_id):
            self.costs[line_id] = cost
            self.lot.addItem(f"{name} · {ref}" + (f" · DOT {dot}" if dot else "") + f" · المتاح {avail} · {fmt(cost)}",
                             (line_id, avail))
        self.qty = ltr_edit("1")
        self.qty.setText("1")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب المرتجع (إلزامي)")
        self.date = ltr_edit("YYYY-MM-DD")
        self.date.setText(date.today().isoformat())
        f.addRow("الدفعة *", self.lot)
        f.addRow("الكمية *", self.qty)
        f.addRow("السبب *", self.reason)
        f.addRow("التاريخ *", self.date)
        self.body.addLayout(f)
        self.list_label = muted("لم تُضف أسطر بعد.")
        self.body.addWidget(self.list_label)
        add = QPushButton("إضافة السطر")
        add.clicked.connect(self.add_line)
        self.add_footer_widget(add)
        self.add_footer("تسجيل المرتجع", self.save)
        if not self.lot.count():
            self.notice.show_message("لا توجد دفعات متاحة للإرجاع لهذا المورد.")

    def add_line(self) -> None:
        data = self.lot.currentData()
        if data is None:
            return
        try:
            qty = int(self.qty.text().strip())
        except ValueError:
            self.notice.show_message("الكمية عدد صحيح")
            return
        line_id, avail = data
        if any(x.purchase_line_id == line_id for x in self.lines):
            self.notice.show_message("الدفعة مضافة سابقًا؛ احذف المرتجع وابدأ من جديد لتعديل الكمية")
            return
        if qty <= 0 or qty > avail:
            self.notice.show_message(f"الكمية بين 1 و{avail}")
            return
        self.lines.append(ReturnLine(line_id, qty))
        total = sum(x.qty * self.costs[x.purchase_line_id] for x in self.lines)
        self.list_label.setText(f"أسطر المرتجع: {len(self.lines)} · القيمة {fmt(total)}")

    def save(self) -> None:
        if not self.lines:
            self.add_line()
        try:
            self.saved = purchase_returns.post_return(
                self.session.con, self.session.user, request_key=self._key, supplier_id=self.supplier_id,
                lines=self.lines, reason=self.reason.text(), return_date=self.date.text().strip())
        except (PurchaseReturnError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class ReversePurchaseReturnDialog(Modal):
    def __init__(self, session, return_id: int, parent=None):
        super().__init__("عكس مرتجع مورد", parent, width=520)
        self.session, self.return_id, self.saved = session, return_id, None
        self._key = uuid.uuid4().hex
        self.body.addWidget(muted("يعود الصنف إلى المخزون ويُسجَّل مستند عكسي بتاريخ اليوم."))
        f = form()
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("سبب العكس (إلزامي)")
        f.addRow("سبب العكس *", self.reason)
        self.body.addLayout(f)
        self.add_footer("عكس المرتجع", self.save)

    def save(self) -> None:
        try:
            self.saved = purchase_returns.reverse_return(
                self.session.con, self.session.user, request_key=self._key, return_id=self.return_id,
                reason=self.reason.text())
        except (PurchaseReturnError, ledger.LedgerError) as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class SupplierReturnsDialog(Modal):
    """Returns of one supplier, newest first; the selected return can be reversed."""

    def __init__(self, session, supplier_id: int, supplier_name: str, parent=None):
        super().__init__(f"مرتجعات المورد {supplier_name}", parent, width=640)
        self.session, self.supplier_id, self.changed = session, supplier_id, False
        self.docs = QComboBox()
        self.body.addWidget(self.docs)
        self.note = muted("")
        self.body.addWidget(self.note)
        self.reverse_btn = danger_button("عكس المرتجع المحدد")
        self.reverse_btn.clicked.connect(self.reverse_selected)
        self.body.addWidget(self.reverse_btn)
        self.docs.currentIndexChanged.connect(self._selected)
        self.add_footer("إغلاق", self.accept)
        self._fill()

    def _fill(self) -> None:
        rows = purchase_returns.history(self.session.con, self.supplier_id)
        self.docs.blockSignals(True)
        self.docs.clear()
        for rid, number, kind, day, _name, total, reason, reversed_ in rows:
            label = "مرتجع" if kind == "return" else "عكس مرتجع"
            reversible = kind == "return" and not reversed_
            self.docs.addItem(f"{label} {number} · {day} · {fmt(total)} · {reason}" + ("" if reversible else " — لا يُعكس"),
                              (rid, reversible))
        self.docs.blockSignals(False)
        self.note.setText(f"عدد المستندات: {self.docs.count()}" if self.docs.count() else "لا توجد مرتجعات لهذا المورد.")
        self._selected()

    def _selected(self) -> None:
        data = self.docs.currentData()
        self.reverse_btn.setEnabled(bool(data and data[1]))

    def reverse_selected(self) -> None:
        dlg = ReversePurchaseReturnDialog(self.session, self.docs.currentData()[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.changed = True
            self._fill()
