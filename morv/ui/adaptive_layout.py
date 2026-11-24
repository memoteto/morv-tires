"""Shared adaptive card layout and page chrome without business dependencies."""
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QScrollArea,
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
    def __init__(self, cards, *, minimum_width, max_columns=2, column_weights=None, fill_height=False):
        super().__init__()
        self.cards = tuple(cards)
        self.minimum_width = minimum_width
        self.max_columns = max_columns
        self.column_weights = column_weights
        self.fill_height = fill_height
        self.columns = 0
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(LAYOUT["pane_gap"])
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
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest,
                            QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._timer.start(0)
        return super().eventFilter(watched, event)

    def _reflow(self):
        gap = LAYOUT["pane_gap"]
        minimum = max((max(self.minimum_width, c.minimumWidth(), c.minimumSizeHint().width())
                       for c in self.cards), default=self.minimum_width)
        width = self.viewport().width()
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


class AdaptiveFields(QWidget):
    def __init__(self, fields):
        super().__init__()
        self.fields = tuple(fields)
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
        return max((max(LAYOUT["form_field_min"], w.minimumWidth(), w.minimumSizeHint().width())
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


class PageFrame(QWidget):
    header_changed = Signal()

    def __init__(self, back_text, back_icon):
        super().__init__()
        root = self._root = QVBoxLayout(self)
        self._header_host = None
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

    def set_header(self, title, description="", *, back=False):
        self.title.setText(title)
        self.description.setText(description)
        self.description.setVisible(bool(description))
        self.back.setVisible(back)
        self.header_changed.emit()
