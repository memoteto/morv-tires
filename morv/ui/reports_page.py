"""Reports screen: a report picker, period and account controls, and CSV or PDF export."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QPushButton,
                               QWidget)

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import income_statement, ledger, reports
from morv.infra.reports import CODE, DATE, MONEY, ReportError
from morv.ui import report_export
from morv.ui.listpage import ListPage
from morv.ui.widgets import DateEdit, muted

_INDENT = {"head": "", "line": "    ", "total": "", "grand": ""}
CUSTOM = "custom"


class _Rows(list):
    table = None
    error = ""


def _cell_text(value, kind: str) -> str:
    if kind == MONEY and type(value) is int:
        return ltr(fmt(value))
    text = "" if value is None else str(value)
    return ltr(text) if kind in (CODE, DATE) and text else text


class ReportsPage(ListPage):
    title = "التقارير"
    add_label = None               # reading and exporting only; the financial actions live in the treasury
    search_placeholder = "بحث في البنود (F2)"
    empty_message = "لا توجد قيود في هذه الفترة"
    filters_as_dropdown = True
    filters = list(income_statement.PERIODS) + [(CUSTOM, "فترة مخصصة")]
    columns = [("البند", lambda r: ""), ("المبلغ", lambda r: "")]

    def __init__(self, session):
        super().__init__(session)
        self._sync_controls()

    def extra_header(self) -> QWidget:
        self.table = None
        self._params = ("income", "", "", "", False)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.report_combo = QComboBox()
        self._fill_report_combo()
        self.account_combo = QComboBox()
        self._filled_for = None
        self.from_edit, self.to_edit = (DateEdit(optional=True, allow_future=True),
                                        DateEdit(optional=True, allow_future=True))
        self.range_labels = (muted("من"), muted("إلى"))
        self.compare_box = QCheckBox("مقارنة بالفترة السابقة")
        self.export_csv_btn = QPushButton("تصدير Excel")
        self.export_pdf_btn = QPushButton("طباعة / PDF")
        row.addWidget(muted("التقرير"))
        row.addWidget(self.report_combo)
        row.addWidget(self.account_combo)
        row.addWidget(self.range_labels[0])
        row.addWidget(self.from_edit)
        row.addWidget(self.range_labels[1])
        row.addWidget(self.to_edit)
        row.addWidget(self.compare_box)
        row.addStretch(1)
        row.addWidget(self.export_csv_btn)
        row.addWidget(self.export_pdf_btn)
        self.report_combo.currentIndexChanged.connect(lambda _i: self._controls_changed())
        self.account_combo.currentIndexChanged.connect(lambda _i: self.reload())
        self.compare_box.toggled.connect(lambda _on: self.reload())
        self.from_edit.editingFinished.connect(self.reload)
        self.to_edit.editingFinished.connect(self.reload)
        self.export_csv_btn.clicked.connect(self.export_csv)
        self.export_pdf_btn.clicked.connect(self.export_pdf)
        return host

    def _fill_report_combo(self) -> None:
        """Group headers carry no data and cannot be picked, so currentData() is always a real report."""
        for group, keys in reports.GROUPS:
            self.report_combo.addItem(group)
            head = self.report_combo.model().item(self.report_combo.count() - 1)
            head.setEnabled(False)
            head.setData(QFont(self.report_combo.font().family(), weight=QFont.Weight.Bold),
                         Qt.ItemDataRole.FontRole)
            for key in keys:
                self.report_combo.addItem("   " + reports.TITLES[key], key)
        self.report_combo.setCurrentIndex(1)          # index 0 is the first group's header

    def _key(self) -> str:
        return self.report_combo.currentData()

    def _fill_params(self, key: str) -> None:
        """Accounts for the statement, partners for the partner statement; refilled only when the kind changes."""
        kind = reports.NEEDS_PARAM[key]
        if kind == self._filled_for:
            return
        self._filled_for = kind
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for value, label in reports.param_choices(self.session.con, key):
            self.account_combo.addItem(label, value)
        default = {"account": "1110", "days": str(reports.DEFAULT_IDLE_DAYS)}.get(kind)
        self.account_combo.setCurrentIndex(max(0, self.account_combo.findData(default)))
        self.account_combo.blockSignals(False)

    def _sync_controls(self) -> None:
        key = self._key()
        if key in reports.NEEDS_PARAM:
            self._fill_params(key)
        self.account_combo.setVisible(key in reports.NEEDS_PARAM)
        self.compare_box.setVisible(key == "income")
        custom = self.filter_key == CUSTOM
        for w in (self.from_edit, self.to_edit, *self.range_labels):
            w.setVisible(custom)
        if getattr(self, "filter_combo", None) is not None:
            self.filter_combo.setVisible(key != "suspended")

    def _controls_changed(self) -> None:
        self._sync_controls()
        self.reload()

    def set_filter(self, key) -> None:
        self.filter_key = key
        self._sync_controls()
        super().set_filter(key)

    def reload(self, keep_id: int | None = None) -> None:
        # Read the controls on the UI thread: fetch runs on a worker.
        self._params = (self._key(), self.account_combo.currentData() or "", self.from_edit.text(), self.to_edit.text(),
                        self.compare_box.isChecked() and self._key() == "income")
        super().reload(keep_id)

    def can_edit(self) -> bool:
        """False, so a double click or Ctrl+N on a report line cannot open a financial dialog."""
        return False

    def can_view(self, con=None) -> bool:
        return self.session.can("reports.view", con)

    def _range(self, filter_key: str, from_text: str, to_text: str) -> tuple[str, str]:
        if filter_key == CUSTOM:
            return from_text, to_text
        return income_statement.period_range(filter_key)

    def fetch(self, con, query, filter_key, offset):
        rows = _Rows()
        if offset or not self.can_view(con):
            return rows
        key, account, from_text, to_text, compare = self._params
        try:
            start, end = self._range(filter_key, from_text, to_text)
            table = reports.build(con, key, start, end, account, compare)
        except ReportError as e:
            rows.error = str(e)
            return rows
        text = (query or "").strip()
        data = [(i, kind, cells) for i, (kind, cells) in enumerate(table.rows, start=1)]
        if text:
            data = [r for r in data if r[1] != "line" or any(text in str(c) for c in r[2])]
        rows.extend(data)
        rows.table = table
        return rows

    def _apply_columns(self, table) -> None:
        indent = table.key == "income"
        cols = []
        for i, (header, kind) in enumerate(zip(table.headers, table.kinds, strict=True)):
            pad = i == table.stretch and indent
            cols.append((header, lambda r, i=i, k=kind, pad=pad: (_INDENT[r[1]] if pad else "") + _cell_text(r[2][i], k)))
        self.model.beginResetModel()
        self.model.columns = cols
        self.model.rows = []
        self.model.endResetModel()
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(table.stretch, QHeaderView.ResizeMode.Stretch)

    def _on_fetch_done(self, rows, err, keep_id, gen) -> None:
        if rows is not None and not err and gen == self._fetch_gen and rows.table is not None:
            self._apply_columns(rows.table)
        super()._on_fetch_done(rows, err, keep_id, gen)
        if rows is None or err or gen != self._fetch_gen:
            return
        self.table = rows.table
        self.export_csv_btn.setEnabled(self.table is not None)
        self.export_pdf_btn.setEnabled(self.table is not None)
        if rows.error:
            self.empty_label.setText(rows.error)
            self.empty_label.setVisible(True)
            self.count_label.setText(rows.error)

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على التقارير"
        locked = ledger.locked_through(self.session.con)
        note = f"الفترة مقفلة حتى {ltr(locked)}" if locked else "لا توجد فترة مقفلة"
        head = f"{self.table.title} · {self.table.subtitle} · " if self.table else ""
        return f"{head}{note} · من القيود المرحّلة فقط"

    def build_preview(self, row_id: int) -> None:
        row = next((r for r in self.model.rows if r[0] == row_id), None)
        if row is None or self.table is None:
            return
        cells = row[2]
        name = next((c for c, k in zip(cells, self.table.kinds, strict=True) if k == "text" and c), "")
        self.heading(str(name) or self.table.title, ltr(str(cells[0])) if cells and cells[0] else "")
        self.summary_section("التفاصيل", "cash")
        self.summary_details([(h, _cell_text(v, k)) for h, v, k in zip(self.table.headers, cells, self.table.kinds,
                                                                       strict=True)])

    def _export_table(self):
        """The shown table, or the full one when the screen stops at a row limit (the journal)."""
        key, account, from_text, to_text, compare = self._params
        if self.table.key != "journal":
            return self.table
        start, end = self._range(self.filter_key, from_text, to_text)
        return reports.build(self.session.con, key, start, end, account, compare, full=True)

    def export_csv_to(self, path: str) -> None:
        reports.to_csv(self._export_table(), path)

    def export_pdf_to(self, path: str) -> None:
        report_export.export_pdf(self._export_table(), path)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "تصدير التقرير", f"{self.table.title}.csv", "Excel (*.csv)")
        if path:
            self.export_csv_to(path)

    def export_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "حفظ التقرير", f"{self.table.title}.pdf", "PDF (*.pdf)")
        if path:
            self.export_pdf_to(path)
