"""Customer statement dialog: period, totals and the ledger movements of one customer."""
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableView,
                               QVBoxLayout)

from morv.core.money import MoneyError, fmt
from morv.core.text import ltr
from morv.ui.listpage import TupleModel
from morv.ui.modal import Modal
from morv.ui.widgets import form, ltr_edit, muted, primary_button


class CustomerStatementDialog(Modal):
    def __init__(self, customer_name, load, parent=None):
        super().__init__(f"كشف حساب — {customer_name}", parent, width=940)
        self._load = load
        self.statement = None

        head = form()
        self.date_from = ltr_edit("YYYY-MM-DD")
        self.date_to = ltr_edit("YYYY-MM-DD")
        self.date_from.editingFinished.connect(self.reload)
        self.date_to.editingFinished.connect(self.reload)
        head.addRow("من", self.date_from)
        head.addRow("إلى", self.date_to)
        self.body.addLayout(head)
        self.strip = muted("")
        self.body.addWidget(self.strip)

        kpis = QFrame()
        kpis.setObjectName("card")
        grid = QHBoxLayout(kpis)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(16)
        self.kpi_debit, self.kpi_credit, self.kpi_balance = QLabel(), QLabel(), QLabel()
        self.kpi_balance.setObjectName("cardTitle")
        for title, w in (("إجمالي المستحق عليه", self.kpi_debit), ("إجمالي المسدد", self.kpi_credit),
                         ("الرصيد المستحق", self.kpi_balance)):
            cell = QVBoxLayout()
            cell.setSpacing(4)
            cell.addWidget(muted(title))
            cell.addWidget(w)
            grid.addLayout(cell, 1)
        self.body.addWidget(kpis)
        self.body.addWidget(muted("المتأخر من الأقساط يظهر هنا بعد بناء التقسيط (المرحلة 5)."))

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
        self.view.setMinimumHeight(260)
        self.body.addWidget(self.view)
        self.opening_label = muted("")
        self.body.addWidget(self.opening_label)
        self.empty_label = muted("لا توجد حركات على حساب هذا العميل في هذه الفترة.")
        self.body.addWidget(self.empty_label)

        close = primary_button("إغلاق")
        close.clicked.connect(self.accept)
        self.add_footer_widget(close)
        for label in ("طباعة الكشف", "تصدير PDF", "إرسال واتساب"):
            btn = QPushButton(f"{label} — قريبًا")
            btn.setEnabled(False)
            self.add_footer_widget(btn)
        self.reload()

    def reload(self) -> None:
        try:
            self.statement = self._load(self.date_from.text().strip(), self.date_to.text().strip())
        except (LookupError, MoneyError, ValueError) as e:
            self.notice.show_message(str(e))
            return
        st = self.statement
        self.notice.show_message("")
        self.strip.setText(f"{st.customer_name} · {ltr(st.mobile)}" if st.mobile else st.customer_name)
        self.kpi_debit.setText(f"{ltr(fmt(st.debit_total))} جنيه")
        self.kpi_credit.setText(f"{ltr(fmt(st.credit_total))} جنيه")
        self.kpi_balance.setText(f"{ltr(fmt(st.closing))} جنيه")
        self.opening_label.setText(f"رصيد أول المدة: {ltr(fmt(st.opening))} جنيه")
        self.model.reset_rows([(r.date, r.doc, r.text, r.debit, r.credit, r.balance) for r in st.rows])
        self.empty_label.setVisible(not st.rows)
