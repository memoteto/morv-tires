"""Shared invoice item lookup and keyboard selection."""
import re

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from morv.core.money import fmt
from morv.core.text import ltr, norm_ar
from morv.infra import inventory, items
from morv.ui import icons
from morv.ui.inventory_page import ItemDialog
from morv.ui.invoice_lines import wrapping_line_label
from morv.ui.widgets import ICON_MUTED, chip, muted, trail_icon

KIND_ICON = {"tire": "tire", "battery": "battery", "service": "service"}


def _extra_spec(name, spec):
    if not spec:
        return ""
    # A full token match tolerates spaces, digit variants and letter case.
    pattern = r"\s*".join(re.escape(ch) for ch in norm_ar(spec) if not ch.isspace())
    return "" if re.search(r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])", norm_ar(name)) else spec


class _ItemPopup(QFrame):
    """Floating list that appears under the item search field."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(2)

    def show_results(self, rows, on_pick, on_new, query: str, *, sale_price=None, balances=None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        price_labels, stock_labels = [], []
        for row in rows[:8]:
            item_id, kind, _code, name = row[0], row[1], row[2], row[3]
            spec = _extra_spec(name, row[4])
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
            name_lbl = wrapping_line_label(name)
            name_lbl.setObjectName("cardTitle")
            name_lbl.setTextFormat(Qt.TextFormat.PlainText)
            name_lbl.setToolTip(name)
            inner.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)
            inner.addWidget(name_lbl, 1)
            if spec:
                spec_lbl = muted(ltr(spec))
                spec_lbl.setWordWrap(False)
                inner.addWidget(spec_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
            if sale_price is not None:
                price = chip(f"{ltr(fmt(sale_price(*row[5:8])))} ج.م")
                price.setAlignment(Qt.AlignmentFlag.AlignCenter)
                price_labels.append(price)
                stock = QLabel()
                stock.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if kind in ("tire", "battery"):
                    qty = balances.get(item_id, 0)
                    stock.setText(f"متوفر: {ltr(qty)}")
                    stock.setObjectName("pillDanger" if qty <= 0 else
                                        "pillWarn" if qty < row[8] else "pillOk")
                    stock.setToolTip("الرصيد الفعلي قبل اعتماد الفاتورة")
                stock_labels.append(stock)
                inner.addWidget(stock, 0, Qt.AlignmentFlag.AlignVCenter)
                inner.addWidget(price, 0, Qt.AlignmentFlag.AlignVCenter)
            btn.clicked.connect(lambda _, iid=item_id, ik=kind, iname=name: on_pick(iid, ik, iname))
            self._layout.addWidget(btn)
        for labels in (price_labels, stock_labels):
            for label in labels:
                label.ensurePolished()
            width = max((label.sizeHint().width() for label in labels), default=0)
            for label in labels:
                label.setFixedWidth(width)
        new_btn = QPushButton(f"+ صنف جديد سريع: {query}" if query else "+ صنف جديد سريع")
        new_btn.setObjectName("primary")
        new_btn.setFlat(True)
        new_btn.setFixedHeight(38)
        new_btn.clicked.connect(on_new)
        self._layout.addWidget(new_btn)
        self.selected = -1

    def _row_metrics(self, button):
        inner = button.layout()
        name = inner.itemAt(1).widget()
        margins = inner.contentsMargins()
        others = [inner.itemAt(i).widget() for i in range(inner.count()) if i != 1]
        for widget in (name, *others):
            widget.ensurePolished()
        reserved = (margins.left() + margins.right()
                    + max(0, inner.spacing()) * (inner.count() - 1)
                    + sum(max(w.minimumWidth(), w.sizeHint().width()) for w in others))
        natural = name.fontMetrics().horizontalAdvance(name.text()) + 4
        return name, reserved, natural, margins.top() + margins.bottom()

    def _size_rows(self, metrics, row_width):
        for button, (name, reserved, _, padding) in metrics:
            name_width = max(1, row_width - reserved - 4)
            button.setFixedHeight(max(50, name.heightForWidth(name_width) + padding + 4))
        self._layout.invalidate()
        return self._layout.sizeHint().height()

    def place_below_or_above(self, field):
        screen = field.screen().availableGeometry()
        anchor = field.mapToGlobal(QPoint(0, 0))
        margins = self._layout.contentsMargins()
        frame = self.contentsMargins()
        inset = margins.left() + margins.right() + frame.left() + frame.right() + 4
        metrics = [(self._layout.itemAt(i).widget(),
                    self._row_metrics(self._layout.itemAt(i).widget()))
                   for i in range(self._layout.count() - 1)]
        wanted = max((reserved + natural for _, (_, reserved, natural, _) in metrics), default=0)
        width = min(max(field.width(), wanted + inset), screen.width())
        height = self._size_rows(metrics, width - inset)
        vertical_inset = frame.top() + frame.bottom() + 4
        top = max(screen.top(), min(anchor.y(), screen.bottom() + 1))
        bottom = max(screen.top(), min(anchor.y() + field.height(), screen.bottom() + 1))
        above, below = top - screen.top(), screen.bottom() + 1 - bottom
        use_below = height + vertical_inset <= below or below >= above
        available = max(1, below if use_below else above)
        if height + vertical_inset > available:
            bar_width = self._scroll.verticalScrollBar().sizeHint().width()
            width = min(max(width, wanted + inset + bar_width), screen.width())
            height = self._size_rows(metrics, width - inset - bar_width)
        self._content.setMinimumHeight(height)
        popup_height = min(height + vertical_inset, available)
        x = max(screen.left(), min(anchor.x() + field.width() - width,
                                   screen.right() + 1 - width))
        y = bottom if use_below else top - popup_height
        self.resize(width, popup_height)
        self.move(x, y)
        self.show()
        self._scroll.verticalScrollBar().setValue(0)

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
        self._scroll.ensureWidgetVisible(self._layout.itemAt(self.selected).widget(), 0, 0)

    def choose(self):
        if self.selected < 0:
            return False
        self._layout.itemAt(self.selected).widget().click()
        return True


class ItemSearch(QLineEdit):
    def __init__(self, form, kinds, icon_size=16, *, sale_price=None):
        super().__init__(form)
        self.form = form
        self.kinds = kinds
        self.sale_price = sale_price
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
        balances = (inventory.on_hand_many(self.form.session.con,
                                           (row[0] for row in rows if row[1] in ("tire", "battery")))
                    if self.sale_price is not None else {})
        self.popup.show_results(rows, self.pick, self.new_item, query,
                                sale_price=self.sale_price, balances=balances)
        self.popup.place_below_or_above(self)

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
