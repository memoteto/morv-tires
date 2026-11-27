"""Shared live name and mobile lookup for invoice parties."""
from PySide6.QtCore import QPoint, QSize, Qt, QTimer, QSignalBlocker
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QStyle, QStylePainter, QVBoxLayout, QWidget)

from morv.core.money import to_latin
from morv.core.text import ltr, norm_ar
from morv.ui import icons
from morv.ui.modal import Modal
from morv.ui.widgets import ICON_MUTED, field, kbd, ltr_edit, muted


def phone_query(text):
    return to_latin(text).replace(" ", "").replace("-", "")


class PartyPopup(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("card")
        self.setWindowFlags(Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(2)
        self.buttons = []
        self.selected = -1

    def show_results(self, rows, pick, new, cash):
        while self._layout.count():
            widget = self._layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.buttons = []
        self.selected = -1
        for row in rows:
            button = self._button("", lambda _checked=False, r=row: pick(r))
            layout = QHBoxLayout(button)
            layout.setContentsMargins(8, 4, 8, 4)
            name = QLabel(row[1])
            name.setObjectName("cardTitle")
            phone = muted(ltr(row[2] or ""))
            phone.setWordWrap(False)
            for label in (name, phone):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                layout.addWidget(label)
            layout.addStretch(1)
        if cash is not None:
            self._button("عميل نقدي", cash)
        if new is not None:
            text, callback = new
            button = self._button(text, callback)
            button.setObjectName("primary")
        self.adjustSize()

    def _button(self, text, callback):
        button = QPushButton(text)
        button.setObjectName("itemResult")
        button.setFlat(True)
        button.setCheckable(True)
        button.setMinimumHeight(44)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        self._layout.addWidget(button)
        self.buttons.append(button)
        return button

    def move_selection(self, step):
        count = len(self.buttons)
        if not count:
            return
        self.selected = ((0 if step > 0 else count - 1) if self.selected < 0
                         else (self.selected + step) % count)
        for i, button in enumerate(self.buttons):
            button.setChecked(i == self.selected)

    def choose(self):
        if not 0 <= self.selected < len(self.buttons):
            return False
        self.buttons[self.selected].click()
        return True


class PartySearch(QLineEdit):
    def __init__(self, parent, *, label, lookup, pick, new, can_new,
                 cash=None, unresolved=None):
        super().__init__(parent)
        self.lookup = lookup
        self.pick = pick
        self.new = new
        self.can_new = can_new
        self.cash = cash
        self.unresolved = unresolved
        self.label = label
        self.selected_text = ""
        self.pending = False
        self.setPlaceholderText(f"اسم {label} أو رقم الموبايل")
        self.addAction(icons.icon("search", ICON_MUTED, 16),
                       QLineEdit.ActionPosition.LeadingPosition)
        self.popup = PartyPopup(self)
        self.popup.hide()
        self.timer = QTimer(self, singleShot=True, interval=200)
        self.timer.timeout.connect(self.search)
        self.close_timer = QTimer(self, singleShot=True, interval=150)
        self.close_timer.timeout.connect(self.close_results)
        self.textChanged.connect(self._changed)

    def _changed(self, _text):
        self.pending = True
        self.close_results()
        if self.isVisible():
            self.timer.start()

    def close_results(self):
        self.timer.stop()
        self.close_timer.stop()
        self.popup.hide()

    def commit_text(self, text):
        self.close_results()
        self.selected_text = text
        self.pending = False
        with QSignalBlocker(self):
            self.setText(text)

    def open_search(self):
        self.setFocus()
        self.selectAll()
        self.search()

    def _query(self):
        return self.text().strip() if self.pending else ""

    def _pick(self, row):
        self.close_results()
        self.pick(row)

    def _new(self):
        query = self._query()
        self.close_results()
        if self.can_new():
            self.new(query)

    def search(self):
        self.close_results()
        if not self.isVisible():
            return
        query = self._query()
        rows = self.lookup(query, 8)
        new = (f"+ {self.label.removeprefix('ال')} جديد", self._new) if self.can_new() else None
        self.popup.show_results(rows, self._pick, new, self.cash)
        if not self.popup.buttons:
            return
        area = self.screen().availableGeometry()
        size = self.popup.sizeHint()
        width = min(area.width(), max(self.width(), size.width()))
        height = size.height()
        pos = self.mapToGlobal(QPoint(0, self.height()))
        x = max(area.left(), min(pos.x(), area.right() - width + 1))
        y = pos.y()
        if y + height > area.bottom() + 1:
            above = self.mapToGlobal(QPoint(0, 0)).y() - height
            y = max(area.top(), min(above, area.bottom() - height + 1))
        self.popup.resize(width, height)
        self.popup.move(x, y)
        self.popup.show()

    def accept_query(self):
        if self.popup.isVisible() and self.popup.choose():
            return
        query = self._query()
        if query == "عميل نقدي" and self.cash is not None:
            self.cash()
            return
        if query:
            rows = self.lookup(query, 9)
            digits = phone_query(query)
            exact = [r for r in rows if
                     (digits.isascii() and digits.isdigit() and r[2] == digits)
                     or (not digits.isdigit() and norm_ar(r[1]) == norm_ar(query))]
            # Do not choose an arbitrary same-name match or a truncated result.
            if len(rows) < 9 and len(exact) == 1:
                self._pick(exact[0])
                return
            if self.unresolved is not None:
                self.unresolved(query, bool(rows))
        self.search()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.commit_text(self.selected_text)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            if not self.popup.isVisible():
                self.search()
            self.popup.move_selection(1 if key == Qt.Key.Key_Down else -1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept_query()
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


def party_field(search, shortcut=None):
    """Identical field chrome; only the optional keyboard hint differs."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(search, 1)
    if shortcut:
        layout.addWidget(kbd(shortcut))
    return field(f"{search.label} *", row)


class _ElidedSummary(QLabel):
    """Keep complete text while allowing a single-line summary to shrink."""
    def __init__(self):
        super().__init__()
        self.setObjectName("muted")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(False)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def display_text(self):
        return self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width()))

    def paintEvent(self, event):
        displayed = self.display_text()
        if displayed == self.text():
            super().paintEvent(event)
            return
        QFrame.paintEvent(self, event)
        painter = QStylePainter(self)
        alignment = QStyle.visualAlignment(self.layoutDirection(), self.alignment())
        flags = int(alignment) | int(Qt.TextFlag.TextSingleLine)
        painter.drawItemText(self.contentsRect(), flags, self.palette(),
                             self.isEnabled(), displayed, QPalette.ColorRole.WindowText)
        painter.end()


class FoldableDetails(QWidget):
    """Own the detail grid, toggle and summary; hiding never resets a field."""
    def __init__(self, fields, title, summary, stretches=(3, 2, 2)):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for column, stretch in enumerate(stretches):
            grid.setColumnStretch(column, stretch)
        for column, (label, editor) in enumerate(fields):
            grid.addWidget(field(label, editor), 0, column)
            editor.textChanged.connect(self.refresh)
        self.summary_text = summary
        self.summary = _ElidedSummary()
        self.toggle = QPushButton(title)
        self.toggle.setObjectName("link")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.setVisible)
        self.hide()
        self.refresh()

    def add_detail_action(self, title, callback):
        """Secondary action inside the existing folded details, never a page action."""
        button = QPushButton(title)
        button.setObjectName("link")
        button.clicked.connect(callback)
        grid = self.layout()
        grid.addWidget(button, grid.rowCount(), 0, 1, grid.columnCount())
        return button

    def refresh(self, *_args):
        self.summary.setText(self.summary_text())


class PartyInfoStrip(QFrame):
    """Shared selected-party status and folded-detail controls."""
    def __init__(self, details):
        super().__init__()
        self.setObjectName("infoStrip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        self.kind = QLabel()
        self.stats = muted("")
        self.stats.setWordWrap(False)
        layout.addWidget(self.kind)
        layout.addWidget(self.stats)
        layout.addStretch(1)
        layout.addWidget(details.summary)
        layout.addWidget(details.toggle)

    def set_info(self, status, *, registered=False, mobile="", description=""):
        self.kind.setText(status)
        self.kind.setObjectName("pillOk" if registered else "chip")
        self.stats.setText(" · ".join(part for part in (ltr(mobile) if mobile else "", description) if part))
        self.kind.style().unpolish(self.kind)
        self.kind.style().polish(self.kind)


class QuickPartyDialog(Modal):
    """Shared quick-create layout; subclasses supply only entity persistence."""
    def __init__(self, session, label, name_hint, mobile_hint, error_type, *, optional=False, parent=None):
        super().__init__(f"{label.removeprefix('ال')} جديد", parent, width=560)
        self.session = session
        self.created = None
        self.error_type = error_type
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(f"اسم {label}")
        self.name_edit.setText(name_hint)
        self.mobile_edit = ltr_edit("01xxxxxxxxx")
        self.mobile_edit.setText(mobile_hint)
        self.body.addWidget(field(f"اسم {label} *", self.name_edit))
        self.body.addWidget(field("رقم الموبايل *", self.mobile_edit))
        if optional:
            self.addr_edit = QLineEdit()
            self.addr_edit.setPlaceholderText("العنوان")
            self.notes_edit = QLineEdit()
            self.notes_edit.setPlaceholderText("ملاحظات")
            self.body.addWidget(field("العنوان", self.addr_edit))
            self.body.addWidget(field("ملاحظات", self.notes_edit))
            self.body.addWidget(muted("باقي بيانات المورد تُكمل لاحقًا من شاشة الموردين."))
        self.add_footer(f"حفظ واختيار {label}", self.save)

    def save(self):
        try:
            self.created = self._create_party(self.name_edit.text().strip(), self.mobile_edit.text().strip())
        except self.error_type as error:
            self.notice.show_message(str(error))
            return
        self.accept()
