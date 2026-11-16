"""Purchases screen: invoice list and a full-page form matching the approved v1.0 design."""
import uuid
from datetime import date

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from morv.core import dot as dotmod
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import items, purchase_view, purchasing, suppliers
from morv.infra.purchasing import PurchaseError, PurchaseLine
from morv.infra import suppliers as sup_infra
from morv.ui import icons
from morv.ui.listpage import ListPage
from morv.ui.widgets import Notice, ltr_edit, muted, primary_button

ITEM_CHOICES = 50
KIND_ICON = {"tire": "tire", "battery": "battery"}
ICON_MUTED = "#8792A3"
ICON_PRIMARY = "#1F5FD6"


class PurchasesPage(QStackedWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.list_page = PurchasesListPage(session, self)
        self.form_page = None
        self.addWidget(self.list_page)

    def open_form(self) -> None:
        if self.form_page is not None:
            self.removeWidget(self.form_page)
            self.form_page.deleteLater()
        self.form_page = PurchaseForm(self.session, self)
        self.addWidget(self.form_page)
        self.setCurrentWidget(self.form_page)

    def back_to_list(self, keep_id: int | None = None) -> None:
        self.setCurrentWidget(self.list_page)
        self.list_page.reload(keep_id=keep_id)


class PurchasesListPage(ListPage):
    title = "المشتريات"
    add_label = "فاتورة شراء جديدة"
    search_placeholder = "رقم الفاتورة، المورد، أو رقم فاتورة المورد (F2)"
    columns = [
        ("الفاتورة", lambda r: ltr(r[1])),
        ("المورد", lambda r: r[2]),
        ("رقم فاتورة المورد", lambda r: ltr(r[3])),
        ("التاريخ", lambda r: ltr(r[4])),
        ("الإجمالي", lambda r: ltr(fmt(r[5]))),
    ]

    def __init__(self, session, stack=None):
        super().__init__(session)
        self.stack = stack

    def can_edit(self) -> bool:
        return self.session.can("purchases.post")

    def can_view(self) -> bool:
        return self.session.can("purchases.post") or self.session.can("items.cost_view")

    def fetch(self, query, filter_key, offset):
        if not self.can_view():
            return []
        return purchase_view.list_invoices(self.session.con, query, offset=offset)

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير الشراء"
        return f"{purchase_view.count(self.session.con)} فاتورة شراء"

    def build_preview(self, row_id: int) -> None:
        inv = purchase_view.get_invoice(self.session.con, row_id)
        self.heading(inv.number, f"المورد: {inv.supplier_name}")
        self.kv("رقم فاتورة المورد", ltr(inv.supplier_invoice_no))
        self.kv("تاريخ الفاتورة", ltr(inv.invoice_date))
        self.kv("تاريخ الاستلام", ltr(inv.received_date))
        self.kv("الإجمالي", ltr(fmt(inv.total)))
        if inv.notes:
            self.kv("ملاحظات", inv.notes)
        for ln in inv.lines:
            dot = f" · DOT {ltr(ln.dot)}" if ln.dot else ""
            self.kv(f"{ln.line_no}. {ln.item_name}",
                    f"{ltr(str(ln.qty))} × {ltr(fmt(ln.unit_cost))} = {ltr(fmt(ln.line_total))}{dot}")

    def open_dialog(self, row_id):
        if row_id is not None:
            self.show_preview_id(row_id)
            return
        if not self.can_edit() or self.stack is None:
            return
        self.stack.open_form()


# ---------------------------------------------------------------------------
# Supplier search popup (live search, shown below the supplier field)
# ---------------------------------------------------------------------------

class _SupplierPopup(QFrame):
    """Floating list that appears under the supplier search field."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        # Qt.WindowType.Popup grabs the keyboard on show, which stole focus from
        # the search field and swallowed the next keystroke. A non-activating
        # tool window lets typing continue; see PurchaseForm.eventFilter for the
        # focus-out close.
        self.setWindowFlags(Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(2)

    def show_results(self, rows, on_pick, on_new, query: str) -> None:
        # Clear previous buttons
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row in rows[:8]:
            sup_id, name, mobile = row[0], row[1], row[2]
            btn = QPushButton()
            btn.setFlat(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(44)
            inner = QHBoxLayout(btn)
            inner.setContentsMargins(8, 4, 8, 4)
            name_lbl = QLabel(name)
            name_lbl.setObjectName("cardTitle")
            mob_lbl = muted(ltr(mobile))
            inner.addWidget(name_lbl)
            inner.addWidget(mob_lbl)
            inner.addStretch(1)
            btn.clicked.connect(lambda _, sid=sup_id, sname=name, smob=mobile: on_pick(sid, sname, smob))
            self._layout.addWidget(btn)
        # "New supplier" option
        new_btn = QPushButton(f"+ مورد جديد: {query}" if query else "+ مورد جديد")
        new_btn.setObjectName("primary")
        new_btn.setFlat(True)
        new_btn.setFixedHeight(38)
        new_btn.clicked.connect(on_new)
        self._layout.addWidget(new_btn)
        self.adjustSize()


# ---------------------------------------------------------------------------
# Item search popup
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# "New supplier" inline dialog
# ---------------------------------------------------------------------------

class _NewSupplierDialog(QFrame):
    """Overlay dialog for adding a new supplier inline (design: .overlay / .dlg)."""

    def __init__(self, name_hint: str, mobile_hint: str, session, on_save, on_cancel, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.session = session
        self._on_save = on_save
        self._on_cancel = on_cancel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        head_w = QWidget()
        head_w.setObjectName("cardHead")
        head_layout = QHBoxLayout(head_w)
        head_layout.setContentsMargins(18, 12, 18, 12)
        title = QLabel("مورد جديد")
        title.setObjectName("cardTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("dangerLink")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(on_cancel)
        head_layout.addWidget(title)
        head_layout.addStretch(1)
        head_layout.addWidget(close_btn)
        layout.addWidget(head_w)

        # Body
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 14, 18, 14)
        body_layout.setSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("اسم المورد")
        self.name_edit.setText(name_hint)
        self.mobile_edit = ltr_edit("01xxxxxxxxx")
        self.mobile_edit.setText(mobile_hint)
        self.addr_edit = QLineEdit()
        self.addr_edit.setPlaceholderText("العنوان")
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("ملاحظات")
        grid.addWidget(self._field("اسم المورد *", self.name_edit), 0, 0, 1, 2)
        grid.addWidget(self._field("رقم الموبايل *", self.mobile_edit), 1, 0)
        grid.addWidget(self._field("العنوان", self.addr_edit), 1, 1)
        grid.addWidget(self._field("ملاحظات", self.notes_edit), 2, 0, 1, 2)
        body_layout.addLayout(grid)
        self.hint = muted("باقي بيانات المورد تُكمل لاحقًا من شاشة الموردين.")
        body_layout.addWidget(self.hint)
        self.notice = Notice()
        body_layout.addWidget(self.notice)
        layout.addWidget(body)

        # Footer
        foot = QWidget()
        foot.setObjectName("cardHead")
        foot_layout = QHBoxLayout(foot)
        foot_layout.setContentsMargins(18, 10, 18, 10)
        save_btn = primary_button("حفظ واختيار المورد")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("إلغاء")
        cancel_btn.clicked.connect(on_cancel)
        foot_layout.addWidget(save_btn)
        foot_layout.addWidget(cancel_btn)
        foot_layout.addStretch(1)
        layout.addWidget(foot)

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        mobile = self.mobile_edit.text().strip()
        if not name:
            self.notice.show_message("اسم المورد مطلوب.")
            return
        if not mobile:
            self.notice.show_message("رقم الموبايل مطلوب.")
            return
        try:
            s = sup_infra.Supplier(id=None, name=name, mobile=mobile,
                                   address=self.addr_edit.text().strip(),
                                   notes=self.notes_edit.text().strip())
            saved = sup_infra.create(self.session.con, self.session.user, s)
        except sup_infra.SupplierError as e:
            self.notice.show_message(str(e))
            return
        self._on_save(saved)

    @staticmethod
    def _field(title: str, widget: QWidget) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        lbl = QLabel(title)
        lbl.setObjectName("fieldLabel")
        v.addWidget(lbl)
        v.addWidget(widget)
        return w


# ---------------------------------------------------------------------------
# Full-page purchase form
# ---------------------------------------------------------------------------

class PurchaseForm(QWidget):
    def __init__(self, session, stack, parent=None):
        super().__init__(parent)
        self.session = session
        self.stack = stack
        self.posted = None
        self._busy = False
        self._request_key = uuid.uuid4().hex
        self._supplier_id: int | None = None
        self._supplier_name: str = ""
        self._supplier_mobile: str = ""
        self._new_sup_dialog: _NewSupplierDialog | None = None

        # Scroll area wrapping all content
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(12)

        self.notice = Notice()
        root.addWidget(self._header())
        root.addWidget(self.notice)

        # Two-column layout: mainCol + sumCol
        cols = QHBoxLayout()
        cols.setSpacing(14)

        main_col = QVBoxLayout()
        main_col.setSpacing(14)

        # ---- Card 1: Supplier & invoice header ----
        box, body, _head = self._card("المورد والفاتورة", "suppliers")

        # Supplier search field
        self.sup_search = QLineEdit()
        self.sup_search.setPlaceholderText("اسم المورد أو رقم الموبايل")
        self.sup_search.addAction(icons.icon("search", ICON_MUTED, 16),
                                  QLineEdit.ActionPosition.LeadingPosition)
        self.sup_search.textChanged.connect(self._on_sup_query)
        self.sup_search.installEventFilter(self)
        self._sup_timer = QTimer(self)
        self._sup_timer.setSingleShot(True)
        self._sup_timer.timeout.connect(self._do_sup_search)

        self._sup_popup = _SupplierPopup(self)
        self._sup_popup.hide()

        # Supplier strip (shown after selection)
        self._sup_strip = QFrame()
        self._sup_strip.setObjectName("pillWarn")
        sup_strip_layout = QHBoxLayout(self._sup_strip)
        sup_strip_layout.setContentsMargins(12, 8, 12, 8)
        sup_strip_layout.setSpacing(10)
        self._sup_strip_name = QLabel()
        self._sup_strip_name.setObjectName("cardTitle")
        self._sup_strip_mobile = muted("")
        change_btn = QPushButton("تغيير")
        change_btn.setObjectName("dangerLink")
        change_btn.clicked.connect(self._clear_supplier)
        sup_strip_layout.addWidget(self._sup_strip_name)
        sup_strip_layout.addWidget(self._sup_strip_mobile)
        sup_strip_layout.addStretch(1)
        sup_strip_layout.addWidget(change_btn)
        self._sup_strip.hide()

        sup_field_w = QWidget()
        sup_field_v = QVBoxLayout(sup_field_w)
        sup_field_v.setContentsMargins(0, 0, 0, 0)
        sup_field_v.setSpacing(6)
        sup_field_v.addWidget(self._label("المورد *"))
        sup_field_v.addWidget(self.sup_search)
        sup_field_v.addWidget(self._sup_strip)

        # Invoice header fields grid
        hdr_grid = QGridLayout()
        hdr_grid.setHorizontalSpacing(12)
        hdr_grid.setVerticalSpacing(12)
        for c in range(4):
            hdr_grid.setColumnStretch(c, 1)

        self._received_date = date.today().isoformat()
        entry_no_preview = purchasing.next_number_preview(self.session.con)
        self.entry_no = ltr_edit("")
        self.entry_no.setText(entry_no_preview)
        self.entry_no.setReadOnly(True)
        self.recv_date = ltr_edit("")
        self.recv_date.setText(self._received_date)
        self.recv_date.setReadOnly(True)

        self.inv_no = ltr_edit("كما في فاتورته")
        self.inv_date = ltr_edit("YYYY-MM-DD")
        self.inv_date.setText(date.today().isoformat())
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("اختياري")

        hdr_grid.addWidget(self._field("رقم الإدخال", self.entry_no), 0, 0)
        hdr_grid.addWidget(sup_field_w, 0, 1, 1, 2)
        hdr_grid.addWidget(self._field("تاريخ الاستلام", self.recv_date), 0, 3)
        hdr_grid.addWidget(self._field("رقم فاتورة المورد *", self.inv_no), 1, 0)
        hdr_grid.addWidget(self._field("تاريخ فاتورة المورد", self.inv_date), 1, 1)
        hdr_grid.addWidget(self._field("ملاحظة", self.notes), 1, 2, 1, 2)
        body.addLayout(hdr_grid)
        main_col.addWidget(box)

        # ---- Card 2: Invoice lines ----
        ibox, ibody, ihead_w = self._card("الأصناف المستلمة", "purchases")

        # Item count chip in card header (we'll update it)
        self._line_count_lbl = muted("0 سطر")
        ihead_w.findChild(QHBoxLayout).addWidget(self._line_count_lbl)

        # Item search — picking a result adds it straight to the lines below
        item_search_row = QHBoxLayout()
        item_search_row.setSpacing(8)
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("اسم الصنف، الكود، المقاس أو امسح الباركود")
        self.item_search.addAction(icons.icon("search", ICON_MUTED, 16),
                                   QLineEdit.ActionPosition.LeadingPosition)
        self.item_search.addAction(icons.icon("barcode", ICON_MUTED, 16),
                                   QLineEdit.ActionPosition.TrailingPosition)
        self.item_search.textChanged.connect(self._on_item_query)
        self.item_search.installEventFilter(self)
        self._item_timer = QTimer(self)
        self._item_timer.setSingleShot(True)
        self._item_timer.timeout.connect(self._do_item_search)

        self._item_popup = _ItemPopup(self)
        self._item_popup.hide()

        new_item_btn = QPushButton("صنف جديد")
        new_item_btn.clicked.connect(self._open_new_item_dialog)
        item_search_row.addWidget(self.item_search, 1)
        item_search_row.addWidget(new_item_btn)
        ibody.addLayout(item_search_row)

        # Lines header row (column titles, once)
        self._rows: list[dict] = []
        lines_head = QHBoxLayout()
        lines_head.setContentsMargins(4, 0, 4, 0)
        lines_head.setSpacing(10)
        for text, stretch in (("#", 0), ("الصنف", 3), ("الكمية", 1),
                              ("تكلفة الوحدة", 1), ("DOT", 1), ("الإجمالي", 1), ("", 0)):
            h_lbl = muted(text)
            h_lbl.setWordWrap(False)
            lines_head.addWidget(h_lbl, stretch)
        ibody.addLayout(lines_head)

        # Line rows: each row is self-contained (inline qty/cost/DOT + delete icon)
        self._rows_container = QVBoxLayout()
        self._rows_container.setSpacing(4)
        ibody.addLayout(self._rows_container, 1)

        self._empty_lbl = muted("لا توجد أصناف بعد. ابحث عن صنف أو امسح الباركود، أو أضف صنفًا جديدًا.")
        self._rows_container.addWidget(self._empty_lbl)

        main_col.addWidget(ibox, 1)
        cols.addLayout(main_col, 3)

        # ---- sumCol: summary card ----
        sum_col = QVBoxLayout()
        sum_col.setSpacing(14)

        sbox, sbody, _shead = self._card("ملخص فاتورة الشراء", "purchases")

        self._sub_lbl = QLabel(ltr(fmt(0)))
        self._sub_lbl.setObjectName("cardTitle")
        sbody.addWidget(self._kv_row("إجمالي الأصناف", self._sub_lbl))

        self.disc = ltr_edit("0.00")
        self.tax = ltr_edit("0.00")
        self.ship = ltr_edit("0.00")
        self.disc.textChanged.connect(self._refresh_summary)
        self.tax.textChanged.connect(self._refresh_summary)
        self.ship.textChanged.connect(self._refresh_summary)

        sbody.addWidget(self._field("خصم المورد", self.disc))
        sbody.addWidget(self._field("ضريبة على فاتورة المورد", self.tax))
        sbody.addWidget(self._field("نقل وتحميل", self.ship))

        self._total_lbl = QLabel()
        self._total_lbl.setObjectName("pageTitle")
        sbody.addWidget(self._total_lbl)

        # Payment row
        pay_grid = QGridLayout()
        pay_grid.setHorizontalSpacing(10)
        self.paid = ltr_edit("0.00")
        self.paid.textChanged.connect(self._refresh_summary)
        self.pay_method = QComboBox()
        for label in ("الخزينة (نقدي)", "تحويل بنكي", "InstaPay / محفظة"):
            self.pay_method.addItem(label)
        pay_grid.addWidget(self._field("مدفوع الآن", self.paid), 0, 0)
        pay_grid.addWidget(self._field("من", self.pay_method), 0, 1)
        sbody.addLayout(pay_grid)

        self._rem_lbl = QLabel()
        self._rem_lbl.setObjectName("cardTitle")
        sbody.addWidget(self._rem_lbl)

        # Main action
        self.post_btn = primary_button("اعتماد الاستلام وتحديث المخزون ⌨ F9")
        self.post_btn.clicked.connect(self.post)
        sbody.addWidget(self.post_btn)

        # Deferred actions
        sbody.addWidget(self._deferred_button("حفظ كمسودة"))
        sbody.addWidget(self._deferred_button("طباعة إذن الاستلام"))

        sbody.addStretch(1)

        sum_col.addWidget(sbox)
        cols.addLayout(sum_col, 1)
        root.addLayout(cols, 1)

        self._refresh_summary()
        if not suppliers.count(session.con):
            self.notice.show_message("لا يوجد موردون نشطون؛ أضف مورّدًا من شاشة الموردين أولًا.", "warn")

    # ---------- Header ----------
    def _header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        title = QLabel("فاتورة شراء / استلام بضاعة")
        title.setObjectName("pageTitle")
        h.addWidget(title)
        subtitle = QLabel("المشتريات · الاستلام في: المخزن الرئيسي")
        subtitle.setObjectName("muted")
        h.addWidget(subtitle)
        h.addStretch(1)
        h.addWidget(self._deferred_button("إرفاق صورة فاتورة المورد"))
        back = QPushButton("رجوع للقائمة")
        back.clicked.connect(lambda: self.stack.back_to_list())
        h.addWidget(back)
        return w

    # ---------- Builder helpers ----------
    @staticmethod
    def _card(title: str, icon_name: str) -> tuple:
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
        return box, body, head_w

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def _field(self, title: str, widget: QWidget) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(self._label(title))
        v.addWidget(widget)
        return w

    @staticmethod
    def _kv_row(label: str, value_widget: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 4)
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        h.addWidget(lbl)
        h.addStretch(1)
        h.addWidget(value_widget)
        return w

    @staticmethod
    def _deferred_button(label: str) -> QPushButton:
        b = QPushButton(label)
        b.setEnabled(False)
        b.setToolTip("قريبًا — غير مبني بعد")
        return b

    # ---------- Supplier search ----------
    def _on_sup_query(self, text: str) -> None:
        if self._supplier_id is not None:
            return
        self._sup_timer.start(200)

    def _do_sup_search(self) -> None:
        q = self.sup_search.text().strip()
        rows = suppliers.search(self.session.con, q, limit=8)
        if not rows and not q:
            self._sup_popup.hide()
            return
        self._sup_popup.show_results(
            rows,
            on_pick=self._pick_supplier,
            on_new=self._open_new_supplier_dialog,
            query=q,
        )
        # Position below the search field
        pos = self.sup_search.mapToGlobal(self.sup_search.rect().bottomLeft())
        self._sup_popup.move(pos)
        self._sup_popup.resize(self.sup_search.width(), self._sup_popup.sizeHint().height())
        self._sup_popup.show()

    def _pick_supplier(self, sup_id: int, name: str, mobile: str) -> None:
        self._supplier_id = sup_id
        self._supplier_name = name
        self._supplier_mobile = mobile
        self.sup_search.hide()
        self._sup_strip_name.setText(name)
        self._sup_strip_mobile.setText(ltr(mobile))
        self._sup_strip.show()
        self._sup_popup.hide()

    def _clear_supplier(self) -> None:
        self._supplier_id = None
        self._supplier_name = ""
        self._supplier_mobile = ""
        self.sup_search.clear()
        self.sup_search.show()
        self._sup_strip.hide()

    def _open_new_supplier_dialog(self) -> None:
        self._sup_popup.hide()
        q = self.sup_search.text().strip()
        name_hint = q if not q.replace(" ", "").isdigit() else ""
        mobile_hint = q if q.replace(" ", "").isdigit() else ""
        if self._new_sup_dialog:
            self._new_sup_dialog.deleteLater()
        self._new_sup_dialog = _NewSupplierDialog(
            name_hint=name_hint, mobile_hint=mobile_hint,
            session=self.session,
            on_save=self._on_new_supplier_saved,
            on_cancel=lambda: self._new_sup_dialog.hide() if self._new_sup_dialog else None,
            parent=self,
        )
        self._new_sup_dialog.setMinimumWidth(500)
        # Center over form
        self._new_sup_dialog.move(
            self.rect().center() - self._new_sup_dialog.rect().center()
        )
        self._new_sup_dialog.show()
        self._new_sup_dialog.raise_()

    def _on_new_supplier_saved(self, saved_supplier) -> None:
        if self._new_sup_dialog:
            self._new_sup_dialog.hide()
        self._pick_supplier(saved_supplier.id, saved_supplier.name, saved_supplier.mobile)

    # ---------- Item search ----------
    def _on_item_query(self, text: str) -> None:
        self._item_timer.start(200)

    def _do_item_search(self) -> None:
        q = self.item_search.text().strip()
        if not q:
            self._item_popup.hide()
            return
        rows = []
        for kind in ("tire", "battery"):
            rows += items.search(self.session.con, q, kind=kind, limit=ITEM_CHOICES)
        rows = rows[:8]
        self._item_popup.show_results(
            rows,
            on_pick=self._pick_item,
            on_new=self._open_new_item_dialog,
            query=q,
        )
        pos = self.item_search.mapToGlobal(self.item_search.rect().bottomLeft())
        self._item_popup.move(pos)
        self._item_popup.resize(self.item_search.width(), self._item_popup.sizeHint().height())
        self._item_popup.show()

    def _pick_item(self, item_id: int, kind: str, name: str) -> None:
        self.item_search.clear()
        self._item_popup.hide()
        self._add_row(item_id, kind, name)

    def _open_new_item_dialog(self) -> None:
        self._item_popup.hide()
        self.notice.show_message("إضافة صنف جديد — قريبًا", "warn")

    # ---------- Lines: one self-contained row per item, added on pick ----------
    def _add_row(self, item_id: int, kind: str, name: str) -> None:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(10)

        num_lbl = muted("")
        num_lbl.setWordWrap(False)
        h.addWidget(num_lbl, 0)

        item_w = QWidget()
        iw = QHBoxLayout(item_w)
        iw.setContentsMargins(0, 0, 0, 0)
        iw.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.icon(KIND_ICON.get(kind, "inventory"), ICON_MUTED, 16).pixmap(16, 16))
        name_lbl = QLabel(name)
        iw.addWidget(ic)
        iw.addWidget(name_lbl, 1)
        h.addWidget(item_w, 3)

        qty_spin = QSpinBox()
        qty_spin.setRange(1, 100000)
        qty_spin.setValue(1)
        h.addWidget(qty_spin, 1)

        cost_edit = ltr_edit("0.00")
        h.addWidget(cost_edit, 1)

        dot_edit = ltr_edit("WWYYYY")
        h.addWidget(dot_edit, 1)

        total_lbl = QLabel(ltr(fmt(0)))
        total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
                               | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(total_lbl, 1)

        del_btn = QPushButton()
        del_btn.setFlat(True)
        del_btn.setIcon(icons.icon("trash", ICON_MUTED, 16))
        del_btn.setFixedSize(30, 30)
        del_btn.setToolTip("حذف السطر")
        h.addWidget(del_btn, 0)

        row = {"widget": row_w, "item_id": item_id, "kind": kind, "name": name,
              "num_lbl": num_lbl, "qty_spin": qty_spin, "cost_edit": cost_edit,
              "dot_edit": dot_edit, "total_lbl": total_lbl}
        qty_spin.valueChanged.connect(self._on_row_changed)
        cost_edit.textChanged.connect(self._on_row_changed)
        del_btn.clicked.connect(lambda _=False, rw=row: self._remove_row(rw))

        self._rows.append(row)
        self._empty_lbl.hide()
        self._rows_container.addWidget(row_w)
        self._renumber()
        self._on_row_changed()

    def _remove_row(self, row: dict) -> None:
        self._rows.remove(row)
        self._rows_container.removeWidget(row["widget"])
        row["widget"].deleteLater()
        if not self._rows:
            self._empty_lbl.show()
        self._renumber()
        self._on_row_changed()

    def _renumber(self) -> None:
        for i, row in enumerate(self._rows, start=1):
            row["num_lbl"].setText(str(i))

    def _on_row_changed(self) -> None:
        for row in self._rows:
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError:
                cost = 0
            row["total_lbl"].setText(ltr(fmt(row["qty_spin"].value() * cost)))
        self._line_count_lbl.setText(f"{len(self._rows)} سطر")
        self._refresh_summary()

    def _sub_total(self) -> int:
        total = 0
        for row in self._rows:
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError:
                cost = 0
            total += row["qty_spin"].value() * cost
        return total

    def _validate_lines(self) -> tuple[list[PurchaseLine] | None, str | None]:
        """Parse and validate every row. Returns (lines, None) or (None, error)."""
        if not self._rows:
            return None, "أضف سطرًا واحدًا على الأقل قبل الترحيل."
        lines = []
        for i, row in enumerate(self._rows, start=1):
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError as e:
                return None, f"السطر {i} ({row['name']}): تكلفة الوحدة: {e}"
            if cost <= 0:
                return None, f"السطر {i} ({row['name']}): تكلفة الوحدة يجب أن تكون أكبر من صفر."
            dot_text = row["dot_edit"].text().strip()
            dot = None
            if dot_text:
                try:
                    dot = dotmod.parse_dot(dot_text)
                except dotmod.DotError as e:
                    return None, f"السطر {i} ({row['name']}): {e}"
            elif row["kind"] == "tire":
                return None, f"رمز DOT إلزامي للإطار «{row['name']}»."
            lines.append(PurchaseLine(row["item_id"], row["qty_spin"].value(), cost, dot))
        return lines, None

    def _refresh_summary(self) -> None:
        sub = self._sub_total()
        self._sub_lbl.setText(ltr(fmt(sub)))
        try:
            disc = parse_money(self.disc.text()) if self.disc.text().strip() else 0
        except MoneyError:
            disc = 0
        try:
            tax = parse_money(self.tax.text()) if self.tax.text().strip() else 0
        except MoneyError:
            tax = 0
        try:
            ship = parse_money(self.ship.text()) if self.ship.text().strip() else 0
        except MoneyError:
            ship = 0
        total = sub - disc + tax + ship
        self._total_lbl.setText(f"إجمالي الفاتورة: {ltr(fmt(total))} ج.م")
        try:
            paid = parse_money(self.paid.text()) if self.paid.text().strip() else 0
        except MoneyError:
            paid = 0
        rem = total - paid
        if rem > 0:
            self._rem_lbl.setText(f"يُضاف لرصيد المورد: {ltr(fmt(rem))} ج.م")
        elif rem < 0:
            self._rem_lbl.setText(f"المدفوع أكبر من الفاتورة بـ {ltr(fmt(-rem))} ج.م")
        else:
            self._rem_lbl.setText("مدفوعة بالكامل")

    def _fail(self, text: str) -> bool:
        self.notice.show_message(text)
        return False


    def eventFilter(self, obj, event) -> bool:
        # Live-search popups must not steal keyboard focus from the field (see
        # _SupplierPopup/_ItemPopup window flags); close them shortly after the
        # field loses focus so a click on a popup button still registers first.
        if event.type() == QEvent.Type.FocusOut:
            if obj is self.sup_search:
                QTimer.singleShot(150, self._sup_popup.hide)
            elif obj is self.item_search:
                QTimer.singleShot(150, self._item_popup.hide)
        return super().eventFilter(obj, event)

    # ---------- Posting ----------
    def post(self) -> None:
        if self._busy:
            return
        if self._supplier_id is None:
            self._fail("اختر المورد.")
            return
        if not self.inv_no.text().strip():
            self._fail("رقم فاتورة المورد مطلوب.")
            return
        lines, err = self._validate_lines()
        if err:
            self._fail(err)
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = purchasing.post_invoice(
                self.session.con, self.session.user,
                request_key=self._request_key,
                supplier_id=self._supplier_id,
                supplier_invoice_no=self.inv_no.text(),
                invoice_date=self.inv_date.text().strip(),
                received_date=self._received_date,
                lines=lines,
                notes=self.notes.text(),
            )
        except PurchaseError as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self.stack.back_to_list(keep_id=self.posted.id)
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)
