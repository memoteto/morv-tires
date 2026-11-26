"""Shared invoice line columns with aligned cells and flexible item names."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from morv.ui.tokens import LAYOUT
from morv.ui.widgets import muted


def wrapping_line_label(text=""):
    label = QLabel(text)
    label.setWordWrap(True)
    policy = label.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    return label


class InvoiceLineTable(QWidget):
    """Only the name column stretches; all other columns share measured widths."""

    def __init__(self, headings, *, purchase=False):
        super().__init__()
        self.purchase = purchase
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(LAYOUT["invoice_line_header_gap"])
        header = QWidget()
        head = self.row_layout(header, header=True)
        self.headers = []
        for column, title in enumerate(headings):
            label = muted(title)
            label.setWordWrap(False)
            if column != 1:
                label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(label, 1 if column == 1 else 0)
            self.headers.append(label)
        outer.addWidget(header)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(LAYOUT["invoice_line_row_gap"])
        outer.addLayout(self.rows, 1)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.sync_columns)
        self.sync_columns()

    @staticmethod
    def row_layout(widget, *, header=False):
        layout = QHBoxLayout(widget)
        margin = LAYOUT["invoice_line_margin"]
        vertical = 0 if header else LAYOUT["invoice_line_row_padding"]
        layout.setContentsMargins(margin, vertical, margin, vertical)
        layout.setSpacing(LAYOUT["invoice_line_column_gap"])
        return layout

    @staticmethod
    def _text_width(widget, text):
        return widget.fontMetrics().horizontalAdvance(text)

    def _align_row_height(self, cells):
        reference = cells[3].layout().itemAt(0).widget() if self.purchase else cells[3]
        reference.ensurePolished()
        height = max(reference.minimumHeight(), reference.minimumSizeHint().height(),
                     reference.sizeHint().height())
        # Keep single-line labels centered on the first input, even in taller rows.
        for label in (cells[0], cells[5]):
            if label.minimumHeight() != height or label.maximumHeight() != height:
                label.setFixedHeight(height)
        # A wrapped name may grow naturally; its icon remains centered beside it.
        if cells[1].minimumHeight() != height:
            cells[1].setMinimumHeight(height)

    def sync_columns(self):
        cells_by_row = [self.headers]
        for index in range(self.rows.count()):
            widget = self.rows.itemAt(index).widget()
            layout = widget.layout() if widget is not None else None
            if layout is not None and layout.count() == len(self.headers):
                cells_by_row.append([layout.itemAt(i).widget() for i in range(layout.count())])

        widths = [self._text_width(label, label.text()) + 2 for label in self.headers]
        widths[0] = max(widths[0], self._text_width(self.headers[0], "999") + 2)
        inset = LAYOUT["invoice_line_edit_inset"]
        for column in (3, 4):
            samples = ("9999", "WWYY") if self.purchase and column == 4 else ("999,999.99",)
            widths[column] = max(widths[column], max(self._text_width(self.headers[column], sample)
                                                     for sample in samples) + inset)
        widths[5] = max(widths[5], self._text_width(self.headers[5], "999,999.99") + 4)
        widths[6] = LAYOUT["invoice_line_delete_width"]

        for cells in cells_by_row[1:]:
            widths[0] = max(widths[0], self._text_width(cells[0], cells[0].text()) + 2)
            widths[2] = max(widths[2], cells[2].minimumSizeHint().width())
            for column in (3, 4):
                cell = cells[column]
                editor = cell.layout().itemAt(0).widget() if self.purchase and column == 3 else cell
                # A generic line edit's minimum hint reserves room for unrelated longer text.
                # Use its own font, placeholder, normal value and themed horizontal inset instead.
                samples = ("9999", "WWYY") if self.purchase and column == 4 else ("999,999.99",)
                values = (*samples, editor.placeholderText(), editor.text()[:17])
                widths[column] = max(widths[column], max(self._text_width(editor, value)
                                                         for value in values) + inset)
            widths[5] = max(widths[5], self._text_width(cells[5], cells[5].text()) + 4)

        name_min = max(widths[1], max(widths[2:6]) + LAYOUT["invoice_line_name_icon_space"])
        for cells in cells_by_row:
            for column, cell in enumerate(cells):
                if column == 1:
                    if cell.minimumWidth() != name_min:
                        cell.setMinimumWidth(name_min)
                elif cell.minimumWidth() != widths[column] or cell.maximumWidth() != widths[column]:
                    cell.setFixedWidth(widths[column])
        for cells in cells_by_row[1:]:
            self._align_row_height(cells)
        if self.purchase:
            for cells in cells_by_row[1:]:
                # The cost editor sits inside a vertical cell; override its generic hint as well.
                editor = cells[3].layout().itemAt(0).widget()
                if editor.minimumWidth() != widths[3] or editor.maximumWidth() != widths[3]:
                    editor.setFixedWidth(widths[3])

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "_timer"):
            self._timer.start(0)
