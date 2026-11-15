"""شاشة نقطة البيع: فاتورة بيع جديدة داخل الشاشة نفسها (لا نافذة منبثقة)، ترحّل عبر محرك المبيعات."""
import uuid
from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox, QTableView, QToolButton, QVBoxLayout,
                               QWidget)

from morv.core import sales_rules
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import customers, items, sale_view, sales
from morv.infra.sales import SaleError, SaleLineIn
from morv.ui import icons
from morv.ui.listpage import TupleModel
from morv.ui.widgets import Notice, ltr_edit, muted

ITEM_CHOICES = 50
PRICE_COLUMN = {"retail": 5, "wholesale": 6, "super": 7}      # موضع سعر الشريحة في صف items.search
TIER_LABELS = {"retail": "قطاعي (تجزئة)", "wholesale": "جملة", "super": "جملة الجملة"}
TIER_SHORT = {"retail": "قطاعي", "wholesale": "جملة", "super": "جملة الجملة"}
PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة / ماكينة POS", "transfer": "تحويل / محفظة", "wallet": "محفظة"}
PAYMENT_HINTS = {"cash": "استلام نقدية وحساب الباقي", "card": "يلزم رقم الإيصال", "transfer": "يلزم رقم المرجع",
                 "wallet": "يلزم رقم المرجع"}
KIND_TABS = (("الكل", None), ("إطارات", "tire"), ("بطاريات", "battery"), ("خدمات", "service"))
KIND_ICON = {"tire": "tire", "battery": "battery", "service": "service"}
ICON_MUTED = "#8792A3"
ICON_PRIMARY = "#1F5FD6"


class PosPage(QWidget):
    """نقطة البيع حسب التصميم الموحّد: بيانات الفاتورة والأصناف يمينًا، والملخص والدفع يسارًا."""

    def __init__(self, session, parent=None):
        """بناء الشاشة وتحميل الأصناف والعملاء."""
        super().__init__(parent)
        self.session = session
        self.posted = None
        self.lines: list[SaleLineIn] = []
        self._names: list[str] = []
        self._meta: list[tuple] = []             # (النوع، الكود) لكل سطر للعرض فقط
        self._busy = False
        self._request_key = uuid.uuid4().hex     # مفتاح واحد لكل فاتورة: إعادة الضغط لا تكرر الفاتورة
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
        main.addLayout(self._build_right_column(), 3)
        main.addLayout(self._build_left_column(), 2)
        content = QWidget()
        content.setLayout(main)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addLayout(self._build_hints())
        self._shortcuts()

        self._stamp()
        self._on_customer_changed()
        self.refresh_items()
        self._refresh_lines()
        if not items.search(self.session.con, "", limit=1):
            self.notice.show_message("لا توجد أصناف نشطة؛ أضف أصنافًا من شاشة المخزون أولًا.", "warn")

    # ---------- الهيكل ----------
    def _build_header(self) -> QFrame:
        """العنوان وشارة المسودة وسطر المخزن والكاشير، وأزرار الفاتورة."""
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        title = QLabel("فاتورة بيع جديدة")
        title.setObjectName("pageTitle")
        draft = QLabel("مسودة")
        draft.setObjectName("pillWarn")
        user = getattr(self.session.user, "display_name", "") or ""
        h.addWidget(title)
        h.addWidget(draft)
        h.addWidget(muted(f"نقطة البيع · المخزن الرئيسي · الكاشير: {user}"))
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
        """سطر اختصارات لوحة المفاتيح المفعّلة في الشاشة."""
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addStretch(1)
        for key, label in (("F2", "صنف"), ("F4", "عميل"), ("F8", "الدفع"), ("F9", "اعتماد")):
            bar.addWidget(muted(label))
            bar.addWidget(self._kbd(key))
            bar.addSpacing(8)
        return bar

    def _build_right_column(self) -> QVBoxLayout:
        """الجهة اليمنى: بطاقة بيانات الفاتورة والعميل، ثم بطاقة الأصناف والخدمات."""
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addWidget(self._invoice_card())
        col.addWidget(self._items_card(), 1)
        return col

    def _invoice_card(self) -> QFrame:
        """بطاقة بيانات الفاتورة والعميل بشبكة من أربعة أعمدة."""
        box, body, _head = self._card("بيانات الفاتورة والعميل", "sales")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for c in range(4):
            grid.setColumnStretch(c, 1)

        self.number_edit = QLineEdit("يُحدَّد عند الاعتماد")
        self.number_edit.setReadOnly(True)
        self._trail_icon(self.number_edit, "lock")
        self.sale_date = ltr_edit()
        self.sale_date.setReadOnly(True)
        self._trail_icon(self.sale_date, "lock")
        self.tier_combo = QComboBox()
        for key, label in TIER_LABELS.items():
            self.tier_combo.addItem(label, key)
        self.tier_combo.currentIndexChanged.connect(lambda _i: self._set_tier(self.tier_combo.currentData()))
        self.tier_combo.setEnabled(bool(getattr(self.session.user, "is_manager", False)))
        self.odometer = ltr_edit("مثال: 84500")
        grid.addWidget(self._field("رقم الفاتورة", self.number_edit), 0, 0)
        grid.addWidget(self._field("التاريخ والوقت", self.sale_date), 0, 1)
        grid.addWidget(self._field("فئة السعر", self.tier_combo, locked=True), 0, 2)
        grid.addWidget(self._field("عداد الكيلومترات", self.odometer), 0, 3)

        self.customer = QComboBox()
        self.customer.addItem("عميل نقدي", ("", "retail", "", ""))
        for row in customers.search(self.session.con, "", limit=300):
            self.customer.addItem(row[1], (row[0], row[3], row[2] or "", (row[5] or "").strip()))
        self.customer.currentIndexChanged.connect(self._on_customer_changed)
        self.mobile = ltr_edit("—")
        self.mobile.setReadOnly(True)
        self._trail_icon(self.mobile, "phone")
        self.plate = ltr_edit("رقم اللوحة")
        self._trail_icon(self.plate, "car")
        grid.addWidget(self._field("العميل *", self.customer), 1, 0, 1, 2)
        grid.addWidget(self._field("رقم الموبايل", self.mobile), 1, 2)
        grid.addWidget(self._field("المركبة / اللوحة", self.plate), 1, 3)

        self.note = QLineEdit()
        self.note.setPlaceholderText("اختياري — تظهر في الطباعة")
        grid.addWidget(self._field("ملاحظة على الفاتورة", self.note), 2, 0, 1, 4)
        body.addLayout(grid)

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
        s.addWidget(self.cust_kind)
        s.addWidget(self.cust_tier_chip)
        s.addWidget(self.cust_stats)
        s.addStretch(1)
        s.addWidget(change)
        body.addWidget(strip)
        return box

    def _items_card(self) -> QFrame:
        """بطاقة الأصناف: البحث والتصنيفات، وسطر الإدخال، وجدول الأسطر."""
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
        self.item_search.textChanged.connect(self.refresh_items)
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

        entry = QHBoxLayout()
        entry.setSpacing(10)
        self.item_combo = QComboBox()
        self.item_combo.currentIndexChanged.connect(self._on_item_changed)
        self.qty = QSpinBox()
        self.qty.setRange(1, 100000)
        self.qty.setValue(1)
        self.qty.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.qty.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        stepper = QWidget()
        st = QHBoxLayout(stepper)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(4)
        for name, delta in (("plus", 1), (None, 0), ("minus", -1)):
            if name is None:
                st.addWidget(self.qty, 1)
                continue
            b = QToolButton()
            b.setObjectName("stepBtn")
            b.setText("+" if delta > 0 else "−")
            b.clicked.connect(lambda _c=False, d=delta: self.qty.setValue(self.qty.value() + d))
            st.addWidget(b)
        self.price = ltr_edit("0.00")
        self.line_disc = ltr_edit("0.00")
        self.add_btn = QPushButton("إضافة")
        self.add_btn.setObjectName("primary")
        self.add_btn.setIcon(icons.icon("plus", "#FFFFFF", 16))
        self.add_btn.clicked.connect(self.add_line)
        entry.addWidget(self._field("الصنف المختار", self.item_combo), 4)
        entry.addWidget(self._field("الكمية", stepper), 2)
        entry.addWidget(self._field("السعر", self.price), 2)
        entry.addWidget(self._field("الخصم", self.line_disc), 2)
        entry.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignBottom)
        body.addLayout(entry)

        self.lines_model = TupleModel([
            ("م", lambda r: ltr(str(r[0]))),
            ("الصنف / الخدمة", lambda r: f"{r[1]}   ·   {ltr(r[6])}"),
            ("الكمية", lambda r: ltr(str(r[2]))),
            ("السعر", lambda r: ltr(fmt(r[3]))),
            ("الخصم", lambda r: ltr(fmt(-r[4]) if r[4] else fmt(0))),
            ("الإجمالي", lambda r: ltr(fmt(r[2] * r[3] - r[4]))),
        ], icon_for=lambda r: KIND_ICON.get(r[5], "inventory"))
        self.view = QTableView()
        self.view.setModel(self.lines_model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(52)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.view.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.view.setMinimumHeight(220)
        body.addWidget(self.view, 1)
        bar = QHBoxLayout()
        self.empty_label = muted("الفاتورة فارغة. ابحث عن صنف أو امسح الباركود للبدء.")
        self.remove_btn = QPushButton("حذف السطر المختار")
        self.remove_btn.setObjectName("dangerLink")
        self.remove_btn.clicked.connect(self.remove_selected)
        bar.addWidget(self.empty_label, 1)
        bar.addStretch(1)
        bar.addWidget(self.remove_btn)
        body.addLayout(bar)
        return box

    def _build_left_column(self) -> QVBoxLayout:
        """الجهة اليسرى: ملخص الفاتورة ثم الدفع وأزرار الاعتماد."""
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addWidget(self._summary_card())
        col.addWidget(self._payment_card())
        col.addStretch(1)
        return col

    def _summary_card(self) -> QFrame:
        """ملخص الفاتورة: قبل الخصم، وخصم الفاتورة، والضريبة، والإجمالي النهائي."""
        box, body, _head = self._card("ملخص الفاتورة", "sales")
        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        self.subtotal_label = QLabel()
        self.invoice_disc = ltr_edit("0.00")
        self.invoice_disc.setFixedWidth(150)
        self.invoice_disc.textChanged.connect(self._refresh_total)
        trail = Qt.AlignmentFlag.AlignTrailing | Qt.AlignmentFlag.AlignVCenter
        for r, (label, w) in enumerate((("الإجمالي قبل الخصم", self.subtotal_label),
                                        ("خصم على الفاتورة", self.invoice_disc),
                                        ("الضريبة", self._chip("المحل غير مسجّل — بدون ضريبة")))):
            grid.addWidget(muted(label), r, 0)
            grid.addWidget(w, r, 1, trail)
        body.addLayout(grid)
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
        """الدفع: الوسيلة، والمبلغ المستلم وأزرار المبالغ، والباقي، ثم أزرار الاعتماد."""
        box, body, _head = self._card("الدفع", "cash")
        body.addWidget(self._label("طريقة الدفع"))
        method_row = QHBoxLayout()
        self.pay1_method = self._method_combo(first_blank=False)
        self.pay1_method.setObjectName("payMethod")
        self.pay1_method.currentIndexChanged.connect(self._on_method_changed)
        method_row.addWidget(self.pay1_method, 1)
        method_row.addWidget(self._kbd("F8"))
        body.addLayout(method_row)

        pbox = QFrame()
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

        self.post_btn = QPushButton("اعتماد وتحصيل")
        self.post_btn.setObjectName("primaryLarge")
        self.post_btn.setIcon(icons.icon("check", "#FFFFFF", 20))
        inner = QHBoxLayout(self.post_btn)
        inner.setContentsMargins(16, 0, 16, 0)
        inner.addStretch(1)
        inner.addWidget(self._kbd("F9", on_primary=True))
        self.post_btn.clicked.connect(self.post)
        body.addWidget(self.post_btn)
        row = QHBoxLayout()
        row.addWidget(self._deferred_button("حفظ كمسودة"), 1)
        row.addWidget(self._deferred_button("اعتماد وطباعة", "printer"), 1)
        body.addLayout(row)
        self._on_method_changed()
        return box

    # ---------- أدوات بناء ----------
    @staticmethod
    def _card(title: str, icon_name: str) -> tuple:
        """بطاقة برأس فيه أيقونة وعنوان وخط فاصل؛ تعيد (البطاقة، جسمها، تخطيط رأسها)."""
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
        """عنوان حقل صغير باهت فوق الحقل."""
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def _field(self, title: str, widget: QWidget, locked: bool = False) -> QWidget:
        """حقل بعنوانه فوقه؛ locked يضيف قفلًا و«بصلاحية» للحقل المقيّد بالصلاحية."""
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
    def _chip(text: str) -> QLabel:
        """شارة معلومات صغيرة بإطار."""
        lbl = QLabel(text)
        lbl.setObjectName("chip")
        return lbl

    @staticmethod
    def _kbd(key: str, on_primary: bool = False) -> QLabel:
        """شارة مفتاح اختصار مثل F9."""
        lbl = QLabel(ltr(key))
        lbl.setObjectName("kbdOnPrimary" if on_primary else "kbd")
        return lbl

    @staticmethod
    def _trail_icon(edit: QLineEdit, name: str) -> None:
        """أيقونة زخرفية في طرف الحقل."""
        edit.addAction(icons.icon(name, ICON_MUTED, 16), QLineEdit.ActionPosition.TrailingPosition)

    def _set_tier(self, key: str) -> None:
        """تغيير فئة السعر ثم تحديث شاراتها وسعر الصنف المختار."""
        self.tier = key or "retail"
        self.tier_combo.blockSignals(True)
        self.tier_combo.setCurrentIndex(max(self.tier_combo.findData(self.tier), 0))
        self.tier_combo.blockSignals(False)
        for chip in (self.tier_chip, self.cust_tier_chip):
            chip.setText(f"فئة السعر: {TIER_SHORT[self.tier]}")
        self._on_item_changed()

    def _set_kind(self, kind) -> None:
        """تصفية الأصناف حسب التبويب المختار."""
        self._kind = kind
        for k, b in self.kind_tabs.items():
            b.setChecked(k == kind)
        self.refresh_items()

    @staticmethod
    def _deferred_button(label: str, icon_name: str | None = None) -> QPushButton:
        """زر لعنصر غير مبني بعد: معطّل مع تلميح «قريبًا»."""
        b = QPushButton(label)
        if icon_name:
            b.setIcon(icons.icon(icon_name, ICON_MUTED, 16))
        b.setEnabled(False)
        b.setToolTip("قريبًا — غير مبني بعد")
        return b

    @staticmethod
    def _method_combo(first_blank: bool) -> QComboBox:
        """قائمة وسيلة دفع مدعومة في المحرك فقط؛ الثانية تبدأ بخانة «لا» تعني عدم وجود وسيلة ثانية."""
        c = QComboBox()
        if first_blank:
            c.addItem("— لا —", None)
        for key, label in PAYMENT_LABELS.items():
            text = label if first_blank else f"{label}  —  {PAYMENT_HINTS[key]}"
            c.addItem(icons.icon("cash", ICON_PRIMARY, 20), text, key)
        return c

    def _shortcuts(self) -> None:
        """اختصارات الشاشة: F2 صنف، F4 عميل، F8 الدفع، F9 اعتماد."""
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for key, slot in (("F2", self.item_search.setFocus), ("F4", self._focus_customer),
                          ("F8", self.pay1_method.setFocus), ("F9", self.post)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(ctx)
            sc.activated.connect(slot)

    def _focus_customer(self) -> None:
        """نقل التركيز إلى اختيار العميل وفتح القائمة."""
        self.customer.setFocus()
        self.customer.showPopup()

    def _stamp(self) -> str:
        """تاريخ اليوم للفاتورة، ويحدّث حقل التاريخ والوقت المقفل."""
        now = datetime.now()
        self.sale_date.setText(now.strftime("%Y-%m-%d  %H:%M"))
        return date.today().isoformat()

    def _on_method_changed(self, *_args) -> None:
        """حقل المرجع يظهر لغير النقدي فقط."""
        self.pay1_ref.setVisible(self.pay1_method.currentData() != "cash")
        self._refresh_change()

    # ---------- العميل والأصناف ----------
    def _on_customer_changed(self, *_args) -> None:
        """تغيير العميل يضبط فئة السعر والموبايل وشريط بياناته."""
        cid, level, mobile, vehicle = self.customer.currentData() or ("", "retail", "", "")
        self.mobile.setText(ltr(mobile) if mobile else "")
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

    def refresh_items(self, *_args) -> None:
        """تحديث قائمة اختيار الصنف حسب نص البحث."""
        q = self.item_search.text()
        rows = []
        for kind in ((self._kind,) if self._kind else ("tire", "battery", "service")):
            rows += items.search(self.session.con, q, kind=kind, limit=ITEM_CHOICES)
        self.item_combo.blockSignals(True)
        self.item_combo.clear()
        for r in rows[:ITEM_CHOICES]:
            spec = f" · {ltr(r[4])}" if r[4] else ""
            self.item_combo.addItem(f"{r[3]} ({ltr(r[2])}){spec}", r)
        self.item_combo.blockSignals(False)
        self._on_item_changed()

    def _on_item_changed(self, *_args) -> None:
        """يملأ سعر الوحدة من فئة السعر المختارة للصنف."""
        r = self.item_combo.currentData()
        if r is None:
            self.price.clear()
            return
        cents = r[PRICE_COLUMN[self.tier]] or r[5]
        self.price.setText(f"{cents // 100}.{cents % 100:02d}")

    # ---------- الأسطر ----------
    def add_line(self) -> bool:
        """التحقق من مدخلات السطر ثم إضافته؛ يعيد True عند الإضافة، وإلا يعرض الخطأ ويترك المدخلات."""
        r = self.item_combo.currentData()
        if r is None:
            return self._fail("اختر صنفًا للسطر.")
        try:
            unit = parse_money(self.price.text())
            disc = parse_money(self.line_disc.text() or "0")
        except MoneyError as e:
            return self._fail(str(e))
        if unit <= 0:
            return self._fail("سعر الوحدة يجب أن يكون أكبر من صفر.")
        if disc < 0 or disc > self.qty.value() * unit:
            return self._fail("خصم السطر أكبر من قيمة السطر.")
        self.lines.append(SaleLineIn(item_id=r[0], qty=self.qty.value(), unit_price=unit, line_discount=disc))
        self._names.append(r[3])
        self._meta.append((r[1], r[2]))
        self.notice.show_message("")
        self.line_disc.clear()
        self.qty.setValue(1)
        self._refresh_lines()
        return True

    def remove_selected(self) -> None:
        """حذف السطر المختار من الفاتورة قبل الترحيل."""
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر سطرًا من الجدول أولًا.", "warn")
            return
        del self.lines[idx.row()]
        del self._names[idx.row()]
        del self._meta[idx.row()]
        self._refresh_lines()

    def _refresh_lines(self) -> None:
        """إعادة عرض الأسطر والعدّاد والإجمالي ورسالة الفاتورة الفارغة."""
        self.lines_model.reset_rows([(i, self._names[i - 1], ln.qty, ln.unit_price, ln.line_discount,
                                      *self._meta[i - 1]) for i, ln in enumerate(self.lines, start=1)])
        self.empty_label.setVisible(not self.lines)
        pieces = sum(ln.qty for ln in self.lines)
        self.count_chip.setText(f"{ltr(str(len(self.lines)))} سطر · {ltr(str(pieces))} قطعة")
        self._refresh_total()

    def subtotal_minor(self) -> int:
        """مجموع الأسطر قبل خصومها بالقرش."""
        return sum(ln.qty * ln.unit_price for ln in self.lines)

    def total_minor(self) -> int:
        """الإجمالي بعد خصم الأسطر وخصم الفاتورة، بالقرش، كما يحسبه المحرك."""
        line_disc = sum(ln.line_discount for ln in self.lines)
        try:
            inv_disc = parse_money(self.invoice_disc.text() or "0")
        except MoneyError:
            inv_disc = 0
        return self.subtotal_minor() - line_disc - inv_disc

    def _refresh_total(self, *_args) -> None:
        """نصوص الملخص: قبل الخصم والنهائي، وأزرار المبالغ السريعة والباقي."""
        total = self.total_minor()
        self.subtotal_label.setText(ltr(fmt(self.subtotal_minor())))
        self.total_label.setText(f"{ltr(fmt(total))} ج.م")
        labels = ["المبلغ بالضبط"]
        amounts = [total]
        for step in (10000, 50000):                 # تقريب لأعلى إلى أقرب 100 و500 جنيه
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
        """الباقي للعميل = المستلم نقدًا − المستحق على الوسيلة الأولى (لا يقل عن صفر)."""
        try:
            due = parse_money(self.pay1_amount.text()) if self.pay1_amount.text().strip() else self.total_minor()
            received = parse_money(self.pay1_received.text()) if self.pay1_received.text().strip() else due
        except MoneyError:
            self.change_label.setText("—")
            return
        self.change_label.setText(ltr(fmt(max(received - due, 0))))

    def _fail(self, text: str) -> bool:
        """عرض خطأ في شريط الرسائل؛ يعيد False ليسهل الخروج."""
        self.notice.show_message(text)
        return False

    # ---------- الدفع والترحيل ----------
    def _payments(self, total: int) -> list:
        """وسائل الدفع من النموذج؛ الفارغ من المبلغ يأخذ الباقي."""
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
        """فحص الفاتورة ثم الترحيل عبر المحرك؛ أي رفض يُعرض بنصه ولا يُحفظ شيء، وتبقى المدخلات."""
        if self._busy:
            return
        if not self.lines:
            self._fail("أضف سطرًا واحدًا على الأقل قبل الترحيل.")
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
                customer_id=customer[0] or None, lines=list(self.lines), payments=payments,
                sale_date=self._stamp(), invoice_discount=inv_disc,
                odometer=odometer, vehicle_plate=self.plate.text().strip() or None,
                notes=self.note.text().strip() or None)
        except (SaleError, sales_rules.SaleRuleError) as e:
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # أي عطل غير متوقع: المحرك ذري فلا يبقى شيء ناقص
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            self._reset_after_post()
            self.notice.show_message(f"تم ترحيل الفاتورة رقم {self.posted.number}", "success")
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)

    def _reset_after_post(self) -> None:
        """فاتورة جديدة فارغة بعد الترحيل، بمفتاح طلب جديد."""
        self._request_key = uuid.uuid4().hex
        self.lines.clear()
        self._names.clear()
        self._meta.clear()
        self.odometer.clear()
        self.plate.clear()
        self.note.clear()
        self.invoice_disc.clear()
        self.pay1_amount.clear()
        self.pay1_received.clear()
        self.pay1_ref.clear()
        self.pay2_method.setCurrentIndex(0)
        self.pay2_amount.clear()
        self.split_btn.setChecked(False)
        self.customer.setCurrentIndex(0)
        self._stamp()
        self._refresh_lines()

    def cancel_invoice(self) -> None:
        """إلغاء الفاتورة الحالية قبل الترحيل والبدء بفاتورة فارغة."""
        if self._busy:
            return
        self._reset_after_post()
        self.notice.show_message("")
