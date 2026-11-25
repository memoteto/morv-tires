"""Shared adaptive card layout and page chrome without business dependencies."""
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from morv.ui.tokens import LAYOUT
from morv.ui.widgets import Notice, action_row, icon_button, muted, primary_button


def scroll_content(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    area.setWidget(content)
    return area


class AdaptiveCards(QScrollArea):
    def __init__(self, cards, *, minimum_width, max_columns=2, column_weights=None, fill_height=False,
                 spacing=None, content_margins=(0, 0, 0, 0)):
        super().__init__()
        self.cards = tuple(cards)
        self.minimum_width = minimum_width
        self.max_columns = max_columns
        self.column_weights = column_weights
        self.fill_height = fill_height
        self.columns = 0
        self._reflowing = False
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(*content_margins)
        self.grid.setSpacing(LAYOUT["pane_gap"] if spacing is None else spacing)
        if not fill_height:
            self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        for card in self.cards:
            card.setMinimumWidth(minimum_width)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setWidget(self.content)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reflow)
        self.viewport().installEventFilter(self)
        self.content.installEventFilter(self)
        self._reflow()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show,
                    QEvent.Type.FontChange, QEvent.Type.StyleChange):
            # A deferred pass catches secondary changes caused by scrollbars and style updates.
            self._timer.start(0)
            if self.isVisible() and (
                (watched is self.viewport() and kind in (QEvent.Type.Resize, QEvent.Type.Show))
                or (watched is self.content and kind == QEvent.Type.LayoutRequest)
            ):
                self._reflow()
        return super().eventFilter(watched, event)

    def _reflow(self):
        if self._reflowing:
            return
        self._reflowing = True
        try:
            self._apply_columns()
        finally:
            self._reflowing = False

    def _apply_columns(self):
        gap = self.grid.horizontalSpacing()
        minimum = max((max(self.minimum_width, c.minimumWidth(), c.minimumSizeHint().width())
                       for c in self.cards), default=self.minimum_width)
        margins = self.grid.contentsMargins()
        width = max(0, self.viewport().width() - margins.left() - margins.right())
        if self.column_weights is None:
            columns = max(1, min(self.max_columns, len(self.cards), (width + gap) // (minimum + gap)))
        else:
            # A weighted pair has different natural minima, not two copies of the wider one.
            needed = sum(max(self.minimum_width, c.minimumWidth(), c.minimumSizeHint().width())
                         for c in self.cards) + gap * (len(self.cards) - 1)
            columns = len(self.cards) if width >= needed else 1
        if columns == self.columns:
            return
        for card in self.cards:
            self.grid.removeWidget(card)
        for col in range(self.max_columns):
            weight = self.column_weights[col] if self.column_weights and columns > 1 else 1
            self.grid.setColumnStretch(col, weight if col < columns else 0)
        if self.fill_height:
            for row in range(len(self.cards)):
                self.grid.setRowStretch(row, int(row == (len(self.cards) - 1) // columns))
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        self.columns = columns
        self.grid.invalidate()
        self.grid.activate()


class AdaptiveSplit(QWidget):
    """Two panes with a single-pane fallback and preserved widget state."""

    mode_changed = Signal(bool)

    def __init__(self, primary, detail, *, detail_max=None, detail_first=False):
        super().__init__()
        self.primary, self.detail = primary, detail
        self.detail_max = LAYOUT["preview_max"] if detail_max is None else detail_max
        self.compact = False
        self._detail_open = False
        self._state = None
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(LAYOUT["pane_gap"])
        self.row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        panes = ((detail, 2), (primary, 3)) if detail_first else ((primary, 3), (detail, 2))
        for pane, stretch in panes:
            self.row.addWidget(pane, stretch)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reflow)
        for pane in (primary, detail):
            pane.installEventFilter(self)
        self._timer.start(0)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._timer.start(0)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._timer.start(0)

    def show_detail(self):
        self._detail_open = True
        self._reflow()

    def show_primary(self):
        self._detail_open = False
        self._reflow()

    def _reflow(self):
        primary_min = max(LAYOUT["list_min"], self.primary.minimumSizeHint().width())
        detail_min = max(LAYOUT["preview_min"], self.detail.minimumSizeHint().width())
        compact = self.width() < primary_min + detail_min + LAYOUT["pane_gap"]
        state = (compact, self._detail_open if compact else False, primary_min, detail_min)
        if state == self._state:
            return
        self._state = state
        self.compact = compact
        self.primary.setMinimumWidth(0 if compact else primary_min)
        self.detail.setMinimumWidth(0 if compact else detail_min)
        self.detail.setMaximumWidth(16777215 if compact else max(detail_min, self.detail_max))
        self.primary.setVisible(not compact or not self._detail_open)
        self.detail.setVisible(not compact or self._detail_open)
        self.mode_changed.emit(compact)


class AdaptiveTableColumns(QObject):
    """Opt-in measured column allocation for tables with flexible text and compact controls."""

    def __init__(self, table, text_weights, controls):
        super().__init__(table)
        self.table = table
        self.text_weights = dict(text_weights)
        self.controls = dict(controls)
        header = table.horizontalHeader()
        # Qt's automatic minimum can be wider than an entire check-state cell.
        header.setMinimumSectionSize(1)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        rows = table.verticalHeader()
        rows.setMinimumSectionSize(LAYOUT["adaptive_table_row_min"])
        rows.setDefaultSectionSize(LAYOUT["adaptive_table_row_min"])
        table.setWordWrap(True)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.reflow)
        table.installEventFilter(self)
        table.viewport().installEventFilter(self)
        self.schedule()

    def schedule(self):
        self.timer.start(0)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show,
                            QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.schedule()
        return super().eventFilter(watched, event)

    def _headers(self, compact):
        for col, (label, icon) in self.controls.items():
            item = self.table.horizontalHeaderItem(col)
            text = "" if compact else label
            if item.text() != text or item.icon().isNull() == compact:
                item.setText(text)
                item.setIcon(icon if compact else QIcon())

    def _natural_width(self, col):
        return max(1, self.table.sizeHintForColumn(col),
                   self.table.horizontalHeader().sectionSizeHint(col)) + 1

    def _weighted_widths(self, budget, minima):
        # Honor measured minima before dividing the remaining width by the reference weights.
        if budget <= sum(minima.values()):
            return dict(minima)
        result = {}
        pending = list(self.text_weights)
        while pending:
            total_weight = sum(self.text_weights[c] for c in pending)
            constrained = [c for c in pending
                           if budget * self.text_weights[c] < minima[c] * total_weight]
            if not constrained:
                for col in pending[:-1]:
                    result[col] = budget * self.text_weights[col] // total_weight
                result[pending[-1]] = budget - sum(result[c] for c in pending[:-1])
                break
            for col in constrained:
                result[col] = minima[col]
                budget -= minima[col]
                pending.remove(col)
        return result

    def reflow(self):
        if not self.table.isVisible():
            return
        width = self.table.viewport().width()
        if width <= 0:
            return
        header = self.table.horizontalHeader()
        self._headers(False)
        text_need = {c: self._natural_width(c) for c in self.text_weights}
        controls = {c: max(LAYOUT["adaptive_table_control_target"], self._natural_width(c))
                    for c in self.controls}
        if sum(text_need.values()) + sum(controls.values()) > width:
            controls = {c: self._natural_width(c) for c in self.controls}
        if sum(text_need.values()) + sum(controls.values()) > width:
            self._headers(True)
            controls = {c: self._natural_width(c) for c in self.controls}
        budget = width - sum(controls.values())
        minima = text_need
        if sum(minima.values()) > budget:
            minima = {c: max(1, header.sectionSizeHint(c)) + 1 for c in self.text_weights}
        widths = self._weighted_widths(budget, minima)
        widths.update(controls)
        for col, size in widths.items():
            if self.table.columnWidth(col) != size:
                self.table.setColumnWidth(col, size)
        self.table.resizeRowsToContents()


class AdaptiveFields(QWidget):
    def __init__(self, fields, *, minimum_width=None):
        super().__init__()
        self.fields = tuple(fields)
        self.minimum_width = LAYOUT["form_field_min"] if minimum_width is None else minimum_width
        self.columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(LAYOUT["pane_gap"])
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reflow)
        self._reflow()

    def _field_minimum(self):
        return max((max(self.minimum_width, w.minimumWidth(), w.minimumSizeHint().width())
                    for w in self.fields), default=0)

    def minimumSizeHint(self):
        # Permit the parent card to shrink before rearranging the same inputs.
        return QSize(self._field_minimum(), self.grid.minimumSize().height())

    def event(self, event):
        if hasattr(self, "_timer") and event.type() in (
                QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._timer.start(0)
        return super().event(event)

    def _reflow(self):
        columns = 2 if len(self.fields) > 1 and self.width() >= (
            2 * self._field_minimum() + LAYOUT["pane_gap"]) else 1
        if columns == self.columns:
            return
        for widget in self.fields:
            self.grid.removeWidget(widget)
        for col in range(2):
            self.grid.setColumnStretch(col, int(col < columns))
        for i, widget in enumerate(self.fields):
            self.grid.addWidget(widget, i // columns, i % columns)
        self.columns = columns
        self.grid.invalidate()
        self.updateGeometry()


class FormBody(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(LAYOUT["pane_gap"])
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.body, 1)
        self.footer = QWidget()
        self.footer.setMinimumHeight(LAYOUT["page_footer_min"])
        actions = QVBoxLayout(self.footer)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(LAYOUT["page_back_gap"])
        self.notice = Notice()
        self.save_btn = primary_button("حفظ", "save")
        self.save_btn.clicked.connect(self.save)
        actions.addWidget(self.notice)
        actions.addLayout(action_row(self.save_btn))
        root.addWidget(self.footer)


class HeaderPlacement:
    def __init__(self, header, root):
        self.header = header
        self._root = root
        self._header_host = None

    def header_fits(self, width: int, height: int) -> bool:
        layout = self.header.layout()
        minimum = layout.minimumSize().expandedTo(self.header.minimumSize())
        if width < minimum.width() or height < minimum.height():
            return False
        needed = layout.totalHeightForWidth(width)
        return max(minimum.height(), needed) <= height

    def set_header_host(self, host=None):
        """Move the same header between the page and an optional shell layout."""
        if host is self._header_host:
            return
        if self._header_host is None:
            self._root.removeWidget(self.header)
        else:
            self._header_host.removeWidget(self.header)
        self._header_host = host
        if host is None:
            self._root.insertWidget(0, self.header)
        else:
            host.addWidget(self.header)
        self.header.show()


class PageFrame(QWidget):
    header_changed = Signal()

    def __init__(self, back_text, back_icon):
        super().__init__()
        root = self._root = QVBoxLayout(self)
        root.setContentsMargins(LAYOUT["page_margin"], LAYOUT["page_margin_y"],
                                LAYOUT["page_margin"], LAYOUT["page_margin_y"])
        root.setSpacing(LAYOUT["pane_gap"])
        self.header = QWidget()
        self.header.setMinimumHeight(LAYOUT["page_header_min"])
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(LAYOUT["page_back_gap"])
        self.back = icon_button("", back_icon)
        self.back.setIconSize(QSize(LAYOUT["page_back_icon"], LAYOUT["page_back_icon"]))
        self.back.setMinimumSize(LAYOUT["page_back_min"], LAYOUT["page_back_min"])
        self.back.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.back.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.back.setToolTip(back_text)
        self.back.setAccessibleName(back_text)
        head.addWidget(self.back, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.title.setWordWrap(True)
        self.description = muted("")
        titles.addWidget(self.title)
        titles.addWidget(self.description)
        head.addLayout(titles, 1)
        root.addWidget(self.header)
        self.header_placement = HeaderPlacement(self.header, root)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.body, 1)
        self.footer = QWidget()
        self.footer.setMinimumHeight(LAYOUT["page_footer_min"])
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.footer)
        self.footer.hide()

    def header_fits(self, width: int, height: int) -> bool:
        return self.header_placement.header_fits(width, height)

    def set_header_host(self, host=None):
        self.header_placement.set_header_host(host)

    def set_header(self, title, description="", *, back=False):
        self.title.setText(title)
        self.description.setText(description)
        self.description.setVisible(bool(description))
        self.back.setVisible(back)
        self.header_changed.emit()
