"""Shared list-and-preview skeleton for the master-data screens."""
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStackedLayout, QTableView,
                               QVBoxLayout, QWidget)

from morv.core.text import RLM
from morv.infra.db.connection import ClosedDatabase
from morv.ui import icons
from morv.ui.tokens import LAYOUT
from morv.ui.widgets import muted, primary_button

PAGE = 300


class TupleModel(QAbstractTableModel):
    def __init__(self, columns: list, icon_for=None, *, column_alignments=None):
        super().__init__()
        self.columns = columns
        if column_alignments is not None and len(column_alignments) != len(columns):
            raise ValueError("Column alignments do not match table columns")
        self.column_alignments = tuple(column_alignments) if column_alignments is not None else None
        self.icon_for = icon_for
        self.icon_color = "#8792A3"
        self._icon_cache: dict = {}
        self.rows: list = []

    def reset_rows(self, rows: list) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def append_rows(self, rows: list) -> None:
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()

    def rowCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None) -> int:
        return len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.columns[index.column()][1](self.rows[index.row()])
        if role == Qt.ItemDataRole.TextAlignmentRole and self.column_alignments is not None:
            return self.column_alignments[index.column()]
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0 and self.icon_for:
            name = self.icon_for(self.rows[index.row()])
            if name not in self._icon_cache:
                from morv.ui import icons
                self._icon_cache[name] = icons.icon(name, self.icon_color, 18)
            return self._icon_cache[name]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.columns[section][0]
            if role == Qt.ItemDataRole.TextAlignmentRole and self.column_alignments is not None:
                return self.column_alignments[section]
        return None


def _summary_cell_text(value: object, header: str, *, one_line_dates: bool = False) -> str:
    text = str(value)
    if not one_line_dates and header == "التاريخ" and re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
        return text.replace(" ", "\n", 1)
    return text


class SummaryAdaptiveTable(QWidget):
    def __init__(self, table: QTableView, cards: QWidget, row_count: int):
        super().__init__()
        self.setObjectName("summaryAdaptiveTable")
        self.table = table
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setObjectName("summaryCardsScroll")
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_scroll.setWidget(cards)
        self.cards_scroll.setFixedHeight(min(
            LAYOUT["summary_cards_max_height"],
            max(LAYOUT["summary_cards_min_height"], row_count * LAYOUT["summary_cards_row_height"])))
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(table)
        self.stack.addWidget(self.cards_scroll)
        self._adapt()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adapt()

    def _adapt(self) -> None:
        use_cards = self.width() < LAYOUT["summary_table_cards_below"]
        target = self.cards_scroll if use_cards else self.table
        if self.stack.currentWidget() is not target:
            self.stack.setCurrentWidget(target)
        height = target.height()
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)


class SummaryDetailRow(QWidget):
    def __init__(self, key: str, value: str):
        super().__init__()
        self.setObjectName("summaryDetailRow")
        self.key_label = muted(key)
        self.key_label.setWordWrap(False)
        self.value_label = QLabel(RLM + (value or "—"))
        self.value_label.setWordWrap(True)
        self.value_label.setMinimumWidth(0)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value_label.setToolTip(value or "—")
        self.row = QGridLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.row.setColumnStretch(1, 1)
        self.row.addWidget(self.key_label, 0, 0)
        self.row.addWidget(self.value_label, 0, 1)
        self._stacked = False
        self._adapt()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adapt()

    def _adapt(self) -> None:
        width = self.width()
        required = self.key_label.fontMetrics().horizontalAdvance(self.key_label.text()) + 8
        key_width = min(LAYOUT["field_label_max"],
                        max(LAYOUT["summary_field_label_min"], required))
        value_width = max(0, width - key_width - 8)
        text_width = self.value_label.fontMetrics().horizontalAdvance(self.value_label.text())
        stacked = (width < LAYOUT["summary_field_stack_below"]
                   or required > LAYOUT["field_label_max"]
                   or value_width < LAYOUT["summary_field_value_min"]
                   or text_width > value_width * 2)
        if stacked != self._stacked:
            self.row.removeWidget(self.value_label)
            if stacked:
                self.row.addWidget(self.value_label, 1, 0, 1, 2)
            else:
                self.row.addWidget(self.value_label, 0, 1)
            self._stacked = stacked
        self.row.setSpacing(5 if stacked else 8)
        self.row.setColumnMinimumWidth(0, 0 if stacked else key_width)
        self.key_label.setMinimumWidth(0 if stacked else key_width)
        self.key_label.setMaximumWidth(16777215 if stacked else key_width)


class ListPage(QWidget):
    columns: list = []
    title = ""
    add_label = "جديد"
    add_icon = "plus"
    row_icon = None            # row -> icon name, for the name column
    filters: list = []          # [(key, title)]; empty means no filters
    search_placeholder = "بحث"

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.filter_key = self.filters[0][0] if self.filters else None
        self.offset = 0
        self._fetch_thread: QThread | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(LAYOUT["page_margin"], 16, LAYOUT["page_margin"], 16)
        root.setSpacing(LAYOUT["pane_gap"])

        head = QHBoxLayout()
        self.count_label = muted("")
        head.addWidget(self.count_label, 1)
        self.add_btn = primary_button(self.add_label, self.add_icon)
        self.add_btn.clicked.connect(lambda: self.open_dialog(None))
        self.add_btn.setEnabled(self.can_edit())
        head.addWidget(self.add_btn)
        root.addLayout(head)
        extra = self.extra_header()
        if extra is not None:
            root.addWidget(extra)

        body = QHBoxLayout()
        body.setSpacing(LAYOUT["pane_gap"])
        self.list_panel = QWidget()
        self.list_panel.setMinimumWidth(0)
        left = QVBoxLayout(self.list_panel)
        left.setContentsMargins(0, 0, 0, 0)
        self.tools_layout = QGridLayout()
        self.tools_layout.setHorizontalSpacing(8)
        self.tools_layout.setVerticalSpacing(8)
        self._filter_layout_state = None
        tools = self.tools_layout
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.search_placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", "#8792A3", 16), QLineEdit.ActionPosition.LeadingPosition)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._debounced_reload)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        tools.addWidget(self.search, 0, 0)
        self.filter_buttons = {}
        for key, label in self.filters:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("segment")
            b.setChecked(key == self.filter_key)
            b.clicked.connect(lambda _c=False, k=key: self.set_filter(k))
            self.filter_buttons[key] = b
            tools.addWidget(b, 0, len(self.filter_buttons))
        tools.setColumnStretch(0, 1)
        left.addLayout(tools)

        self.model = TupleModel(self.columns, self.row_icon)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # the name column stretches
        self.view.horizontalHeader().setMinimumSectionSize(90)
        self.view.setIconSize(QSize(18, 18))
        self.view.clicked.connect(lambda idx: self.show_preview(self.model.rows[idx.row()]))
        self.view.doubleClicked.connect(lambda idx: self.open_dialog(self.model.rows[idx.row()][0]) if self.can_edit() else None)
        left.addWidget(self.view, 1)
        self.more_btn = QPushButton("تحميل المزيد")
        self.more_btn.clicked.connect(self.load_more)
        self.more_btn.hide()
        self.empty_label = muted("لا توجد نتائج مطابقة.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        left.addWidget(self.empty_label)
        left.addWidget(self.more_btn, 0, Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.list_panel, 3)

        # Preview pane, hidden until a row is clicked
        self.preview = QFrame()
        self.preview.setObjectName("card")
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pv = QVBoxLayout(self.preview)
        pv.setContentsMargins(LAYOUT["preview_padding"], 12, LAYOUT["preview_padding"], 16)
        # Fixed header: identity stays visible while only the details scroll.
        top = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(4)
        self.preview_title = QLabel("المعاينة")
        self.preview_title.setObjectName("cardTitle")
        self.preview_title.setWordWrap(True)
        self.preview_subtitle = muted("")
        self.preview_subtitle.hide()
        header_text.addWidget(self.preview_title)
        header_text.addWidget(self.preview_subtitle)
        top.addLayout(header_text, 1)
        self.preview_close = QPushButton("إغلاق")
        self.preview_close.clicked.connect(self.hide_preview)
        top.addWidget(self.preview_close, 0, Qt.AlignmentFlag.AlignTop)
        pv.addLayout(top)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("previewScroll")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_body = QWidget()
        self.preview_layout = QVBoxLayout(self.preview_body)
        self.preview_layout.setContentsMargins(
            LAYOUT["preview_scroll_gutter"], 0, LAYOUT["preview_scroll_end_gap"], 0)
        self.preview_layout.setSpacing(LAYOUT["field_spacing"])
        self.preview_scroll.setWidget(self.preview_body)
        pv.addWidget(self.preview_scroll, 1)

        # Fixed footer: actions are separate from the scrollable details.
        self.preview_footer = QWidget()
        self.preview_footer_layout = QVBoxLayout(self.preview_footer)
        self.preview_footer_layout.setContentsMargins(0, 8, 0, 0)
        self.preview_footer_layout.setSpacing(8)
        self.preview_footer.hide()
        pv.addWidget(self.preview_footer)
        self.preview.hide()
        body.addWidget(self.preview, 1)
        root.addLayout(body, 1)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.open_dialog(None) if self.can_edit() else None)
        QShortcut(QKeySequence("Esc"), self, activated=self.hide_preview)
        QShortcut(QKeySequence("F2"), self, activated=self.search.setFocus)
        self.current_id = None
        self._action_groups = []
        self.reload()
        self._update_responsive_layout()

    # ---------- What a subclass defines ----------
    def can_edit(self) -> bool:
        return True

    def fetch(self, query: str, filter_key, offset: int) -> list:
        raise NotImplementedError

    def total(self) -> str:
        return ""

    def build_preview(self, row_id: int) -> None:
        raise NotImplementedError

    def open_dialog(self, row_id: int | None) -> None:
        raise NotImplementedError

    def extra_header(self) -> QWidget | None:
        """A widget placed between the add-button row and the search/table (e.g. KPI cards). None by default."""
        return None

    # ---------- Responsive layout ----------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "current_id"):
            self._update_responsive_layout()

    def _update_responsive_layout(self) -> None:
        available = max(0, self.width() - 2 * LAYOUT["page_margin"])
        compact = available < LAYOUT["list_min"] + LAYOUT["preview_min"] + LAYOUT["pane_gap"]
        preview_open = self.current_id is not None and not self.preview.isHidden()
        self.list_panel.setVisible(not (compact and preview_open))
        if compact and preview_open:
            self.preview.setMinimumWidth(0)
            self.preview.setMaximumWidth(16777215)
            preview_width = available
        else:
            preview_width = min(
                LAYOUT["preview_max"],
                max(LAYOUT["preview_min"], available * LAYOUT["preview_share_pct"] // 100),
                max(LAYOUT["preview_min"], available - LAYOUT["list_min"] - LAYOUT["pane_gap"]),
            )
            self.preview.setFixedWidth(preview_width)
        list_width = (available if not preview_open or compact else
                      max(0, available - preview_width - LAYOUT["pane_gap"]))
        self._arrange_filters(list_width)
        self._arrange_actions(max(0, preview_width - 2 * LAYOUT["preview_padding"]))

    def _arrange_filters(self, available: int) -> None:
        buttons = list(self.filter_buttons.values())
        needed = LAYOUT["search_min"] + sum(b.sizeHint().width() for b in buttons) + 8 * len(buttons)
        wrapped = bool(buttons) and available < needed
        cols = (max(1, min(len(buttons), (available + 8) // (LAYOUT["filter_min"] + 8)))
                if wrapped else len(buttons))
        state = (wrapped, cols)
        if state == self._filter_layout_state:
            return
        self._filter_layout_state = state
        for w in (self.search, *buttons):
            self.tools_layout.removeWidget(w)
        if wrapped:
            self.tools_layout.addWidget(self.search, 0, 0, 1, cols)
            for i, b in enumerate(buttons):
                self.tools_layout.addWidget(b, 1 + i // cols, i % cols)
        else:
            self.tools_layout.addWidget(self.search, 0, 0)
            for i, b in enumerate(buttons):
                self.tools_layout.addWidget(b, 0, i + 1)

    def _arrange_actions(self, available: int) -> None:
        for grid, buttons in self._action_groups:
            needed = sum(b.sizeHint().width() for b in buttons) + 8 * (len(buttons) - 1)
            if needed <= available:
                slots = [(0, i, 1, 1) for i in range(len(buttons))]
            elif len(buttons) == 3:
                slots = [(0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1)]
            else:
                slots = [(i, 0, 1, 1) for i in range(len(buttons))]
            for b, (row, col, height, width) in zip(buttons, slots, strict=True):
                grid.addWidget(b, row, col, height, width)

    # ---------- Behaviour ----------
    def set_filter(self, key) -> None:
        self.filter_key = key
        for k, b in self.filter_buttons.items():
            b.setChecked(k == key)
        self.reload()

    def _stop_thread(self) -> None:
        if self._fetch_thread is not None and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            self._fetch_thread.wait()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_thread()
        super().hideEvent(event)

    def reload(self, keep_id: int | None = None) -> None:
        self.offset = 0
        # Generation counter: results from an older generation are silently dropped.
        self._fetch_gen = getattr(self, "_fetch_gen", 0) + 1
        gen = self._fetch_gen
        query = self.search.text()
        filter_key = self.filter_key
        self._stop_thread()
        worker = _FetchWorker(lambda: self.fetch(query, filter_key, 0))
        thread = QThread()   # no parent — parent-owned threads can be destroyed too early
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(lambda rows, err, g=gen: self._on_fetch_done(rows, err, keep_id, g))
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._fetch_thread = thread
        thread.start()

    def _on_fetch_done(self, rows: list, err: str, keep_id: int | None, gen: int) -> None:
        if gen != self._fetch_gen:   # stale result — a newer reload() was already called
            return
        if err:
            self.empty_label.setText("تعذّر تحميل البيانات")
            self.empty_label.setVisible(True)
            self.model.reset_rows([])
            return
        self.empty_label.setText(getattr(self, "empty_message", "لا توجد نتائج"))
        self.model.reset_rows(rows)
        self.view.resizeColumnsToContents()
        self.more_btn.setVisible(len(rows) == PAGE)
        self.empty_label.setVisible(not rows)
        self.count_label.setText(self.total())
        if keep_id is not None:
            self.show_preview_id(keep_id)
        elif self.current_id is not None and not any(r[0] == self.current_id for r in rows):
            self.hide_preview()

    def _debounced_reload(self) -> None:
        try:
            self.reload()
        except ClosedDatabase:
            pass

    def load_more(self) -> None:
        self.offset += PAGE
        rows = self.fetch(self.search.text(), self.filter_key, self.offset)
        self.model.append_rows(rows)
        self.more_btn.setVisible(len(rows) == PAGE)

    def show_preview(self, row: tuple) -> None:
        self.show_preview_id(row[0])

    def show_preview_id(self, row_id: int) -> None:
        self.current_id = row_id
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.preview_footer.hide()
        self._action_groups.clear()
        while self.preview_footer_layout.count():
            item = self.preview_footer_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.preview_title.setText("المعاينة")
        self.preview_subtitle.clear()
        self.preview_subtitle.hide()
        self.build_preview(row_id)
        self.preview_layout.addStretch(1)
        self.preview.show()
        self._update_responsive_layout()
        self.preview_scroll.verticalScrollBar().setValue(0)

    def hide_preview(self) -> None:
        self.current_id = None
        self.preview.hide()
        self.list_panel.show()
        self._update_responsive_layout()
        self.view.clearSelection()

    # ---------- Preview helpers ----------
    def _detail_row(self, key: str, value: str) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        k = muted(key)
        k.setFixedWidth(LAYOUT["field_label_max"])
        k.setAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
        raw = value or "—"
        v = QLabel(RLM + raw)
        clean = raw.strip("\u2066\u2069\u200f")
        atomic = bool(re.fullmatch(r"[A-Za-z0-9٠-٩][A-Za-z0-9٠-٩ .,:/+%()_×=\-]*", clean))
        v.setWordWrap(not atomic)
        if atomic:
            v.setToolTip(clean)
        v.setMinimumWidth(0)
        v.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v.setAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(k)
        row.addWidget(v, 1)
        w = QWidget()
        w.setLayout(row)
        return w

    def kv(self, key: str, value: str) -> None:
        self.preview_layout.addWidget(self._detail_row(key, value))

    def summary_section(self, title: str, icon_name: str | None = None) -> None:
        section = QFrame()
        section.setObjectName("summarySection")
        row = QHBoxLayout(section)
        row.setContentsMargins(4, 10, 4, 8)
        row.setSpacing(8)
        if icon_name:
            image = QLabel()
            image.setPixmap(icons.icon(icon_name, "#8792A3", 18).pixmap(18, 18))
            row.addWidget(image)
        label = QLabel(title)
        label.setObjectName("summarySectionTitle")
        row.addWidget(label, 1)
        self.preview_layout.addWidget(section)

    def summary_metrics(self, metrics: list[tuple[str, str]]) -> None:
        if not metrics:
            return
        wrapper = QWidget()
        grid = QGridLayout(wrapper)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        columns = len(metrics) if len(metrics) <= 3 else 2
        for index, (label, value) in enumerate(metrics):
            tile = QFrame()
            tile.setObjectName("summaryMetric")
            layout = QVBoxLayout(tile)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(5)
            caption = QLabel(label)
            caption.setObjectName("muted")
            caption.setWordWrap(True)
            caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter)
            amount = QLabel(str(value))
            amount.setObjectName("summaryMetricValue")
            amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter)
            clean_value = str(value).strip("\u2066\u2069\u200f")
            atomic = bool(re.fullmatch(r"[-+]?[A-Za-z0-9٠-٩][A-Za-z0-9٠-٩ .,:/+%()_×=\-]*", clean_value))
            amount.setWordWrap(not atomic)
            if atomic:
                amount.setToolTip(clean_value)
            amount.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(caption)
            layout.addWidget(amount)
            grid.addWidget(tile, index // columns, index % columns)
        self.preview_layout.addWidget(wrapper)

    def summary_details(self, details: list[tuple[str, str]]) -> None:
        if not details:
            return
        card = QFrame()
        card.setObjectName("summaryDetails")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        for key, value in details:
            layout.addWidget(SummaryDetailRow(key, str(value)))
        self.preview_layout.addWidget(card)

    def _summary_records_widget(self, headers: list[str], rows: list[tuple]) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for row in rows:
            if len(row) != len(headers):
                raise ValueError("Summary record fields do not match column headers")
            card = QFrame()
            card.setObjectName("summaryRecord")
            body = QVBoxLayout(card)
            body.setContentsMargins(10, 10, 10, 10)
            body.setSpacing(8)
            title = QLabel(str(row[0]))
            title.setObjectName("summaryRecordTitle")
            title.setWordWrap(True)
            title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            title.setToolTip(str(row[0]))
            body.addWidget(title)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(8)
            for col in range(LAYOUT["summary_record_columns"]):
                grid.setColumnStretch(col, 1)
            index = 0
            for header, value in zip(headers[1:], row[1:], strict=True):
                if value is None or value == "":
                    continue
                cell = QWidget()
                cell.setMinimumWidth(0)
                cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                column = QVBoxLayout(cell)
                column.setContentsMargins(0, 0, 0, 0)
                column.setSpacing(3)
                caption = muted(header)
                caption.setObjectName("summaryRecordLabel")
                caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                data = QLabel(str(value))
                data.setObjectName("summaryRecordValue")
                data.setAlignment(Qt.AlignmentFlag.AlignCenter)
                data.setWordWrap(True)
                data.setToolTip(str(value))
                data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                column.addWidget(caption)
                column.addWidget(data)
                grid.addWidget(cell, index // LAYOUT["summary_record_columns"],
                               index % LAYOUT["summary_record_columns"])
                index += 1
            body.addLayout(grid)
            layout.addWidget(card)
        return wrapper

    def summary_records(self, headers: list[str], rows: list[tuple]) -> QWidget:
        wrapper = self._summary_records_widget(headers, rows)
        self.preview_layout.addWidget(wrapper)
        return wrapper

    def summary_table(self, headers: list[str], rows: list[tuple], *,
                      column_alignments=None, one_line_dates: bool = False,
                      keep_table: bool = False, multiline_rows: bool = False) -> QTableView:
        table = QTableView()
        table.setObjectName("summaryTable")
        model = TupleModel(
            [(header, lambda row, pos=index, name=header:
              _summary_cell_text(row[pos], name, one_line_dates=one_line_dates))
             for index, header in enumerate(headers)],
            column_alignments=column_alignments)
        model.reset_rows(rows)
        table._summary_model = model
        table.setModel(model)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().hide()
        timestamp_columns = [i for i, name in enumerate(headers)
                             if name == "التاريخ" and any(
                                 re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", str(row[i]))
                                 for row in rows)]
        row_height = (LAYOUT["summary_table_datetime_row_height"]
                      if timestamp_columns and not one_line_dates else LAYOUT["summary_table_row_height"])
        if multiline_rows:
            row_height = max(row_height, 2 * LAYOUT["summary_table_row_height"])
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.setWordWrap(multiline_rows or not one_line_dates)
        if multiline_rows:
            table.verticalHeader().setMinimumSectionSize(row_height)
            table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header = table.horizontalHeader()
        header.setMinimumSectionSize(LAYOUT["summary_table_min_column"])
        if len(headers) <= 3:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for index in timestamp_columns:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        else:
            header.setMaximumSectionSize(LAYOUT["summary_table_max_column"])
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setFixedHeight(40 + row_height *
                             min(len(rows), LAYOUT["summary_table_visible_rows"]) +
                             (18 if len(headers) > 3 else 0))
        if len(headers) > 3 and not keep_table:
            cards = self._summary_records_widget(headers, rows)
            adaptive = SummaryAdaptiveTable(table, cards, len(rows))
            self.preview_layout.addWidget(adaptive)
        else:
            self.preview_layout.addWidget(table)
        return table

    def summary_note(self, message: str, icon_name: str = "alert") -> None:
        note = QFrame()
        note.setObjectName("summaryNote")
        row = QHBoxLayout(note)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(8)
        image = QLabel()
        image.setPixmap(icons.icon(icon_name, "#8792A3", 18).pixmap(18, 18))
        text = muted(message)
        row.addWidget(image, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(text, 1)
        self.preview_layout.addWidget(note)

    def heading(self, text: str, sub: str = "") -> None:
        self.preview_title.setText(text)
        self.preview_subtitle.setText(sub)
        self.preview_subtitle.setVisible(bool(sub))

    def actions(self, *buttons) -> None:
        if not buttons:
            return
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        w = QWidget()
        w.setLayout(row)
        self._action_groups.append((row, buttons))
        self.preview_footer_layout.addWidget(w)
        self.preview_footer.show()
        self._arrange_actions(max(0, self.preview.width() - 2 * LAYOUT["preview_padding"]))


class _FetchWorker(QObject):
    """Runs a fetch callable in a worker thread and emits (rows, error) back to the UI thread."""
    done = Signal(list, str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn(), "")
        except Exception as e:
            self.done.emit([], str(e))
