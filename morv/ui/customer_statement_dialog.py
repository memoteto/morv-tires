"""Customer statement dialog: period, totals and the ledger movements of one customer."""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCalendarWidget, QComboBox, QDateEdit, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QSizePolicy, QStackedWidget, QTableView, QVBoxLayout, QWidget)

from morv.core.money import MoneyError, fmt
from morv.core.text import ltr
from morv.ui import icons
from morv.ui.listpage import TupleModel
from morv.ui.modal import Modal
from morv.ui.widgets import field, muted, primary_button


class CustomerStatementDialog(Modal):
    def __init__(self, customer_name, load, parent=None):
        super().__init__("كشف حساب العميل", parent, width=1080, table_mode=True, height=680)
        self._load = load
        self.statement = None
        self.body.setSpacing(10)

        identity = QVBoxLayout()
        identity.setSpacing(2)
        self.customer_name_label = QLabel(customer_name)
        self.customer_name_label.setObjectName("statementCustomerName")
        self.strip = muted("")
        identity.addWidget(self.customer_name_label, 0, Qt.AlignmentFlag.AlignLeading)
        identity.addWidget(self.strip, 0, Qt.AlignmentFlag.AlignLeading)
        self.body.addLayout(identity)

        filters = QHBoxLayout()
        filters.setSpacing(12)
        self.period_filter = QComboBox()
        for key, title in (("all", "جميع التواريخ"), ("today", "اليوم"), ("month", "هذا الشهر"),
                           ("previous", "الشهر السابق"), ("custom", "فترة مخصصة")):
            self.period_filter.addItem(title, key)
        self.period_filter.setMinimumWidth(185)
        self.period_filter.setObjectName("statementFilter")
        self.movement_filter = QComboBox()
        for key, title in (("all", "جميع الحركات"), ("sale", "فواتير البيع الآجل"),
                           ("return", "مرتجعات البيع"), ("debit", "الحركات المدينة"),
                           ("credit", "الحركات الدائنة")):
            self.movement_filter.addItem(title, key)
        self.movement_filter.setMinimumWidth(200)
        self.movement_filter.setObjectName("statementFilter")
        filters.addWidget(field("الفترة الزمنية", self.period_filter))
        filters.addWidget(field("نوع الحركة", self.movement_filter))

        self.date_controls = QWidget()
        dates = QHBoxLayout(self.date_controls)
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(12)
        today = QDate.currentDate()
        self.date_from = QDateEdit()
        self.date_to = QDateEdit()
        for edit in (self.date_from, self.date_to):
            edit.setObjectName("statementDate")
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setKeyboardTracking(False)
            edit.setMinimumWidth(155)
            edit.setMaximumWidth(185)
            edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute)
            calendar = edit.calendarWidget()
            calendar.setObjectName("statementCalendar")
            calendar.setFixedSize(316, 272)
            calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
            calendar.setGridVisible(False)
        self.date_from.setDate(QDate(today.year(), today.month(), 1))
        self.date_to.setDate(today)
        dates.addWidget(field("من تاريخ", self.date_from))
        dates.addWidget(field("إلى تاريخ", self.date_to))
        filters.addWidget(self.date_controls)
        filters.addStretch(1)
        self.body.addLayout(filters)
        self.date_controls.hide()

        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        self.kpi_debit, self.kpi_credit, self.kpi_balance = QLabel(), QLabel(), QLabel()
        for title, value in (("إجمالي المستحق عليه", self.kpi_debit), ("إجمالي المسدد", self.kpi_credit),
                             ("الرصيد المستحق", self.kpi_balance)):
            card = QFrame()
            card.setObjectName("statementKpiCard")
            col = QVBoxLayout(card)
            col.setContentsMargins(14, 10, 14, 10)
            col.setSpacing(4)
            value.setObjectName("statementKpiValue")
            col.addWidget(muted(title))
            col.addWidget(value)
            kpis.addWidget(card, 1)
        self.body.addLayout(kpis)

        opening_row = QHBoxLayout()
        self.opening_label = muted("")
        opening_row.addWidget(self.opening_label)
        opening_row.addStretch(1)
        self.body.addLayout(opening_row)
        section_title = QLabel("حركات الحساب")
        section_title.setObjectName("statementSectionTitle")
        self.body.addWidget(section_title)

        self.model = TupleModel([
            ("التاريخ", lambda r: ltr(r[0])),
            ("المستند", lambda r: ltr(r[1])),
            ("البيان", lambda r: r[2]),
            ("مدين (عليه)", lambda r: ltr(fmt(r[3])) if r[3] else "—"),
            ("دائن (سدّد)", lambda r: ltr(fmt(r[4])) if r[4] else "—"),
            ("الرصيد", lambda r: ltr(fmt(r[5]))),
        ])
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.empty_panel = QWidget()
        self.empty_panel.setObjectName("statementEmpty")
        empty_layout = QVBoxLayout(self.empty_panel)
        empty_layout.setContentsMargins(18, 18, 18, 18)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(1)
        image = QLabel()
        image.setPixmap(icons.icon("sales", "#8792A3", 36).pixmap(36, 36))
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(image)
        self.empty_label = QLabel("لا توجد حركات خلال الفترة المحددة")
        self.empty_label.setObjectName("statementEmptyTitle")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_label)
        hint = muted("يمكن تغيير الفترة أو نوع الحركة لعرض نتائج أخرى.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(hint)
        empty_layout.addStretch(1)

        self.results = QStackedWidget()
        self.results.addWidget(self.view)
        self.results.addWidget(self.empty_panel)
        self.body.addWidget(self.results, 1)

        close = primary_button("إغلاق")
        close.clicked.connect(self.accept)
        self.add_footer_widget(close)

        self.period_filter.currentIndexChanged.connect(self._period_changed)
        self.movement_filter.currentIndexChanged.connect(self._filter_rows)
        self.date_from.dateChanged.connect(self._custom_date_changed)
        self.date_to.dateChanged.connect(self._custom_date_changed)
        self.reload()

    def _dates(self) -> tuple[str, str]:
        period = self.period_filter.currentData()
        today = QDate.currentDate()
        if period == "all":
            return "", ""
        if period == "today":
            day = today.toString("yyyy-MM-dd")
            return day, day
        if period == "month":
            return QDate(today.year(), today.month(), 1).toString("yyyy-MM-dd"), today.toString("yyyy-MM-dd")
        if period == "previous":
            start = QDate(today.year(), today.month(), 1).addMonths(-1)
            return start.toString("yyyy-MM-dd"), start.addMonths(1).addDays(-1).toString("yyyy-MM-dd")
        return self.date_from.date().toString("yyyy-MM-dd"), self.date_to.date().toString("yyyy-MM-dd")

    def _period_changed(self, _index: int) -> None:
        self.date_controls.setVisible(self.period_filter.currentData() == "custom")
        self.reload()

    def _custom_date_changed(self, _date: QDate) -> None:
        if self.period_filter.currentData() == "custom":
            self.reload()

    def _filter_rows(self, _index: int = 0) -> None:
        if self.statement is None:
            return
        kind = self.movement_filter.currentData()
        rows = self.statement.rows
        if kind == "sale":
            rows = [r for r in rows if r.text == "فاتورة بيع آجلة"]
        elif kind == "return":
            rows = [r for r in rows if r.text == "مرتجع بيع"]
        elif kind == "debit":
            rows = [r for r in rows if r.debit > 0]
        elif kind == "credit":
            rows = [r for r in rows if r.credit > 0]
        self.model.reset_rows([(r.date, r.doc, r.text, r.debit, r.credit, r.balance) for r in rows])
        self.results.setCurrentWidget(self.view if rows else self.empty_panel)

    def reload(self) -> None:
        try:
            date_from, date_to = self._dates()
            statement = self._load(date_from, date_to)
        except (LookupError, MoneyError, ValueError) as e:
            self.notice.show_message(str(e))
            return
        self.statement = statement
        self.notice.show_message("")
        self.customer_name_label.setText(statement.customer_name)
        self.strip.setText(ltr(statement.mobile) if statement.mobile else "")
        self.kpi_debit.setText(f"{ltr(fmt(statement.debit_total))} جنيه")
        self.kpi_credit.setText(f"{ltr(fmt(statement.credit_total))} جنيه")
        self.kpi_balance.setText(f"{ltr(fmt(statement.closing))} جنيه")
        self.opening_label.setText(f"رصيد أول المدة: {ltr(fmt(statement.opening))} جنيه")
        self._filter_rows()
