"""Shared table-document layout: business rules stay in each document model."""
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHeaderView, QHBoxLayout, QLabel, QSizePolicy, QSpinBox,
                               QStyledItemDelegate, QTableView, QWidget)

from morv.ui.adaptive_layout import AdaptiveFields
from morv.ui.modal import Modal
from morv.ui.tokens import LAYOUT
from morv.ui.widgets import card, field, icon_button, muted


class DocumentEditor(QStyledItemDelegate):
    """Commit an open editor before approval, including the keyboard shortcut."""

    def __init__(self, parent):
        super().__init__(parent)
        self.active_editor = None

    @staticmethod
    def quantity_editor(parent, maximum):
        editor = QSpinBox(parent)
        editor.setRange(0, maximum)
        editor.setKeyboardTracking(False)
        return editor

    def destroyEditor(self, editor, index):
        if self.active_editor is editor:
            self.active_editor = None
        super().destroyEditor(editor, index)

    def finish(self):
        editor = self.active_editor
        if editor is not None:
            self.commitData.emit(editor)
            if self.active_editor is editor:
                self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)


class DocumentTableDialog(Modal):
    """Fixed controls, summary and footer; only the document table scrolls."""

    def __init__(self, title, parent=None):
        super().__init__(title, parent, width=LAYOUT["document_dialog_width"],
                         table_mode=True, height=LAYOUT["document_dialog_height"], keep_on_screen=True)
        x, y = LAYOUT["document_margin_x"], LAYOUT["document_margin_y"]
        self.body.setContentsMargins(x, y, x, y)
        self.body.setSpacing(LAYOUT["compact_card_spacing"])

    @staticmethod
    def fields(entries):
        return AdaptiveFields([field(caption, widget) for caption, widget in entries])

    def add_document_metrics(self, entries, *, columns=3):
        grid = QGridLayout()
        grid.setSpacing(LAYOUT["compact_card_spacing"])
        labels = {}
        for i, (caption, value) in enumerate(entries):
            box, layout = card(compact=True)
            layout.addWidget(muted(caption))
            label = QLabel(value)
            label.setObjectName("cardTitle")
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setToolTip(value)
            layout.addWidget(label)
            grid.addWidget(box, i // columns, i % columns)
            grid.setColumnStretch(i % columns, 1)
            labels[caption] = label
        self.body.addLayout(grid)
        return labels

    def add_quantity_toolbar(self, title, hint):
        toolbar = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        toolbar.addWidget(heading)
        toolbar.addWidget(muted(hint))
        toolbar.addStretch(1)
        self.all_btn = icon_button("إرجاع الكل", "check")
        self.clear_btn = icon_button("تصفير", "close")
        self.all_btn.clicked.connect(lambda: self._bulk_quantities(True))
        self.clear_btn.clicked.connect(lambda: self._bulk_quantities(False))
        toolbar.addWidget(self.all_btn)
        toolbar.addWidget(self.clear_btn)
        self.body.addLayout(toolbar)

    def _bulk_quantities(self, all_available):
        self.editor.finish()
        if all_available:
            self.model.select_all()
        else:
            self.model.clear_quantities()
        self.refresh_quantity_actions()

    def refresh_quantity_actions(self):
        self.all_btn.setEnabled(any(r.available > 0 for r in self.model.rows))
        self.clear_btn.setEnabled(any(self.model.quantities))

    def add_document_table(self, model, delegate, *, stretch_column):
        view = QTableView()
        view.setModel(model)
        self.editor = delegate(view)
        view.setItemDelegate(self.editor)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.CurrentChanged |
                             QAbstractItemView.EditTrigger.SelectedClicked |
                             QAbstractItemView.EditTrigger.EditKeyPressed |
                             QAbstractItemView.EditTrigger.AnyKeyPressed)
        view.setAlternatingRowColors(True)
        view.verticalHeader().hide()
        view.verticalHeader().setDefaultSectionSize(LAYOUT["adaptive_table_row_min"])
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        view.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
        view.setMinimumHeight(LAYOUT["document_table_min"])
        self.body.addWidget(view, 1)
        return view

    @staticmethod
    def amount_row(layout, caption, *, strong=False):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(caption) if strong else muted(caption)
        value = QLabel()
        if strong:
            label.setObjectName("cardTitle")
            value.setObjectName("totalValue")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value)
        layout.addWidget(widget)
        return value
