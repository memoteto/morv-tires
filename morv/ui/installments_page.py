"""Instalments screen: the contract list, the new-contract form, and installment collection."""
import uuid
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableView,
                               QVBoxLayout, QWidget)

from morv.core import installments as calc
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import installment_view, installments
from morv.infra.installments import ContractError, Guarantor
from morv.ui.interest_dialogs import InterestRecognitionDialog
from morv.ui.listpage import ListPage, TupleModel
from morv.ui.widgets import Notice, card, field, ltr_edit, muted, primary_button, ro_field

DOWN_METHODS = (("cash", "نقدي"), ("card", "بطاقة / ماكينة POS"), ("transfer", "تحويل / محفظة"))
COLLECT_METHODS = (("cash", "نقدي"), ("card", "بطاقة / ماكينة POS"), ("transfer", "تحويل / محفظة"),
                   ("wallet", "محفظة"))
RELATIONS = ("قريب", "زميل عمل", "صديق", "أخرى")


class InstallmentsPage(QStackedWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.list_page = ContractsPage(session, self)
        self.refresh = self.list_page.refresh
        self.form_page = None
        self.collect_page = None
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

    def open_collect(self, contract_id: int) -> None:
        if self.collect_page is not None:
            self.removeWidget(self.collect_page)
            self.collect_page.deleteLater()
        self.collect_page = CollectPage(self.session, self, contract_id)
        self.addWidget(self.collect_page)
        self.setCurrentWidget(self.collect_page)

    def back_to_list(self, keep_id: int | None = None) -> None:
        self.setCurrentWidget(self.list_page)
        self.list_page.reload(keep_id=keep_id)


KPI_TILES = (("late", "متأخر"), ("today", "مستحق اليوم"), ("week", "خلال 7 أيام"), ("active", "المتبقي على العقود النشطة"))


class ContractsPage(ListPage):
    title = "التقسيط والتحصيل"
    add_label = "عقد تقسيط جديد"
    search_placeholder = "اسم العميل أو رقم الموبايل (F2)"
    columns = [
        ("العميل", lambda r: r[2]),
        ("العقد", lambda r: ltr(r[1])),
        ("الاستحقاق", lambda r: ltr(r[4]) if r[4] else "—"),
        ("المطلوب الآن", lambda r: ltr(fmt(r[5])) if r[5] else "—"),
        ("الحالة", lambda r: r[6]),
        ("السداد", lambda r: r[7]),
        ("المتبقي على العقد", lambda r: ltr(fmt(r[8]))),
    ]

    def __init__(self, session, stack=None):
        self.stack = stack
        self.notice = Notice()
        self._kpis = None
        self._kpi_values: dict[str, tuple] = {}
        self._kpi_buttons: dict[str, QPushButton] = {}
        super().__init__(session)       # Qt refuses attributes set before its own init

    def can_edit(self, con=None) -> bool:
        return self.session.can("installments.manage", con)

    def extra_header(self) -> QWidget:
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        col.addWidget(self.notice)
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (key, title) in enumerate(KPI_TILES):
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setObjectName("kpiTile")
            inner = QVBoxLayout(btn)
            inner.setContentsMargins(14, 10, 14, 10)
            inner.setSpacing(4)
            inner.addWidget(muted(title))
            value = QLabel("0")
            value.setObjectName("cardTitle")
            inner.addWidget(value)
            sub = muted("")
            inner.addWidget(sub)
            btn.clicked.connect(lambda _c=False, k=key: self._pick_kpi(k))
            self._kpi_values[key] = (value, sub)
            self._kpi_buttons[key] = btn
            grid.addWidget(btn, 0, i)
        col.addLayout(grid)
        self.interest_btn = QPushButton("اعتراف بفوائد الأشهر المنتهية")
        self.interest_btn.clicked.connect(self.recognise_interest)
        col.addWidget(self.interest_btn)
        return box

    def recognise_interest(self) -> None:
        if not self.session.can("installments.manage", self.session.con):
            self.notice.show_message("لا تملك صلاحية الاعتراف بفوائد التقسيط")
            return
        dlg = InterestRecognitionDialog(self.session, self)
        if dlg.exec() and dlg.saved:
            self.notice.show_message(f"رُحِّلت فوائد {dlg.saved.posted} شهر", "success")

    def _pick_kpi(self, key: str) -> None:
        self.set_filter("all" if self.filter_key == key else key)

    def set_filter(self, key) -> None:
        self.filter_key = key
        for k, b in self._kpi_buttons.items():
            b.setChecked(k == key)
        self.reload()

    def fetch(self, con, query, filter_key, offset):
        if not self.can_edit(con):
            return []
        view = installment_view.list_view(con, query=query, filter_key=filter_key or "all")
        rows = _RowsWithKpis(
            (r.contract_id, r.number, r.customer_name, r.customer_mobile, r.next_due, r.amount_now,
             r.status, r.progress_text, r.remaining_total, r.can_collect) for r in view.rows)
        rows.kpis = view.kpis
        return rows

    def on_loaded(self, rows: list) -> None:
        k = self._kpis = getattr(rows, "kpis", None)
        if k is None:
            return
        self._kpi_values["late"][0].setText(ltr(fmt(k.late_amount)))
        self._kpi_values["late"][1].setText(f"{k.late_count} عقدًا")
        self._kpi_values["today"][0].setText(ltr(fmt(k.today_amount)))
        self._kpi_values["today"][1].setText(f"{k.today_count} عقدًا")
        self._kpi_values["week"][0].setText(ltr(fmt(k.week_amount)))
        self._kpi_values["week"][1].setText(f"{k.week_count} عقدًا")
        self._kpi_values["active"][0].setText(ltr(fmt(k.active_amount)))
        self._kpi_values["active"][1].setText(f"{k.active_count} عقدًا")

    def total(self) -> str:
        if not self.can_edit():
            return "لا تملك صلاحية الاطلاع على عقود التقسيط"
        waiting = len(installment_view.invoices_without_contract(self.session.con))
        text = f"{installment_view.count(self.session.con)} عقد تقسيط"
        return f"{text} · {waiting} فاتورة بلا عقد" if waiting else text

    def build_preview(self, row_id: int) -> None:
        c = installment_view.get_contract(self.session.con, row_id)
        self.heading(ltr(c.number), f"{c.customer_name} · {ltr(c.customer_mobile)}")
        self.summary_metrics([
            ("إجمالي العقد", ltr(fmt(c.contract_total))),
            ("إجمالي الأقساط", ltr(fmt(c.instalments_total))),
            ("المبلغ المموّل", ltr(fmt(c.financed))),
        ])
        self.summary_section("بيانات العقد", "calendar")
        details = [
            ("فاتورة البيع", ltr(c.sale_number)),
            ("تاريخ العقد", ltr(c.contract_date)),
            ("سعر الكاش", ltr(fmt(c.cash_price))),
            ("المقدم", ltr(fmt(c.down))),
            (f"الفائدة {ltr(f'{c.rate_bp / 100:g}%')}", ltr(fmt(c.markup))),
        ]
        if c.guarantor_name:
            details.append(("الضامن", c.guarantor_name))
        details.append(("تذكيرات واتساب", "موافق" if c.whatsapp_ok else "لم يوافق"))
        self.summary_details(details)
        self.summary_section("جدول الأقساط", "cash")
        self.summary_table(
            ["القسط", "الاستحقاق", "المبلغ", "الأصل", "الفائدة"],
            [(ltr(str(r[0])), ltr(r[1]), ltr(fmt(r[2])), ltr(fmt(r[3])), ltr(fmt(r[4])))
             for r in c.rows],
            column_alignments=(Qt.AlignmentFlag.AlignCenter,) * 5,
            keep_table=True)
        if self.can_edit() and self.stack is not None:
            btn = primary_button("تحصيل قسط")
            btn.clicked.connect(lambda: self.stack.open_collect(row_id))
            self.actions(btn)
            statement_btn = QPushButton("كشف حساب العقد — قريبًا")
            statement_btn.setEnabled(False)
            self.actions(statement_btn)

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

        # The form + schedule table are taller than the default window; scroll rather than overlap.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
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

        client_box, client_lay = card("العميل والضامن")
        f = QGridLayout()
        f.setSpacing(10)
        self.national_id = ltr_edit("14 رقمًا")
        self.address = QLineEdit()
        self.work_place = QLineEdit()
        self.work_place.setPlaceholderText("مثال: مدرس — مدرسة النصر الإعدادية")
        self.whatsapp_ok = QCheckBox("يوافق العميل على تلقي تذكيرات الأقساط عبر واتساب")
        f.addWidget(field("الرقم القومي *", self.national_id), 0, 0)
        f.addWidget(ro_field("الموبايل", ltr(self.sale[5]), is_ltr=True), 0, 1)
        f.addWidget(ro_field("العميل", self.sale[2]), 0, 2)
        f.addWidget(field("العنوان", self.address), 1, 0, 1, 2)
        f.addWidget(field("جهة العمل", self.work_place), 1, 2)
        client_lay.addLayout(f)
        client_lay.addWidget(self.whatsapp_ok)

        self.has_guarantor = QCheckBox("إضافة ضامن (اختياري)")
        self.has_guarantor.toggled.connect(self._toggle_guarantor)
        client_lay.addWidget(self.has_guarantor)
        self.guarantor = QFrame()
        g = QGridLayout()
        g.setSpacing(10)
        self.g_name = QLineEdit()
        self.g_mobile = ltr_edit("01xxxxxxxxx")
        self.g_nid = ltr_edit("14 رقمًا")
        self.g_relation = QComboBox()
        self.g_relation.addItems(RELATIONS)
        g.addWidget(field("اسم الضامن", self.g_name), 0, 0)
        g.addWidget(field("موبايل الضامن", self.g_mobile), 0, 1)
        g.addWidget(field("الرقم القومي للضامن", self.g_nid), 1, 0)
        g.addWidget(field("الصلة بالعميل", self.g_relation), 1, 1)
        gl = QVBoxLayout(self.guarantor)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.addLayout(g)
        self.guarantor.setVisible(False)
        client_lay.addWidget(self.guarantor)
        col.addWidget(client_box)

        terms_box, terms_lay = card("بنود العقد")
        t = QGridLayout()
        t.setSpacing(10)
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
        t.addWidget(ro_field("رقم الفاتورة", ltr(self.sale[1]), is_ltr=True), 0, 0)
        t.addWidget(ro_field("سعر الكاش (قطاعي)", f"{ltr(fmt(self.sale[4]))} جنيه", is_ltr=True), 0, 1)
        t.addWidget(field("تاريخ العقد", self.contract_date), 0, 2)
        t.addWidget(field("الفائدة % *", self.rate), 1, 0)
        t.addWidget(field("المقدم *", self.down), 1, 1)
        t.addWidget(field("عدد الأشهر", self.months), 1, 2)
        t.addWidget(field("تاريخ أول قسط", self.first_due), 2, 0)
        t.addWidget(field("تحصيل المقدم", self.down_method), 2, 1)
        terms_lay.addLayout(t)
        col.addWidget(terms_box)

        sched_box, sched_lay = card("جدول التوزيع الشهري · الفائدة موزّعة بالتساوي")
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
        sched_lay.addWidget(self.view, 1)
        self.diff_hint = muted("فرق القروش يُجمع في القسط الأخير.")
        sched_lay.addWidget(self.diff_hint)
        col.addWidget(sched_box, 1)
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


class CollectPage(QWidget):
    def __init__(self, session, stack, contract_id, parent=None):
        super().__init__(parent)
        self.session = session
        self.stack = stack
        self.contract_id = contract_id
        self._busy = False
        self._request_key = uuid.uuid4().hex
        self.view_data: installment_view.CollectionView | None = None

        # The stats row + schedule table are taller than the default window; scroll rather than overlap.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        root.addWidget(self._header())
        self.notice = Notice()
        root.addWidget(self.notice)
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addLayout(self._left(), 3)
        body.addLayout(self._right(), 2)
        content = QWidget()
        content.setLayout(body)
        root.addWidget(content, 1)
        self.reload()

    def _header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("تحصيل قسط")
        title.setObjectName("cardTitle")
        back = QPushButton("رجوع للقائمة")
        back.clicked.connect(lambda: self.stack.back_to_list(keep_id=self.contract_id))
        for label in ("كشف حساب العميل", "تسوية مبكرة للعقد"):
            b = QPushButton(f"{label} — قريبًا")
            b.setEnabled(False)
            h.addWidget(b)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(back)
        return w

    def _left(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        self.contract_title = muted("")
        col.addWidget(self.contract_title)

        stats = QGridLayout()
        self.stat_labels = {}
        for i, key in enumerate(("القسط الشهري", "المسدد", "المتبقي على العقد", "متأخر")):
            box = QFrame()
            box.setObjectName("card")
            inner = QVBoxLayout(box)
            inner.setContentsMargins(12, 10, 12, 10)
            inner.addWidget(muted(key))
            value = QLabel()
            value.setObjectName("cardTitle")
            inner.addWidget(value)
            self.stat_labels[key] = value
            stats.addWidget(box, 0, i)
        col.addLayout(stats)

        col.addWidget(muted("أقساط العقد"))
        self.model = TupleModel([
            ("م", lambda r: ltr(str(r[0]))),
            ("الاستحقاق", lambda r: ltr(r[1])),
            ("قيمة القسط", lambda r: ltr(fmt(r[2]))),
            ("المسدد", lambda r: ltr(fmt(r[3])) if r[3] else "—"),
            ("المتبقي منه", lambda r: ltr(fmt(r[4])) if r[4] else "—"),
            ("الحالة", lambda r: r[5]),
        ])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(260)
        col.addWidget(self.table, 1)
        col.addWidget(muted("المبلغ المحصَّل يُوزَّع تلقائيًا على الأقساط من الأقدم للأحدث."))
        return col

    def _right(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(12)
        box, inner = card("التحصيل")

        presets = QHBoxLayout()
        for label, pick in (("قسط واحد", self._preset_one), ("كل المتأخر", self._preset_overdue),
                            ("كل المتبقي", self._preset_full)):
            b = QPushButton(label)
            b.clicked.connect(pick)
            presets.addWidget(b)
        inner.addLayout(presets)

        self.amount = ltr_edit("0.00")
        self.amount.textChanged.connect(self._recompute)
        inner.addWidget(field("المبلغ المستلم *", self.amount))
        self.method = QComboBox()
        for key, label in COLLECT_METHODS:
            self.method.addItem(label, key)
        inner.addWidget(field("طريقة الدفع", self.method))

        inner.addWidget(muted("التوزيع على الأقساط"))
        self.alloc_box = QVBoxLayout()
        inner.addLayout(self.alloc_box)

        self.after_label = QLabel()
        self.after_label.setObjectName("cardTitle")
        inner.addWidget(self.after_label)
        self.next_due_label = muted("")
        inner.addWidget(self.next_due_label)

        self.collect_btn = primary_button("تحصيل")
        self.collect_btn.clicked.connect(self.collect)
        inner.addWidget(self.collect_btn)
        print_btn = QPushButton("تحصيل وطباعة الإيصال — قريبًا")
        print_btn.setEnabled(False)
        inner.addWidget(print_btn)
        col.addWidget(box)
        col.addStretch(1)
        return col

    # ---------- Data ----------

    def reload(self) -> None:
        self.view_data = installment_view.collection_view(self.session.con, self.contract_id)
        v = self.view_data
        self.contract_title.setText(f"عقد {ltr(v.number)} · {v.customer_name} · فاتورة {ltr(v.sale_number)}")
        self.stat_labels["القسط الشهري"].setText(ltr(fmt(v.monthly)))
        self.stat_labels["المسدد"].setText(ltr(fmt(v.paid_total)))
        self.stat_labels["المتبقي على العقد"].setText(ltr(fmt(v.remaining_total)))
        self.stat_labels["متأخر"].setText(
            f"{ltr(fmt(v.overdue_amount))} ({v.overdue_count})" if v.overdue_count else "—")
        self.model.reset_rows([(r.no, r.due_date, r.amount, r.paid, r.remaining, r.status) for r in v.rows])
        default = v.overdue_amount if v.overdue_count else (v.next_due[1] if v.next_due else 0)
        self.amount.setText(fmt(default) if default else "")
        self._recompute()

    def _preset_one(self) -> None:
        if self.view_data and self.view_data.next_due:
            self.amount.setText(fmt(self.view_data.next_due[1]))

    def _preset_overdue(self) -> None:
        if self.view_data and self.view_data.overdue_count:
            self.amount.setText(fmt(self.view_data.overdue_amount))

    def _preset_full(self) -> None:
        if self.view_data:
            self.amount.setText(fmt(self.view_data.remaining_total))

    def _recompute(self, *_args) -> None:
        while self.alloc_box.count():
            item = self.alloc_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.view_data is None:
            return
        try:
            amount = parse_money(self.amount.text() or "0")
        except MoneyError:
            amount = -1
        over = amount > self.view_data.remaining_total
        valid = amount > 0 and not over
        self.collect_btn.setEnabled(valid and not self._busy)
        if over:
            self.notice.show_message(
                f"المبلغ أكبر من المتبقي على العقد ({ltr(fmt(self.view_data.remaining_total))})", "warn")
        else:
            self.notice.show_message("")
        left = amount if valid else 0
        for r in self.view_data.rows:
            if left <= 0 or r.remaining <= 0:
                continue
            take = min(left, r.remaining)
            left -= take
            tail = " — كامل" if take == r.remaining else f" — جزئي، يتبقى {ltr(fmt(r.remaining - take))}"
            row = QHBoxLayout()
            row.addWidget(muted(f"القسط {r.no} ({ltr(r.due_date)}){tail}"))
            row.addStretch(1)
            row.addWidget(QLabel(ltr(fmt(take))))
            self.alloc_box.addLayout(row)
        after = self.view_data.remaining_total - (amount if valid else 0)
        self.after_label.setText(f"بعد التحصيل يتبقى: {ltr(fmt(after))}")
        self.next_due_label.setText(
            f"القسط القادم: {ltr(self.view_data.next_due[0])} · {ltr(fmt(self.view_data.next_due[1]))}"
            if self.view_data.next_due else "القسط القادم: لا يوجد — العقد مسدَّد بالكامل")

    def collect(self) -> None:
        if self._busy or self.view_data is None:
            return
        try:
            amount = parse_money(self.amount.text() or "0")
        except MoneyError as e:
            self.notice.show_message(str(e))
            return
        self._busy = True
        self.collect_btn.setEnabled(False)
        try:
            installments.collect_payment(
                self.session.con, self.session.user, request_key=self._request_key, contract_id=self.contract_id,
                amount=amount, method=self.method.currentData())
        except ContractError as e:
            self.notice.show_message(f"لم يُحصَّل القسط: {e}")
        except Exception as e:      # the engine is atomic, so nothing half-written remains
            self.notice.show_message(f"تعذّر التحصيل ولم يُحفظ شيء ({type(e).__name__}).")
        else:
            self._request_key = uuid.uuid4().hex   # a fresh key for the next collection on this screen
            self.notice.show_message("تم التحصيل", "success")
            self.reload()
        finally:
            self._busy = False
            self._recompute()


class _RowsWithKpis(list):
    """Rows plus the KPI totals computed in the same read, so both come from one snapshot."""
    kpis = None
