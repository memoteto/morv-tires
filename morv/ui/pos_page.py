"""Point of sale: the whole sale invoice in the screen, posted by the sales engine."""
import uuid
from datetime import date, datetime

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from morv.core import sales_rules
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import customers, items, sale_view, sales
from morv.infra.sales import SaleError, SaleLineIn
from morv.ui import icons
from morv.ui.inventory_page import ItemDialog
from morv.ui.purchases_page import _ItemPopup
from morv.ui.widgets import Notice, ltr_edit, muted

ITEM_CHOICES = 50
TIER_ATTR = {"retail": "price_retail", "wholesale": "price_wholesale", "super": "price_super"}
TIER_LABELS = {"retail": "قطاعي (تجزئة)", "wholesale": "جملة", "super": "جملة الجملة"}
TIER_SHORT = {"retail": "قطاعي", "wholesale": "جملة", "super": "جملة الجملة"}
PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة / ماكينة POS", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                  "installment": "تقسيط"}
PAYMENT_HINTS = {"cash": "استلام نقدية وحساب الباقي", "card": "يلزم رقم الإيصال", "transfer": "يلزم رقم المرجع",
                 "wallet": "يلزم رقم المرجع", "installment": "يُفتح العقد بعد الاعتماد"}
KIND_TABS = (("الكل", None), ("إطارات", "tire"), ("بطاريات", "battery"), ("خدمات", "service"))
KIND_ICON = {"tire": "tire", "battery": "battery", "service": "service"}
ICON_MUTED = "#8792A3"
ICON_PRIMARY = "#1F5FD6"


class PosPage(QWidget):
    contract_needed = Signal(int)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.posted = None
        self._busy = False
        self._request_key = uuid.uuid4().hex     # One key per invoice: pressing again cannot duplicate it
        self.tier = "retail"
        self._kind = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 8)
        root.setSpacing(12)
        self.notice = Notice()
        root.addWidget(self._build_header())
        root.addWidget(self.notice)
        main = QHBoxLayout()
        main.setSpacing(16)
        content = QWidget()
        content.setLayout(self._build_right_column())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The old per-column QTableView forced its own width (ResizeToContents
        # columns) wider than the viewport, which showed as a horizontal
        # scrollbar under the whole screen. Rows are self-built, stretch-based
        # widgets now, so they compress to fit; this policy is a hard backstop.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        main.addWidget(scroll, 3)
        main.addWidget(self._build_left_panel(), 1)
        root.addLayout(main, 1)
        root.addLayout(self._build_hints())
        self._shortcuts()

        self._stamp()
        self._on_customer_changed()
        self._on_row_changed()
        if not items.search(self.session.con, "", limit=1):
            self.notice.show_message("لا توجد أصناف نشطة؛ أضف أصنافًا من شاشة المخزون أولًا.", "warn")

    # ---------- Layout ----------
    def _build_header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        title = QLabel("فاتورة بيع جديدة")
        title.setObjectName("pageTitle")
        h.addWidget(title)
        user = getattr(self.session.user, "display_name", "") or ""
        # A plain, non-wrapping label: muted() turns word-wrap on, which broke
        # this long line onto two lines instead of letting it truncate.
        subtitle = QLabel(f"نقطة البيع · المخزن الرئيسي · الكاشير: {user}")
        subtitle.setObjectName("muted")
        h.addWidget(subtitle)
        draft = QLabel("مسودة")
        draft.setObjectName("pillWarn")
        h.addWidget(draft)
        h.addStretch(1)
        h.addWidget(self._deferred_button("تعليق الفاتورة", "pause"))
        h.addWidget(self._deferred_button("معاينة الطباعة", "printer"))
        self.cancel_btn = QPushButton("إلغاء")
        self.cancel_btn.setObjectName("dangerLink")
        self.cancel_btn.setIcon(icons.icon("close", "#B42323", 16))
        self.cancel_btn.clicked.connect(self.cancel_invoice)
        h.addWidget(self.cancel_btn)
        return w

    def _build_hints(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addStretch(1)
        for key, label in (("F2", "صنف"), ("F4", "عميل"), ("F8", "الدفع"), ("F9", "اعتماد")):
            bar.addWidget(muted(label))
            bar.addWidget(self._kbd(key))
            bar.addSpacing(8)
        return bar

    def _build_right_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addWidget(self._invoice_card())
        col.addWidget(self._items_card(), 1)
        return col

    def _invoice_card(self) -> QFrame:
        box, body, head = self._card("بيانات الفاتورة والعميل", "sales")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for c, stretch in enumerate((3, 2, 2)):
            grid.setColumnStretch(c, stretch)

        # Read-only facts live in the card header, so they take no field row
        self.number_edit = self._chip(sales.next_number_preview(self.session.con))
        self.sale_date = self._chip("")
        self.number_edit.setToolTip("رقم الفاتورة")
        self.sale_date.setToolTip("التاريخ والوقت")
        head.addWidget(self.number_edit)
        head.addWidget(self.sale_date)
        self.tier_combo = QComboBox()
        for key, label in TIER_LABELS.items():
            self.tier_combo.addItem(label, key)
        self.tier_combo.currentIndexChanged.connect(lambda _i: self._set_tier(self.tier_combo.currentData()))
        self.tier_combo.setEnabled(bool(getattr(self.session.user, "is_manager", False)))
        self.odometer = ltr_edit("مثال: 84500")

        self.customer = QComboBox()
        self.customer.addItem("عميل نقدي", ("", "retail", "", ""))
        for row in customers.search(self.session.con, "", limit=300):
            self.customer.addItem(row[1], (row[0], row[3], row[2] or "", (row[5] or "").strip()))
        self.customer.setEditable(True)        # Typing the customer name searches, as the design asks
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.customer.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.customer.lineEdit().setPlaceholderText("اكتب اسم العميل للبحث")
        self.customer.lineEdit().addAction(icons.icon("user", ICON_MUTED, 18), QLineEdit.ActionPosition.LeadingPosition)
        self.customer.lineEdit().editingFinished.connect(self._on_customer_typed)
        # The list preloads only the first 300 customers, so typing also looks the rest up
        self._customer_lookup = QTimer(self)
        self._customer_lookup.setSingleShot(True)
        self._customer_lookup.setInterval(250)
        self._customer_lookup.timeout.connect(lambda: self._load_customer_matches(self.customer.lineEdit().text()))
        self.customer.lineEdit().textEdited.connect(lambda _t: self._customer_lookup.start())
        self.customer.currentIndexChanged.connect(self._on_customer_changed)
        self.mobile = ltr_edit("ابحث برقم الموبايل")
        self._trail_icon(self.mobile, "phone")
        self.mobile.editingFinished.connect(self._on_mobile_typed)
        self.plate = ltr_edit("رقم اللوحة")
        self._trail_icon(self.plate, "car")
        grid.addWidget(self._field("العميل *", self.customer), 0, 0)
        grid.addWidget(self._field("رقم الموبايل", self.mobile), 0, 1)
        grid.addWidget(self._field("فئة السعر", self.tier_combo, locked=True), 0, 2)
        body.addLayout(grid)

        # Optional details stay folded until needed, which leaves the room to the item rows
        self.note = QLineEdit()
        self.note.setPlaceholderText("اختياري — تظهر في الطباعة")
        self.extra_box = QWidget()
        extra = QGridLayout(self.extra_box)
        extra.setContentsMargins(0, 0, 0, 0)
        extra.setHorizontalSpacing(12)
        extra.setVerticalSpacing(12)
        for c, stretch in enumerate((3, 2, 2)):
            extra.setColumnStretch(c, stretch)
        extra.addWidget(self._field("ملاحظة على الفاتورة", self.note), 0, 0)
        extra.addWidget(self._field("عداد الكيلومترات", self.odometer), 0, 1)
        extra.addWidget(self._field("المركبة / اللوحة", self.plate), 0, 2)
        self.extra_box.setVisible(False)
        body.addWidget(self.extra_box)

        strip = QFrame()
        strip.setObjectName("infoStrip")
        s = QHBoxLayout(strip)
        s.setContentsMargins(12, 8, 12, 8)
        s.setSpacing(8)
        self.cust_kind = QLabel()
        self.cust_tier_chip = self._chip("")
        self.cust_stats = muted("")
        self.cust_stats.setWordWrap(False)
        change = QPushButton("تغيير العميل")
        change.setObjectName("link")
        change.clicked.connect(self._focus_customer)
        self.extra_summary = muted("")
        self.extra_summary.setWordWrap(False)
        self.extra_toggle = QPushButton("تفاصيل المركبة والملاحظة")
        self.extra_toggle.setObjectName("link")
        self.extra_toggle.setCheckable(True)
        self.extra_toggle.toggled.connect(self.extra_box.setVisible)
        for field in (self.odometer, self.plate, self.note):
            field.textChanged.connect(self._refresh_extra_summary)
        s.addWidget(self.cust_kind)
        s.addWidget(self.cust_tier_chip)
        s.addWidget(self.cust_stats)
        s.addStretch(1)
        s.addWidget(self.extra_summary)
        s.addWidget(self.extra_toggle)
        s.addWidget(change)
        body.addWidget(strip)
        return box

    def _items_card(self) -> QFrame:
        box, body, head = self._card("الأصناف والخدمات", "purchases")
        self.tier_chip = self._chip("")
        self.count_chip = self._chip("")
        head.addWidget(self.tier_chip)
        head.addWidget(self.count_chip)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("اسم الصنف، الكود، المقاس (205/55R16) أو امسح الباركود")
        self.item_search.addAction(icons.icon("search", ICON_MUTED, 18), QLineEdit.ActionPosition.LeadingPosition)
        self._trail_icon(self.item_search, "barcode")
        # Live popup search, same component as the purchase-invoice screen:
        # picking a result adds the line straight away, no dropdown list step.
        self.item_search.textChanged.connect(self._on_item_query)
        self.item_search.installEventFilter(self)
        self._item_timer = QTimer(self)
        self._item_timer.setSingleShot(True)
        self._item_timer.timeout.connect(self._do_item_search)
        self._item_popup = _ItemPopup(self)
        self._item_popup.hide()
        top.addWidget(self.item_search, 1)
        top.addWidget(self._kbd("F2"))
        seg = QFrame()
        seg.setObjectName("segBar")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(2)
        self.kind_tabs = {}
        for label, kind in KIND_TABS:
            b = QPushButton(label)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setChecked(kind is None)
            b.clicked.connect(lambda _c=False, k=kind: self._set_kind(k))
            self.kind_tabs[kind] = b
            sl.addWidget(b)
        top.addWidget(seg)
        body.addLayout(top)

        quick = QHBoxLayout()
        quick.setSpacing(8)
        quick.addWidget(muted("إضافة سريعة:"))
        chip = self._deferred_button("الأصناف الأكثر بيعًا", "plus")
        chip.setObjectName("quickChip")
        quick.addWidget(chip)
        quick.addStretch(1)
        body.addLayout(quick)

        # Lines header row (column titles, once)
        self._rows: list[dict] = []
        lines_head = QHBoxLayout()
        lines_head.setContentsMargins(4, 0, 4, 0)
        lines_head.setSpacing(10)
        for text, stretch in (("#", 0), ("الصنف / الخدمة", 3), ("الكمية", 1),
                              ("السعر", 1), ("الخصم", 1), ("الإجمالي", 1), ("", 0)):
            h_lbl = muted(text)
            h_lbl.setWordWrap(False)
            lines_head.addWidget(h_lbl, stretch)
        body.addLayout(lines_head)

        # Line rows: each row is self-contained (inline qty/price/discount + delete icon)
        self._rows_container = QVBoxLayout()
        self._rows_container.setSpacing(4)
        body.addLayout(self._rows_container, 1)

        self.empty_label = muted("الفاتورة فارغة. ابحث عن صنف أو امسح الباركود للبدء.")
        self._rows_container.addWidget(self.empty_label)
        # Trailing stretch absorbs leftover height so rows stay pinned at the
        # top of the card instead of stretching/centering when there are few.
        self._rows_container.addStretch(1)
        return box

    def _build_left_panel(self) -> QWidget:
        """Summary and payment scroll on their own; the action buttons stay pinned underneath."""
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        footer = self._actions_footer()      # built first: the payment card reacts to post_btn
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)
        col.addWidget(self._summary_card())
        col.addWidget(self._payment_card())
        col.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        outer.addWidget(footer)
        return panel

    def _actions_footer(self) -> QWidget:
        footer = QWidget()
        col = QVBoxLayout(footer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self.post_btn = QPushButton("اعتماد وتحصيل")
        self.post_btn.setObjectName("primaryLarge")
        self.post_btn.setIcon(icons.icon("check", "#FFFFFF", 20))
        inner = QHBoxLayout(self.post_btn)
        inner.setContentsMargins(16, 0, 16, 0)
        inner.addStretch(1)
        inner.addWidget(self._kbd("F9", on_primary=True))
        self.post_btn.clicked.connect(self.post)
        col.addWidget(self.post_btn)
        row = QHBoxLayout()
        row.addWidget(self._deferred_button("حفظ كمسودة"), 1)
        row.addWidget(self._deferred_button("اعتماد وطباعة", "printer"), 1)
        col.addLayout(row)
        return footer

    def _summary_card(self) -> QFrame:
        box, body, _head = self._card("ملخص الفاتورة", "sales")
        self.subtotal_label = QLabel()
        self.invoice_disc = ltr_edit("0.00")
        self.invoice_disc.setFixedWidth(150)
        self.invoice_disc.textChanged.connect(self._refresh_total)
        for label, w in (("الإجمالي قبل الخصم", self.subtotal_label),
                          ("خصم على الفاتورة", self.invoice_disc),
                          ("الضريبة", self._chip("المحل غير مسجّل — بدون ضريبة"))):
            body.addWidget(self._kv_row(label, w))
        total = QFrame()
        total.setObjectName("totalBox")
        t = QHBoxLayout(total)
        t.setContentsMargins(16, 16, 16, 16)
        lbl = QLabel("الإجمالي النهائي")
        lbl.setObjectName("totalLabel")
        self.total_label = QLabel()
        self.total_label.setObjectName("totalValue")
        t.addWidget(lbl)
        t.addStretch(1)
        t.addWidget(self.total_label)
        body.addWidget(total)
        return box

    def _payment_card(self) -> QFrame:
        box, body, _head = self._card("الدفع", "cash")
        body.addWidget(self._label("طريقة الدفع"))
        method_row = QHBoxLayout()
        self.pay1_method = self._method_combo(first_blank=False)
        self.pay1_method.setObjectName("payMethod")
        self.pay1_method.currentIndexChanged.connect(self._on_method_changed)
        method_row.addWidget(self.pay1_method, 1)
        method_row.addWidget(self._kbd("F8"))
        body.addLayout(method_row)
        # The design offers credit beside the cash methods; the engine posts it but the screen
        # does not expose it yet. Instalments are a method of their own above.
        deferred_row = QHBoxLayout()
        deferred_row.addWidget(self._deferred_button("آجل", "customers"), 1)
        body.addLayout(deferred_row)

        pbox = QFrame()
        self.pay_box = pbox
        pbox.setObjectName("payBox")
        pb = QVBoxLayout(pbox)
        pb.setContentsMargins(12, 12, 12, 12)
        pb.setSpacing(10)
        rec = QHBoxLayout()
        self.pay1_received = ltr_edit("0.00")
        self.pay1_received.textChanged.connect(self._refresh_change)
        rec.addWidget(muted("المبلغ المستلم"))
        rec.addStretch(1)
        rec.addWidget(self.pay1_received, 2)
        pb.addLayout(rec)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.amount_chips = []
        for _ in range(3):
            b = QPushButton()
            b.setObjectName("quickChip")
            b.clicked.connect(lambda _c=False, btn=b: self.pay1_received.setText(btn.property("amount") or ""))
            self.amount_chips.append(b)
            chips.addWidget(b, 1)
        pb.addLayout(chips)
        self.pay1_ref = ltr_edit("رقم الإيصال / المرجع")
        pb.addWidget(self.pay1_ref)
        change = QFrame()
        change.setObjectName("changeBox")
        c = QHBoxLayout(change)
        c.setContentsMargins(14, 10, 14, 10)
        cl = QLabel("الباقي للعميل")
        cl.setObjectName("changeLabel")
        self.change_label = QLabel()
        self.change_label.setObjectName("changeValue")
        c.addWidget(cl)
        c.addStretch(1)
        c.addWidget(self.change_label)
        pb.addWidget(change)
        body.addWidget(pbox)
        self.instalment_hint = muted("يُرحَّل كامل المبلغ على حساب التقسيط، ثم يُعتمد العقد ويُحصَّل المقدم.")
        self.instalment_hint.setVisible(False)
        body.addWidget(self.instalment_hint)

        self.split_btn = QPushButton("تقسيم الدفع على وسيلتين")
        self.split_btn.setObjectName("link")
        self.split_btn.setCheckable(True)
        self.split = QFrame()
        sp = QGridLayout(self.split)
        sp.setContentsMargins(0, 0, 0, 0)
        self.pay1_amount = ltr_edit("فارغ = كامل الإجمالي")
        self.pay2_method = self._method_combo(first_blank=True)
        self.pay2_amount = ltr_edit("فارغ = المتبقي")
        self.pay1_amount.textChanged.connect(self._refresh_change)
        sp.addWidget(self._field("مبلغ الوسيلة الأولى", self.pay1_amount), 0, 0, 1, 2)
        sp.addWidget(self._field("الوسيلة الثانية", self.pay2_method), 1, 0)
        sp.addWidget(self._field("مبلغها", self.pay2_amount), 1, 1)
        self.split.setVisible(False)
        self.split_btn.toggled.connect(self.split.setVisible)
        body.addWidget(self.split_btn, 0, Qt.AlignmentFlag.AlignLeading)
        body.addWidget(self.split)

        self._on_method_changed()
        return box

    # ---------- Builders ----------
    @staticmethod
    def _card(title: str, icon_name: str) -> tuple:
        """Card with an icon header; returns (card, body, header layout)."""
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

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def _field(self, title: str, widget: QWidget, locked: bool = False) -> QWidget:
        """Field with its label; locked adds a lock icon and the permission note."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        if locked:
            h = QHBoxLayout()
            h.setSpacing(4)
            h.addWidget(self._label(title))
            ic = QLabel()
            ic.setPixmap(icons.icon("lock", ICON_MUTED, 13).pixmap(13, 13))
            h.addWidget(ic)
            h.addWidget(self._label("بصلاحية"))
            h.addStretch(1)
            v.addLayout(h)
        else:
            v.addWidget(self._label(title))
        v.addWidget(widget)
        return w

    @staticmethod
    def _kv_row(label: str, value_widget: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 4)
        lbl = muted(label)
        h.addWidget(lbl)
        h.addStretch(1)
        h.addWidget(value_widget)
        return w

    @staticmethod
    def _chip(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("chip")
        return lbl

    @staticmethod
    def _kbd(key: str, on_primary: bool = False) -> QLabel:
        lbl = QLabel(ltr(key))
        lbl.setObjectName("kbdOnPrimary" if on_primary else "kbd")
        return lbl

    @staticmethod
    def _trail_icon(edit: QLineEdit, name: str) -> None:
        edit.addAction(icons.icon(name, ICON_MUTED, 16), QLineEdit.ActionPosition.TrailingPosition)

    def _set_tier(self, key: str) -> None:
        self.tier = key or "retail"
        self.tier_combo.blockSignals(True)
        self.tier_combo.setCurrentIndex(max(self.tier_combo.findData(self.tier), 0))
        self.tier_combo.blockSignals(False)
        for chip in (self.tier_chip, self.cust_tier_chip):
            chip.setText(f"فئة السعر: {TIER_SHORT[self.tier]}")

    def _set_kind(self, kind) -> None:
        self._kind = kind
        for k, b in self.kind_tabs.items():
            b.setChecked(k == kind)
        if self.item_search.text().strip():
            self._do_item_search()

    @staticmethod
    def _deferred_button(label: str, icon_name: str | None = None) -> QPushButton:
        b = QPushButton(label)
        if icon_name:
            b.setIcon(icons.icon(icon_name, ICON_MUTED, 16))
        b.setEnabled(False)
        b.setToolTip("قريبًا — غير مبني بعد")
        return b

    @staticmethod
    def _method_combo(first_blank: bool) -> QComboBox:
        """Only the methods the engine supports; the second starts with a 'none' entry."""
        c = QComboBox()
        if first_blank:
            c.addItem("— لا —", None)
        for key, label in PAYMENT_LABELS.items():
            text = label if first_blank else f"{label}  —  {PAYMENT_HINTS[key]}"
            c.addItem(icons.icon("cash", ICON_PRIMARY, 20), text, key)
        return c

    def _shortcuts(self) -> None:
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for key, slot in (("F2", self.item_search.setFocus), ("F4", self._focus_customer),
                          ("F8", self.pay1_method.setFocus), ("F9", self.post)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(ctx)
            sc.activated.connect(slot)

    def _focus_customer(self) -> None:
        self.customer.setFocus()
        self.customer.showPopup()

    def _stamp(self) -> str:
        now = datetime.now()
        self.sale_date.setText(now.strftime("%Y-%m-%d %H:%M"))
        return date.today().isoformat()

    def _refresh_extra_summary(self, *_args) -> None:
        parts = []
        if self.odometer.text().strip():
            parts.append(f"العداد {ltr(self.odometer.text().strip())}")
        if self.plate.text().strip():
            parts.append(f"اللوحة {ltr(self.plate.text().strip())}")
        if self.note.text().strip():
            parts.append("ملاحظة")
        self.extra_summary.setText(" · ".join(parts))

    def _on_method_changed(self, *_args) -> None:
        on_instalments = self.pay1_method.currentData() == "installment"
        self.pay1_ref.setVisible(not on_instalments and self.pay1_method.currentData() != "cash")
        # A contract covers the whole invoice, so there is nothing received and nothing to split
        self.pay_box.setVisible(not on_instalments)
        self.split_btn.setVisible(not on_instalments)
        if on_instalments:
            self.split_btn.setChecked(False)
        self.instalment_hint.setVisible(on_instalments)
        self._refresh_change()

    # ---------- Customer and items ----------
    def _load_customer_matches(self, text: str) -> int:
        """Adds registered customers beyond the preloaded first 300; returns how many were added."""
        text = text.strip()
        if len(text) < 2:
            return 0
        known = {self.customer.itemData(i)[0] for i in range(self.customer.count())}
        added = 0
        for row in customers.search(self.session.con, text, limit=30):
            if row[0] not in known:
                self.customer.addItem(row[1], (row[0], row[3], row[2] or "", (row[5] or "").strip()))
                added += 1
        return added

    def _on_customer_typed(self) -> None:
        """A typed name picks that customer; anything else falls back to the chosen one."""
        text = self.customer.lineEdit().text().strip()
        idx = self.customer.findText(text, Qt.MatchFlag.MatchFixedString)
        if idx < 0 and text:
            self._load_customer_matches(text)
            idx = self.customer.findText(text, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.customer.setCurrentIndex(idx)
            return
        if text:
            self.notice.show_message(f"لا يوجد عميل مسجّل باسم «{text}»؛ أضفه من شاشة العملاء.", "warn")
        self.customer.lineEdit().setText(self.customer.itemText(self.customer.currentIndex()))

    def _on_mobile_typed(self) -> None:
        """A typed mobile picks the customer registered with it, if any."""
        digits = "".join(ch for ch in self.mobile.text() if ch.isdigit())
        current = (self.customer.currentData() or ("", "", "", ""))[2]
        if not digits or digits == current:
            return
        if not self._select_by_mobile(digits):
            self._load_customer_matches(digits)
            if self._select_by_mobile(digits):
                return
            self.notice.show_message(f"لا يوجد عميل مسجّل بالرقم {ltr(digits)}.", "warn")

    def _select_by_mobile(self, digits: str) -> bool:
        for i in range(1, self.customer.count()):
            if self.customer.itemData(i)[2] == digits:
                self.customer.setCurrentIndex(i)
                self.notice.show_message("")
                return True
        return False

    def _on_customer_changed(self, *_args) -> None:
        cid, level, mobile, vehicle = self.customer.currentData() or ("", "retail", "", "")
        self.mobile.setText(mobile or "")
        self.plate.setPlaceholderText(f"{vehicle} · رقم اللوحة" if vehicle else "رقم اللوحة")
        if cid:
            n, last = sale_view.customer_stats(self.session.con, cid)
            self.cust_kind.setText("عميل مسجّل")
            self.cust_kind.setObjectName("pillOk")
            self.cust_stats.setText(f"{ltr(str(n))} فواتير سابقة" + (f" · آخر زيارة {ltr(last)}" if last else ""))
        else:
            self.cust_kind.setText("عميل نقدي")
            self.cust_kind.setObjectName("chip")
            self.cust_stats.setText("بيع نقدي بدون حساب عميل")
        self.cust_kind.style().unpolish(self.cust_kind)
        self.cust_kind.style().polish(self.cust_kind)
        self._set_tier(level or "retail")

    def _on_item_query(self, _text: str) -> None:
        self._item_timer.start(200)

    def _do_item_search(self) -> None:
        q = self.item_search.text().strip()
        if not q:
            self._item_popup.hide()
            return
        rows = []
        for kind in ((self._kind,) if self._kind else ("tire", "battery", "service")):
            rows += items.search(self.session.con, q, kind=kind, limit=ITEM_CHOICES)
        rows = rows[:8]
        self._item_popup.show_results(rows, on_pick=self._pick_item, on_new=self._open_new_item_dialog, query=q)
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
        dlg = ItemDialog(self.session, None, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved:
            self.item_search.clear()
            self._add_row(dlg.saved.id, dlg.saved.kind, dlg.saved.name)

    def eventFilter(self, obj, event) -> bool:
        # The popup is a non-activating tool window (see _ItemPopup) so typing
        # keeps working; close it shortly after the field loses focus so a
        # click on one of its buttons still registers first.
        if event.type() == QEvent.Type.FocusOut and obj is self.item_search:
            QTimer.singleShot(150, self._item_popup.hide)
        return super().eventFilter(obj, event)

    # ---------- Lines: one self-contained row per item, added on pick ----------
    def _add_row(self, item_id: int, kind: str, name: str) -> None:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(10)

        num_lbl = muted("")
        num_lbl.setWordWrap(False)
        h.addWidget(num_lbl, 0, Qt.AlignmentFlag.AlignTop)

        item_w = QWidget()
        iw = QHBoxLayout(item_w)
        iw.setContentsMargins(0, 0, 0, 0)
        iw.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.icon(KIND_ICON.get(kind, "inventory"), ICON_MUTED, 16).pixmap(16, 16))
        name_lbl = QLabel(name)
        iw.addWidget(ic)
        iw.addWidget(name_lbl, 1)
        h.addWidget(item_w, 3, Qt.AlignmentFlag.AlignTop)

        qty_spin = QSpinBox()
        qty_spin.setRange(1, 100000)
        qty_spin.setValue(1)
        qty_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        qty_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        minus_btn = QPushButton("−")
        minus_btn.setObjectName("stepperBtn")
        minus_btn.clicked.connect(qty_spin.stepDown)
        plus_btn = QPushButton("+")
        plus_btn.setObjectName("stepperBtn")
        plus_btn.clicked.connect(qty_spin.stepUp)
        qty_w = QWidget()
        qh = QHBoxLayout(qty_w)
        qh.setContentsMargins(0, 0, 0, 0)
        qh.setSpacing(2)
        qh.addWidget(minus_btn)
        qh.addWidget(qty_spin, 1)
        qh.addWidget(plus_btn)
        h.addWidget(qty_w, 1, Qt.AlignmentFlag.AlignTop)

        price_edit = ltr_edit("0.00")
        price_edit.setText(self._tier_price_text(item_id))
        h.addWidget(price_edit, 1, Qt.AlignmentFlag.AlignTop)

        disc_edit = ltr_edit("0.00")
        h.addWidget(disc_edit, 1, Qt.AlignmentFlag.AlignTop)

        total_lbl = QLabel(ltr(fmt(0)))
        total_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
                               | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(total_lbl, 1, Qt.AlignmentFlag.AlignTop)

        del_btn = QPushButton()
        del_btn.setFlat(True)
        del_btn.setIcon(icons.icon("trash", ICON_MUTED, 16))
        del_btn.setFixedSize(30, 30)
        del_btn.setToolTip("حذف السطر")
        h.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

        row = {"widget": row_w, "item_id": item_id, "kind": kind, "name": name,
              "num_lbl": num_lbl, "qty_spin": qty_spin, "price_edit": price_edit,
              "disc_edit": disc_edit, "total_lbl": total_lbl}
        qty_spin.valueChanged.connect(self._on_row_changed)
        price_edit.textChanged.connect(self._on_row_changed)
        disc_edit.textChanged.connect(self._on_row_changed)
        del_btn.clicked.connect(lambda _=False, rw=row: self._remove_row(rw))

        self._rows.append(row)
        self.empty_label.hide()
        # Insert before the trailing stretch (always the layout's last item)
        # so rows stay stacked at the top instead of it.
        self._rows_container.insertWidget(self._rows_container.count() - 1, row_w)
        self._renumber()
        self._on_row_changed()

    def _tier_price_text(self, item_id: int) -> str:
        try:
            it = items.get(self.session.con, item_id)
        except items.ItemError:
            return "0.00"
        cents = getattr(it, TIER_ATTR[self.tier], 0) or it.price_retail
        return f"{cents // 100}.{cents % 100:02d}"

    def _remove_row(self, row: dict) -> None:
        self._rows.remove(row)
        self._rows_container.removeWidget(row["widget"])
        row["widget"].deleteLater()
        if not self._rows:
            self.empty_label.show()
        self._renumber()
        self._on_row_changed()

    def _renumber(self) -> None:
        for i, row in enumerate(self._rows, start=1):
            row["num_lbl"].setText(str(i))

    def _on_row_changed(self) -> None:
        for row in self._rows:
            try:
                price = parse_money(row["price_edit"].text())
            except MoneyError:
                price = 0
            try:
                disc = parse_money(row["disc_edit"].text() or "0")
            except MoneyError:
                disc = 0
            row["total_lbl"].setText(ltr(fmt(max(row["qty_spin"].value() * price - disc, 0))))
        pieces = sum(row["qty_spin"].value() for row in self._rows)
        self.count_chip.setText(f"{ltr(str(len(self._rows)))} سطر · {ltr(str(pieces))} قطعة")
        self._refresh_total()

    def _validate_lines(self) -> tuple[list[SaleLineIn] | None, str | None]:
        """Parse and validate every row. Returns (lines, None) or (None, error)."""
        if not self._rows:
            return None, "أضف سطرًا واحدًا على الأقل قبل الترحيل."
        lines = []
        for i, row in enumerate(self._rows, start=1):
            try:
                unit = parse_money(row["price_edit"].text())
            except MoneyError as e:
                return None, f"السطر {i} ({row['name']}): السعر: {e}"
            if unit <= 0:
                return None, f"السطر {i} ({row['name']}): سعر الوحدة يجب أن يكون أكبر من صفر."
            try:
                disc = parse_money(row["disc_edit"].text() or "0")
            except MoneyError as e:
                return None, f"السطر {i} ({row['name']}): الخصم: {e}"
            qty = row["qty_spin"].value()
            if disc < 0 or disc > qty * unit:
                return None, f"السطر {i} ({row['name']}): خصم السطر أكبر من قيمة السطر."
            lines.append(SaleLineIn(item_id=row["item_id"], qty=qty, unit_price=unit, line_discount=disc))
        return lines, None

    def subtotal_minor(self) -> int:
        total = 0
        for row in self._rows:
            try:
                price = parse_money(row["price_edit"].text())
            except MoneyError:
                price = 0
            total += row["qty_spin"].value() * price
        return total

    def _line_discount_minor(self) -> int:
        total = 0
        for row in self._rows:
            try:
                total += parse_money(row["disc_edit"].text() or "0")
            except MoneyError:
                pass
        return total

    def total_minor(self) -> int:
        """Total after line and invoice discounts, in piasters, as the engine computes it."""
        try:
            inv_disc = parse_money(self.invoice_disc.text() or "0")
        except MoneyError:
            inv_disc = 0
        return self.subtotal_minor() - self._line_discount_minor() - inv_disc

    def _refresh_total(self, *_args) -> None:
        total = self.total_minor()
        self.subtotal_label.setText(ltr(fmt(self.subtotal_minor())))
        self.total_label.setText(f"{ltr(fmt(total))} ج.م")
        labels = ["المبلغ بالضبط"]
        amounts = [total]
        for step in (10000, 50000):                 # round up to the nearest 100 and 500
            up = -(-total // step) * step if total > 0 else 0
            if up not in amounts:
                labels.append(fmt(up))
                amounts.append(up)
        for i, b in enumerate(self.amount_chips):
            b.setVisible(i < len(amounts) and total > 0)
            if i < len(amounts):
                b.setText(ltr(labels[i]))
                b.setProperty("amount", f"{amounts[i] // 100}.{amounts[i] % 100:02d}")
        self._refresh_change()

    def _refresh_change(self, *_args) -> None:
        """Change = cash received minus what the first method owes, never below zero."""
        try:
            due = parse_money(self.pay1_amount.text()) if self.pay1_amount.text().strip() else self.total_minor()
            received = parse_money(self.pay1_received.text()) if self.pay1_received.text().strip() else due
        except MoneyError:
            self.change_label.setText("—")
            return
        self.change_label.setText(ltr(fmt(max(received - due, 0))))

    def _fail(self, text: str) -> bool:
        self.notice.show_message(text)
        return False

    # ---------- Payment and posting ----------
    def _payments(self, total: int) -> list:
        if self.pay1_method.currentData() == "installment":
            return [sales_rules.Payment(method="installment", amount=total)]
        first = sales_rules.Payment(
            method=self.pay1_method.currentData(),
            amount=parse_money(self.pay1_amount.text()) if self.pay1_amount.text().strip() else total,
            received=parse_money(self.pay1_received.text()) if self.pay1_received.text().strip() else None,
            reference=self.pay1_ref.text().strip() or None)
        out = [first]
        if self.pay2_method.currentData() is not None:
            rest = total - first.amount
            amount = parse_money(self.pay2_amount.text()) if self.pay2_amount.text().strip() else rest
            out.append(sales_rules.Payment(method=self.pay2_method.currentData(), amount=amount))
        return out

    def post(self) -> None:
        """Posts through the engine; any refusal is shown and nothing is written."""
        if self._busy:
            return
        lines, err = self._validate_lines()
        if err:
            self._fail(err)
            return
        try:
            total = self.total_minor()
            payments = self._payments(total)
            inv_disc = parse_money(self.invoice_disc.text() or "0")
            odometer = int(self.odometer.text()) if self.odometer.text().strip() else None
        except (MoneyError, ValueError, sales_rules.SaleRuleError) as e:
            self._fail(f"تحقق من المدخلات: {e}")
            return
        customer = self.customer.currentData()
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = sales.post_sale(
                self.session.con, self.session.user, request_key=self._request_key,
                customer_id=customer[0] or None, lines=lines, payments=payments,
                sale_date=self._stamp(), invoice_discount=inv_disc,
                odometer=odometer, vehicle_plate=self.plate.text().strip() or None,
                notes=self.note.text().strip() or None)
        except (SaleError, sales_rules.SaleRuleError) as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # Any unexpected failure: the engine is atomic, so nothing half-written remains
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            on_instalments = self.pay1_method.currentData() == "installment"
            sale_id = self.posted.id
            self._reset_after_post()
            self.notice.show_message(f"تم ترحيل الفاتورة رقم {self.posted.number}", "success")
            if on_instalments:
                self.contract_needed.emit(sale_id)
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)

    def _reset_after_post(self) -> None:
        self._request_key = uuid.uuid4().hex
        for row in list(self._rows):
            self._rows_container.removeWidget(row["widget"])
            row["widget"].deleteLater()
        self._rows.clear()
        self.empty_label.show()
        self.odometer.clear()
        self.plate.clear()
        self.note.clear()
        self.extra_toggle.setChecked(False)
        self.invoice_disc.clear()
        self.pay1_amount.clear()
        self.pay1_received.clear()
        self.pay1_ref.clear()
        self.pay2_method.setCurrentIndex(0)
        self.pay2_amount.clear()
        self.split_btn.setChecked(False)
        self.customer.setCurrentIndex(0)
        self.number_edit.setText(sales.next_number_preview(self.session.con))
        self._stamp()
        self._on_row_changed()

    def cancel_invoice(self) -> None:
        if self._busy:
            return
        self._reset_after_post()
        self.notice.show_message("")
