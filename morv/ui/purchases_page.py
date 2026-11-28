"""Purchases screen: invoice list and a full-page form matching the approved v1.0 design."""
import uuid
from dataclasses import replace
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from morv.core import dot as dotmod
from morv.core.money import MoneyError, fmt, parse_money, percent_of
from morv.core.text import ltr
from morv.infra import purchase_view, purchasing, settings, suppliers
from morv.infra.purchasing import FREIGHT_BASES, PurchaseError, PurchaseLine
from morv.infra import suppliers as sup_infra
from morv.ui import icons
from morv.ui.adaptive_layout import HeaderPlacement, InvoiceWorkspace
from morv.ui.invoice_lines import InvoiceLineTable, wrapping_line_label
from morv.ui.item_search import ItemSearch, KIND_ICON
from morv.ui.party_search import (FoldableDetails, PartyInfoStrip, PartySearch,
                                  QuickPartyDialog, party_field, phone_query)
from morv.ui.listpage import ListPage
from morv.ui.purchase_return_dialogs import PurchaseReturnDialog, SupplierReturnsDialog
from morv.ui.purchase_vat import PurchaseVatDialog, SupplierVatFields, VAT_MODES
from morv.ui.widgets import (DateEdit, InvoiceFooter, InvoiceTotal, chip, confirm_discard_invoice, deferred_button, field, ICON_MUTED, kv_row, ltr_edit, muted, Notice, primary_button, section_card)



class PurchasesPage(QStackedWidget):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.list_page = PurchasesListPage(session, self)
        self.refresh = self.list_page.refresh
        self.form_page = None
        self.addWidget(self.list_page)

    def open_form(self) -> None:
        if self.form_page is not None:
            old = self.form_page
            if not confirm_discard_invoice(
                    old, dirty=old.has_draft(), busy=old._busy):
                if not old._busy:
                    self.setCurrentWidget(old)
                return
            self.removeWidget(old)
            old.deleteLater()
        self.form_page = PurchaseForm(self.session, self)
        self.addWidget(self.form_page)
        self.setCurrentWidget(self.form_page)

    def back_to_list(self, keep_id: int | None = None) -> None:
        form = self.form_page
        posted = (
            form is not None and form.posted is not None
            and keep_id == form.posted.id
        )
        if form is not None and not posted:
            if not confirm_discard_invoice(
                    form, dirty=form.has_draft(), busy=form._busy):
                return
        self.setCurrentWidget(self.list_page)
        if form is not None and not posted:
            self.removeWidget(form)
            self.form_page = None
            form.deleteLater()
        self.list_page.reload(keep_id=keep_id)


class PurchasesListPage(ListPage):
    title = "المشتريات"
    add_label = "فاتورة شراء جديدة"
    search_placeholder = "رقم الفاتورة، المورد، أو رقم فاتورة المورد (F2)"
    columns = [
        ("الفاتورة", lambda r: ltr(r[1])),
        ("التاريخ", lambda r: ltr(r[4])),
        ("المورد", lambda r: r[2]),
        ("رقم فاتورة المورد", lambda r: ltr(r[3])),
        ("الإجمالي", lambda r: ltr(fmt(r[5]))),
        ("المرتجع", lambda r: ltr(fmt(r[6])) if r[6] else "—"),
    ]

    def __init__(self, session, stack=None):
        super().__init__(session)
        self.stack = stack

    def can_edit(self) -> bool:
        return self.session.can("purchases.post")

    def extra_actions(self) -> list:
        """A purchase return had no trace on this screen; it could only be found under its supplier."""
        self.returns_btn = None
        if not self.session.can("purchases.post"):
            return []
        self.returns_btn = QPushButton("مرتجعات الشراء")
        self.returns_btn.clicked.connect(self.open_returns)
        return [self.returns_btn]

    def open_returns(self) -> None:
        SupplierReturnsDialog(self.session, parent=self).exec()
        self.reload(keep_id=self.current_id)

    def can_view(self, con=None) -> bool:
        return self.session.can("purchases.post", con) or self.session.can("items.cost_view", con)

    def fetch(self, con, query, filter_key, offset):
        if not self.can_view(con):
            return []
        return purchase_view.list_invoices(con, query, offset=offset)

    def total(self) -> str:
        if not self.can_view():
            return "لا تملك صلاحية الاطلاع على فواتير الشراء"
        return f"{purchase_view.count(self.session.con)} فاتورة شراء"

    def build_preview(self, row_id: int) -> None:
        inv = purchase_view.get_invoice(self.session.con, row_id)
        self.heading(f"فاتورة شراء {inv.number}", f"المورد: {inv.supplier_name}")
        self.summary_section("بيانات الفاتورة", "truck")
        details = [
            ("رقم فاتورة المورد", ltr(inv.supplier_invoice_no)),
            ("تاريخ الفاتورة", ltr(inv.invoice_date)),
            ("تاريخ الاستلام", ltr(inv.received_date)),
        ]
        if inv.notes:
            details.append(("ملاحظات", inv.notes))
        self.summary_details(details)
        metrics = [("إجمالي الفاتورة", ltr(fmt(inv.total)))]
        if inv.tax_total:
            metrics.append(("الضريبة", ltr(fmt(inv.tax_total))))
        if inv.invoice_discount:
            metrics.append(("خصم الفاتورة", ltr(fmt(inv.invoice_discount))))
        if inv.freight_amount:
            basis_label = {"qty": "كمية", "value": "قيمة", "manual": "يدوي"}[inv.freight_basis]
            metrics.append((f"نقل وتحميل (أساس {basis_label})", ltr(fmt(inv.freight_amount))))
        if inv.returned:
            metrics += [("المرتجع", ltr(fmt(inv.returned))), ("الصافي", ltr(fmt(inv.total - inv.returned)))]
        self.summary_metrics(metrics)
        self.summary_section("تفاصيل الأصناف", "tire")
        self.summary_records(
            ["الصنف", "الكمية", "تكلفة الوحدة", "الإجمالي قبل الخصم والشحن",
             "الإجمالي" if inv.tax_total else "الصافي", "DOT"],
            [(f"{ln.line_no}. {ln.item_name}", ltr(str(ln.qty)), ltr(fmt(ln.unit_cost)),
              ltr(fmt(ln.line_total)),
              ltr(fmt(ln.net_total + (0 if inv.prices_tax_inclusive else ln.tax_amount))),
              ltr(ln.dot) if ln.dot else "") for ln in inv.lines])
        if self.session.can("purchases.post"):
            send_back = QPushButton("مرتجع للمورد")
            send_back.clicked.connect(lambda: self.open_return(inv))
            self.actions(send_back)

    def open_return(self, inv) -> None:
        dlg = PurchaseReturnDialog(self.session, inv.supplier_id, inv.supplier_name, self, invoice_id=inv.id)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(keep_id=inv.id)

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

class _NewSupplierDialog(QuickPartyDialog):
    def __init__(self, name_hint: str, mobile_hint: str, session, parent=None):
        super().__init__(session, "المورد", name_hint, mobile_hint, sup_infra.SupplierError,
                         optional=True, parent=parent)
        self.tax_id = ltr_edit("")
        self.body.addWidget(field("رقم التسجيل الضريبي", self.tax_id))
        self.vat_fields = SupplierVatFields(self.body)

    def _create_party(self, name, mobile):
        return sup_infra.create(self.session.con, self.session.user,
                                sup_infra.Supplier(id=None, name=name, mobile=mobile,
                                                   address=self.addr_edit.text().strip(),
                                                   notes=self.notes_edit.text().strip(),
                                                   tax_id=self.tax_id.text().strip(),
                                                   vat_status=self.vat_fields.values()[0],
                                                   vat_prices_inclusive=self.vat_fields.values()[1]))

    def _save(self):
        self.save()


def _amount_or_percent_row(label: str = "", placeholder: str = "0.00", *,
                           percent_only: bool = False) -> tuple[QWidget, QLineEdit, QPushButton]:
    """An amount-or-percent input row, shared by the line and invoice discount inputs."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    if label:
        h.addWidget(muted(label))
    edit = ltr_edit(placeholder)
    h.addWidget(edit, 1)
    unit_btn = QPushButton("%" if percent_only else "ج.م")
    unit_btn.setObjectName("stepperBtn")
    unit_btn.setCheckable(True)
    unit_btn.setFixedWidth(34)
    if percent_only:
        unit_btn.setProperty("percentToggle", True)
        unit_btn.setAccessibleName("الخصم بالنسبة المئوية")
        unit_btn.setToolTip("مفعّل: نسبة مئوية؛ غير مفعّل: مبلغ")
    else:
        unit_btn.setToolTip("بالمبلغ أو كنسبة من قيمة السطر")
        unit_btn.toggled.connect(lambda on, b=unit_btn: b.setText("%" if on else "ج.م"))
    h.addWidget(unit_btn)
    return row, edit, unit_btn


def _resolve_amount_or_percent(edit: QLineEdit, unit_btn: QPushButton, base: int) -> int | None:
    """None means the field could not be parsed; caller decides how to surface that."""
    text = edit.text().strip()
    if not text:
        return 0
    try:
        return percent_of(base, text) if unit_btn.isChecked() else parse_money(text)
    except MoneyError:
        return None


# ---------------------------------------------------------------------------
# Full-page purchase form
# ---------------------------------------------------------------------------

NO_SUPPLIERS_NOTICE = "لا يوجد موردون نشطون؛ أضف مورّدًا من شاشة الموردين أولًا."


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
        self._vat_enabled = settings.get(session.con, "tax.enabled", "0") == "1"
        self._vat_mode = "unknown"
        self._vat_freight = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(12)

        self.notice = Notice()
        header = self._header()
        root.addWidget(header)
        self.header_placement = HeaderPlacement(header, root)
        root.addWidget(self.notice)

        # Shared adaptive invoice columns
        primary = QWidget()
        main_col = QVBoxLayout(primary)
        main_col.setContentsMargins(0, 0, 0, 0)
        main_col.setSpacing(14)

        # ---- Card 1: Supplier & invoice header ----
        box, body, head = section_card("المورد والفاتورة", "suppliers")

        self.sup_search = PartySearch(
            self, label="المورد",
            lookup=lambda q, limit: suppliers.search(self.session.con, q, limit=limit),
            pick=lambda row: self._pick_supplier(*row[:3]),
            new=self._open_new_supplier_dialog,
            can_new=lambda: self.session.can("suppliers.manage"),
        )

        body.addWidget(party_field(self.sup_search))

        self._received_date = date.today().isoformat()
        entry_no_preview = purchasing.next_number_preview(self.session.con)
        self.entry_no = chip(entry_no_preview)
        self.entry_no.setToolTip("رقم الإدخال")
        self.recv_date = chip(ltr(self._received_date))
        self.recv_date.setToolTip("تاريخ الاستلام")
        head.addWidget(self.entry_no)
        head.addWidget(self.recv_date)

        self.inv_no = ltr_edit("كما في فاتورته")
        self.inv_date = DateEdit()
        self.inv_date.setText(date.today().isoformat())
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("اختياري")

        self.extra_box = FoldableDetails(
            [("رقم فاتورة المورد", self.inv_no), ("تاريخ فاتورة المورد", self.inv_date),
             ("ملاحظة", self.notes)],
            "تفاصيل الفاتورة والملاحظة", self._extra_summary_text, stretches=(2, 2, 3))
        self.vat_details_btn = self.extra_box.add_detail_action("تفاصيل الضريبة", self._edit_vat_details)
        self.vat_details_btn.setVisible(self._vat_enabled)
        self.extra_toggle = self.extra_box.toggle
        self.extra_summary = self.extra_box.summary
        self.party_info = PartyInfoStrip(self.extra_box)
        self.party_info.set_info("اختر المورد")
        body.addWidget(self.extra_box)
        body.addWidget(self.party_info)
        main_col.addWidget(box)

        # ---- Card 2: Invoice lines ----
        ibox, ibody, ihead_w = section_card("الأصناف المستلمة", "purchases")

        # Item count chip in card header (we'll update it)
        self._line_count_lbl = muted("0 سطر")
        ihead_w.addWidget(self._line_count_lbl)

        # Item search — picking a result adds it straight to the lines below
        item_search_row = QHBoxLayout()
        item_search_row.setSpacing(8)
        self.item_search = ItemSearch(self, lambda: ("tire", "battery"))
        self._item_popup = self.item_search.popup
        self._do_item_search = self.item_search.search

        new_item_btn = QPushButton("صنف جديد")
        new_item_btn.clicked.connect(self.item_search.new_item)
        item_search_row.addWidget(self.item_search, 1)
        item_search_row.addWidget(new_item_btn)
        ibody.addLayout(item_search_row)

        self._rows: list[dict] = []
        self._line_table = InvoiceLineTable(
            ("#", "الصنف", "الكمية", "تكلفة الوحدة", "DOT", "الخصم", "الشحن", "الإجمالي", ""),
            purchase=True)
        self._rows_container = self._line_table.rows
        ibody.addWidget(self._line_table, 1)

        self._empty_lbl = muted("لا توجد أصناف بعد. ابحث عن صنف أو امسح الباركود، أو أضف صنفًا جديدًا.")
        self._rows_container.addWidget(self._empty_lbl)
        # Trailing stretch absorbs leftover height so rows stay pinned at the
        # top of the card instead of stretching/centering when there are few.
        self._rows_container.addStretch(1)

        main_col.addWidget(ibox, 1)

        # ---- sumCol: summary card ----
        summary = QWidget()
        sum_col = QVBoxLayout(summary)
        sum_col.setContentsMargins(0, 0, 0, 0)
        sum_col.setSpacing(14)

        sbox, sbody, _shead = section_card("ملخص فاتورة الشراء", "purchases", compact=True)

        self._sub_lbl = QLabel(ltr(fmt(0)))
        self._sub_lbl.setObjectName("cardTitle")
        sbody.addWidget(kv_row("إجمالي الأصناف", self._sub_lbl))

        # Read-only calculated VAT; no input field or tax controls in the summary.
        self.tax = QLabel(ltr(fmt(0)))
        self.tax.setObjectName("cardTitle")
        self.tax.setEnabled(self._vat_enabled)
        self.tax_row = kv_row("الضريبة", self.tax)
        self.tax_row.setVisible(self._vat_enabled)
        sbody.addWidget(self.tax_row)

        # Invoice discount: amount-or-percent, same pattern as the per-line discount.
        disc_field, self.disc_edit, self.disc_unit_btn = _amount_or_percent_row()
        sbody.addWidget(field("الخصم", disc_field))

        # Shipping amount + allocation basis (qty/value/manual, per P01).
        self.ship = ltr_edit("0.00")
        sbody.addWidget(field("نقل وتحميل", self.ship))
        basis_labels = {"qty": "بالكمية", "value": "بالقيمة", "manual": "توزيع يدوي"}
        self.freight_basis = QComboBox()
        for basis in FREIGHT_BASES:
            self.freight_basis.addItem(basis_labels[basis], basis)
        self.freight_basis_field = field("توزيع الشحن", self.freight_basis)
        self.freight_basis_field.hide()
        sbody.addWidget(self.freight_basis_field)

        self.summary_footer = InvoiceFooter()
        footer = self.summary_footer.layout()
        self.total_box = InvoiceTotal("إجمالي الفاتورة", compact=True)
        self._total_lbl = self.total_box.value
        footer.addWidget(self.total_box)

        # Payment row
        pay_grid = QGridLayout()
        pay_grid.setHorizontalSpacing(10)
        self.paid = ltr_edit("0.00")
        self.pay_method = QComboBox()
        for label in ("الخزينة (نقدي)", "تحويل بنكي", "InstaPay / محفظة"):
            self.pay_method.addItem(label)
        # Same as discount/tax/shipping above: no partial-payment/cash-ledger
        # handling exists in the engine yet, so this stays disabled, not wired.
        for disabled_edit in (self.paid, self.pay_method):
            disabled_edit.setEnabled(False)
            disabled_edit.setToolTip("غير مفعّل حاليًا — سيُضاف لاحقًا")
        pay_grid.addWidget(field("مدفوع الآن", self.paid), 0, 0)
        pay_grid.addWidget(field("من", self.pay_method), 0, 1)
        sbody.addLayout(pay_grid)

        self._rem_lbl = QLabel()
        self._rem_lbl.setObjectName("cardTitle")
        sbody.addWidget(self._rem_lbl)

        # Main action
        self.post_btn = primary_button("اعتماد الاستلام وتحديث المخزون ⌨ F9")
        self.post_btn.clicked.connect(self.post)
        QShortcut(QKeySequence("F2"), self,
                  activated=self.item_search.setFocus)
        QShortcut(QKeySequence("F9"), self, activated=self.post)
        self.summary_footer.add_action(self.post_btn)

        # Deferred actions
        self.summary_footer.add_action(deferred_button("حفظ كمسودة"))
        self.summary_footer.add_action(deferred_button("طباعة إذن الاستلام"))

        sbody.addStretch(1)

        sum_col.addWidget(sbox)
        self.invoice_layout = InvoiceWorkspace(
            primary, summary, self.summary_footer, spacing=14, line_table=self._line_table)
        root.addWidget(self.invoice_layout, 1)

        self.disc_edit.textChanged.connect(self._on_row_changed)
        self.disc_unit_btn.toggled.connect(self._on_row_changed)
        self.ship.textChanged.connect(self._on_freight_basis_changed)
        self.freight_basis.currentIndexChanged.connect(self._on_freight_basis_changed)
        self._on_freight_basis_changed()
        if not suppliers.count(session.con):
            self.notice.show_message(NO_SUPPLIERS_NOTICE, "warn")

    # ---------- Header ----------
    def _header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        title = QLabel("فاتورة شراء / استلام بضاعة")
        title.setObjectName("pageTitle")
        h.addWidget(title)
        subtitle = QLabel("المخزن الرئيسي")
        subtitle.setObjectName("muted")
        h.addWidget(subtitle)
        draft_badge = QLabel("مسودة")
        draft_badge.setObjectName("pillWarn")
        h.addWidget(draft_badge)
        h.addStretch(1)
        h.addWidget(deferred_button("إرفاق صورة فاتورة المورد"))
        back = QPushButton("رجوع للقائمة")
        back.clicked.connect(lambda: self.stack.back_to_list())
        h.addWidget(back)
        return w

    # ---------- Builder helpers ----------
    def _pick_supplier(self, sup_id: int, name: str, mobile: str) -> None:
        changed = self._supplier_id != sup_id
        self._supplier_id = sup_id
        if self._vat_enabled and changed:
            self._vat_mode = suppliers.get(self.session.con, sup_id).vat_mode
            self._vat_freight = False
            for row in self._rows:
                row.pop("vat_override", None)
        self._supplier_name = name
        self._supplier_mobile = mobile
        self.sup_search.commit_text(name)
        self.party_info.set_info("مورد مسجّل", registered=True, mobile=mobile)
        if self.notice.text() == NO_SUPPLIERS_NOTICE:
            self.notice.show_message("")
        if self._vat_enabled:
            if changed:
                self._refresh_vat_costs()
            self.extra_box.refresh()
            self._on_row_changed()

    def _extra_summary_text(self):
        parts = []
        if self.inv_no.text().strip():
            parts.append(f"رقم {ltr(self.inv_no.text().strip())}")
        if self.inv_date.text() != self._received_date:
            parts.append(ltr(self.inv_date.text()))
        if self.notes.text().strip():
            parts.append("ملاحظة")
        if self._vat_enabled:
            parts.append(VAT_MODES[self._vat_mode])
        return " · ".join(parts)

    def _last_cost_for_mode(self, item_id):
        if not self._vat_enabled:
            return purchasing.last_cost(self.session.con, item_id)
        if self._vat_mode == "unknown":
            return None
        return purchasing.last_cost(self.session.con, item_id,
                                    prices_tax_inclusive=self._vat_mode == "inclusive")

    def _refresh_vat_costs(self):
        for row in self._rows:
            row["last_cost"] = self._last_cost_for_mode(row["item_id"])

    def _default_vat_rate(self):
        try:
            rate = int(settings.get(self.session.con, "tax.rate", ""))
            return rate if rate >= 0 else None
        except ValueError:
            return None

    def _edit_vat_details(self):
        if not self._vat_enabled:
            return
        dlg = PurchaseVatDialog(self._vat_mode, self._vat_freight, self._rows,
                                self._default_vat_rate(), self)
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            mode, freight, values = dlg.result_values
            self._vat_mode, self._vat_freight = mode, freight
            for row in self._rows:
                row.pop("vat_override", None)
            if mode != "none":
                for row, value in zip(self._rows, values, strict=True):
                    row["vat_override"] = value
            self._refresh_vat_costs()
            self.extra_box.refresh()
            self._on_row_changed()
        finally:
            dlg.deleteLater()

    def _vat_lines(self, lines, *, preview=False):
        if not self._vat_enabled:
            return lines
        if self._supplier_id is None or self.sup_search.pending:
            raise PurchaseError("اختر المورد أولًا.")
        if self._vat_mode == "unknown":
            raise PurchaseError("حدد حالة ضريبة المورد من بياناته أو تفاصيل الفاتورة.")
        if self._vat_mode == "none":
            return [replace(line, input_tax_rate=0, input_tax_deductible=False) for line in lines]
        result = []
        for i, (row, line) in enumerate(zip(self._rows, lines, strict=True), 1):
            rate, eligible = row.get("vat_override") or (self._default_vat_rate(), None)
            if rate is None:
                raise PurchaseError("نسبة الضريبة غير محددة في الإعدادات أو تفاصيل الفاتورة.")
            if not rate:
                eligible = False
            if eligible is None and not preview:
                raise PurchaseError(f"السطر {i}: أكد قابلية خصم الضريبة في تفاصيل الفاتورة.")
            # Preview of supplier payable is independent of deduction eligibility.
            # Do not post or display book cost using this provisional choice.
            result.append(replace(line, input_tax_rate=rate, input_tax_deductible=bool(eligible)))
        return result

    def _vat_kwargs(self):
        return dict(prices_tax_inclusive=self._vat_enabled and self._vat_mode == "inclusive",
                    freight_taxable=self._vat_enabled and self._vat_mode != "none" and self._vat_freight)

    def _open_new_supplier_dialog(self, query=None) -> None:
        self.sup_search.close_results()
        if not self.session.can("suppliers.manage"):
            return
        q = self.sup_search.text().strip() if query is None else query
        digits = phone_query(q)
        name_hint = "" if digits.isdigit() else q
        mobile_hint = digits if digits.isdigit() else ""
        dlg = _NewSupplierDialog(name_hint=name_hint, mobile_hint=mobile_hint,
                                 session=self.session, parent=self)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.created:
                self._pick_supplier(dlg.created.id, dlg.created.name, dlg.created.mobile)
        finally:
            dlg.deleteLater()

    # ---------- Item search ----------
    # ---------- Lines: one self-contained row per item, added on pick ----------
    def _add_row(self, item_id: int, kind: str, name: str) -> None:
        row_w = QWidget()
        h = self._line_table.purchase_row_layout(row_w)

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

        # Quantity stepper: explicit −/+ buttons instead of the spin box's
        # own tiny native arrows.
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 100000)
        qty_spin.setValue(1)
        qty_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        qty_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # The base QPushButton rule pads every button 14px each side; a
        # fixed-width button narrower than that padding has no room left
        # to draw its own glyph, which is why it looked blank. #stepperBtn
        # overrides the padding instead of fighting it with widget sizing.
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

        # Cost + an inline note comparing it against the item's last purchase cost
        cost_w = QWidget()
        cw = QVBoxLayout(cost_w)
        cw.setContentsMargins(0, 0, 0, 0)
        cw.setSpacing(2)
        cost_edit = ltr_edit("0.00")
        cost_note = wrapping_line_label()
        # Hidden (not just empty) so the cell is single-line height like its
        # row-mates; it only grows the row when there is a note to show,
        # instead of always reserving a second line that pushed cost_edit
        # up and out of vertical alignment with the rest of the row.
        cost_note.hide()
        cw.addWidget(cost_edit)
        cw.addWidget(cost_note)
        h.addWidget(cost_w, 1, Qt.AlignmentFlag.AlignTop)

        dot_edit = ltr_edit("WWYY")
        if kind == "battery":
            policy = dot_edit.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            dot_edit.setSizePolicy(policy)
            dot_edit.hide()
        h.addWidget(dot_edit, 1, Qt.AlignmentFlag.AlignTop)

        disc_row, disc_edit, disc_unit_btn = _amount_or_percent_row(percent_only=True)
        h.addWidget(disc_row, 0, Qt.AlignmentFlag.AlignTop)

        # Keep manual input separate from the calculated share to preserve the draft when switching basis.
        freight_row = QWidget()
        freight_layout = QHBoxLayout(freight_row)
        freight_layout.setContentsMargins(0, 0, 0, 0)
        freight_layout.setSpacing(0)
        freight_edit = ltr_edit("0.00")
        freight_value = QLabel("—")
        freight_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        freight_layout.addWidget(freight_edit)
        freight_layout.addWidget(freight_value)
        manual = self._freight_basis() == "manual"
        freight_edit.setVisible(manual)
        freight_value.setVisible(not manual)
        h.addWidget(freight_row, 0, Qt.AlignmentFlag.AlignTop)

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
        self._line_table.register_purchase_row(row_w)

        row = {"widget": row_w, "item_id": item_id, "kind": kind, "name": name,
              "last_cost": self._last_cost_for_mode(item_id),
              "num_lbl": num_lbl, "qty_spin": qty_spin, "cost_edit": cost_edit,
              "disc_edit": disc_edit, "disc_unit_btn": disc_unit_btn,
              "freight_row": freight_row, "freight_edit": freight_edit, "freight_value": freight_value,
              "cost_note": cost_note, "dot_edit": dot_edit, "total_lbl": total_lbl}
        qty_spin.valueChanged.connect(self._on_row_changed)
        cost_edit.textChanged.connect(self._on_row_changed)
        disc_edit.textChanged.connect(self._on_row_changed)
        disc_unit_btn.toggled.connect(self._on_row_changed)
        freight_edit.textChanged.connect(self._on_row_changed)
        del_btn.clicked.connect(lambda _=False, rw=row: self._remove_row(rw))

        self._rows.append(row)
        self._empty_lbl.hide()
        # Insert before the trailing stretch (always the layout's last item)
        # so rows stay stacked at the top instead of it.
        self._rows_container.insertWidget(self._rows_container.count() - 1, row_w)
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

    def _on_freight_basis_changed(self) -> None:
        self.freight_basis_field.setVisible(self._has_positive_freight())
        manual = self._freight_basis() == "manual"
        for row in self._rows:
            row["freight_edit"].setVisible(manual)
            row["freight_value"].setVisible(not manual)
        self._on_row_changed()

    def _on_row_changed(self) -> None:
        for row in self._rows:
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError:
                cost = 0
            self._update_cost_note(row, cost)
        self._line_count_lbl.setText(f"{len(self._rows)} سطر")
        self._refresh_summary()

    def _draft_lines(self) -> list[PurchaseLine] | None:
        """Best-effort PurchaseLines from the current row widgets, for the live preview only.

        DOT is not needed to calculate cost. Invalid money/quantity fields make the
        preview unavailable rather than showing a misleading gross amount.
        """
        lines = []
        for row in self._rows:
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError:
                return None
            qty = row["qty_spin"].value()
            disc = _resolve_amount_or_percent(row["disc_edit"], row["disc_unit_btn"], qty * cost)
            if cost <= 0 or disc is None or not 0 <= disc <= qty * cost:
                return None
            freight_share = None
            if self._freight_basis() == "manual":
                try:
                    freight_share = parse_money(row["freight_edit"].text().strip() or "0")
                except MoneyError:
                    return None
                if freight_share < 0:
                    return None
            lines.append(PurchaseLine(row["item_id"], qty, cost, None,
                                       line_discount=disc, freight_share=freight_share))
        return lines

    @staticmethod
    def _update_cost_note(row: dict, cost: int) -> None:
        last = row["last_cost"]
        note = row["cost_note"]
        if not last or cost <= 0:
            note.setText("")
            note.hide()
            return
        pct = (cost - last) / last * 100
        if cost > last:
            note.setText(f"أعلى من آخر شراء +{pct:.1f}%")
            note.setObjectName("costUp")
            note.show()
        elif cost < last:
            note.setText(f"أقل من آخر شراء {abs(pct):.1f}%")
            note.setObjectName("costDown")
            note.show()
        else:
            note.setText("")
            note.hide()
        note.style().unpolish(note)
        note.style().polish(note)

    def _sub_total(self) -> int:
        total = 0
        for row in self._rows:
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError:
                cost = 0
            total += row["qty_spin"].value() * cost
        return total

    def _has_positive_freight(self) -> bool:
        try:
            return parse_money(self.ship.text().strip() or "0") > 0
        except MoneyError:
            return False

    def _freight_basis(self) -> str:
        # Zero freight must never use hidden manual shares. Keep the selected
        # basis and draft shares available if the user enters a positive amount again.
        if not self._has_positive_freight():
            return "qty"
        return self.freight_basis.currentData() or "qty"

    def _validate_lines(self) -> tuple[list[PurchaseLine] | None, str | None]:
        """Parse and validate every row. Returns (lines, None) or (None, error)."""
        if not self._rows:
            return None, "أضف سطرًا واحدًا على الأقل قبل الترحيل."
        manual = self._freight_basis() == "manual"
        lines = []
        for i, row in enumerate(self._rows, start=1):
            try:
                cost = parse_money(row["cost_edit"].text())
            except MoneyError as e:
                return None, f"السطر {i} ({row['name']}): تكلفة الوحدة: {e}"
            if cost <= 0:
                return None, f"السطر {i} ({row['name']}): تكلفة الوحدة يجب أن تكون أكبر من صفر."
            qty = row["qty_spin"].value()
            dot_text = row["dot_edit"].text().strip()
            dot = None
            if dot_text:
                try:
                    dot = dotmod.parse_dot(dot_text)
                except dotmod.DotError as e:
                    return None, f"السطر {i} ({row['name']}): {e}"
            elif row["kind"] == "tire":
                return None, f"رمز DOT إلزامي للإطار «{row['name']}»."
            disc = _resolve_amount_or_percent(row["disc_edit"], row["disc_unit_btn"], qty * cost)
            if disc is None:
                return None, f"السطر {i} ({row['name']}): خصم السطر: قيمة غير صحيحة."
            freight_share = None
            if manual:
                try:
                    freight_share = parse_money(row["freight_edit"].text().strip() or "0")
                except MoneyError:
                    return None, f"السطر {i} ({row['name']}): نصيب الشحن: قيمة غير صحيحة."
                if freight_share < 0:
                    return None, f"السطر {i} ({row['name']}): نصيب الشحن لا يمكن أن يكون سالبًا."
            lines.append(PurchaseLine(row["item_id"], qty, cost, dot,
                                       line_discount=disc, freight_share=freight_share))
        return lines, None

    def _refresh_summary(self) -> None:
        sub = self._sub_total()
        self._sub_lbl.setText(ltr(fmt(sub)))
        allocations = None
        tax_calcs = None
        lines = self._draft_lines()
        try:
            freight_amount = parse_money(self.ship.text().strip() or "0")
            invoice_discount = _resolve_amount_or_percent(self.disc_edit, self.disc_unit_btn, sub)
            if lines and freight_amount >= 0 and invoice_discount is not None and invoice_discount >= 0:
                allocations = purchasing.compute_allocations(
                    lines, freight_amount, self._freight_basis(), invoice_discount)
                tax_calcs = purchasing.compute_line_taxes(
                    self._vat_lines(lines, preview=True), allocations,
                    tax_enabled=self._vat_enabled, **self._vat_kwargs())
        except (MoneyError, purchasing.PurchaseError):
            allocations = None  # Incomplete/invalid draft: show no misleading total.

        for i, row in enumerate(self._rows):
            total_label = row["total_lbl"]
            if allocations is None:
                row["freight_value"].setText("—")
                total_label.setText("—")
                total_label.setToolTip("")
                continue
            allocation, line = allocations[i], lines[i]
            row["freight_value"].setText(ltr(fmt(allocation.freight_share)))
            calc = tax_calcs[i]
            payable = calc.cost_amount + calc.deductible_amount
            total_label.setText(ltr(fmt(payable)))
            # Preserve the allocation breakdown on demand, without adding another visible row.
            if line.line_discount or allocation.invoice_share or allocation.freight_share:
                details = (f"خصم الصنف {ltr(fmt(line.line_discount))} ج.م\n"
                           f"حصة خصم الفاتورة {ltr(fmt(allocation.invoice_share))} ج.م\n"
                           f"حصة الشحن {ltr(fmt(allocation.freight_share))} ج.م")
                if not self._vat_enabled or not calc.tax_amount or row.get("vat_override") is not None:
                    average = (2 * calc.cost_amount + line.qty) // (2 * line.qty)
                    approx = "≈ " if calc.cost_amount % line.qty else ""
                    details += f"\nمتوسط تكلفة الوحدة {approx}{ltr(fmt(average))} ج.م"
                total_label.setToolTip(details)
            else:
                total_label.setToolTip("")

        self.tax.setText("—" if allocations is None and self._rows else
                         ltr(fmt(sum(c.tax_amount for c in tax_calcs or []))))
        if allocations is None and self._rows:
            self._total_lbl.setText("—")
            self._rem_lbl.setText("المعاينة غير مكتملة")
        else:
            net = sum(c.cost_amount + c.deductible_amount for c in tax_calcs or [])
            self._total_lbl.setText(f"{ltr(fmt(net))} ج.م")
            self._rem_lbl.setText(f"يُضاف بالكامل لرصيد المورد: {ltr(fmt(net))} ج.م")
        self._line_table.sync_columns()

    def _fail(self, text: str) -> bool:
        self.notice.show_message(text)
        return False


    def has_draft(self) -> bool:
        return self.posted is None and bool(
            self._rows
            or self._supplier_id is not None
            or self.sup_search.text().strip()
            or self.inv_no.text().strip()
            or self.notes.text().strip()
            or self.inv_date.text() != self._received_date
        )

    # ---------- Posting ----------
    def post(self) -> None:
        if self._busy:
            return
        if self._supplier_id is None or self.sup_search.pending:
            self._fail("اختر المورد.")
            return
        # Supplier invoice number is optional: when left blank, this invoice's
        # own request key stands in so the uniqueness rule on (supplier,
        # invoice no) still holds without forcing the user to type one.
        inv_no_text = self.inv_no.text().strip() or f"AUTO-{self._request_key[:10].upper()}"
        lines, err = self._validate_lines()
        if err:
            self._fail(err)
            return
        ship_text = self.ship.text().strip()
        try:
            freight_amount = parse_money(ship_text) if ship_text else 0
        except MoneyError as e:
            self._fail(f"الشحن: {e}")
            return
        sub = sum(ln.qty * ln.unit_cost for ln in lines)
        invoice_discount = _resolve_amount_or_percent(self.disc_edit, self.disc_unit_btn, sub)
        if invoice_discount is None:
            self._fail("خصم الفاتورة: قيمة غير صحيحة.")
            return
        try:
            lines = self._vat_lines(lines)
        except PurchaseError as error:
            self._fail(str(error))
            if self._vat_enabled:
                self.extra_toggle.setChecked(True)
            return
        self._busy = True
        self.post_btn.setEnabled(False)
        try:
            self.posted = purchasing.post_invoice(
                self.session.con, self.session.user,
                request_key=self._request_key,
                supplier_id=self._supplier_id,
                supplier_invoice_no=inv_no_text,
                invoice_date=self.inv_date.text().strip(),
                received_date=self._received_date,
                lines=lines,
                notes=self.notes.text(),
                freight_amount=freight_amount,
                freight_basis=self._freight_basis(),
                invoice_discount=invoice_discount,
                **self._vat_kwargs(),
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
