"""Sales return dialog: pick lines off a posted invoice and post the reversing document."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSpinBox, QTableView, QVBoxLayout)

from morv.core import return_rules
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra.sale_returns import ReturnError, ReturnLineIn, post_return, preview_installment_refund
from morv.ui.listpage import TupleModel
from morv.ui.modal import Modal
from morv.ui.widgets import DateEdit, form, ltr_edit, muted

CONDITIONS = (("sound", "سليم — يعود للمخزون"), ("defective", "معيب — مخزن التالف"))
REFUND_LABELS = {"cash": "نقدي", "card": "بطاقة / ماكينة POS", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                 "credit": "على حساب العميل", "installment": "تقسيط — رد المدفوع وإلغاء المتبقي"}
REASONS = ("عيب في الصنف", "مقاس خاطئ", "لم يُركَّب", "طلب العميل")


class SaleReturnDialog(Modal):
    def __init__(self, session, sale, returnable, methods, parent=None):
        super().__init__(f"مرتجع من الفاتورة {ltr(sale.number)}", parent, width=900)
        self.session = session
        self.sale = sale
        self.returnable = {r.sale_line_id: r for r in returnable}
        self.posted = None
        self.lines: list[ReturnLineIn] = []
        self._busy = False
        self._request_key = uuid.uuid4().hex

        head = form()
        self.return_date = DateEdit()
        self.return_date.setText(max(date.today().isoformat(), sale.sale_date))
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems(REASONS)
        self.reason.setCurrentText("")
        self.refund = QComboBox()
        for m in methods:
            self.refund.addItem(REFUND_LABELS.get(m, m), m)
        self.note = ltr_edit("ملاحظة (اختيارية)")
        head.addRow("العميل", QLabel(sale.customer_name))
        head.addRow("إجمالي الفاتورة", QLabel(f"{ltr(fmt(sale.total))} جنيه"))
        head.addRow("تاريخ المرتجع *", self.return_date)
        head.addRow("سبب المرتجع *", self.reason)
        head.addRow("طريقة الرد", self.refund)
        head.addRow("ملاحظة", self.note)
        self.body.addLayout(head)

        entry = QFrame()
        entry.setObjectName("card")
        col = QVBoxLayout(entry)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(8)
        self.line_combo = QComboBox()
        self.line_combo.currentIndexChanged.connect(self._on_line_changed)
        col.addWidget(self.line_combo)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.qty = QSpinBox()
        self.qty.setRange(1, 100000)
        self.condition = QComboBox()
        for key, label in CONDITIONS:
            self.condition.addItem(label, key)
        self.add_btn = QPushButton("إضافة السطر")
        self.add_btn.clicked.connect(self.add_line)
        for title, w in (("كمية المرتجع", self.qty), ("حالة القطعة", self.condition)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            row.addLayout(cell, 1)
        row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(row)
        self.available_hint = muted("")
        col.addWidget(self.available_hint)
        self.body.addWidget(entry)

        self.model = TupleModel([
            ("م", lambda r: ltr(str(r[0]))),
            ("الصنف", lambda r: r[1]),
            ("كمية المرتجع", lambda r: ltr(str(r[2]))),
            ("حالة القطعة", lambda r: r[3]),
            ("القيمة", lambda r: ltr(fmt(r[4]))),
        ])
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.view.setMinimumHeight(150)
        self.body.addWidget(self.view)

        bar = QHBoxLayout()
        self.remove_btn = QPushButton("حذف السطر المختار")
        self.remove_btn.clicked.connect(self.remove_selected)
        self.total_label = QLabel()
        self.total_label.setObjectName("cardTitle")
        bar.addWidget(self.remove_btn)
        bar.addStretch(1)
        bar.addWidget(muted("قيمة الأصناف المرتجعة"))
        bar.addWidget(self.total_label)
        self.body.addLayout(bar)
        self.refund_breakdown = muted("")
        self.refund_breakdown.setWordWrap(True)
        self.body.addWidget(self.refund_breakdown)
        self.body.addWidget(muted("المحل غير مسجّل — بدون ضريبة"))
        override = QPushButton("موافقة المدير على تجاوز مدة الإرجاع — قريبًا")
        override.setEnabled(False)
        self.body.addWidget(override)

        self.post_btn = self.add_footer("اعتماد المرتجع", self.post)
        print_btn = QPushButton("طباعة الإيصال — قريبًا")
        print_btn.setEnabled(False)
        self.add_footer_widget(print_btn)
        self.return_date.textChanged.connect(lambda _text: self._refresh_total())
        self._refresh_choices()
        self._refresh_total()

    def _taken(self, sale_line_id: int) -> int:
        return sum(ln.qty for ln in self.lines if ln.sale_line_id == sale_line_id)

    def _refresh_choices(self) -> None:
        self.line_combo.blockSignals(True)
        self.line_combo.clear()
        for r in self.returnable.values():
            left = r.available - self._taken(r.sale_line_id)
            if left > 0:
                self.line_combo.addItem(f"{r.line_no}. {r.item_name} — المتاح {ltr(str(left))}", r.sale_line_id)
        self.line_combo.blockSignals(False)
        self._on_line_changed()

    def _on_line_changed(self, *_args) -> None:
        sale_line_id = self.line_combo.currentData()
        if sale_line_id is None:
            self.available_hint.setText("لا يوجد صنف متاح للإرجاع في هذه الفاتورة.")
            self.qty.setRange(1, 1)
            self.add_btn.setEnabled(False)
            return
        r = self.returnable[sale_line_id]
        left = r.available - self._taken(sale_line_id)
        self.qty.setRange(1, left)
        self.qty.setValue(1)
        self.add_btn.setEnabled(True)
        self.available_hint.setText(f"المباع {ltr(str(r.sold))} · سبق إرجاع {ltr(str(r.returned))} · "
                                    f"المتاح {ltr(str(left))}")

    def _value(self, sale_line_id: int, qty: int) -> int:
        """Same share the engine will post, so the shown total matches the document."""
        r = self.returnable[sale_line_id]
        return return_rules.portion(r.net_total, r.returned + self._taken(sale_line_id), qty, r.sold)

    def add_line(self) -> bool:
        sale_line_id = self.line_combo.currentData()
        if sale_line_id is None:
            self.notice.show_message("لا يوجد صنف متاح للإرجاع.")
            return False
        self.lines.append(ReturnLineIn(sale_line_id, self.qty.value(), self.condition.currentData()))
        self.notice.show_message("")
        self._refresh_lines()
        return True

    def remove_selected(self) -> None:
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر سطرًا من الجدول أولًا.", "warn")
            return
        del self.lines[idx.row()]
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        rows, taken = [], {}
        for i, ln in enumerate(self.lines, start=1):
            r = self.returnable[ln.sale_line_id]
            done = r.returned + taken.get(ln.sale_line_id, 0)
            value = return_rules.portion(r.net_total, done, ln.qty, r.sold)
            taken[ln.sale_line_id] = taken.get(ln.sale_line_id, 0) + ln.qty
            label = dict(CONDITIONS)[ln.condition]
            rows.append((i, r.item_name, ln.qty, label, value))
        self.model.reset_rows(rows)
        self._total = sum(r[4] for r in rows)
        self._refresh_choices()
        self._refresh_total()

    def _refresh_total(self) -> None:
        total = getattr(self, "_total", 0)
        self.total_label.setText(f"{ltr(fmt(total))} جنيه")
        if self.refund.currentData() != "installment":
            self.refund_breakdown.setText("")
            self.post_btn.setEnabled(True)
            return
        if not self.lines:
            self.refund_breakdown.setText(
                "يلزم إرجاع كل أصناف الفاتورة لإلغاء عقد التقسيط؛ المرتجع الجزئي غير متاح.")
            self.post_btn.setEnabled(False)
            return
        try:
            preview = preview_installment_refund(
                self.session.con, self.sale.id, self.lines, self.return_date.text().strip())
        except ReturnError as exc:
            self.refund_breakdown.setText(f"لا يمكن تسوية التقسيط: {exc}")
            self.post_btn.setEnabled(False)
            return
        methods = "، ".join(
            f"{REFUND_LABELS[method]}: {ltr(fmt(amount))}" for method, amount in preview.refunds)
        self.refund_breakdown.setText(
            f"إلغاء عقد التقسيط بالكامل — سيُرد للعميل: {ltr(fmt(preview.refunded))} جنيه. "
            f"وسائل الرد: {methods or 'لا توجد مبالغ محصّلة'}. "
            f"تُلغى المديونية: {ltr(fmt(preview.receivable_reduced))} جنيه. "
            f"تُعكس الفوائد المؤجلة: {ltr(fmt(preview.deferred_reversal))} جنيه "
            f"والفوائد المعترف بها سابقًا: {ltr(fmt(preview.recognized_reversal))} جنيه.")
        self.post_btn.setEnabled(True)

    def post(self) -> None:
        if self._busy:
            return
        if not self.lines:
            self.notice.show_message("أضف سطرًا واحدًا على الأقل قبل الاعتماد.")
            return
        merged: dict[tuple, int] = {}
        for ln in self.lines:
            merged[(ln.sale_line_id, ln.condition)] = merged.get((ln.sale_line_id, ln.condition), 0) + ln.qty
        if len({k[0] for k in merged}) != len(merged):
            self.notice.show_message("لا يمكن إرجاع السطر نفسه بحالتين في مستند واحد؛ افصلهما في مرتجعين.")
            return
        lines = [ReturnLineIn(line_id, qty, cond) for (line_id, cond), qty in merged.items()]
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = post_return(
                self.session.con, self.session.user, request_key=self._request_key, sale_id=self.sale.id,
                lines=lines, reason=self.reason.currentText(), refund_method=self.refund.currentData(),
                return_date=self.return_date.text().strip(), notes=self.note.text().strip() or None)
        except ReturnError as e:
            self.notice.show_message(f"لم يُرحَّل المرتجع: {e}")
        except Exception as e:      # the engine is atomic, so nothing half-written remains
            self.notice.show_message(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}).")
        else:
            self.accept()
        finally:
            self._busy = False
            self._refresh_total()
