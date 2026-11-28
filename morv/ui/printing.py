"""Turns a saved document into a printed page and sends it to the shop's printer."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from morv.core.money import fmt
from morv.ui import print_doc
from morv.ui.modal import Modal

PAYMENT_LABELS = {"cash": "نقدي", "card": "بطاقة", "transfer": "تحويل / محفظة", "wallet": "محفظة",
                  "credit": "آجل", "installment": "تقسيط", "deposit": "من العربون"}


class PrintError(RuntimeError):
    """No printer on this device, or the page could not be sent to it."""


def shape_for(shop) -> print_doc.Shape:
    return print_doc.SHAPES.get(shop.paper, print_doc.SHAPES["a4"])


def sale_document(sale, shop) -> print_doc.Document:
    info = [("العميل", sale.customer_name)]
    if sale.vehicle_plate:
        info.append(("المركبة", sale.vehicle_plate))
    paid = " + ".join(PAYMENT_LABELS.get(p[0], p[0]) for p in sale.payments)
    info.append(("الدفع", paid or "—"))
    if sale.tax_enabled:
        info.append(("أساس السعر", sale.tax_basis_label))
    lines = []
    for line in sale.lines:
        detail = ""
        if sale.tax_enabled:
            detail = f"{line.tax_label}: {fmt(line.tax_amount)}"
            discount = line.line_discount + line.invoice_share
            if discount:
                detail += f" · خصم: {fmt(discount)}"
        lines.append(print_doc.Line(line.item_name, detail, str(line.qty), line.unit_price, line.net_total))
    totals = [("الإجمالي قبل الخصم" if sale.tax_enabled else "الإجمالي", fmt(sale.subtotal))]
    discount = sale.line_discount + sale.invoice_discount
    if discount:
        totals.append(("الخصم", fmt(discount)))
    if sale.tax_enabled:
        totals.extend([
            ("القيمة قبل الضريبة", fmt(sale.value_before_tax)),
            ("ضريبة القيمة المضافة", fmt(sale.tax_total)),
        ])
    if sale.returned:
        if sale.tax_enabled:
            totals.append(("إجمالي الفاتورة", fmt(sale.total)))
        totals.append(("المرتجع", fmt(sale.returned)))
    caption = ("الصافي بعد المرتجع" if sale.returned else "الإجمالي المستحق") if sale.tax_enabled else "الصافي"
    totals.append((caption, f"{fmt(sale.total - sale.returned)} ج.م"))
    return print_doc.Document(title="فاتورة بيع", number=sale.number, date=sale.sale_date,
                              info=info, lines=lines, totals=totals)


def printer_for(shop) -> QPrinter:
    chosen = QPrinterInfo.printerInfo(shop.printer) if shop.printer else QPrinterInfo.defaultPrinter()
    if chosen.isNull():
        raise PrintError("لا توجد طابعة مثبتة على هذا الجهاز؛ اخترها من الإعدادات ← المحل")
    return QPrinter(chosen, QPrinter.PrinterMode.HighResolution)


def send(shop, doc: print_doc.Document) -> str:
    printer = printer_for(shop)
    try:
        print_doc.to_printer(shop, doc, printer, shape_for(shop))
    except Exception as e:      # noqa: BLE001 - a printer failure must reach the user, not the log alone
        raise PrintError(f"تعذّرت الطباعة: {e}") from e
    return printer.printerName()


class PrintPreview(Modal):
    """Shows the page as it will print, so a reprint is never a guess."""

    def __init__(self, parent, shop, doc: print_doc.Document):
        super().__init__(f"معاينة الطباعة · {doc.title} {doc.number}", parent, width=620, height=720)
        self.shop = shop
        self.doc = doc
        self.printed = False
        page = QLabel()
        page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page.setPixmap(QPixmap.fromImage(print_doc.preview_image(shop, doc, shape_for(shop), 560)))
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(page)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(holder)
        self.body.addWidget(area, 1)
        row = QHBoxLayout()
        close = QPushButton("إغلاق")
        close.clicked.connect(self.reject)
        self.print_btn = QPushButton("طباعة")
        self.print_btn.setObjectName("primary")
        self.print_btn.clicked.connect(self._print)
        row.addWidget(self.print_btn)
        row.addWidget(close)
        row.addStretch(1)
        self.body.addLayout(row)

    def _print(self) -> None:
        try:
            send(self.shop, self.doc)
        except PrintError as e:
            self.print_btn.setText(str(e))
            return
        self.printed = True
        self.accept()
