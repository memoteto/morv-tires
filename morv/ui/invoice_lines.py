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
        self._sale_tax_header = None
        self.total_column = len(headings) - 2
        self.compact_discount = purchase
        self.compact_freight = purchase
        self._discount_budget = 0
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

    def purchase_row_layout(self, widget):
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        primary = QWidget()
        secondary = QWidget()
        widget._invoice_primary = self.row_layout(primary)
        widget._invoice_secondary = self.row_layout(secondary)
        widget._invoice_discount_label = muted("خصم")
        widget._invoice_freight_label = muted("شحن")
        widget._invoice_secondary.addWidget(widget._invoice_discount_label)
        widget._invoice_secondary.addWidget(widget._invoice_freight_label)
        widget._invoice_secondary.addStretch(1)
        outer.addWidget(primary)
        outer.addWidget(secondary)
        secondary.hide()
        return widget._invoice_primary

    def register_purchase_row(self, widget):
        primary = widget._invoice_primary
        widget._invoice_cells = tuple(primary.itemAt(i).widget() for i in range(primary.count()))
        self._place_adjustments(widget)

    def _place_adjustments(self, widget):
        primary, secondary = widget._invoice_primary, widget._invoice_secondary
        discount, freight = widget._invoice_cells[5:7]
        for column, cell, label, compact in (
            (5, discount, widget._invoice_discount_label, self.compact_discount),
            (6, freight, widget._invoice_freight_label, self.compact_freight),
        ):
            label.setVisible(compact)
            if compact == (secondary.indexOf(cell) >= 0):
                continue
            focus = cell.focusWidget()
            restore_focus = focus is not None and focus.hasFocus()
            if compact:
                primary.removeWidget(cell)
                secondary.insertWidget(secondary.indexOf(label) + 1, cell, 0, Qt.AlignmentFlag.AlignTop)
            else:
                secondary.removeWidget(cell)
                primary.insertWidget(column, cell, 0, Qt.AlignmentFlag.AlignTop)
            cell.show()
            if restore_focus:
                focus.setFocus()
        secondary.parentWidget().setVisible(self.compact_discount or self.compact_freight)

    def set_sale_tax_visible(self, enabled, rows):
        """Insert or remove the optional read-only sale VAT column in shared geometry."""
        if self.purchase:
            raise ValueError("Sale VAT column is not a purchase adjustment")
        if self._sale_tax_header is None and not enabled:
            return
        if self._sale_tax_header is None:
            self._sale_tax_header = muted("الضريبة")
            self._sale_tax_header.setWordWrap(False)
            self._sale_tax_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header = self._sale_tax_header
        layout = self.headers[0].parentWidget().layout()
        if enabled and header not in self.headers:
            self.headers.insert(5, header)
            layout.insertWidget(5, header)
            header.show()
        elif not enabled and header in self.headers:
            self.headers.remove(header)
            layout.removeWidget(header)
            header.hide()
        for row, cell in rows:
            layout = row.layout()
            if enabled and layout.indexOf(cell) < 0:
                layout.insertWidget(5, cell, 0, Qt.AlignmentFlag.AlignTop)
                cell.show()
            elif not enabled and layout.indexOf(cell) >= 0:
                layout.removeWidget(cell)
                cell.hide()
        self.total_column = len(self.headers) - 2

    @property
    def sale_tax_visible(self):
        return self._sale_tax_header is not None and self._sale_tax_header in self.headers

    def set_discount_budget(self, width):
        if width != self._discount_budget:
            self._discount_budget = width
            self.sync_columns()

    @staticmethod
    def _text_width(widget, text):
        return widget.fontMetrics().horizontalAdvance(text)

    def _align_row_height(self, cells):
        reference = cells[3].layout().itemAt(0).widget() if self.purchase else cells[3]
        reference.ensurePolished()
        height = max(reference.minimumHeight(), reference.minimumSizeHint().height(),
                     reference.sizeHint().height())
        # Keep single-line labels centered on the first input, even in taller rows.
        labels = (cells[0], cells[self.total_column])
        if self.purchase:
            labels += (cells[6].layout().itemAt(1).widget(),)  # calculated freight share
        elif self.sale_tax_visible:
            labels += (cells[5],)
        for label in labels:
            if label.minimumHeight() != height or label.maximumHeight() != height:
                label.setFixedHeight(height)
        # A wrapped name may grow naturally; its icon remains centered beside it.
        if cells[1].minimumHeight() != height:
            cells[1].setMinimumHeight(height)

    def sync_columns(self):
        cells_by_row = [self.headers]
        purchase_rows = []
        for index in range(self.rows.count()):
            widget = self.rows.itemAt(index).widget()
            if self.purchase:
                if hasattr(widget, "_invoice_cells"):
                    purchase_rows.append(widget)
                    cells_by_row.append(widget._invoice_cells)
            else:
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
        total = self.total_column
        widths[total] = max(widths[total], self._text_width(self.headers[total], "999,999.99") + 4)
        if self.sale_tax_visible:
            widths[5] = max(widths[5], self._text_width(self.headers[5], "999,999.99") + 4)
        widths[-1] = LAYOUT["invoice_line_delete_width"]

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
            if self.purchase:
                discount = cells[5].layout()
                editor = discount.itemAt(0).widget()
                toggle = discount.itemAt(1).widget()
                values = ("999,999.99", editor.placeholderText(), editor.text()[:17])
                editor_width = max(self._text_width(editor, value) for value in values) + inset
                widths[5] = max(widths[5], editor_width + toggle.minimumWidth() + discount.spacing())
                freight = cells[6].layout()
                edit, value = freight.itemAt(0).widget(), freight.itemAt(1).widget()
                freight_width = max(self._text_width(edit, sample)
                                    for sample in ("999,999.99", edit.placeholderText(), edit.text()[:17])) + inset
                widths[6] = max(widths[6], freight_width, self._text_width(value, value.text()) + 4)
            if self.sale_tax_visible:
                widths[5] = max(widths[5], self._text_width(cells[5], cells[5].text()) + 4)
            widths[total] = max(widths[total], self._text_width(cells[total], cells[total].text()) + 4)

        name_min = max(widths[1], max(widths[3], widths[4], widths[total])
                       + LAYOUT["invoice_line_name_icon_space"])
        if self.purchase:
            wide_min = (sum(widths) - widths[1] + name_min + 2 * LAYOUT["invoice_line_margin"]
                        + (len(widths) - 1) * LAYOUT["invoice_line_column_gap"])
            self.compact_freight = wide_min > self._discount_budget
            without_freight = wide_min - widths[6] - LAYOUT["invoice_line_column_gap"]
            self.compact_discount = without_freight > self._discount_budget
            self.headers[5].setVisible(not self.compact_discount)
            self.headers[6].setVisible(not self.compact_freight)
            for widget in purchase_rows:
                self._place_adjustments(widget)
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
                freight = cells[6].layout()
                for index in (0, 1):
                    control = freight.itemAt(index).widget()
                    if control.minimumWidth() != widths[6] or control.maximumWidth() != widths[6]:
                        control.setFixedWidth(widths[6])

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "_timer"):
            self._timer.start(0)
