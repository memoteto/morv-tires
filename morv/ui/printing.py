"""Turns a saved document into a printed page and sends it to the shop's printer."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea

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
    for label, value in (
        ("الموبايل", sale.customer_mobile),
        ("عنوان العميل", sale.customer_address),
        ("رقم العميل الضريبي", sale.customer_tax_id),
        ("المركبة", sale.vehicle_plate),
        ("قراءة العداد", str(sale.odometer) if sale.odometer is not None else ""),
        ("الكاشير", sale.cashier_name),
        ("وقت التسجيل", sale.recorded_at),
    ):
        if value:
            info.append((label, value))
    paid = " + ".join(PAYMENT_LABELS.get(p[0], p[0]) for p in sale.payments)
    info.append(("الدفع", paid or "—"))
    if sale.tax_enabled:
        info.append(("أساس السعر", sale.tax_basis_label))
    for method, amount, received, reference in sale.payments:
        label = PAYMENT_LABELS.get(method, method)
        info.append((label, fmt(amount)))
        if method == "cash" and received is not None and received != amount:
            info.extend([("المستلم نقدًا", fmt(received)), ("الباقي للعميل", fmt(received - amount))])
        if reference:
            info.append((f"مرجع {label}", reference))
    lines = []
    for line in sale.lines:
        detail = line.tax_label if sale.tax_enabled else ""
        lines.append(print_doc.Line(
            line.item_name, detail, str(line.qty), line.unit_price, line.net_total,
            discount=line.line_discount + line.invoice_share if sale.tax_enabled else None,
            tax_amount=line.tax_amount if sale.tax_enabled else None))
    totals = [("الإجمالي قبل الخصم" if sale.tax_enabled else "الإجمالي", fmt(sale.subtotal))]
    discount = sale.line_discount + sale.invoice_discount
    if discount or sale.tax_enabled:
        totals.append(("الخصم", fmt(discount)))
    if sale.tax_enabled:
        totals.extend([
            ("الإجمالي قبل الضريبة", fmt(sale.value_before_tax)),
            ("ضريبة القيمة المضافة", fmt(sale.tax_total)),
        ])
    if sale.returned:
        if sale.tax_enabled:
            totals.append(("الإجمالي شامل القيمة المضافة", fmt(sale.total)))
        totals.append(("المرتجع", fmt(sale.returned)))
    caption = ("الصافي بعد المرتجع" if sale.returned else
               "الإجمالي شامل القيمة المضافة") if sale.tax_enabled else "الصافي"
    totals.append((caption, f"{fmt(sale.total - sale.returned)} ج.م"))
    return print_doc.Document(title="فاتورة بيع", number=sale.number, date=sale.sale_date,
                              info=info, lines=lines, totals=totals,
                              remarks=sale.notes)


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
        super().__init__(f"معاينة الطباعة · {doc.title} {doc.number}", parent,
                         width=1100, height=1200, table_mode=True)
        self.shop = shop
        self.doc = doc
        self.printed = False
        self.shape = shape_for(shop)
        self.page_index = 0
        self.page_count = print_doc.preview_page_count(shop, doc, self.shape)
        self._source = QPixmap()
        self._fit_key = None
        self.page = QLabel()
        self.page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_area = QScrollArea()
        self.preview_area.setWidgetResizable(False)
        self.preview_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_area.setWidget(self.page)
        self.preview_area.viewport().installEventFilter(self)
        self.body.addWidget(self.preview_area, 1)
        row = QHBoxLayout()
        close = QPushButton("إغلاق")
        close.clicked.connect(self.reject)
        self.print_btn = QPushButton("طباعة")
        self.print_btn.setObjectName("primary")
        self.print_btn.clicked.connect(self._print)
        self.zoom_btn = QPushButton("تكبير")
        self.zoom_btn.setCheckable(True)
        self.zoom_btn.toggled.connect(self._fit_page)
        self.previous_btn = QPushButton("السابقة")
        self.next_btn = QPushButton("التالية")
        self.page_number = QLabel()
        self.previous_btn.clicked.connect(lambda: self._show_page(self.page_index - 1))
        self.next_btn.clicked.connect(lambda: self._show_page(self.page_index + 1))
        for control in (self.previous_btn, self.page_number, self.next_btn):
            control.setVisible(self.page_count > 1)
        row.addWidget(self.print_btn)
        row.addWidget(close)
        row.addStretch(1)
        row.addWidget(self.zoom_btn)
        row.addWidget(self.previous_btn)
        row.addWidget(self.page_number)
        row.addWidget(self.next_btn)
        self.body.addLayout(row)
        self._fit_timer = QTimer(self, singleShot=True, interval=0)
        self._fit_timer.timeout.connect(self._fit_page)
        self._show_page(0)

    def _show_page(self, index):
        if not 0 <= index < self.page_count:
            return
        self.page_index = index
        self._source = QPixmap.fromImage(print_doc.preview_image(
            self.shop, self.doc, self.shape, page_index=index))
        self.page_number.setText(f"{index + 1} / {self.page_count}")
        self.previous_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index + 1 < self.page_count)
        self._fit_page()
        self.preview_area.verticalScrollBar().setValue(0)

    def _fit_page(self, *_args):
        if self._source.isNull():
            return
        size = self.preview_area.viewport().size()
        width, height = max(1, size.width() - 2), max(1, size.height() - 2)
        fit_width = min(width, max(1, height * self._source.width() // self._source.height()))
        zoomed = self.zoom_btn.isChecked()
        if zoomed:
            target = min(width, 560) if self.shape.roll else width
        elif self.shape.roll:
            # Fit short receipts; keep long ones legible rather than shrink to a strip.
            target = min(width, max(fit_width, min(self._source.width(), 320)))
        else:
            target = fit_width
        self.zoom_btn.setText("ملاءمة الصفحة" if zoomed else "تكبير")
        key = (self._source.cacheKey(), target)
        if key != self._fit_key:
            pix = self._source.scaledToWidth(target, Qt.TransformationMode.SmoothTransformation)
            self.page.setPixmap(pix)
            self.page.setFixedSize(pix.size())
            self._fit_key = key

    def eventFilter(self, watched, event):
        if (hasattr(self, "_fit_timer") and watched is self.preview_area.viewport()
                and event.type() == QEvent.Type.Resize):
            self._fit_timer.start()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_timer.start()

    def _print(self) -> None:
        try:
            send(self.shop, self.doc)
        except PrintError as e:
            self.print_btn.setText(str(e))
            return
        self.printed = True
        self.accept()
