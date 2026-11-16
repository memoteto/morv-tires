"""Instalments screen: the contract list, and the new-contract form of the approved design in place."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QStackedWidget, QTableView, QVBoxLayout,
                               QWidget)

from morv.core import installments as calc
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import installment_view, installments
from morv.infra.installments import ContractError, Guarantor
from morv.ui.listpage import ListPage, TupleModel
from morv.ui.widgets import Notice, form, ltr_edit, muted, primary_button

DOWN_METHODS = (("cash", "نقدي"), ("card", "بطاقة / ماكينة POS"), ("transfer", "تحويل / محفظة"))
RELATIONS = ("قريب", "زميل عمل", "صديق", "أخرى")


class InstallmentsPage(QStackedWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.list_page = ContractsPage(session, self)
        self.form_page = None
        self.addWidget(self.list_page)

    def open_form(self, sale: tuple) -> None:
        if self.form_page is not None:
            self.removeWidget(self.form_page)
            self.form_page.deleteLater()
        self.form_page = ContractForm(self.session, sale, self)
        self.addWidget(self.form_page)
        self.setCurrentWidget(self.form_page)

    def open_form_for_sale(self, sale_id: int) -> None:
        try:
            sale = installment_view.invoice_for_contract(self.session.con, sale_id)
        except LookupError as e:
            self.setCurrentWidget(self.list_page)
            self.list_page.notice.show_message(str(e), "warn")
            return
        self.open_form(sale)

    def back_to_list(self, keep_id: int | None = None) -> None:
        self.setCurrentWidget(self.list_page)
        self.list_page.reload(keep_id=keep_id)


class ContractsPage(ListPage):
    title = "التقسيط والتحصيل"
    add_label = "عقد تقسيط جديد"
    search_placeholder = "رقم العقد، اسم العميل، أو الموبايل (F2)"
    columns = [
        ("العقد", lambda r: ltr(r[1])),
        ("العميل", lambda r: r[2]),
        ("التاريخ", lambda r: ltr(r[3])),
        ("إجمالي العقد", lambda r: ltr(fmt(r[4]))),
        ("الأشهر", lambda r: ltr(str(r[5]))),
    ]

    def __init__(self, session, stack=None):
        super().__init__(session)       # Qt refuses attributes set before its own init
        self.stack = stack

    def can_edit(self) -> bool:
        return self.session.can("installments.manage")

    def fetch(self, query, filter_key, offset):
        if not self.can_edit():
            return []
        return installment_view.list_contracts(self.session.con, query, offset=offset)

    def total(self) -> str:
        if not self.can_edit():
            return "لا تملك صلاحية الاطلاع على عقود التقسيط"
        waiting = len(installment_view.invoices_without_contract(self.session.con))
        text = f"{installment_view.count(self.session.con)} عقد تقسيط"
        return f"{text} · {waiting} فاتورة بلا عقد" if waiting else text

    def build_preview(self, row_id: int) -> None:
        c = installment_view.get_contract(self.session.con, row_id)
        self.heading(ltr(c.number), f"{c.customer_name} · {ltr(c.customer_mobile)}")
        self.kv("فاتورة البيع", ltr(c.sale_number))
        self.kv("تاريخ العقد", ltr(c.contract_date))
        self.kv("سعر الكاش", ltr(fmt(c.cash_price)))
        self.kv("المقدم", ltr(fmt(c.down)))
        self.kv("المبلغ المموّل", ltr(fmt(c.financed)))
        self.kv(f"الفائدة {ltr(f'{c.rate_bp / 100:g}%')}", ltr(fmt(c.markup)))
        self.kv("إجمالي الأقساط", ltr(fmt(c.instalments_total)))
        self.kv("إجمالي العقد", ltr(fmt(c.contract_total)))
        if c.guarantor_name:
            self.kv("الضامن", c.guarantor_name)
        self.kv("تذكيرات واتساب", "موافق" if c.whatsapp_ok else "لم يوافق")
        for r in c.rows:
            self.kv(f"{ltr(str(r[0]))}. {ltr(r[1])}",
                    f"{ltr(fmt(r[2]))} · أصل {ltr(fmt(r[3]))} · فائدة {ltr(fmt(r[4]))}")

    def open_dialog(self, row_id):
        if row_id is not None:
            self.show_preview_id(row_id)
            return
        if not self.can_edit() or self.stack is None:
            return
        waiting = installment_view.invoices_without_contract(self.session.con)
        if not waiting:
            self.notice.show_message("لا توجد فاتورة مرحّلة بوسيلة التقسيط بانتظار عقد. ارحّل الفاتورة أولًا من "
                                     "نقطة البيع.", "warn")
            return
        self.stack.open_form(waiting[0])


class ContractForm(QWidget):
    def __init__(self, session, sale, stack, parent=None):
        super().__init__(parent)
        self.session = session
        self.sale = sale
        self.stack = stack
        self.posted = None
        self._busy = False
        self._request_key = uuid.uuid4().hex
        self._terms = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        self.notice = Notice()
        root.addWidget(self._header())
        root.addWidget(self.notice)
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addLayout(self._left(), 3)
        body.addLayout(self._right(), 2)
        content = QWidget()
        content.setLayout(body)
        root.addWidget(content, 1)
        self.recompute()

    def _header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("عقد تقسيط جديد")
        title.setObjectName("cardTitle")
        draft_chip = QLabel("مسودة")
        draft_chip.setObjectName("chip")
        back = QPushButton("رجوع للقائمة")
        back.clicked.connect(lambda: self.stack.back_to_list())
        preview = QPushButton("معاينة العقد — قريبًا")
        preview.setEnabled(False)
        h.addWidget(title)
        h.addWidget(draft_chip)
        h.addWidget(muted(f"من فاتورة البيع {ltr(self.sale[1])}"))
        h.addStretch(1)
        h.addWidget(preview)
        h.addWidget(back)
        return w

    def _left(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)

        col.addWidget(muted("العميل والضامن"))
        f = form()
        self.national_id = ltr_edit("14 رقمًا")
        self.address = QLineEdit()
        self.work_place = QLineEdit()
        self.work_place.setPlaceholderText("مثال: مدرس — مدرسة النصر الإعدادية")
        self.whatsapp_ok = QCheckBox("يوافق العميل على تلقي تذكيرات الأقساط عبر واتساب")
        f.addRow("العميل", QLabel(self.sale[2]))
        f.addRow("الموبايل", QLabel(ltr(self.sale[5])))
        f.addRow("الرقم القومي *", self.national_id)
        f.addRow("العنوان", self.address)
        f.addRow("جهة العمل", self.work_place)
        f.addRow("", self.whatsapp_ok)
        col.addLayout(f)

        self.has_guarantor = QCheckBox("إضافة ضامن (اختياري)")
        self.has_guarantor.toggled.connect(self._toggle_guarantor)
        col.addWidget(self.has_guarantor)
        self.guarantor = QFrame()
        g = form()
        self.g_name = QLineEdit()
        self.g_mobile = ltr_edit("01xxxxxxxxx")
        self.g_nid = ltr_edit("14 رقمًا")
        self.g_relation = QComboBox()
        self.g_relation.addItems(RELATIONS)
        g.addRow("اسم الضامن", self.g_name)
        g.addRow("موبايل الضامن", self.g_mobile)
        g.addRow("الرقم القومي للضامن", self.g_nid)
        g.addRow("الصلة بالعميل", self.g_relation)
        gl = QVBoxLayout(self.guarantor)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.addLayout(g)
        self.guarantor.setVisible(False)
        col.addWidget(self.guarantor)

        col.addWidget(muted("بنود العقد"))
        t = form()
        self.contract_date = ltr_edit("YYYY-MM-DD")
        self.contract_date.setText(max(date.today().isoformat(), self.sale[3]))
        self.rate = ltr_edit("مثال: 15")
        self.rate.setText("15")
        self.rate.textChanged.connect(self.recompute)
        self.down = ltr_edit("0.00")
        self.down.setText("0.00")
        self.down.textChanged.connect(self.recompute)
        self.months = QSpinBox()
        self.months.setRange(calc.MIN_MONTHS, calc.MAX_MONTHS)
        self.months.setValue(10)
        self.months.valueChanged.connect(self.recompute)
        self.first_due = ltr_edit("YYYY-MM-DD")
        self.first_due.setText(calc.add_months(self.contract_date.text(), 1))
        self.first_due.textChanged.connect(self.recompute)
        self.down_method = QComboBox()
        for key, label in DOWN_METHODS:
            self.down_method.addItem(label, key)
        t.addRow("رقم الفاتورة", QLabel(ltr(self.sale[1])))
        t.addRow("تاريخ العقد", self.contract_date)
        t.addRow("سعر الكاش (قطاعي)", QLabel(f"{ltr(fmt(self.sale[4]))} جنيه"))
        t.addRow("الفائدة % *", self.rate)
        t.addRow("المقدم *", self.down)
        t.addRow("عدد الأشهر", self.months)
        t.addRow("تاريخ أول قسط", self.first_due)
        t.addRow("تحصيل المقدم", self.down_method)
        col.addLayout(t)

        col.addWidget(muted("جدول التوزيع الشهري · الفائدة موزّعة بالتساوي"))
        self.model = TupleModel([
            ("م", lambda r: ltr(str(r[0]))),
            ("تاريخ الاستحقاق", lambda r: ltr(r[1])),
            ("قيمة القسط", lambda r: ltr(fmt(r[2]))),
            ("منها أصل", lambda r: ltr(fmt(r[3]))),
            ("منها فائدة", lambda r: ltr(fmt(r[4]))),
            ("المتبقي بعد السداد", lambda r: ltr(fmt(r[5]))),
        ])
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.view.setMinimumHeight(220)
        col.addWidget(self.view, 1)
        self.diff_hint = muted("فرق القروش يُجمع في القسط الأخير.")
        col.addWidget(self.diff_hint)
        return col

    def _right(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        card = QFrame()
        card.setObjectName("card")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(14, 12, 14, 12)
        inner.setSpacing(8)
        inner.addWidget(muted("ملخص العقد"))
        self.monthly_label = QLabel()
        self.monthly_label.setObjectName("cardTitle")
        inner.addWidget(self.monthly_label)
        grid = QGridLayout()
        self.summary = {}
        for row, key in enumerate(("سعر الكاش (قطاعي)", "المقدم", "المبلغ المموّل", "الفائدة",
                                   "فائدة كل شهر", "إجمالي الأقساط (بدون المقدم)", "إجمالي العقد")):
            value = QLabel()
            self.summary[key] = value
            grid.addWidget(muted(key), row, 0)
            grid.addWidget(value, row, 1, alignment=Qt.AlignmentFlag.AlignLeading)
        inner.addLayout(grid)
        col.addWidget(card)

        self.post_btn = primary_button("اعتماد العقد وتحصيل المقدم")
        self.post_btn.clicked.connect(self.post)
        col.addWidget(self.post_btn)
        for label in ("حفظ كمسودة", "طباعة العقد والجدول"):
            b = QPushButton(f"{label} — قريبًا")
            b.setEnabled(False)
            col.addWidget(b)
        col.addStretch(1)
        return col

    def _toggle_guarantor(self, on: bool) -> None:
        self.guarantor.setVisible(on)

    def _rate_bp(self) -> int:
        text = (self.rate.text() or "").strip()
        try:
            return parse_money(text)      # a percentage with two decimals is basis points
        except MoneyError as e:
            raise ValueError("نسبة الفائدة غير صالحة") from e

    def recompute(self, *_args) -> None:
        self._terms = None
        try:
            terms = calc.plan(self.sale[4], parse_money(self.down.text() or "0"), self._rate_bp(),
                              self.months.value(), self.first_due.text().strip())
        except (calc.InstalmentError, MoneyError, ValueError) as e:
            self.model.reset_rows([])
            self._show_summary(None)
            self.notice.show_message(str(e), "warn")
            self.post_btn.setEnabled(False)
            return
        self._terms = terms
        self.notice.show_message("")
        self.post_btn.setEnabled(True)
        self.model.reset_rows([(r.no, r.due_date, r.amount, r.principal, r.markup, r.balance_after)
                               for r in terms.rows])
        self._show_summary(terms)

    def _show_summary(self, terms) -> None:
        if terms is None:
            self.monthly_label.setText("—")
            for label in self.summary.values():
                label.setText("—")
            return
        self.monthly_label.setText(f"القسط الشهري: {ltr(fmt(terms.monthly))} جنيه × {ltr(str(terms.months))}")
        shown = {
            "سعر الكاش (قطاعي)": terms.cash_price,
            "المقدم": terms.down,
            "المبلغ المموّل": terms.financed,
            "الفائدة": terms.markup,
            "فائدة كل شهر": terms.markup // terms.months,
            "إجمالي الأقساط (بدون المقدم)": terms.instalments_total,
            "إجمالي العقد": terms.contract_total,
        }
        for key, value in shown.items():
            self.summary[key].setText(f"{ltr(fmt(value))} جنيه")

    def post(self) -> None:
        if self._busy or self._terms is None:
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        g = Guarantor(self.g_name.text(), self.g_mobile.text(), self.g_nid.text(),
                      self.g_relation.currentText()) if self.has_guarantor.isChecked() else None
        try:
            self.posted = installments.post_contract(
                self.session.con, self.session.user, request_key=self._request_key, sale_id=self.sale[0],
                down=self._terms.down, rate_bp=self._terms.rate_bp, months=self._terms.months,
                first_due=self.first_due.text().strip(), national_id=self.national_id.text(),
                down_method=self.down_method.currentData() if self._terms.down else None,
                contract_date=self.contract_date.text().strip(), address=self.address.text(),
                work_place=self.work_place.text(), whatsapp_ok=self.whatsapp_ok.isChecked(), guarantor=g)
        except ContractError as e:
            self.notice.show_message(f"لم يُعتمد العقد: {e}")
        except Exception as e:      # the engine is atomic, so nothing half-written remains
            self.notice.show_message(f"تعذّر الاعتماد ولم يُحفظ شيء ({type(e).__name__}).")
        else:
            self.stack.back_to_list(keep_id=self.posted.id)
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
