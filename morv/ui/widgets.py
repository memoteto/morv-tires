"""Small shared UI pieces, so screens stay consistent."""
from PySide6.QtCore import QDate, QEvent, QLocale, Qt, Signal
from PySide6.QtGui import QColor, QPalette, QTextCharFormat
from PySide6.QtWidgets import (QCalendarWidget, QDateEdit, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTableView, QToolButton, QVBoxLayout, QWidget)

from morv.core.text import ltr
from morv.ui import icons
from morv.ui.tokens import DARK, LIGHT


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


ICON_MUTED = "#8792A3"
ICON_PRIMARY = "#1F5FD6"


def section_card(title: str, icon_name: str) -> tuple:
    """Card with an icon header row; returns (card, body layout, header layout) for invoice screens."""
    box = QFrame()
    box.setObjectName("card")
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    head_w = QWidget()
    head_w.setObjectName("cardHead")
    head = QHBoxLayout(head_w)
    head.setContentsMargins(18, 12, 18, 12)
    head.setSpacing(8)
    ic = QLabel()
    ic.setPixmap(icons.icon(icon_name, ICON_PRIMARY, 18).pixmap(18, 18))
    t = QLabel(title)
    t.setObjectName("cardTitle")
    head.addWidget(ic)
    head.addWidget(t)
    head.addStretch(1)
    outer.addWidget(head_w)
    body = QVBoxLayout()
    body.setContentsMargins(18, 14, 18, 16)
    body.setSpacing(12)
    outer.addLayout(body)
    return box, body, head


def field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("fieldLabel")
    return lbl


def kv_row(label: str, value_widget: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 4, 0, 4)
    h.addWidget(field_label(label))
    h.addStretch(1)
    h.addWidget(value_widget)
    return w


def chip(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("chip")
    return lbl


def kbd(key: str, on_primary: bool = False) -> QLabel:
    lbl = QLabel(ltr(key))
    lbl.setObjectName("kbdOnPrimary" if on_primary else "kbd")
    return lbl


def deferred_button(label: str, icon_name: str | None = None) -> QPushButton:
    """A planned action that is not built yet: shown disabled with a 'coming soon' tip."""
    b = QPushButton(label)
    if icon_name:
        b.setIcon(icons.icon(icon_name, ICON_MUTED, 16))
    b.setEnabled(False)
    b.setToolTip("قريبًا — غير مبني بعد")
    return b


def trail_icon(edit: QLineEdit, name: str) -> None:
    edit.addAction(icons.icon(name, ICON_MUTED, 16), QLineEdit.ActionPosition.TrailingPosition)


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


def field(title: str, widget: QWidget, locked: bool = False) -> QWidget:
    """A label above its input (the approved layout); locked marks a value changed only with permission."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    if locked:
        h = QHBoxLayout()
        h.setSpacing(4)
        h.addWidget(field_label(title))
        ic = QLabel()
        ic.setPixmap(icons.icon("lock", ICON_MUTED, 13).pixmap(13, 13))
        h.addWidget(ic)
        h.addWidget(field_label("بصلاحية"))
        h.addStretch(1)
        v.addLayout(h)
    else:
        v.addWidget(field_label(title))
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


def arabic_latin_locale() -> QLocale:
    """Arabic day and month names with Latin digits, which Egypt's own Arabic locale does not give."""
    for name in ("ar_LY", "ar_TN", "ar_DZ", "ar_MA"):
        loc = QLocale(name)
        if loc.language() == QLocale.Language.Arabic and loc.zeroDigit() == "0" and loc.monthName(5) == "مايو":
            loc.setNumberOptions(QLocale.NumberOption.OmitGroupSeparator)      # a year reads 2026, not 2.026
            return loc
    return QLocale(QLocale.Language.Arabic, QLocale.Territory.Egypt)


def style_calendar(cal: QCalendarWidget) -> None:
    """One calendar look for every date field: Arabic names, Latin digits, Friday and Saturday weekend."""
    cal.setLocale(arabic_latin_locale())
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Saturday)
    cal.setFixedSize(316, 272)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
    cal.setGridVisible(False)
    view = cal.findChild(QTableView)
    if view is not None:      # the hidden header's text-based minimum pushed four day columns out of view
        view.horizontalHeader().setMinimumSectionSize(1)
        view.verticalHeader().setMinimumSectionSize(1)
    header = QTextCharFormat()
    header.setFontPointSize(8)      # Arabic day names are full words; this keeps the longest uncut in 316px
    cal.setHeaderTextFormat(header)
    dark = cal.palette().color(QPalette.ColorRole.Window).lightness() < 128
    weekend, plain = QTextCharFormat(), QTextCharFormat()
    weekend.setForeground(QColor((DARK if dark else LIGHT)["danger"]))
    plain.setForeground(cal.palette().color(QPalette.ColorRole.Text))
    for day in Qt.DayOfWeek:
        cal.setWeekdayTextFormat(day, weekend if day in (Qt.DayOfWeek.Friday, Qt.DayOfWeek.Saturday) else plain)


class DateEdit(QDateEdit):
    """Calendar date field speaking ISO text like the line edit it replaced; engines still validate it."""

    textChanged = Signal(str)
    _BLANK = QDate(2000, 1, 1)      # the 'no date' value of an optional field, shown as an empty box

    def __init__(self, optional: bool = False, allow_future: bool = False):
        super().__init__()
        self.setObjectName("dateEdit")
        self.optional = optional
        self._raw: str | None = None
        self.setCalendarPopup(True)
        self._apply_format()
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute)
        self.setMinimumDate(self._BLANK)
        if not allow_future:      # posting engines refuse future dates, so the calendar offers none
            self.setMaximumDate(QDate.currentDate())
        if optional:
            self.setSpecialValueText(" ")
        # No step buttons: invisible ones still caught clicks and moved the year (an empty field became 2001)
        self.setButtonSymbols(QDateEdit.ButtonSymbols.NoButtons)
        cal = self.calendarWidget()
        cal.setObjectName("dateCalendar")
        style_calendar(cal)
        cal.installEventFilter(self)
        self.setDate(self._BLANK if optional else QDate.currentDate())
        self.dateChanged.connect(self._changed)

    def _apply_format(self) -> None:
        """Qt lays a date's sections out by direction, so RTL needs the mirrored format to show 2026-11-21."""
        self.setDisplayFormat("dd-MM-yyyy" if self.isRightToLeft() else "yyyy-MM-dd")

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.LayoutDirectionChange:
            self._apply_format()

    def eventFilter(self, obj, e) -> bool:
        # A blank optional field would open its calendar on the 'no date' value (January 2000)
        if obj is self.calendarWidget() and e.type() == QEvent.Type.Show and self.text() == "":
            today = QDate.currentDate()
            obj.setCurrentPage(today.year(), today.month())
        return super().eventFilter(obj, e)

    def stepBy(self, steps: int) -> None:
        if self.text() == "":      # arrows or the wheel on an empty field start from today, not year 2000
            self.setDate(QDate.currentDate())
            return
        super().stepBy(steps)

    def _changed(self, _day) -> None:
        self._raw = None
        self.textChanged.emit(self.text())

    def text(self) -> str:
        if self._raw is not None:
            return self._raw
        if self.optional and self.date() == self._BLANK:
            return ""
        return self.date().toString(Qt.DateFormat.ISODate)

    def setText(self, value: str) -> None:
        """An invalid or out-of-range value is kept as typed, so the engine reports it as before."""
        value = (value or "").strip()
        day = QDate.fromString(value, Qt.DateFormat.ISODate)
        had_raw, self._raw = self._raw is not None, None
        if not value and self.optional:
            self.setDate(self._BLANK)
        elif day.isValid() and self.minimumDate() <= day <= self.maximumDate():
            self.setDate(day)
        else:
            self._raw = value
            self.textChanged.emit(value)
            return
        if had_raw:      # leaving a kept raw value changes the text even when the date did not move
            self.textChanged.emit(self.text())

    def clear(self) -> None:
        self.setText("")


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


def action_row(*buttons: QPushButton) -> QHBoxLayout:
    """Buttons at their natural width, packed to the start, so they never stretch across the page."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    return row


def ask(parent, title: str, text: str, danger: bool = False) -> bool:
    from morv.ui.modal import ask as _ask
    return _ask(parent, title, text, danger)


def info(parent, title: str, text: str, error: bool = False) -> None:
    from morv.ui.modal import info as _info
    _info(parent, title, text, error)
