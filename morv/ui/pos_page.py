"""Point of sale: the whole sale invoice in the screen, posted by the sales engine."""
import uuid
from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from morv.core import sales_rules
from morv.core.money import MoneyError, fmt, parse_money
from morv.core.text import ltr
from morv.infra import customer_receipts, customers, items, sale_view, sales, settings, users
from morv.infra.sales import SaleError, SaleLineIn
from morv.ui import icons, tokens
from morv.ui.adaptive_layout import HeaderPlacement, InvoiceWorkspace
from morv.ui.invoice_lines import InvoiceLineTable, wrapping_line_label
from morv.ui.item_search import ItemSearch, KIND_ICON
from morv.ui.party_search import (FoldableDetails, PartyInfoStrip, PartySearch,
                                  QuickPartyDialog, party_field, phone_query)
from morv.ui.widgets import (InvoiceFooter, InvoiceTotal, confirm_discard_invoice, chip, deferred_button, field, field_label, ICON_MUTED, ICON_PRIMARY, kbd, kv_row, ltr_edit, muted, Notice, section_card, trail_icon)

TIER_ATTR = {"retail": "price_retail", "wholesale": "price_wholesale", "super": "price_super"}
TIER_LABELS = {"retail": "قطاعي (تجزئة)", "wholesale": "جملة", "super": "جملة الجملة"}
TIER_SHORT = {"retail": "قطاعي", "wholesale": "جملة", "super": "جملة الجملة"}
PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة / ماكينة POS", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                  "credit": "آجل", "installment": "تقسيط"}
PAYMENT_HINTS = {"cash": "استلام نقدية وحساب الباقي", "card": "يلزم رقم الإيصال", "transfer": "يلزم رقم المرجع",
                 "wallet": "يلزم رقم المرجع", "credit": "على حساب العميل",
                 "installment": "يُفتح العقد بعد الاعتماد"}
KIND_TABS = (("الكل", None), ("إطارات", "tire"), ("بطاريات", "battery"), ("خدمات", "service"))


CASH_CUSTOMER = (None, "عميل نقدي", "", "retail", "individual", "", 1)


class QuickCustomerDialog(QuickPartyDialog):
    def __init__(self, session, query, parent=None):
        digits = phone_query(query)
        super().__init__(session, "العميل", "" if digits.isdigit() else query,
                         digits if digits.isdigit() else "", customers.CustomerError, parent=parent)

    def _create_party(self, name, mobile):
        return customers.create(self.session.con, self.session.user,
                                customers.Customer(None, name, mobile))


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
        self._tax_enabled = settings.get(session.con, "tax.enabled", "0") == "1"
        self._tax_quote = None
        self._tax_error = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 8)
        root.setSpacing(12)
        self.notice = Notice()
        header = self._build_header()
        root.addWidget(header)
        self.header_placement = HeaderPlacement(header, root)
        root.addWidget(self.notice)
        primary = QWidget()
        primary_layout = self._build_right_column()
        primary_layout.setContentsMargins(0, 0, 0, 0)
        primary.setLayout(primary_layout)
        summary = QWidget()
        summary_layout = self._build_left_column()
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary.setLayout(summary_layout)
        self.invoice_layout = InvoiceWorkspace(
            primary, summary, self.summary_footer, spacing=16, content_margins=(9, 9, 9, 9))
        root.addWidget(self.invoice_layout, 1)
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
        subtitle = QLabel("المخزن الرئيسي")
        subtitle.setObjectName("muted")
        h.addWidget(subtitle)
        draft = QLabel("مسودة")
        draft.setObjectName("pillWarn")
        h.addWidget(draft)
        h.addStretch(1)
        h.addWidget(deferred_button("تعليق الفاتورة", "pause"))
        h.addWidget(deferred_button("معاينة الطباعة", "printer"))
        self.cancel_btn = QPushButton("إلغاء")
        self.cancel_btn.setObjectName("dangerLink")
        self.cancel_btn.setIcon(icons.icon("close", tokens.ICON_DANGER, 16))
        self.cancel_btn.clicked.connect(self.cancel_invoice)
        h.addWidget(self.cancel_btn)
        return w

    def _build_right_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addWidget(self._invoice_card())
        col.addWidget(self._items_card(), 1)
        return col

    def _invoice_card(self) -> QFrame:
        box, body, head = section_card("بيانات الفاتورة والعميل", "sales")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for c, stretch in enumerate((3, 2, 2)):
            grid.setColumnStretch(c, stretch)

        # Read-only facts sit in the card header, so they cost no field row
        self.number_edit = chip(sales.next_number_preview(self.session.con))
        self.number_edit.setToolTip("رقم الفاتورة")
        self.sale_date = chip("")
        self.sale_date.setToolTip("التاريخ والوقت")
        head.addWidget(self.number_edit)
        head.addWidget(self.sale_date)
        self.tier_combo = QComboBox()
        for key, label in TIER_LABELS.items():
            self.tier_combo.addItem(label, key)
        self.tier_combo.currentIndexChanged.connect(lambda _i: self._set_tier(self.tier_combo.currentData()))
        self.tier_combo.setEnabled(users.is_active_manager(self.session.con, self.session.user))
        self.odometer = ltr_edit("مثال: 84500")

        self._customer = CASH_CUSTOMER
        self.customer = PartySearch(
            self, label="العميل",
            lookup=lambda q, limit: customers.search(self.session.con, q, limit=limit),
            pick=self._pick_customer, new=self._open_new_customer_dialog,
            can_new=lambda: self.session.can("customers.manage"),
            cash=lambda: self._pick_customer(CASH_CUSTOMER),
            unresolved=self._unresolved_customer,
        )
        self.customer.setToolTip("تغيير العميل (F4) — البحث بالاسم أو رقم الموبايل")
        self.customer.commit_text(CASH_CUSTOMER[1])
        self.plate = ltr_edit("رقم اللوحة")
        trail_icon(self.plate, "car")
        grid.addWidget(party_field(self.customer, "F4"), 0, 0, 1, 2)
        grid.addWidget(field("فئة السعر", self.tier_combo, locked=True), 0, 2)
        body.addLayout(grid)

        # Vehicle and note are occasional, so they stay folded and leave the room to the item rows
        self.note = QLineEdit()
        self.note.setPlaceholderText("اختياري — تظهر في الطباعة")
        self.extra_box = FoldableDetails(
            [("ملاحظة على الفاتورة", self.note), ("عداد الكيلومترات", self.odometer),
             ("المركبة / اللوحة", self.plate)],
            "تفاصيل المركبة والملاحظة", self._extra_summary_text)
        self.extra_summary = self.extra_box.summary
        self.extra_toggle = self.extra_box.toggle
        self.party_info = PartyInfoStrip(self.extra_box)
        self.cust_kind = self.party_info.kind
        self.cust_stats = self.party_info.stats
        body.addWidget(self.extra_box)
        body.addWidget(self.party_info)
        return box

    def _items_card(self) -> QFrame:
        box, body, head = section_card("الأصناف والخدمات", "purchases")
        self.tier_chip = chip("")
        self.count_chip = chip("")
        head.addWidget(self.tier_chip)
        head.addWidget(self.count_chip)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.item_search = ItemSearch(
            self, lambda: (self._kind,) if self._kind else
            ("tire", "battery", "service"), icon_size=18, sale_price=self._tier_price)
        self._item_popup = self.item_search.popup
        self._do_item_search = self.item_search.search
        top.addWidget(self.item_search, 1)
        top.addWidget(kbd("F2"))
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
        quick_lbl = muted("إضافة سريعة:")
        quick.addWidget(quick_lbl)
        quick_btn = deferred_button("الأصناف الأكثر بيعًا", "plus")
        quick_btn.setObjectName("quickChip")
        quick.addWidget(quick_btn)
        quick_lbl.setVisible(not quick_btn.isHidden())
        quick.addStretch(1)
        body.addLayout(quick)

        self._rows: list[dict] = []
        self._line_table = InvoiceLineTable(
            ("#", "الصنف / الخدمة", "الكمية", "السعر", "الخصم", "الإجمالي", ""))
        self._rows_container = self._line_table.rows
        body.addWidget(self._line_table, 1)

        self.empty_label = muted("الفاتورة فارغة. ابحث عن صنف أو امسح الباركود للبدء.")
        self._rows_container.addWidget(self.empty_label)
        # Trailing stretch absorbs leftover height so rows stay pinned at the
        # top of the card instead of stretching/centering when there are few.
        self._rows_container.addStretch(1)
        return box

    def _build_left_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addWidget(self._summary_card())
        col.addStretch(1)
        return col

    def _summary_card(self) -> QFrame:
        box, body, head = section_card("ملخص الفاتورة والدفع", "sales", compact=True)
        self.no_tax_chip = chip("بدون ضريبة")
        self.no_tax_chip.setToolTip("الضريبة: المحل غير مسجّل — بدون ضريبة")
        head.addWidget(self.no_tax_chip)
        self.tax_inclusive = QCheckBox("شامل الضريبة")
        self.tax_inclusive.setChecked(True)
        self.tax_inclusive.setToolTip("محدد: الضريبة ضمن السعر. غير محدد: تضاف إلى السعر.")
        head.addWidget(self.tax_inclusive)
        self.no_tax_chip.setVisible(not self._tax_enabled)
        self.tax_inclusive.setVisible(self._tax_enabled)
        self.tax_inclusive.toggled.connect(self._refresh_total)
        self.subtotal_label = QLabel()
        self.invoice_disc = ltr_edit("0.00")
        self.invoice_disc.setFixedWidth(150)
        self.invoice_disc.textChanged.connect(self._refresh_total)
        for label, w in (("الإجمالي قبل الخصم", self.subtotal_label),
                          ("خصم على الفاتورة", self.invoice_disc)):
            body.addWidget(kv_row(label, w))
        self.tax_value = QLabel()
        self.tax_row = kv_row("منها ضريبة القيمة المضافة", self.tax_value)
        self.tax_caption = self.tax_row.layout().itemAt(0).widget()
        body.addWidget(self.tax_row)
        self.tax_row.setVisible(self._tax_enabled)
        self.summary_footer = InvoiceFooter()
        footer = self.summary_footer.layout()
        self.total_box = InvoiceTotal(
            "الإجمالي النهائي", self._fill_exact_cash, compact=True)
        self.total_box.setAccessibleName("تعبئة المبلغ النقدي المطلوب")
        self.total_label = self.total_box.value
        footer.addWidget(self.total_box)
        self._payment_section(body)
        return box

    def _payment_section(self, body: QVBoxLayout) -> None:
        self.split_btn = QPushButton("تقسيم الدفع على وسيلتين")
        self.split_btn.setObjectName("link")
        self.split_btn.setCheckable(True)
        label_row = QHBoxLayout()
        label_row.addWidget(field_label("طريقة الدفع"))
        label_row.addStretch(1)
        label_row.addWidget(self.split_btn)
        body.addLayout(label_row)
        method_row = QHBoxLayout()
        self.pay1_method = self._method_combo(first_blank=False)
        self.pay1_method.setObjectName("payMethod")
        # Sized to its longest hint, it forced the payment column wide enough to clip the page at 1366px
        self.pay1_method.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.pay1_method.setMinimumContentsLength(14)
        self.pay1_method.currentIndexChanged.connect(self._on_method_changed)
        method_row.addWidget(self.pay1_method, 1)
        method_row.addWidget(kbd("F8"))
        body.addLayout(method_row)
        pbox = QFrame()
        self.pay_box = pbox
        pbox.setObjectName("payBox")
        pb = QVBoxLayout(pbox)
        pb.setContentsMargins(12, 12, 12, 12)
        pb.setSpacing(10)
        self.cash_box = QWidget()
        cash_layout = QVBoxLayout(self.cash_box)
        cash_layout.setContentsMargins(0, 0, 0, 0)
        cash_layout.setSpacing(10)
        rec = QHBoxLayout()
        self.pay1_received = ltr_edit("0.00")
        self.pay1_received.textChanged.connect(self._refresh_change)
        rec.addWidget(muted("المبلغ المستلم"))
        rec.addStretch(1)
        rec.addWidget(self.pay1_received, 2)
        cash_layout.addLayout(rec)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.amount_chips = []
        for _ in range(2):
            b = QPushButton()
            b.setObjectName("quickChip")
            b.clicked.connect(lambda _c=False, btn=b: self.pay1_received.setText(btn.property("amount") or ""))
            self.amount_chips.append(b)
            chips.addWidget(b, 1)
        cash_layout.addLayout(chips)
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
        cash_layout.addWidget(change)
        pb.addWidget(self.cash_box)
        self.pay1_ref = ltr_edit("رقم الإيصال / المرجع")
        pb.addWidget(self.pay1_ref)
        self.pay2_ref = ltr_edit("مرجع الوسيلة الثانية")
        pb.addWidget(self.pay2_ref)
        body.addWidget(pbox)
        self.credit_hint = muted("")
        self.credit_hint.setVisible(False)
        body.addWidget(self.credit_hint)
        self.instalment_hint = muted("يُرحَّل كامل المبلغ على حساب التقسيط، ثم يُعتمد العقد ويُحصَّل المقدم.")
        self.instalment_hint.setVisible(False)
        body.addWidget(self.instalment_hint)

        self.split = QFrame()
        sp = QGridLayout(self.split)
        sp.setContentsMargins(0, 0, 0, 0)
        self.pay1_amount = ltr_edit("فارغ = المتبقي")
        self.pay2_method = self._method_combo(first_blank=True)
        self.pay2_amount = ltr_edit("فارغ = المتبقي")
        self.pay1_amount.textChanged.connect(self._refresh_change)
        self.pay2_amount.textChanged.connect(self._refresh_change)
        self.pay2_method.currentIndexChanged.connect(self._on_method_changed)
        sp.addWidget(field("مبلغ الوسيلة الأولى", self.pay1_amount), 0, 0, 1, 2)
        sp.addWidget(field("الوسيلة الثانية", self.pay2_method), 1, 0)
        sp.addWidget(field("مبلغها", self.pay2_amount), 1, 1)
        self.split.setVisible(False)
        self.split_btn.toggled.connect(self._on_split_toggled)
        body.addWidget(self.split)

        self.post_btn = QPushButton("اعتماد وتحصيل")
        self.post_btn.setObjectName("primary")
        self.post_btn.setIcon(icons.icon("check", tokens.ICON_ON_PRIMARY, 18))
        self.post_btn.setProperty("shortcutBadge", True)
        inner = QHBoxLayout(self.post_btn)
        inner.setContentsMargins(16, 0, 16, 0)
        inner.addStretch(1)
        inner.addWidget(kbd("F9", on_primary=True))
        self.post_btn.clicked.connect(self.post)
        self.summary_footer.add_action(self.post_btn)
        self.summary_footer.add_action(deferred_button("حفظ كمسودة"))
        self.post_print_btn = QPushButton("اعتماد وطباعة")
        self.post_print_btn.setIcon(icons.icon("printer", tokens.ICON_MUTED, 16))
        self.post_print_btn.clicked.connect(lambda: self.post(and_print=True))
        self.summary_footer.add_action(self.post_print_btn)
        self._on_method_changed()

    # ---------- Builders ----------
    def _set_tier(self, key: str) -> None:
        self.tier = key or "retail"
        self.tier_combo.blockSignals(True)
        self.tier_combo.setCurrentIndex(max(self.tier_combo.findData(self.tier), 0))
        self.tier_combo.blockSignals(False)
        self.tier_chip.setText(f"فئة السعر: {TIER_SHORT[self.tier]}")
        if hasattr(self, "item_search") and self.item_search.popup.isVisible():
            self.item_search.search()

    def _set_kind(self, kind) -> None:
        self._kind = kind
        for k, b in self.kind_tabs.items():
            b.setChecked(k == kind)
        if self.item_search.text().strip():
            self._do_item_search()

    @staticmethod
    def _method_combo(first_blank: bool) -> QComboBox:
        """Only the methods the engine supports; the second starts with a 'none' entry."""
        c = QComboBox()
        if first_blank:
            c.addItem("— لا —", None)
        for key, label in PAYMENT_LABELS.items():
            if first_blank and key == "installment":
                continue
            c.addItem(icons.icon("cash", ICON_PRIMARY, 20), label, key)
            c.setItemData(c.count() - 1, PAYMENT_HINTS[key], Qt.ItemDataRole.ToolTipRole)
        return c

    def _shortcuts(self) -> None:
        # Window-wide while the page shows: arriving from the sidebar leaves focus outside the page,
        # and a children-only context then ignored every key until the user clicked inside
        ctx = Qt.ShortcutContext.WindowShortcut
        for key, slot in (("F2", self.item_search.setFocus), ("F4", self._focus_customer),
                          ("F8", self.pay1_method.setFocus), ("F9", self.post)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(ctx)
            sc.activated.connect(slot)

    def _focus_customer(self) -> None:
        self.customer.open_search()

    def _stamp(self) -> str:
        now = datetime.now()
        self.sale_date.setText(ltr(now.strftime("%Y-%m-%d %H:%M")))
        return date.today().isoformat()

    def _extra_summary_text(self) -> str:
        parts = []
        if self.odometer.text().strip():
            parts.append(f"العداد {ltr(self.odometer.text().strip())}")
        if self.plate.text().strip():
            parts.append(f"اللوحة {ltr(self.plate.text().strip())}")
        if self.note.text().strip():
            parts.append("ملاحظة")
        return " · ".join(parts)

    def _selected_methods(self) -> tuple:
        first = self.pay1_method.currentData()
        second = self.pay2_method.currentData() if self.split_btn.isChecked() else None
        return first, second if first != "installment" else None

    def _on_split_toggled(self, enabled: bool) -> None:
        self.split.setVisible(enabled)
        if not enabled:
            self.pay2_method.blockSignals(True)
            self.pay2_method.setCurrentIndex(0)
            self.pay2_method.blockSignals(False)
            self.pay1_amount.clear()
            self.pay2_amount.clear()
            self.pay2_ref.clear()
        self._on_method_changed()

    def _on_method_changed(self, *_args) -> None:
        first, second = self._selected_methods()
        on_instalments = first == "installment"
        if on_instalments and self.split_btn.isChecked():
            self.split_btn.setChecked(False)
            return
        previous = getattr(self, "_previous_methods", (None, None))
        self._previous_methods = first, second
        if previous[0] != first:
            self.pay1_ref.clear()
        if previous[1] != second:
            self.pay2_ref.clear()
        if ("cash" in previous) != ("cash" in (first, second)):
            self.pay1_received.clear()
        cash = "cash" in (first, second)
        reference_methods = ("card", "transfer", "wallet")
        ref1, ref2 = first in reference_methods, second in reference_methods
        self.cash_box.setVisible(cash)
        self.pay1_ref.setVisible(ref1)
        self.pay2_ref.setVisible(ref2)
        self.pay_box.setVisible(cash or ref1 or ref2)
        self.split_btn.setVisible(not on_instalments)
        self.instalment_hint.setVisible(on_instalments)
        self.post_btn.setText("اعتماد البيع الآجل" if first == "credit" and second is None
                              else "اعتماد وتحصيل")
        self._refresh_credit()
        self._refresh_change()

    # ---------- Customer and items ----------
    def _pick_customer(self, row):
        self._customer = row
        self.customer.commit_text(row[1])
        self._on_customer_changed()
        self.notice.show_message("")

    def _unresolved_customer(self, query, has_matches):
        message = ("اختر العميل من نتائج البحث." if has_matches else
                   f"لا يوجد عميل مسجّل يطابق «{query}».")
        self.notice.show_message(message, "warn")

    def _open_new_customer_dialog(self, query):
        if not self.session.can("customers.manage"):
            return
        dlg = QuickCustomerDialog(self.session, query, parent=self)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.created:
                c = dlg.created
                self._pick_customer((c.id, c.name, c.mobile, c.price_level,
                                     c.customer_type, "", c.active))
        finally:
            dlg.deleteLater()

    def _on_customer_changed(self, *_args) -> None:
        cid, _name, mobile, level, _kind, vehicle, _active = self._customer
        vehicle = (vehicle or "").strip()
        self.plate.setPlaceholderText(f"{vehicle} · رقم اللوحة" if vehicle else "رقم اللوحة")
        if cid:
            n, last = sale_view.customer_stats(self.session.con, cid)
            history = f"{ltr(str(n))} فواتير سابقة" + (f" · آخر زيارة {ltr(last)}" if last else "")
            self.party_info.set_info("عميل مسجّل", registered=True, mobile=mobile, description=history)
        else:
            self.party_info.set_info("عميل نقدي", description="بيع نقدي بدون حساب عميل")
        self._set_tier(level or "retail")
        self._refresh_credit()

    # ---------- Lines: one self-contained row per item, added on pick ----------
    def _add_row(self, item_id: int, kind: str, name: str) -> None:
        row_w = QWidget()
        h = self._line_table.row_layout(row_w)

        num_lbl = muted("")
        num_lbl.setWordWrap(False)
        h.addWidget(num_lbl, 0, Qt.AlignmentFlag.AlignTop)

        item_w = QWidget()
        iw = QHBoxLayout(item_w)
        iw.setContentsMargins(0, 0, 0, 0)
        iw.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.icon(KIND_ICON.get(kind, "inventory"), ICON_MUTED, 16).pixmap(16, 16))
        name_lbl = wrapping_line_label(name)
        iw.addWidget(ic)
        iw.addWidget(name_lbl, 1)
        h.addWidget(item_w, 1, Qt.AlignmentFlag.AlignTop)

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

    def _tier_price(self, retail, wholesale, super_price):
        prices = {"price_retail": retail, "price_wholesale": wholesale, "price_super": super_price}
        return prices[TIER_ATTR[self.tier]] or retail

    def _tier_price_text(self, item_id: int) -> str:
        try:
            it = items.get(self.session.con, item_id)
        except items.ItemError:
            return "0.00"
        cents = self._tier_price(it.price_retail, it.price_wholesale, it.price_super)
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
        self._line_table.sync_columns()
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
        """Gross payable in piasters; never use an invalid VAT preview for payment."""
        if self._tax_enabled:
            if self._tax_error:
                raise SaleError(self._tax_error)
            return self._tax_quote.total if self._tax_quote is not None else 0
        try:
            inv_disc = parse_money(self.invoice_disc.text() or "0")
        except MoneyError:
            inv_disc = 0
        return self.subtotal_minor() - self._line_discount_minor() - inv_disc

    def refresh(self) -> None:
        """Re-read tax settings and item treatments when returning to this page."""
        self._refresh_total()

    def _refresh_total(self, *_args) -> None:
        enabled = settings.get(self.session.con, "tax.enabled", "0") == "1"
        if enabled != self._tax_enabled:
            self._tax_enabled = enabled
            self.tax_inclusive.blockSignals(True)
            self.tax_inclusive.setChecked(True)
            self.tax_inclusive.blockSignals(False)
        self.no_tax_chip.setVisible(not enabled)
        self.tax_inclusive.setVisible(enabled)
        self.tax_row.setVisible(enabled)
        # VAT document rendering is a separate stage; do not print an incomplete
        # tax invoice from this experimental flow.
        self.post_print_btn.setEnabled(not enabled)
        self.post_print_btn.setToolTip("طباعة الفاتورة الضريبية في المرحلة التالية" if enabled else "")
        self._tax_quote = None
        self._tax_error = None
        self.subtotal_label.setText(ltr(fmt(self.subtotal_minor())))
        if enabled:
            self.tax_caption.setText("منها ضريبة القيمة المضافة" if self.tax_inclusive.isChecked()
                                     else "ضريبة القيمة المضافة")
            try:
                discount = parse_money(self.invoice_disc.text() or "0")
                if self._rows:
                    lines, error = self._validate_lines()
                    if error:
                        raise SaleError(error)
                    self._tax_quote = sales.compute_sale_totals(
                        self.session.con, lines, discount,
                        prices_tax_inclusive=self.tax_inclusive.isChecked())
                elif discount != 0:
                    raise SaleError("الخصم أكبر من قيمة السطور")
            except (MoneyError, SaleError) as e:
                self._tax_error = str(e)
            self.tax_value.setText("—" if self._tax_error else ltr(fmt(
                self._tax_quote.tax_total if self._tax_quote is not None else 0)))
            self.tax_value.setToolTip(self._tax_error or "")
        try:
            total = self.total_minor()
        except SaleError:
            self.total_label.setText("—")
        else:
            self.total_label.setText(f"{ltr(fmt(total))} ج.م")
        self._refresh_change()

    def _payment_parts(self, total: int) -> list[tuple]:
        first, second = self._selected_methods()
        if first == "installment" or not self.split_btn.isChecked():
            return [(first, total)]
        if second is None:
            raise sales_rules.SaleRuleError("اختر وسيلة الدفع الثانية أو ألغِ تقسيم الدفع")
        if first == second:
            raise sales_rules.SaleRuleError("لا تتكرر الوسيلة في الفاتورة الواحدة")
        first_text, second_text = self.pay1_amount.text().strip(), self.pay2_amount.text().strip()
        if first_text:
            amount = parse_money(first_text)
        elif second_text:
            amount = total - parse_money(second_text)
        else:
            raise sales_rules.SaleRuleError("حدد مبلغ إحدى وسيلتي الدفع")
        rest = parse_money(second_text) if second_text else total - amount
        if amount <= 0 or rest <= 0 or amount + rest != total:
            raise sales_rules.SaleRuleError("مبالغ الدفع يجب أن تكون موجبة ومجموعها يساوي الإجمالي")
        return [(first, amount), (second, rest)]

    def _cash_due(self) -> int:
        return sum(amount for method, amount in self._payment_parts(self.total_minor()) if method == "cash")

    def _fill_exact_cash(self, *_args) -> None:
        try:
            due = self._cash_due()
        except (MoneyError, SaleError, sales_rules.SaleRuleError):
            return
        if due > 0:
            self.pay1_received.setText(f"{due // 100}.{due % 100:02d}")

    def _refresh_credit(self) -> None:
        credit = "credit" in self._selected_methods()
        self.credit_hint.setVisible(credit)
        if not credit:
            return
        cid = self._customer[0]
        if not cid:
            self.credit_hint.setText("البيع الآجل يحتاج عميلًا مسجلًا")
            return
        con = self.session.con
        try:
            customer = customers.get(con, cid)
        except customers.CustomerError as e:
            self.credit_hint.setText(str(e))
            return
        if not customer.active:
            self.credit_hint.setText("العميل موقوف")
            return
        debt = customer_receipts.customer_balance(con, cid)
        deposit = max(0, customer_receipts.deposit_balance(con, cid))
        room = customer.credit_limit + deposit
        available = max(0, room - debt) if room > 0 else 0
        self.credit_hint.setText(f"حد الائتمان: {ltr(fmt(customer.credit_limit))} · المديونية: {ltr(fmt(debt))}"
                                 f"\nالمتاح: {ltr(fmt(available))} ج.م")

    def _refresh_change(self, *_args) -> None:
        try:
            due = self._cash_due()
        except (MoneyError, SaleError, sales_rules.SaleRuleError):
            due = 0
        self.total_box.setEnabled(due > 0)
        self.total_box.setCursor(Qt.CursorShape.PointingHandCursor if due > 0 else Qt.CursorShape.ArrowCursor)
        self.total_box.setToolTip("اضغط لتعبئة النقد المطلوب" if due > 0 else "")
        amounts = []
        if due > 0:
            for step in (10000, 50000):
                up = -(-due // step) * step
                if up != due and up not in amounts:
                    amounts.append(up)
        for i, b in enumerate(self.amount_chips):
            b.setVisible(i < len(amounts))
            if i < len(amounts):
                b.setText(ltr(fmt(amounts[i])))
                b.setProperty("amount", f"{amounts[i] // 100}.{amounts[i] % 100:02d}")
        try:
            received = parse_money(self.pay1_received.text()) if self.pay1_received.text().strip() else due
            self.change_label.setText(ltr(fmt(max(received - due, 0))) if due > 0 else ltr(fmt(0)))
        except MoneyError:
            self.change_label.setText("—")

    def _fail(self, text: str) -> bool:
        self.notice.show_message(text)
        return False

    # ---------- Payment and posting ----------
    def _payments(self, total: int) -> list:
        out = []
        for index, (method, amount) in enumerate(self._payment_parts(total)):
            received = None
            if method == "cash" and self.pay1_received.text().strip():
                received = parse_money(self.pay1_received.text())
            ref = self.pay1_ref if index == 0 else self.pay2_ref
            reference = (ref.text().strip() or None) if method in ("card", "transfer", "wallet") else None
            out.append(sales_rules.Payment(method=method, amount=amount, received=received, reference=reference))
        return out

    def post(self, and_print: bool = False) -> None:
        """Posts through the engine; any refusal is shown and nothing is written."""
        if self._busy:
            return
        if self.customer.pending:
            self._fail("اختر العميل من نتائج البحث أو اختر عميل نقدي قبل الاعتماد.")
            return
        lines, err = self._validate_lines()
        if err:
            self._fail(err)
            return
        try:
            enabled = settings.get(self.session.con, "tax.enabled", "0") == "1"
            if enabled != self._tax_enabled:
                self._refresh_total()
                self._fail("تغيّر إعداد الضريبة؛ راجع الإجمالي ثم اعتمد مرة أخرى.")
                return
            if enabled and and_print:
                self._fail("طباعة الفاتورة الضريبية لم تكتمل بعد؛ استخدم الاعتماد فقط.")
                return
            basis = self.tax_inclusive.isChecked() if enabled else None
            inv_disc = parse_money(self.invoice_disc.text() or "0")
            quote = sales.compute_sale_totals(self.session.con, lines, inv_disc,
                                              prices_tax_inclusive=basis)
            if enabled and quote != self._tax_quote:
                self._refresh_total()
                self._fail("تغيّر الحساب الضريبي؛ راجع الإجمالي ثم اعتمد مرة أخرى.")
                return
            payments = self._payments(quote.total)
            odometer = int(self.odometer.text()) if self.odometer.text().strip() else None
        except (MoneyError, ValueError, sales_rules.SaleRuleError) as e:
            self._fail(f"تحقق من المدخلات: {e}")
            return
        customer = self._customer
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = sales.post_sale(
                self.session.con, self.session.user, request_key=self._request_key,
                customer_id=customer[0] or None, lines=lines, payments=payments,
                sale_date=self._stamp(), invoice_discount=inv_disc,
                odometer=odometer, vehicle_plate=self.plate.text().strip() or None,
                notes=self.note.text().strip() or None,
                prices_tax_inclusive=basis, expected_totals=quote)
        except (SaleError, sales_rules.SaleRuleError) as e:
            self._refresh_total()
            self._fail(f"لم تُرحَّل الفاتورة: {e}")
        except Exception as e:      # Any unexpected failure: the engine is atomic, so nothing half-written remains
            self._fail(f"تعذّر الترحيل ولم يُحفظ شيء ({type(e).__name__}). أعد المحاولة أو راجع السجل.")
        else:
            on_instalments = self.pay1_method.currentData() == "installment"
            sale_id = self.posted.id
            self._reset_after_post()
            self.notice.show_message(f"تم ترحيل الفاتورة رقم {self.posted.number}", "success")
            if and_print:
                self._print_posted(sale_id)
            if on_instalments:
                self.contract_needed.emit(sale_id)
        finally:
            self._busy = False
            self.post_btn.setEnabled(True)

    def _print_posted(self, sale_id: int) -> None:
        """A failed print never undoes a posted invoice; it is reprinted from the sales screen."""
        from morv.infra import sale_view, shop
        from morv.ui import printing
        try:
            shop_data = shop.load(self.session.con)
            saved = sale_view.get_sale(self.session.con, sale_id)
            where = printing.send(shop_data, printing.sale_document(saved, shop_data))
        except (printing.PrintError, LookupError) as e:
            self.notice.show_message(f"رُحِّلت الفاتورة ولم تُطبع: {e}")
            return
        self.notice.show_message(f"رُحِّلت الفاتورة وأُرسلت إلى {where}", "success")

    def _reset_after_post(self) -> None:
        self._request_key = uuid.uuid4().hex
        for row in list(self._rows):
            self._rows_container.removeWidget(row["widget"])
            row["widget"].deleteLater()
        self._rows.clear()
        self.tax_inclusive.blockSignals(True)
        self.tax_inclusive.setChecked(True)
        self.tax_inclusive.blockSignals(False)
        self.empty_label.show()
        self.odometer.clear()
        self.plate.clear()
        self.note.clear()
        self.extra_toggle.setChecked(False)
        self.invoice_disc.clear()
        self.pay1_amount.clear()
        self.pay1_received.clear()
        self.pay1_ref.clear()
        self.pay2_ref.clear()
        self.pay2_method.setCurrentIndex(0)
        self.pay2_amount.clear()
        self.split_btn.setChecked(False)
        self.pay1_method.setCurrentIndex(self.pay1_method.findData("cash"))
        self._pick_customer(CASH_CUSTOMER)
        self.number_edit.setText(sales.next_number_preview(self.session.con))
        self._stamp()
        self._on_row_changed()

    def has_draft(self) -> bool:
        return bool(
            self._rows
            or self._customer[0] is not None
            or self.customer.pending
            or self.tier != "retail"
            or self.pay1_method.currentData() != "cash"
            or self.split_btn.isChecked()
            or (self._tax_enabled and not self.tax_inclusive.isChecked())
            or any(widget.text().strip() for widget in (
                self.note, self.plate, self.odometer,
                self.invoice_disc, self.pay1_received,
                self.pay1_amount, self.pay2_amount,
                self.pay1_ref, self.pay2_ref,
            ))
        )

    def cancel_invoice(self) -> None:
        if not confirm_discard_invoice(
                self, dirty=self.has_draft(), busy=self._busy):
            return
        self._reset_after_post()
        self.notice.show_message("")
