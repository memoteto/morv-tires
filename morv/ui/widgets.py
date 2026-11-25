"""Small shared UI pieces, so screens stay consistent."""
from PySide6.QtCore import QDate, QEvent, QLocale, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette, QStandardItem, QStandardItemModel, QTextCharFormat
from PySide6.QtWidgets import (QCalendarWidget, QCompleter, QDateEdit, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QPushButton, QTableView, QToolButton, QVBoxLayout, QWidget)

from morv.core.text import ltr
from morv.ui import icons, tokens
from morv.ui.tokens import DARK, LIGHT


def card(title: str | None = None, *, compact: bool = False) -> tuple[QFrame, QVBoxLayout]:
    box = QFrame()
    box.setObjectName("card")
    lay = QVBoxLayout(box)
    if compact:
        x = tokens.LAYOUT["compact_card_padding_x"]
        y = tokens.LAYOUT["compact_card_padding_y"]
        lay.setContentsMargins(x, y, x, y)
        lay.setSpacing(tokens.LAYOUT["compact_card_spacing"])
    else:
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
    if title:
        head = QLabel(title)
        head.setObjectName("cardTitle")
        lay.addWidget(head)
    return box, lay


ICON_MUTED = tokens.ICON_MUTED
ICON_PRIMARY = tokens.ICON_PRIMARY


class InvoiceTotal(QPushButton):
    """Invoice total whose size follows its content rather than button text."""

    def __init__(self, caption, on_click=None):
        super().__init__()
        self.setObjectName("totalBox")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        label = QLabel(caption)
        label.setObjectName("totalLabel")

        self.value = QLabel()
        self.value.setObjectName("totalValue")

        layout.addWidget(label)
        layout.addStretch(1)
        layout.addWidget(self.value)

        for child in (label, self.value):
            child.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        if on_click is None:
            self.setEnabled(False)
        else:
            self.clicked.connect(on_click)

    def sizeHint(self):
        hint = super().sizeHint()
        layout = self.layout()
        return hint.expandedTo(layout.sizeHint()) if layout else hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        layout = self.layout()
        return hint.expandedTo(layout.minimumSize()) if layout else hint

    def event(self, event):
        result = super().event(event)
        if event.type() in (
                QEvent.Type.LayoutRequest,
                QEvent.Type.FontChange,
                QEvent.Type.StyleChange):
            self.updateGeometry()
        return result


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
    """A planned action not built yet: hidden until it works (owner decision), disabled as a safeguard."""
    b = QPushButton(label)
    if icon_name:
        b.setIcon(icons.icon(icon_name, ICON_MUTED, 16))
    b.setEnabled(False)
    b.setToolTip("قريبًا — غير مبني بعد")
    b.setVisible(False)
    return b


def muted_icon(name: str, size: int = 16) -> QIcon:
    """Fixed muted tint, which reads on both themes; only the shell retints its icons on a theme change."""
    return icons.icon(name, ICON_MUTED, size)


def trail_icon(edit: QLineEdit, name: str) -> None:
    edit.addAction(icons.icon(name, ICON_MUTED, 16), QLineEdit.ActionPosition.TrailingPosition)


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


def kpi_tile(title: str, *, danger: bool = False, clickable: bool = False, sub: bool = True,
             initial: str = "—"):
    """One metric card; clickable=True makes it a checkable button (dashboard/treasury/installments)."""
    box = QPushButton() if clickable else QFrame()
    if clickable:
        box.setCheckable(True)
    box.setObjectName("kpiTile" if clickable else "card")
    inner = QVBoxLayout(box)
    inner.setContentsMargins(14, 10, 14, 10)
    inner.setSpacing(4)
    inner.addWidget(muted(title))
    value = QLabel(initial)
    value.setObjectName("cardTitleDanger" if danger else "cardTitle")
    if not clickable:
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
                           | Qt.AlignmentFlag.AlignVCenter)
    inner.addWidget(value)
    sub_lbl = muted("") if sub else None
    if sub_lbl is not None:
        inner.addWidget(sub_lbl)
    return box, value, sub_lbl


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


class _ContainsCompleter(QCompleter):
    def __init__(self, values, normalize, parent):
        model = QStandardItemModel(parent)
        for value in values:
            item = QStandardItem(value)
            item.setData(normalize(value), Qt.ItemDataRole.UserRole)
            model.appendRow(item)
        super().__init__(model, parent)
        self._normalize = normalize
        self.setCompletionRole(Qt.ItemDataRole.UserRole)
        self.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setMaxVisibleItems(10)

    def splitPath(self, path):
        return [self._normalize(path)]

    def pathFromIndex(self, index):
        return index.data(Qt.ItemDataRole.DisplayRole) or ""


def search_completer(parent, values, normalize):
    return _ContainsCompleter(values, normalize, parent)


def searchable_edit(values, normalize, placeholder="") -> QLineEdit:
    edit = ltr_edit(placeholder)
    completer = search_completer(edit, values, normalize)
    edit.setCompleter(completer)
    action = edit.addAction(icons.icon("search", ICON_MUTED, 18), QLineEdit.ActionPosition.TrailingPosition)

    def show_all():
        edit.setFocus()
        completer.setCompletionPrefix("")
        completer.complete()

    action.triggered.connect(show_all)
    return edit


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
        b.setIcon(icons.icon(icon_name, tokens.ICON_ON_PRIMARY, 18))
    return b


def danger_button(text: str, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("danger")
    if icon_name:
        b.setIcon(icons.icon(icon_name, tokens.ICON_DANGER, 18))
    return b


def menu_button(text: str, entries: list, icon_name: str | None = None) -> QPushButton:
    """One button for a group of actions; each entry is (label, callback, enabled) and opens as a menu."""
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icons.icon(icon_name, ICON_MUTED, 18))
    menu = QMenu(b)
    for label, on_click, enabled in entries:
        action = menu.addAction(label)
        action.setEnabled(enabled)
        action.triggered.connect(lambda _checked=False, fn=on_click: fn())
    b.setMenu(menu)                 # the button owns the menu, so it lives as long as the button
    return b


def icon_button(text: str, icon_name: str, color: str = ICON_MUTED) -> QPushButton:
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


def confirm_discard_invoice(parent, *, dirty: bool, busy: bool) -> bool:
    """Protect unposted invoice data using the shared confirmation dialog."""
    if busy:
        return False
    if not dirty:
        return True
    return ask(
        parent,
        "إلغاء الفاتورة",
        "توجد بيانات غير مرحلة. هل تريد إلغاء الفاتورة والتخلص من مدخلاتها؟",
        danger=True,
    )


def info(parent, title: str, text: str, error: bool = False) -> None:
    from morv.ui.modal import info as _info
    _info(parent, title, text, error)
