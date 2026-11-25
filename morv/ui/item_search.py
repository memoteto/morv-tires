"""Shared invoice item lookup and keyboard selection."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QVBoxLayout,
)

from morv.core.text import ltr
from morv.infra import items
from morv.ui import icons
from morv.ui.inventory_page import ItemDialog
from morv.ui.widgets import ICON_MUTED, muted, trail_icon

KIND_ICON = {"tire": "tire", "battery": "battery", "service": "service"}


class _ItemPopup(QFrame):
    """Floating list that appears under the item search field."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setWindowFlags(Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(2)

    def show_results(self, rows, on_pick, on_new, query: str) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row in rows[:8]:
            item_id, kind, _code, name = row[0], row[1], row[2], row[3]
            spec = row[4] if len(row) > 4 else ""
            btn = QPushButton()
            btn.setFlat(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(50)
            btn.setObjectName("itemResult")
            btn.setCheckable(True)
            inner = QHBoxLayout(btn)
            inner.setContentsMargins(8, 4, 8, 4)
            ic = QLabel()
            ic.setPixmap(icons.icon(KIND_ICON.get(kind, "inventory"), ICON_MUTED, 18).pixmap(18, 18))
            name_lbl = QLabel(name)
            name_lbl.setObjectName("cardTitle")
            spec_lbl = muted(ltr(spec) if spec else "")
            inner.addWidget(ic)
            inner.addWidget(name_lbl)
            inner.addWidget(spec_lbl)
            inner.addStretch(1)
            btn.clicked.connect(lambda _, iid=item_id, ik=kind, iname=name: on_pick(iid, ik, iname))
            self._layout.addWidget(btn)
        new_btn = QPushButton(f"+ صنف جديد سريع: {query}" if query else "+ صنف جديد سريع")
        new_btn.setObjectName("primary")
        new_btn.setFlat(True)
        new_btn.setFixedHeight(38)
        new_btn.clicked.connect(on_new)
        self._layout.addWidget(new_btn)
        self.adjustSize()
        self.selected = -1

    def move_selection(self, step):
        count = self._layout.count() - 1
        if count <= 0:
            return
        self.selected = (
            (0 if step > 0 else count - 1)
            if self.selected < 0 else (self.selected + step) % count
        )
        for index in range(count):
            self._layout.itemAt(index).widget().setChecked(
                index == self.selected)

    def choose(self):
        if self.selected < 0:
            return False
        self._layout.itemAt(self.selected).widget().click()
        return True


class ItemSearch(QLineEdit):
    def __init__(self, form, kinds, icon_size=16):
        super().__init__(form)
        self.form = form
        self.kinds = kinds
        self.setPlaceholderText("اسم الصنف، الكود، المقاس أو امسح الباركود")
        self.addAction(icons.icon("search", ICON_MUTED, icon_size),
                       QLineEdit.ActionPosition.LeadingPosition)
        trail_icon(self, "barcode")
        self.popup = _ItemPopup(self)
        self.popup.selected = -1
        self.popup.hide()
        self.timer = QTimer(self, singleShot=True, interval=200)
        self.timer.timeout.connect(self.search)
        self.close_timer = QTimer(self, singleShot=True, interval=150)
        self.close_timer.timeout.connect(self.popup.hide)
        self.textChanged.connect(self._changed)

    def _changed(self, text):
        self.close_results()
        if text.strip():
            self.timer.start()

    def close_results(self):
        self.timer.stop()
        self.close_timer.stop()
        self.popup.hide()

    def search(self):
        self.close_results()
        query = self.text().strip()
        if not query:
            return
        rows = []
        for kind in self.kinds():
            rows.extend(items.search(self.form.session.con, query,
                                     kind=kind, limit=8 - len(rows)))
            if len(rows) == 8:
                break
        self.popup.show_results(rows, self.pick, self.new_item, query)
        self.popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        self.popup.resize(self.width(), self.popup.sizeHint().height())
        self.popup.show()

    def pick(self, item_id, kind, name):
        self.clear()
        self.close_results()
        self.form._add_row(item_id, kind, name)
        self.setFocus()

    def new_item(self):
        self.close_results()
        dlg = ItemDialog(self.form.session, None, parent=self.form)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved:
            self.pick(dlg.saved.id, dlg.saved.kind, dlg.saved.name)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_results()
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            if not self.popup.isVisible():
                self.search()
            self.popup.move_selection(1 if key == Qt.Key.Key_Down else -1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.popup.isVisible() and self.popup.choose():
                return
            query = self.text().strip()
            if query:
                item = items.by_barcode(self.form.session.con, query, unique=True)
                if item and item.kind in self.kinds():
                    self.pick(item.id, item.kind, item.name)
                else:
                    self.search()
        else:
            return super().keyPressEvent(event)
        event.accept()

    def focusOutEvent(self, event):
        self.timer.stop()
        self.close_timer.start()
        super().focusOutEvent(event)

    def focusInEvent(self, event):
        self.close_timer.stop()
        super().focusInEvent(event)

    def hideEvent(self, event):
        self.close_results()
        super().hideEvent(event)
