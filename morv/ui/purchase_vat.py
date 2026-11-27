"""VAT controls shared by full/quick supplier forms and purchase invoice exceptions."""
from PySide6.QtWidgets import QCheckBox, QComboBox, QPushButton, QVBoxLayout, QWidget

from morv.core.money import MoneyError, fmt, parse_money
from morv.infra.suppliers import VAT_STATUSES
from morv.ui.modal import Modal
from morv.ui.widgets import field, ltr_edit, muted


VAT_MODES = {"unknown": "غير محدد", "none": "بدون ضريبة",
             "exclusive": "غير شاملة الضريبة", "inclusive": "شاملة الضريبة"}


def _combo(options, selected):
    combo = QComboBox()
    for key, label in options.items():
        combo.addItem(label, key)
    combo.setCurrentIndex(combo.findData(selected))
    return combo


class SupplierVatFields(QWidget):
    """One shared editor for supplier VAT defaults; no inferred registration."""

    def __init__(self, supplier=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = _combo(VAT_STATUSES, supplier.vat_status if supplier else "unknown")
        self.prices = _combo({False: "غير شاملة الضريبة", True: "شاملة الضريبة"},
                             supplier.vat_prices_inclusive if supplier else False)
        layout.addWidget(field("التسجيل في ضريبة القيمة المضافة", self.status))
        self.prices_field = field("أسعار الشراء المعتادة", self.prices)
        layout.addWidget(self.prices_field)
        self.status.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_args):
        self.prices_field.setVisible(self.status.currentData() == "registered")

    def values(self):
        return self.status.currentData(), bool(self.prices.currentData())


class PurchaseVatDialog(Modal):
    """Exceptions and explicit input deduction confirmation, outside the main summary."""

    def __init__(self, mode, freight_taxable, rows, default_rate, parent=None):
        super().__init__("تفاصيل الضريبة", parent, width=560)
        self.result_values = None
        self.mode = _combo(VAT_MODES, mode)
        self.body.addWidget(field("أسعار فاتورة المورد", self.mode))
        self.freight = QCheckBox("الشحن خاضع للضريبة حسب الفاتورة")
        self.freight.setChecked(freight_taxable)
        self.body.addWidget(self.freight)
        self.line_box = QWidget()
        box = QVBoxLayout(self.line_box)
        box.setContentsMargins(0, 0, 0, 0)
        note = muted("حدد قابلية الخصم من المستند الفعلي. لا يكفي تسجيل المورد وحده.")
        note.setWordWrap(True)
        box.addWidget(note)
        options = {None: "غير محددة", True: "قابلة للخصم", False: "غير قابلة للخصم — ضمن التكلفة"}
        self.bulk = _combo(options, None)
        box.addWidget(field("قابلية الخصم لجميع الأسطر", self.bulk))
        apply_all = QPushButton("تطبيق على الكل")
        apply_all.clicked.connect(self._apply_all)
        box.addWidget(apply_all)
        self.editors = []
        for index, row in enumerate(rows, 1):
            rate, deductible = row.get("vat_override") or (default_rate, None)
            rate_edit = ltr_edit("14.00")
            rate_edit.setText(fmt(rate) if rate is not None else "")
            deduction = _combo(options, deductible)
            label = muted(f"{index}. {row['name']}")
            label.setWordWrap(True)
            box.addWidget(label)
            box.addWidget(field("نسبة الضريبة (%)", rate_edit))
            box.addWidget(field("قابلية الخصم", deduction))
            self.editors.append((rate_edit, deduction))
        self.body.addWidget(self.line_box)
        self.mode.currentIndexChanged.connect(self._refresh)
        self._refresh()
        self.add_footer("اعتماد التفاصيل", self.save)

    def _refresh(self, *_args):
        taxable = self.mode.currentData() in ("exclusive", "inclusive")
        self.line_box.setVisible(taxable)
        self.freight.setEnabled(taxable)

    def _apply_all(self):
        for _, deduction in self.editors:
            deduction.setCurrentIndex(deduction.findData(self.bulk.currentData()))

    def save(self):
        mode = self.mode.currentData()
        if mode == "unknown":
            self.notice.show_message("حدد المعاملة الفعلية لفاتورة المورد.")
            return
        values = []
        if mode != "none":
            for index, (rate_edit, deduction) in enumerate(self.editors, 1):
                try:
                    rate = parse_money(rate_edit.text())
                    if rate < 0:
                        raise MoneyError("النسبة سالبة")
                except MoneyError as error:
                    self.notice.show_message(f"السطر {index}: {error}")
                    return
                eligible = deduction.currentData() if rate else False
                if eligible is None:
                    self.notice.show_message(f"السطر {index}: حدد قابلية خصم الضريبة.")
                    return
                values.append((rate, bool(eligible)))
        self.result_values = (mode, mode != "none" and self.freight.isChecked(), values)
        self.accept()
