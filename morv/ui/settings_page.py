"""Settings tabs: shop, users, audit log, shortcuts, backup."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableView, QTabBar, QTabWidget,
                               QVBoxLayout, QWidget)

from morv.core import security
from morv.core.text import ltr
from morv.infra import backup, settings, shop, users, vault
from morv.ui import print_doc
from morv.ui.modal import Modal
from morv.ui.models import AuditModel, BackupsModel
from morv.ui.widgets import (Notice, PasswordEdit, ask, card, danger_button, form, info, ltr_edit, muted,
                             primary_button)

SHORTCUTS = [("F2", "التركيز على مربع البحث في شاشات القوائم"), ("Ctrl+N", "إضافة جديد في شاشة القوائم المفتوحة"),
             ("Ctrl+B", "طي الشريط الجانبي أو فتحه"), ("Ctrl+D", "التبديل بين الوضع الفاتح والداكن"),
             ("Ctrl+L", "تسجيل الخروج"), ("Ctrl+Q", "إغلاق البرنامج")]


def _table(model) -> QTableView:
    v = QTableView()
    v.setModel(model)
    v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    v.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    v.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    v.setAlternatingRowColors(True)
    v.verticalHeader().setVisible(False)
    v.horizontalHeader().setStretchLastSection(True)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return v


class SettingsPage(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        self.tabs = QTabWidget()
        self.shop_tab = None
        if session.can("settings.edit"):      # A cashier never sees shop data, by the owner's decision
            self.shop_tab = ShopTab(session)
            self.tabs.addTab(self.shop_tab, "المحل")
        if session.can("users.manage"):
            self.users_tab = UsersTab(session)
            self.tabs.addTab(self.users_tab, "المستخدمون والصلاحيات")
            self.audit_tab = AuditTab(session)
            self.tabs.addTab(self.audit_tab, "سجل العمليات")
        self.fields_tab = None
        if session.can("settings.edit"):
            from morv.ui.inventory_page import ItemFieldsTab   # local import to avoid a cycle
            self.fields_tab = ItemFieldsTab(session)
            self.tabs.addTab(self.fields_tab, "حقول بطاقة الصنف")
        self.partners_tab = None
        if session.can("treasury.manage"):
            from morv.ui.partners_tab import PartnersTab
            self.partners_tab = PartnersTab(session)
            self.tabs.addTab(self.partners_tab, "الشركاء")
        if session.can("opening.post"):
            from morv.ui.opening_dialogs import OpeningTab
            self.opening_tab = OpeningTab(session)
            self.tabs.addTab(self.opening_tab, "الأرصدة الافتتاحية")
        self.appearance_tab = AppearanceTab(session)
        self.tabs.addTab(self.appearance_tab, "المظهر")
        self.tabs.addTab(ShortcutsTab(), "الاختصارات")
        self.backup_tab = BackupTab(session)
        self.tabs.addTab(self.backup_tab, "النسخ الاحتياطي")
        lay.addWidget(self.tabs)


class AppearanceTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        box, inner = card("الخط")
        inner.addWidget(muted("الخطان IBM Plex Sans Arabic وCairo مضمّنان مع البرنامج ولا يعتمدان على Windows."))
        f = form()
        self.family = QComboBox()
        from morv.ui import theme
        for name in theme.available_fonts():
            self.family.addItem(name)
        self.family.setCurrentText(settings.get(session.con, "ui.font_family", theme.DEFAULT_FONT))
        self.size = QComboBox()
        for s in theme.FONT_SIZES:
            self.size.addItem(ltr(str(s)), s)
        self.size.setCurrentIndex(list(theme.FONT_SIZES).index(
            int(settings.get(session.con, "ui.font_size", str(theme.DEFAULT_SIZE)))))
        f.addRow("الخط", self.family)
        f.addRow("حجم الخط", self.size)
        inner.addLayout(f)
        self.sample = QLabel("نموذج: إطار ميشلان Primacy 4 مقاس " + ltr("205/55R16") + " بسعر " + ltr("6,450.00"))
        self.sample.setWordWrap(True)
        inner.addWidget(self.sample)
        apply_btn = primary_button("تطبيق", "font")
        apply_btn.clicked.connect(self.apply)
        inner.addWidget(apply_btn, 0, Qt.AlignmentFlag.AlignLeading)
        lay.addWidget(box)
        lay.addWidget(muted("الوضع الفاتح/الداكن من زر الشمس/القمر في الشريط العلوي (Ctrl+D)."))
        lay.addStretch(1)

    def apply(self) -> None:
        win = self.window()
        if hasattr(win, "set_font"):
            win.set_font(self.family.currentText(), self.size.currentData())


PAPER_TABS = (("a4", "A4"), ("a5", "A5"), ("r80", "رول 80مم"), ("r58", "رول 58مم"))


class ShopTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.editable = session.can("settings.edit")
        self.page = None
        self.notice = Notice()
        data = shop.load(session.con)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.addWidget(self._identity_card(data))
        left.addWidget(self._words_card(data))
        left.addWidget(self.notice)
        save = primary_button("حفظ")
        save.clicked.connect(self.save)
        save.setEnabled(self.editable)
        left.addWidget(save, 0, Qt.AlignmentFlag.AlignLeading)
        left.addStretch(1)
        row.addLayout(left, 1)
        row.addWidget(self._print_card(data), 0)
        self.redraw = QTimer(self)
        self.redraw.setSingleShot(True)
        self.redraw.setInterval(250)
        self.redraw.timeout.connect(self._draw_preview)
        self.lines = (self.name, self.activity, self.phone, self.phone2, self.whatsapp, self.address,
                      self.hours, self.website, self.tax_id, self.register_no, self.thanks)
        for w in self.lines:
            w.textChanged.connect(self.redraw.start)
        self.policy.textChanged.connect(self.redraw.start)
        for cb in self.switches.values():
            cb.toggled.connect(self.redraw.start)
        self.paper.currentIndexChanged.connect(self._paper_chosen)
        if not self.editable:
            for w in (*self.lines, self.policy, self.tax_registered, self.pick, self.drop,
                      self.printer, self.paper, *self.switches.values()):
                w.setEnabled(False)
        self._show_logo()
        self._tax_toggled()
        self._draw_preview()

    def _identity_card(self, data) -> QFrame:
        box, inner = card("بيانات المحل")
        self.name = QLineEdit(data.name)
        self.activity = QLineEdit(data.activity)
        self.activity.setPlaceholderText("إطارات · بطاريات · خدمات سريعة")
        self.phone = ltr_edit("01xxxxxxxxx")
        self.phone.setText(data.phone)
        self.phone2 = ltr_edit("اختياري")
        self.phone2.setText(data.phone2)
        self.whatsapp = ltr_edit("اختياري")
        self.whatsapp.setText(data.whatsapp)
        self.address = QLineEdit(data.address)
        self.hours = QLineEdit(data.hours)
        self.hours.setPlaceholderText("السبت - الخميس · 9 ص إلى 10 م")
        self.website = QLineEdit(data.website)
        self.website.setPlaceholderText("صفحة فيسبوك أو موقع")
        self.tax_id = ltr_edit("رقم التسجيل الضريبي")
        self.tax_id.setText(data.tax_id)
        self.register_no = ltr_edit("رقم السجل التجاري")
        self.register_no.setText(data.commercial_register)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        pairs = (("اسم المحل *", self.name), ("النشاط", self.activity), ("هاتف 1 *", self.phone),
                 ("هاتف 2", self.phone2), ("واتساب المحل", self.whatsapp), ("العنوان", self.address),
                 ("مواعيد العمل", self.hours), ("فيسبوك أو موقع", self.website),
                 ("الرقم الضريبي", self.tax_id), ("السجل التجاري", self.register_no))
        for i, (label, widget) in enumerate(pairs):
            row, col = divmod(i, 2)
            grid.addWidget(QLabel(label), row, col * 2)
            grid.addWidget(widget, row, col * 2 + 1)
        self.tax_registered = QCheckBox("المحل مسجل ضريبيًا — بدونه لا يُطبع رقم ضريبي ولا سجل تجاري")
        self.tax_registered.setChecked(data.tax_registered)
        grid.addWidget(self.tax_registered, len(pairs) // 2, 0, 1, 4)
        self.logo = QLabel()
        self.logo.setFixedSize(52, 52)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pick = QPushButton("اختيار شعار")
        self.pick.clicked.connect(self.pick_logo)
        self.drop = QPushButton("إزالة")
        self.drop.clicked.connect(self.remove_logo)
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(0, 0, 0, 0)
        logo_row.addWidget(QLabel("الشعار"))
        logo_row.addWidget(self.logo)
        logo_row.addWidget(self.pick)
        logo_row.addWidget(self.drop)
        logo_row.addStretch(1)
        grid.addLayout(logo_row, len(pairs) // 2 + 1, 0, 1, 4)
        self.tax_registered.toggled.connect(self._tax_toggled)
        inner.addLayout(grid)
        return box

    def _words_card(self, data) -> QFrame:
        box, inner = card("نصوص المستندات")
        f = form()
        self.thanks = QLineEdit(data.thanks)
        self.thanks.setPlaceholderText("شكرًا لتعاملكم معنا")
        self.policy = QPlainTextEdit(data.policy)
        self.policy.setPlaceholderText("الاستبدال خلال 14 يومًا بالفاتورة · الضمان لا يغطي سوء الاستخدام")
        self.policy.setFixedHeight(52)
        f.addRow("عبارة الشكر", self.thanks)
        f.addRow("سياسة الاستبدال والضمان", self.policy)
        inner.addLayout(f)
        return box

    def _print_card(self, data) -> QFrame:
        from PySide6.QtPrintSupport import QPrinterInfo
        box, inner = card("الطباعة")
        f = form()
        self.printer = QComboBox()
        self.printer.addItem("الطابعة الافتراضية", "")
        for name in QPrinterInfo.availablePrinterNames():
            self.printer.addItem(name, name)
        at = self.printer.findData(data.printer)
        self.printer.setCurrentIndex(at if at >= 0 else 0)
        self.paper = QComboBox()
        for key, label in PAPER_TABS:
            self.paper.addItem(label, key)
        at = self.paper.findData(data.paper)
        self.paper.setCurrentIndex(at if at >= 0 else 0)
        f.addRow("الطابعة", self.printer)
        f.addRow("مقاس الورق", self.paper)
        inner.addLayout(f)
        self.switches = {}
        switch_grid = QGridLayout()
        switch_grid.setHorizontalSpacing(10)
        for i, (key, label) in enumerate((("show_logo", "الشعار"), ("show_whatsapp", "واتساب"),
                                          ("show_hours", "المواعيد"), ("show_policy", "السياسة"))):
            cb = QCheckBox(label)
            cb.setChecked(getattr(data, key))
            self.switches[key] = cb
            switch_grid.addWidget(cb, *divmod(i, 2))
        inner.addWidget(muted("يظهر في المستند:"))
        inner.addLayout(switch_grid)
        self.sizes = QTabBar()
        for key, label in PAPER_TABS:
            self.sizes.addTab(label)
            self.sizes.setTabData(self.sizes.count() - 1, key)
        keys = [key for key, _label in PAPER_TABS]
        self.sizes.setCurrentIndex(keys.index(data.paper) if data.paper in keys else 0)
        self.sizes.currentChanged.connect(self._draw_preview)
        inner.addWidget(self.sizes)
        self.preview = QLabel()
        self.preview.setMinimumSize(190, 200)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName("printPreview")
        inner.addWidget(self.preview, 1)
        trial = QPushButton("طباعة صفحة تجريبية")
        trial.clicked.connect(self.trial_print)
        trial.setEnabled(self.editable)
        inner.addWidget(trial, 0, Qt.AlignmentFlag.AlignLeading)
        inner.addWidget(muted("التصميم مناسب للطابعات الأبيض والأسود: خلفيات بيضاء وخطوط فاصلة."))
        box.setMinimumWidth(250)
        box.setMaximumWidth(360)
        return box

    def collect(self):
        """Reads the screen without validating, so the preview keeps drawing while a field is half typed."""
        return shop.Shop(
            name=self.name.text(), activity=self.activity.text(), phone=self.phone.text(),
            phone2=self.phone2.text(), whatsapp=self.whatsapp.text(), address=self.address.text(),
            hours=self.hours.text(), website=self.website.text(),
            tax_registered=self.tax_registered.isChecked(), tax_id=self.tax_id.text(),
            commercial_register=self.register_no.text(), thanks=self.thanks.text(),
            policy=self.policy.toPlainText(), paper=self.paper.currentData(),
            printer=self.printer.currentData(),
            **{k: cb.isChecked() for k, cb in self.switches.items()})

    def shape(self):
        key = self.sizes.tabData(self.sizes.currentIndex()) or "a4"
        return print_doc.SHAPES[key]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    def _fit(self) -> None:
        """Scales the drawn page into whatever room the column has, keeping the paper's proportions."""
        box = self.preview.size() - QSize(18, 18)
        if self.page is None or box.width() < 40 or box.height() < 40:
            return
        self.preview.setPixmap(QPixmap.fromImage(self.page).scaled(
            box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _draw_preview(self) -> None:
        self.page = print_doc.preview_image(self.collect(), print_doc.sample_document(), self.shape())
        self._fit()

    def _paper_chosen(self) -> None:
        keys = [key for key, _label in PAPER_TABS]
        self.sizes.setCurrentIndex(keys.index(self.paper.currentData()))

    def _tax_toggled(self) -> None:
        on = self.tax_registered.isChecked()
        for w in (self.tax_id, self.register_no):
            w.setEnabled(on and self.editable)
        self.redraw.start()

    def _show_logo(self) -> None:
        p = shop.Shop().logo_path
        if p:
            self.logo.setPixmap(QPixmap(str(p)).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
        else:
            self.logo.setPixmap(QPixmap())
            self.logo.setText("لا شعار")

    def pick_logo(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "صورة الشعار", "", "صور (*.png *.jpg *.jpeg)")
        if p:
            shop.save_logo(p)
            self._show_logo()
            self._draw_preview()

    def remove_logo(self) -> None:
        shop.remove_logo()
        self._show_logo()
        self._draw_preview()

    def trial_print(self) -> None:
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
        name = self.printer.currentData()
        chosen = QPrinterInfo.printerInfo(name) if name else QPrinterInfo.defaultPrinter()
        if chosen.isNull():
            self.notice.show_message("لا توجد طابعة مثبتة على هذا الجهاز")
            return
        printer = QPrinter(chosen, QPrinter.PrinterMode.HighResolution)
        print_doc.to_printer(self.collect(), print_doc.sample_document(), printer, self.shape())
        self.notice.show_message(f"أُرسلت صفحة تجريبية إلى {chosen.printerName()}", "success")

    def save(self) -> None:
        try:
            shop.save(self.session.con, self.session.user, self.collect())
        except shop.ShopError as e:
            self.notice.show_message(str(e))
            return
        users.audit(self.session.con, self.session.user, "shop.save")
        self.notice.show_message("تم الحفظ", "success")
        self._draw_preview()


class UsersTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.current: users.User | None = None
        row = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setObjectName("userList")
        self.list.setSpacing(2)
        self.list.setUniformItemSizes(True)
        self.list.currentRowChanged.connect(self._select)
        new = primary_button("مستخدم جديد", "plus")
        new.clicked.connect(self.new_user)
        left.addWidget(new)
        left.addWidget(self.list, 1)
        left.addWidget(muted("كل عملية حساسة تُسجَّل باسم المستخدم ووقتها في سجل العمليات."))
        row.addLayout(left, 1)

        self.detail, inner = card()
        self.title = QLabel()
        self.title.setObjectName("cardTitle")
        inner.addWidget(self.title)
        self.sub = muted("")
        inner.addWidget(self.sub)
        f = form()
        self.role = QComboBox()
        for key, label in security.ROLES.items():
            self.role.addItem(label, key)
        self.role.currentIndexChanged.connect(self._role_changed)
        f.addRow("الدور", self.role)
        inner.addLayout(f)
        inner.addWidget(muted("الصلاحيات (المدير يملكها كلها دائمًا):"))
        self.checks: dict[str, QCheckBox] = {}
        perms_box = QWidget()
        grid = QGridLayout(perms_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        for i, (key, label) in enumerate(security.PERMISSIONS.items()):
            cb = QCheckBox(label)
            cb.toggled.connect(lambda on, k=key: self._perm_changed(k, on))
            self.checks[key] = cb
            grid.addWidget(cb, i // 2, i % 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(perms_box)
        cap_row = QHBoxLayout()
        cap_row.addWidget(muted("الحد الأقصى للخصم (٪ من قيمة الفاتورة):"))
        self.discount_cap = QSpinBox()
        self.discount_cap.setRange(0, 100)
        self.discount_cap.valueChanged.connect(lambda _v: self._perm_changed("sales.discount", self.checks["sales.discount"].isChecked()))
        cap_row.addWidget(self.discount_cap)
        cap_row.addStretch(1)
        inner.addLayout(cap_row)
        inner.addWidget(scroll, 1)       # permissions scroll; the buttons below always stay visible
        btns = QHBoxLayout()
        self.pw_btn = QPushButton("إعادة تعيين كلمة المرور")
        self.pw_btn.clicked.connect(self.reset_password)
        self.active_btn = danger_button("إيقاف الحساب")
        self.active_btn.clicked.connect(self.toggle_active)
        btns.addWidget(self.pw_btn)
        btns.addWidget(self.active_btn)
        btns.addStretch(1)
        inner.addLayout(btns)
        self.notice = Notice()
        inner.addWidget(self.notice)
        row.addWidget(self.detail, 2)
        self._loading = False
        self.reload()

    def reload(self, select_id: int | None = None) -> None:
        self.users = users.list_users(self.session.con)
        self.list.blockSignals(True)
        self.list.clear()
        for u in self.users:
            item = QListWidgetItem(f"{u.display_name} — {security.ROLES[u.role]}" + ("" if u.active else " (موقوف)"))
            item.setData(Qt.ItemDataRole.UserRole, u.id)
            item.setSizeHint(QSize(0, 44))
            self.list.addItem(item)
        self.list.blockSignals(False)
        idx = next((i for i, u in enumerate(self.users) if u.id == select_id), 0)
        self.list.setCurrentRow(idx)
        self._select(idx)

    def _select(self, row: int) -> None:
        if row < 0 or row >= len(self.users):
            return
        self.current = u = self.users[row]
        self._loading = True
        self.title.setText(u.display_name)
        self.sub.setText(f"اسم الدخول: {ltr(u.username)}")
        self.role.setCurrentIndex(list(security.ROLES).index(u.role))
        perms = users.permissions(self.session.con, u)
        for key, cb in self.checks.items():
            cb.setChecked(key in perms)
            cb.setEnabled(not u.is_manager)
        self.discount_cap.setValue(users.permission_limit(self.session.con, u, "sales.discount") or 0)
        self.discount_cap.setEnabled(not u.is_manager)
        self.active_btn.setText("إيقاف الحساب" if u.active else "تفعيل الحساب")
        self.active_btn.setEnabled(u.id != self.session.user.id)
        self.notice.show_message("")
        self._loading = False

    def _role_changed(self, idx: int) -> None:
        if self._loading or not self.current:
            return
        role = self.role.itemData(idx)
        if role == self.current.role:
            return
        try:
            users.set_role(self.session.con, self.session.user, self.current.id, role)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            self._loading = True
            self.role.setCurrentIndex(list(security.ROLES).index(self.current.role))
            self._loading = False
            return
        self.reload(self.current.id)

    def _perm_changed(self, key: str, on: bool) -> None:
        if self._loading or not self.current or self.current.is_manager:
            return
        limit = self.discount_cap.value() if key == "sales.discount" else None
        users.set_permission(self.session.con, self.session.user, self.current.id, key, on, limit)

    def new_user(self) -> None:
        dlg = NewUserDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(dlg.created.id)

    def reset_password(self) -> None:
        if not self.current:
            return
        dlg = TempPasswordDialog(self.session, self.current, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.notice.show_message("تم تعيين كلمة مرور مؤقتة، وسيُطلب تغييرها عند أول دخول", "success")

    def toggle_active(self) -> None:
        if not self.current:
            return
        try:
            users.set_active(self.session.con, self.session.user, self.current.id, not self.current.active)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.reload(self.current.id)


class NewUserDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("مستخدم جديد", parent, width=460)
        self.session = session
        self.created = None
        lay = self.body
        f = form()
        self.name = QLineEdit()
        self.username = ltr_edit("karim")
        self.password = PasswordEdit()
        self.role = QComboBox()
        for key, label in security.ROLES.items():
            self.role.addItem(label, key)
        self.role.setCurrentIndex(list(security.ROLES).index("cashier"))
        f.addRow("الاسم *", self.name)
        f.addRow("اسم الدخول *", self.username)
        f.addRow("كلمة مرور مؤقتة *", self.password)
        f.addRow("الدور", self.role)
        lay.addLayout(f)
        lay.addWidget(muted("يُطلب من المستخدم تغيير كلمة المرور عند أول دخول."))
        self.add_footer("حفظ", self.save)

    def save(self) -> None:
        try:
            self.created = users.create_user(self.session.con, self.session.user, self.username.text(),
                                             self.name.text(), self.password.text(), self.role.currentData(),
                                             must_change_pw=True)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class TempPasswordDialog(Modal):
    def __init__(self, session, target, parent=None):
        super().__init__(f"كلمة مرور مؤقتة — {target.display_name}", parent, width=420)
        self.session, self.target = session, target
        f = form()
        self.password = PasswordEdit()
        f.addRow("كلمة المرور المؤقتة", self.password)
        self.body.addLayout(f)
        self.add_footer("حفظ", self.save)

    def save(self) -> None:
        try:
            users.set_password(self.session.con, self.session.user, self.target.id, self.password.text(), must_change=True)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class AuditTab(QWidget):
    def __init__(self, session):
        super().__init__()
        lay = QVBoxLayout(self)
        self.model = AuditModel(session.con)
        self.view = _table(self.model)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        refresh = QPushButton("تحديث")
        refresh.clicked.connect(self.model.reload)
        more = QPushButton("تحميل الأقدم")
        more.clicked.connect(self.model.load_more)
        row.addWidget(refresh)
        row.addWidget(more)
        row.addStretch(1)
        lay.addLayout(row)


class ShortcutsTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        box, inner = card("اختصارات لوحة المفاتيح")
        for key, desc in SHORTCUTS:
            row = QHBoxLayout()
            k = QLabel(ltr(key))
            k.setObjectName("pillOk")
            k.setFixedWidth(90)
            k.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(k)
            row.addWidget(QLabel(desc), 1)
            inner.addLayout(row)
        inner.addWidget(muted("اختصارات البيع والفواتير تُضاف مع شاشاتها في مراحلها."))
        lay.addWidget(box)
        lay.addStretch(1)


class BackupTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        stats = QHBoxLayout()
        self.stat_local = self._stat("آخر نسخة على الجهاز")
        self.stat_usb = self._stat("نسخة الفلاشة")
        self.stat_test = self._stat("آخر اختبار استعادة")
        for s in (self.stat_local, self.stat_usb, self.stat_test):
            stats.addWidget(s[0], 1)
        lay.addLayout(stats)
        self.health = Notice()
        lay.addWidget(self.health)

        actions = QHBoxLayout()
        self.backup_btn = primary_button("نسخ الآن")
        self.backup_btn.clicked.connect(self.backup_now)
        self.test_btn = QPushButton("اختبار استعادة النسخة المختارة")
        self.test_btn.clicked.connect(self.test_selected)
        self.usb_btn = QPushButton("نسخ أحدث نسخة إلى الفلاشة")
        self.usb_btn.clicked.connect(self.copy_usb)
        self.restore_btn = danger_button("استعادة النسخة المختارة")
        self.restore_btn.clicked.connect(self.restore_selected)
        self.restore_btn.setVisible(session.can("backup.restore"))
        for b in (self.backup_btn, self.test_btn, self.usb_btn, self.restore_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        lay.addLayout(actions)
        self.notice = Notice()
        lay.addWidget(self.notice)

        self.model = BackupsModel()
        self.view = _table(self.model)
        lay.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        usb_box, usb_inner = card("مجلد الفلاشة")
        urow = QHBoxLayout()
        self.usb_dir = ltr_edit("مثال: E:\\MorvBackup")
        self.usb_dir.setText(settings.get(session.con, "backup.usb_dir", ""))
        pick = QPushButton("اختيار")
        pick.clicked.connect(self.pick_usb)
        save = QPushButton("حفظ")
        save.clicked.connect(self.save_usb)
        # Only a manager changes the USB folder and backup password; a cashier backs up and tests
        manager = session.user.is_manager
        for w in (self.usb_dir, pick, save):
            w.setEnabled(manager)
        urow.addWidget(self.usb_dir, 1)
        urow.addWidget(pick)
        urow.addWidget(save)
        usb_inner.addLayout(urow)
        usb_inner.addWidget(muted("فلاشتان بالتناوب: واحدة في المحل وأخرى في البيت، وبدّل بينهما أسبوعيًا."))
        bottom.addWidget(usb_box, 1)
        keys_box, keys_inner = card("كلمة مرور النسخ ومفتاح الاسترداد")
        krow = QHBoxLayout()
        pw_btn = QPushButton("تغيير كلمة مرور النسخ")
        pw_btn.clicked.connect(self.change_password)
        pw_btn.setVisible(manager)
        self.rk_btn = QPushButton("مفتاح استرداد جديد")
        self.rk_btn.clicked.connect(self.new_recovery_key)
        self.rk_btn.setVisible(session.user.is_manager)
        self.relink_btn = QPushButton("إعادة ربط هذا الجهاز")
        self.relink_btn.clicked.connect(self.relink)
        self.pw_btn = pw_btn
        for b in (pw_btn, self.rk_btn, self.relink_btn):
            krow.addWidget(b)
        krow.addStretch(1)
        keys_inner.addLayout(krow)
        keys_inner.addWidget(muted("النسخ القديمة تبقى تُفتح بكلمة المرور التي أُخذت بها أو بمفتاح الاسترداد وقتها."))
        bottom.addWidget(keys_box, 1)
        lay.addLayout(bottom)
        self.refresh()

    @staticmethod
    def _stat(title: str) -> tuple:
        box, inner = card()
        inner.addWidget(muted(title))
        value = QLabel("—")
        value.setObjectName("cardTitle")
        sub = muted("")
        inner.addWidget(value)
        inner.addWidget(sub)
        return box, value, sub

    def refresh(self) -> None:
        con = self.session.con
        last_ok = settings.get(con, "backup.last_ok_at", "")
        self.stat_local[1].setText(ltr(last_ok[:16]) if last_ok else "لا نسخة بعد")
        n = len(self.model.items) if self.model.items else 0
        self.stat_local[2].setText(f"{n} نسخة محفوظة · الاحتفاظ: 14 يومية + 12 شهرية")
        usb = backup.usb_dir(con)
        last_usb = settings.get(con, "backup.last_usb_at", "")
        self.stat_usb[1].setText(ltr(last_usb[:16]) if last_usb else ("لم تُنسخ بعد" if usb else "غير محددة"))
        self.stat_usb[2].setText("الفلاشة موصولة" if backup.usb_available(con) else "الفلاشة غير موصولة" if usb else "")
        last_test = settings.get(con, "backup.last_test_at", "")
        self.stat_test[1].setText(ltr(last_test[:16]) if last_test else "لم يُجرَ بعد")
        self.stat_test[2].setText("يُجرى تلقائيًا كل 7 أيام")
        level, text = backup.health(con)
        self.health.show_message(text, {"ok": "success", "warn": "warn", "danger": "error"}[level])
        self.relink_btn.setVisible(backup.device_dek(con) is None)
        self.model.reload()

    def _selected(self) -> backup.BackupInfo | None:
        rows = self.view.selectionModel().selectedRows() if self.view.selectionModel() else []
        idx = rows[0] if rows else self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر نسخة من القائمة أولًا")
            return None
        return self.model.items[idx.row()]

    def backup_now(self) -> None:
        try:
            r = backup.create_backup(self.session.con, "manual", username=self.session.user.username)
            self.notice.show_message(f"تمت النسخة: {r.path.name} · {r.detail}", "success")
        except backup.BackupError as e:
            self.notice.show_message(str(e))
        self.refresh()

    def test_selected(self) -> None:
        b = self._selected()
        if not b:
            return
        r = backup.restore_test(self.session.con, b.path, username=self.session.user.username)
        self.notice.show_message(("نجح الاختبار: " if r.ok else "فشل الاختبار: ") + r.detail, "success" if r.ok else "error")
        self.refresh()

    def copy_usb(self) -> None:
        try:
            r = backup.copy_to_usb(self.session.con, username=self.session.user.username)
            self.notice.show_message(f"نُسخت إلى الفلاشة: {r.path.name}", "success")
        except backup.BackupError as e:
            self.notice.show_message(str(e))
        self.refresh()

    def pick_usb(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "مجلد النسخ على الفلاشة")
        if d:
            self.usb_dir.setText(d)

    def save_usb(self) -> None:
        folder = self.usb_dir.text().strip()
        if folder and backup.same_drive_as_data(folder):
            if not ask(self, "تنبيه", "هذا المجلد على نفس قرص الجهاز، فلو تعطّل الجهاز ضاعت النسخة معه. "
                                       "المقصود مجلد على فلاشة أو قرص خارجي. هل تحفظه على أي حال؟"):
                return
        settings.put(self.session.con, "backup.usb_dir", folder)
        users.audit(self.session.con, self.session.user, "settings.usb", None, None, folder)
        self.notice.show_message("تم حفظ مجلد الفلاشة" + (" (على نفس القرص — مؤقتًا)" if folder and backup.same_drive_as_data(folder) else ""), "success")
        self.refresh()

    def change_password(self) -> None:
        dlg = SecretDialog("تغيير كلمة مرور النسخ", "كلمة المرور الحالية أو مفتاح الاسترداد", new_password=True, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup.change_backup_password(self.session.con, dlg.secret, dlg.new_secret)
        except (vault.VaultError, backup.BackupError) as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.password")
        self.notice.show_message("تم تغيير كلمة مرور النسخ. النسخ الجديدة تُفتح بها، والقديمة بالقديمة أو بمفتاح الاسترداد.", "success")

    def new_recovery_key(self) -> None:
        dlg = SecretDialog("مفتاح استرداد جديد", "كلمة مرور النسخ الحالية", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        key = security.new_recovery_key()
        try:
            backup.regenerate_recovery_key(self.session.con, dlg.secret, key)
        except (vault.VaultError, backup.BackupError) as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.recovery_key")
        ShowKeyDialog(key, self).exec()
        self.notice.show_message("أُنشئ مفتاح استرداد جديد. المفتاح القديم يفتح النسخ القديمة فقط.", "success")

    def relink(self) -> None:
        dlg = SecretDialog("إعادة ربط الجهاز", "كلمة مرور النسخ أو مفتاح الاسترداد", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup.relink_device(self.session.con, dlg.secret)
        except (vault.VaultError, backup.BackupError) as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.relink")
        self.notice.show_message("أُعيد ربط الجهاز، وعاد النسخ التلقائي يعمل", "success")
        self.refresh()

    def restore_selected(self) -> None:
        b = self._selected()
        if not b:
            return
        dlg = RestoreConfirmDialog(self.session, b, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        users.audit(self.session.con, self.session.user, "backup.restore", None, None, b.path.name)
        self.session.on_restore(str(b.path), dlg.secret)


class SecretDialog(Modal):
    def __init__(self, title: str, label: str, new_password: bool = False, parent=None):
        super().__init__(title, parent, width=460)
        self.secret = ""
        self.new_secret = ""
        self.with_new = new_password
        lay = self.body
        f = form()
        self.cur = PasswordEdit()
        f.addRow(label, self.cur)
        if new_password:
            self.new = PasswordEdit()
            self.new2 = PasswordEdit()
            f.addRow("كلمة المرور الجديدة", self.new)
            f.addRow("تأكيدها", self.new2)
        lay.addLayout(f)
        self.add_footer("متابعة", self.go)

    def go(self) -> None:
        if self.with_new:
            err = security.check_backup_password(self.new.text())
            if err:
                self.notice.show_message(err)
                return
            if self.new.text() != self.new2.text():
                self.notice.show_message("كلمتا المرور غير متطابقتين")
                return
            self.new_secret = self.new.text()
        self.secret = self.cur.text()
        self.accept()


class ShowKeyDialog(Modal):
    def __init__(self, key: str, parent=None):
        super().__init__("مفتاح الاسترداد الجديد", parent, width=580)
        self.key = key
        self.close_btn.hide()          # closes only once the typed confirmation matches
        lay = self.body
        box = QLabel(key)
        box.setObjectName("keybox")
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(box)
        lay.addWidget(muted("اكتبه بخط يدك وضعه في مظروف مغلق خارج المحل. لن يُعرض مرة أخرى."))
        self.confirm = ltr_edit("أعد كتابة المفتاح للتأكيد")
        lay.addWidget(self.confirm)
        ok = primary_button("كتبته وتأكدت منه")
        ok.clicked.connect(self.go)
        self.footer_layout.insertWidget(0, ok)
        self.footer.show()

    def reject(self) -> None:
        self.notice.show_message("أعد كتابة المفتاح للتأكيد قبل الإغلاق")

    def go(self) -> None:
        try:
            if security.normalize_recovery_key(self.confirm.text()) != self.key:
                raise ValueError("المفتاح غير مطابق")
        except ValueError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class RestoreConfirmDialog(Modal):
    def __init__(self, session, b, parent=None):
        super().__init__(f"استعادة نسخة {b.at.strftime('%Y-%m-%d %H:%M')}", parent, width=540)
        self.session = session
        self.secret = None
        lay = self.body
        warn = Notice()
        warn.show_message(f"كل ما سُجّل بعد {ltr(b.at.strftime('%Y-%m-%d %H:%M'))} سيختفي من البرنامج. "
                          "قبل الاستعادة تُحفظ نسخة أمان من الوضع الحالي تلقائيًا، فيمكن الرجوع عنها. "
                          "سيُغلق البرنامج ويُعاد تشغيله.", "warn")
        lay.addWidget(warn)
        f = form()
        self.login_pw = PasswordEdit()
        self.word = QLineEdit()
        self.word.setPlaceholderText("استعادة")
        f.addRow("كلمة مرور دخولك", self.login_pw)
        f.addRow("للتأكيد اكتب: استعادة", self.word)
        lay.addLayout(f)
        self.add_footer("حفظ نسخة أمان ثم الاستعادة", self.go).setObjectName("danger")

    def go(self) -> None:
        if self.word.text().strip() != "استعادة":
            self.notice.show_message("اكتب كلمة «استعادة» للتأكيد")
            return
        try:
            users.login(self.session.con, self.session.user.username, self.login_pw.text())
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.secret = None   # A local backup opens with the device key and needs no secret
        self.accept()
