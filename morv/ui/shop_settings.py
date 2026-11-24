"""Independent shop identity and print forms using the shared adaptive page components."""
from dataclasses import replace

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit,
                               QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget)

from morv.infra import shop, users
from morv.ui import print_doc, tokens
from morv.ui.adaptive_layout import AdaptiveCards, AdaptiveFields, FormBody
from morv.ui.widgets import action_row, field, icon_button, ltr_edit, muted, section_card

PAPER_TABS = (("a4", "A4"), ("a5", "A5"), ("r80", "رول 80مم"), ("r58", "رول 58مم"))


class ShopTab(FormBody):
    changed = Signal()

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.editable = session.can("settings.edit")
        data = shop.load(session.con)
        self.name = QLineEdit(data.name)
        self.activity = QLineEdit(data.activity)
        self.activity.setPlaceholderText("إطارات · بطاريات · خدمات سريعة")
        self.address = QLineEdit(data.address)
        self.hours = QLineEdit(data.hours)
        self.hours.setPlaceholderText("السبت - الخميس · 9 ص إلى 10 م")
        self.phone = ltr_edit("01xxxxxxxxx")
        self.phone.setText(data.phone)
        self.phone2 = ltr_edit("اختياري")
        self.phone2.setText(data.phone2)
        self.whatsapp = ltr_edit("اختياري")
        self.whatsapp.setText(data.whatsapp)
        self.website = QLineEdit(data.website)
        self.website.setPlaceholderText("صفحة فيسبوك أو موقع")
        self.tax_id = ltr_edit("رقم التسجيل الضريبي")
        self.tax_id.setText(data.tax_id)
        self.register_no = ltr_edit("رقم السجل التجاري")
        self.register_no.setText(data.commercial_register)
        self.lines = (self.name, self.activity, self.address, self.hours, self.phone,
                      self.phone2, self.whatsapp, self.website, self.tax_id, self.register_no)
        basic, inner, _ = section_card("المعلومات الأساسية", "home")
        inner.addWidget(AdaptiveFields([field("اسم المحل *", self.name), field("النشاط", self.activity),
                                        field("العنوان", self.address), field("مواعيد العمل", self.hours)]))
        contact, inner, _ = section_card("بيانات الاتصال", "phone")
        inner.addWidget(AdaptiveFields([field("هاتف 1 *", self.phone), field("هاتف 2", self.phone2),
                                        field("واتساب المحل", self.whatsapp), field("فيسبوك أو موقع", self.website)]))
        legal, inner, _ = section_card("البيانات القانونية", "shield")
        self.tax_registered = QCheckBox("المحل مسجل ضريبيًا")
        self.tax_registered.setChecked(data.tax_registered)
        self.tax_registered.toggled.connect(self._tax_toggled)
        inner.addWidget(self.tax_registered)
        inner.addWidget(AdaptiveFields([field("الرقم الضريبي", self.tax_id),
                                        field("السجل التجاري", self.register_no)]))
        inner.addStretch(1)
        logo, inner, _ = section_card("شعار المحل", "home")
        self.logo = QLabel()
        size = tokens.LAYOUT["shop_logo_preview"]
        self.logo.setFixedSize(size, size)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pick = icon_button("اختيار شعار", "plus")
        self.drop = icon_button("إزالة", "trash")
        self.pick.clicked.connect(self.pick_logo)
        self.drop.clicked.connect(self.remove_logo)
        inner.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignLeading)
        inner.addLayout(action_row(self.pick, self.drop))
        inner.addStretch(1)
        self.area = AdaptiveCards((basic, contact, legal, logo), minimum_width=tokens.LAYOUT["settings_card_min"])
        self.body.addWidget(self.area)
        for widget in (*self.lines, self.tax_registered, self.pick, self.drop, self.save_btn):
            widget.setEnabled(self.editable)
        self._tax_toggled()
        self._show_logo()

    def collect(self):
        return shop.Shop(name=self.name.text(), activity=self.activity.text(), phone=self.phone.text(),
                         phone2=self.phone2.text(), whatsapp=self.whatsapp.text(), address=self.address.text(),
                         hours=self.hours.text(), website=self.website.text(),
                         tax_registered=self.tax_registered.isChecked(), tax_id=self.tax_id.text(),
                         commercial_register=self.register_no.text())

    def _tax_toggled(self):
        for widget in (self.tax_id, self.register_no):
            widget.setEnabled(self.tax_registered.isChecked() and self.editable)

    def _show_logo(self):
        path = shop.Shop().logo_path
        pixmap = QPixmap(str(path)) if path else QPixmap()
        if pixmap.isNull():
            self.logo.setPixmap(QPixmap())
            self.logo.setText("—")
        else:
            self.logo.setPixmap(pixmap.scaled(self.logo.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))

    def _require(self):
        users.require(self.session.con, self.session.user, "settings.edit", shop.ShopError)

    def pick_logo(self):
        try:
            self._require()
            path, _ = QFileDialog.getOpenFileName(self, "صورة الشعار", "", "صور (*.png *.jpg *.jpeg)")
            if not path:
                return
            self._require()
            if QPixmap(path).isNull():
                raise shop.ShopError("تعذر قراءة صورة الشعار")
            shop.save_logo(path)
        except (shop.ShopError, OSError) as error:
            self.notice.show_message(str(error))
            return
        self._show_logo()
        self.changed.emit()
        self.notice.show_message("تم تحديث الشعار", "success")

    def remove_logo(self):
        try:
            self._require()
            shop.remove_logo()
        except (shop.ShopError, OSError) as error:
            self.notice.show_message(str(error))
            return
        self._show_logo()
        self.changed.emit()
        self.notice.show_message("تمت إزالة الشعار", "success")

    def save(self):
        try:
            saved = shop.save_identity(self.session.con, self.session.user, self.collect())
        except shop.ShopError as error:
            self.notice.show_message(str(error))
            return
        for widget, value in zip(self.lines, (saved.name, saved.activity, saved.address, saved.hours,
                                              saved.phone, saved.phone2, saved.whatsapp, saved.website,
                                              saved.tax_id, saved.commercial_register), strict=True):
            widget.setText(value)
        self.notice.show_message("تم الحفظ", "success")
        self.changed.emit()


class PrintingTab(FormBody):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.editable = session.can("settings.edit")
        self.page = None
        data = shop.load(session.con)
        options = QWidget()
        layout = QVBoxLayout(options)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.LAYOUT["pane_gap"])
        settings_card, inner, _ = section_card("إعدادات الطباعة", "printer")
        from PySide6.QtPrintSupport import QPrinterInfo
        self.printer = QComboBox()
        self.printer.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.printer.setMinimumContentsLength(12)
        self.printer.addItem("الطابعة الافتراضية", "")
        for name in QPrinterInfo.availablePrinterNames():
            self.printer.addItem(name, name)
        if data.printer and self.printer.findData(data.printer) < 0:
            self.printer.addItem(f"{data.printer} — غير متاحة", data.printer)
        self.printer.setCurrentIndex(max(0, self.printer.findData(data.printer)))
        self.sizes = QComboBox()
        for key, label in PAPER_TABS:
            self.sizes.addItem(label, key)
        self.sizes.setCurrentIndex(max(0, self.sizes.findData(data.paper)))
        inner.addWidget(AdaptiveFields([field("الطابعة", self.printer), field("مقاس الورق", self.sizes)]))
        inner.addWidget(muted("يظهر في المستند:"))
        self.switches = {}
        for key, label in (("show_logo", "الشعار"), ("show_whatsapp", "واتساب"),
                           ("show_hours", "المواعيد"), ("show_policy", "السياسة")):
            check = QCheckBox(label)
            check.setChecked(getattr(data, key))
            self.switches[key] = check
        inner.addWidget(AdaptiveFields(self.switches.values()))
        layout.addWidget(settings_card)
        words, inner, _ = section_card("نصوص المستندات", "edit")
        self.thanks = QLineEdit(data.thanks)
        self.thanks.setPlaceholderText("شكرًا لتعاملكم معنا")
        self.policy = QPlainTextEdit(data.policy)
        self.policy.setPlaceholderText("الاستبدال خلال 14 يومًا بالفاتورة · الضمان لا يغطي سوء الاستخدام")
        self.policy.setMinimumHeight(tokens.LAYOUT["print_policy_min_height"])
        self.policy.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        inner.addWidget(field("عبارة الشكر", self.thanks))
        inner.addWidget(field("سياسة الاستبدال والضمان", self.policy))
        layout.addWidget(words)
        layout.addStretch(1)
        preview_card, inner, _ = section_card("معاينة المستند", "eye")
        self.preview = QLabel()
        self.preview.setMinimumSize(tokens.LAYOUT["print_preview_min_width"],
                                    tokens.LAYOUT["print_preview_min_height"])
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName("printPreview")
        inner.addWidget(self.preview, 1)
        self.trial_btn = icon_button("طباعة صفحة تجريبية", "printer")
        self.trial_btn.clicked.connect(self.trial_print)
        inner.addLayout(action_row(self.trial_btn))
        self.area = AdaptiveCards((options, preview_card), minimum_width=tokens.LAYOUT["settings_card_min"],
                                  column_weights=(3, 2), fill_height=True)
        self.body.addWidget(self.area)
        self.redraw = QTimer(self)
        self.redraw.setSingleShot(True)
        self.redraw.setInterval(250)
        self.redraw.timeout.connect(self._draw_preview)
        for signal in (self.thanks.textChanged, self.policy.textChanged, self.sizes.currentIndexChanged,
                       *(check.toggled for check in self.switches.values())):
            signal.connect(lambda *_args: self.redraw.start())
        for widget in (self.thanks, self.policy, self.sizes, self.printer, *self.switches.values(),
                       self.save_btn, self.trial_btn):
            widget.setEnabled(self.editable)
        self.preview.installEventFilter(self)

    def collect(self):
        return replace(shop.load(self.session.con), thanks=self.thanks.text(), policy=self.policy.toPlainText(),
                       paper=self.shape().key, printer=self.printer.currentData(),
                       **{key: check.isChecked() for key, check in self.switches.items()})

    def shape(self):
        return print_doc.SHAPES[self.sizes.currentData() or "a4"]

    def eventFilter(self, watched, event):
        if watched is self.preview and event.type() == QEvent.Type.Resize:
            self._fit()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._draw_preview()

    def _fit(self):
        box = self.preview.size() - QSize(18, 18)
        if self.page is None or box.width() < 40 or box.height() < 40:
            return
        self.preview.setPixmap(QPixmap.fromImage(self.page).scaled(
            box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _draw_preview(self):
        self.page = print_doc.preview_image(self.collect(), print_doc.sample_document(), self.shape())
        self._fit()

    def refresh_preview(self):
        if self.isVisible():
            self._draw_preview()

    def trial_print(self):
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
        try:
            users.require(self.session.con, self.session.user, "settings.edit", shop.ShopError)
        except shop.ShopError as error:
            self.notice.show_message(str(error))
            return
        name = self.printer.currentData()
        if name and name not in QPrinterInfo.availablePrinterNames():
            self.notice.show_message("الطابعة المحددة غير متاحة")
            return
        chosen = QPrinterInfo.printerInfo(name) if name else QPrinterInfo.defaultPrinter()
        if chosen.isNull():
            self.notice.show_message("لا توجد طابعة مثبتة على هذا الجهاز")
            return
        printer = QPrinter(chosen, QPrinter.PrinterMode.HighResolution)
        print_doc.to_printer(self.collect(), print_doc.sample_document(), printer, self.shape())
        self.notice.show_message(f"أُرسلت صفحة تجريبية إلى {chosen.printerName()}", "success")

    def save(self):
        try:
            saved = shop.save_print(self.session.con, self.session.user, self.collect())
        except shop.ShopError as error:
            self.notice.show_message(str(error))
            return
        self.thanks.setText(saved.thanks)
        self.policy.setPlainText(saved.policy)
        self.notice.show_message("تم الحفظ", "success")
        self._draw_preview()
