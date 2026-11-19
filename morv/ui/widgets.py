"""Small shared UI pieces, so screens stay consistent."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from morv.ui import icons


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("card")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(12)
    if title:
        head = QLabel(title)
        head.setObjectName("cardTitle")
        lay.addWidget(head)
    return box, lay


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


def form() -> QFormLayout:
    f = QFormLayout()
    f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
    f.setFormAlignment(Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignTop)
    f.setSpacing(10)
    return f


def field(title: str, widget: QWidget) -> QWidget:
    """A label above its input, matching the approved design's field layout (not label-beside-input)."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    lbl = QLabel(title)
    lbl.setObjectName("fieldLabel")
    v.addWidget(lbl)
    v.addWidget(widget)
    return w


def ro_field(title: str, text: str, is_ltr: bool = False) -> QWidget:
    """A field() for a locked display value, boxed like an input so it doesn't read as plain text."""
    e = ltr_edit() if is_ltr else QLineEdit()
    e.setText(text)
    e.setReadOnly(True)
    return field(title, e)


def ltr_edit(placeholder: str = "") -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    # The value is Latin so the field alone runs LTR: field content, not layout
    e.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute)
    return e


class PasswordEdit(QWidget):
    def __init__(self, placeholder: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = ltr_edit(placeholder)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle = QToolButton()
        self.toggle.setObjectName("topButton")
        self.toggle.setCheckable(True)
        self.toggle.setToolTip("إظهار كلمة المرور")
        self.toggle.toggled.connect(self._flip)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.toggle)
        self.set_icon_color("#566173")

    def set_icon_color(self, color: str) -> None:
        self._color = color
        self._flip(self.toggle.isChecked())

    def _flip(self, show: bool) -> None:
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password)
        self.toggle.setIcon(icons.icon("eye_off" if show else "eye", self._color, 18))
        self.toggle.setToolTip("إخفاء كلمة المرور" if show else "إظهار كلمة المرور")

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:
        self.edit.setText(value)

    def clear(self) -> None:
        self.edit.clear()


class Notice(QLabel):
    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setObjectName("noticeError")
        self.hide()

    def show_message(self, text: str, level: str = "error") -> None:
        self.setObjectName({"error": "noticeError", "success": "noticeSuccess", "warn": "noticeWarn"}[level])
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(text)
        self.setVisible(bool(text))


def dispose(widget, app) -> None:
    """Destroy a window explicitly before the process exits, or Qt crashes on Windows."""
    from PySide6.QtCore import QEvent
    if widget is None:
        return
    from morv.ui.workers import wait_all
    wait_all()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def primary_button(text: str, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("primary")
    if icon_name:
        b.setIcon(icons.icon(icon_name, "#FFFFFF", 18))
    return b


def danger_button(text: str, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("danger")
    if icon_name:
        b.setIcon(icons.icon(icon_name, "#B42323", 18))
    return b


def icon_button(text: str, icon_name: str, color: str = "#8792A3") -> QPushButton:
    b = QPushButton(text)
    b.setIcon(icons.icon(icon_name, color, 18))
    return b


def ask(parent, title: str, text: str, danger: bool = False) -> bool:
    from morv.ui.modal import ask as _ask
    return _ask(parent, title, text, danger)


def info(parent, title: str, text: str, error: bool = False) -> None:
    from morv.ui.modal import info as _info
    _info(parent, title, text, error)
