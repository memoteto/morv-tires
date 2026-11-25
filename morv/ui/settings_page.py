"""Settings hub and the existing settings pages."""

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QSpinBox, QStackedWidget, QTableView,
                               QVBoxLayout, QWidget)

from morv.core import security
from morv.core.text import ltr
from morv.infra import backup, settings, users, vault
from morv.ui import theme, tokens
from morv.ui.adaptive_layout import AdaptiveCards, AdaptiveFields, AdaptiveTableColumns, PageFrame
from morv.ui.listpage import TupleModel
from morv.ui.modal import Modal
from morv.ui.models import AuditModel, BackupsModel
from morv.ui.shop_settings import PrintingTab, ShopTab
from morv.ui.widgets import (Notice, PasswordEdit, action_row, ask, card, danger_button, field, form, info, ltr_edit, muted,
                             primary_button, section_card, icon_button, kbd)

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


SETTINGS_GROUPS = (
    ("إدارة المحل", "home", (
        ("shop", "بيانات المحل", "home", "settings.edit"),
        ("printing", "المستندات والطباعة", "printer", "settings.edit"),
        ("item_fields", "حقول بطاقة الصنف", "inventory", "settings.edit"),
    )),
    ("الإدارة المالية", "treasury", (
        ("partners", "الشركاء", "customers", "treasury.manage"),
        ("opening", "الأرصدة الافتتاحية", "cash", "opening.post"),
    )),
    ("الأمان والبيانات", "shield", (
        ("users", "المستخدمون والصلاحيات", "user", "users.manage"),
        ("audit", "سجل العمليات", "reports", "users.manage"),
        ("backup", "النسخ الاحتياطي والاستعادة", "usb", None),
    )),
    ("تفضيلات البرنامج", "settings", (
        ("appearance", "المظهر والخطوط", "font", None),
        ("shortcuts", "اختصارات لوحة المفاتيح", "settings", None),
    )),
)


class SettingsPage(PageFrame):
    def __init__(self, session):
        super().__init__("الرجوع إلى جميع الإعدادات", "collapse")
        self.session = session
        self.shop_tab = self.printing_tab = self.fields_tab = self.partners_tab = None
        self.destinations = {}
        self.links = {}
        self._routes = {}
        self._views = {}
        self.current_key = None
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.body.addWidget(self.stack, 1)
        self.back.clicked.connect(self.show_hub)

        cards = []
        for title, icon_name, routes in SETTINGS_GROUPS:
            allowed = [r for r in routes if r[3] is None or session.can(r[3])]
            if not allowed:
                continue
            box, inner, header = section_card(title, icon_name)
            header.parentWidget().setMinimumHeight(tokens.LAYOUT["settings_group_header_min"])
            for key, label, icon, permission in allowed:
                widget = self._build(key)
                self.destinations[key] = widget
                self._routes[key] = (label, permission)
                self._views[key] = widget
                self.stack.addWidget(widget)
                button = icon_button(label, icon)
                button.setMinimumHeight(tokens.LAYOUT["settings_link_min"])
                button.clicked.connect(lambda _checked=False, route=key: self.open_page(route))
                inner.addWidget(button)
                self.links[key] = button
            inner.addStretch(1)
            cards.append(box)
        if self.shop_tab is not None and self.printing_tab is not None:
            self.shop_tab.changed.connect(self.printing_tab.refresh_preview)
        self.hub = AdaptiveCards(cards, minimum_width=tokens.LAYOUT["settings_card_min"])
        self.stack.insertWidget(0, self.hub)
        self.show_hub()

    def _build(self, key):
        if key == "shop":
            self.shop_tab = ShopTab(self.session)
            return self.shop_tab
        if key == "printing":
            self.printing_tab = PrintingTab(self.session)
            return self.printing_tab
        if key == "item_fields":
            from morv.ui.inventory_page import ItemFieldsTab
            self.fields_tab = ItemFieldsTab(self.session)
            return self.fields_tab
        if key == "partners":
            from morv.ui.partners_tab import PartnersTab
            self.partners_tab = PartnersTab(self.session)
            return self.partners_tab
        if key == "opening":
            from morv.ui.opening_dialogs import OpeningTab
            self.opening_tab = OpeningTab(self.session)
            return self.opening_tab
        if key == "users":
            self.users_tab = UsersTab(self.session)
            return self.users_tab
        if key == "audit":
            self.audit_tab = AuditTab(self.session)
            return self.audit_tab
        if key == "backup":
            self.backup_tab = BackupTab(self.session)
            return self.backup_tab
        if key == "appearance":
            self.appearance_tab = AppearanceTab(self.session)
            return self.appearance_tab
        if key == "shortcuts":
            return ShortcutsTab()
        raise KeyError(key)

    def title_for(self, key):
        return self._routes[key][0]

    def open_page(self, key) -> bool:
        if key not in self.destinations:
            return False
        title, permission = self._routes[key]
        if permission is not None and not self.session.can(permission):
            return False
        self.current_key = key
        self.set_header(title, "", back=True)
        self.stack.setCurrentWidget(self._views[key])
        return True

    def show_hub(self):
        previous = self.current_key
        self.current_key = None
        self.set_header("مركز الإعدادات", "إدارة وتخصيص إعدادات المحل والبرنامج", back=False)
        self.stack.setCurrentWidget(self.hub)
        if previous in self.links:
            self.links[previous].setFocus()

    def refresh(self):
        self.show_hub()


class AppearanceTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.LAYOUT["pane_gap"])
        self.options = QWidget()
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(tokens.LAYOUT["pane_gap"])
        box, inner = card("إعدادات الخط", compact=True)
        self.family = QComboBox()
        self.family.addItems(theme.available_fonts())
        self.family.setCurrentText(settings.get(session.con, "ui.font_family", theme.DEFAULT_FONT))
        self.size = QComboBox()
        for size in theme.FONT_SIZES:
            self.size.addItem(ltr(str(size)), size)
        saved_size = int(settings.get(session.con, "ui.font_size", str(theme.DEFAULT_SIZE)))
        self.size.setCurrentIndex(list(theme.FONT_SIZES).index(saved_size))
        inner.addWidget(field("نوع الخط", self.family))
        inner.addWidget(field("حجم الخط", self.size))
        inner.addWidget(muted("الخطان IBM Plex Sans Arabic وCairo مضمّنان مع البرنامج."))
        self.apply_btn = primary_button("تطبيق", "font")
        self.apply_btn.clicked.connect(self.apply)
        inner.addLayout(action_row(self.apply_btn))
        options.addWidget(box)
        appearance, body = card("المظهر", compact=True)
        body.addWidget(muted("الوضع الفاتح والداكن من زر الشمس أو القمر في الشريط العلوي."))
        body.addWidget(kbd("Ctrl+D"), 0, Qt.AlignmentFlag.AlignLeading)
        options.addWidget(appearance)
        options.addStretch(1)

        self.preview, body = card("المعاينة المباشرة", compact=True)
        body.addWidget(muted("نموذج تجريبي فقط — لا تُحفظ بيانات منه."))
        self.sample = QLabel("إطار ميشلان Primacy 4 مقاس " + ltr("205/55R16") + " بسعر " + ltr("6,450.00"))
        self.sample.setWordWrap(True)
        body.addWidget(self.sample)
        body.addWidget(QLabel("اسم العميل"))
        self.sample_input = QLineEdit("عميل تجريبي")
        self.sample_input.setReadOnly(True)
        body.addWidget(self.sample_input)
        self.sample_model = TupleModel([("الصنف", lambda row: row[0]), ("السعر", lambda row: ltr(row[1]))])
        self.sample_model.reset_rows([("إطار سيارة", "6,450.00"), ("بطارية", "3,200.00")])
        self.sample_table = _table(self.sample_model)
        self.sample_columns = AdaptiveTableColumns(self.sample_table, {0: 3, 1: 2}, {})
        self.sample_table.setFixedHeight(4 * tokens.LAYOUT["summary_table_row_height"])
        body.addWidget(self.sample_table)
        body.addLayout(action_row(primary_button("حفظ", "save"), QPushButton("إلغاء")))
        self.preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for widget in self.preview.findChildren(QWidget):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # In RTL the first grid cell is physically on the right; stack settings first at narrow widths.
        self.panes = AdaptiveCards([self.preview, self.options], minimum_width=tokens.LAYOUT["settings_card_min"],
                                   column_weights=(3, 2), stack_order=(1, 0))
        lay.addWidget(self.panes, 1)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.update_preview)
        self.family.currentIndexChanged.connect(self.update_preview)
        self.size.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "preview_timer") and event.type() in (
                QEvent.Type.StyleChange, QEvent.Type.ApplicationFontChange):
            # Reapply the draft after the global stylesheet has finished repolishing widgets.
            self.preview_timer.start(0)

    def update_preview(self) -> None:
        font = theme.pick_font(self.size.currentData(), self.family.currentText())
        self.preview.setFont(font)
        for widget in self.preview.findChildren(QWidget):
            widget.setFont(font)
        self.sample_columns.schedule()

    def apply(self) -> None:
        win = self.window()
        if hasattr(win, "set_font"):
            win.set_font(self.family.currentText(), self.size.currentData())
            self.update_preview()


class UsersTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.current: users.User | None = None
        row = QHBoxLayout(self)
        row.setSpacing(tokens.LAYOUT["pane_gap"])
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumWidth(tokens.LAYOUT["settings_user_list_max"])
        self.list.setObjectName("userList")
        self.list.setSpacing(2)
        self.list.setUniformItemSizes(True)
        self.list.currentRowChanged.connect(self._select)
        self.new_btn = primary_button("مستخدم جديد", "plus")
        self.new_btn.clicked.connect(self.new_user)
        left.addWidget(self.new_btn, alignment=Qt.AlignmentFlag.AlignLeading)
        left.addWidget(self.list, 1)
        row.addLayout(left, 1)

        self.detail, inner = card()
        self.title = QLabel()
        self.title.setObjectName("cardTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        self.title.setWordWrap(True)
        self.sub = muted("")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        inner.addWidget(AdaptiveFields([self.title, self.sub]))
        self.role = QComboBox()
        for key, label in security.ROLES.items():
            self.role.addItem(label, key)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.discount_cap = QSpinBox()
        self.discount_cap.setRange(0, 100)
        self.discount_cap.valueChanged.connect(lambda _v: self._perm_changed("sales.discount", self.checks["sales.discount"].isChecked()))
        inner.addWidget(AdaptiveFields([
            field("الدور", self.role),
            field("الحد الأقصى للخصم (٪)", self.discount_cap),
        ]))
        heading = QLabel("الصلاحيات")
        heading.setObjectName("cardTitle")
        inner.addWidget(heading)

        # Presentation groups only; permissions and labels still come from security.PERMISSIONS.
        groups = [
            ("المبيعات والعملاء", ("customers", "sales", "installments", "receipts")),
            ("المشتريات والمخزون", ("suppliers", "items", "purchases", "stock")),
            ("الإدارة المالية", ("treasury", "reports", "profit", "period", "opening")),
            ("إدارة النظام", ("users", "settings", "backup")),
        ]
        cards = []
        bodies = {}
        for title, prefixes in groups:
            box, body = card(title, compact=True)
            cards.append(box)
            for prefix in prefixes:
                bodies[prefix] = body
        self.checks: dict[str, QCheckBox] = {}
        for key, label in security.PERMISSIONS.items():
            cb = QCheckBox(label)
            cb.toggled.connect(lambda on, k=key: self._perm_changed(k, on))
            self.checks[key] = cb
            bodies.get(key.split(".", 1)[0], bodies["users"]).addWidget(cb)
        for body in set(bodies.values()):
            body.addStretch(1)
        self.permission_grid = AdaptiveCards(cards, minimum_width=tokens.LAYOUT["form_field_min"])
        inner.addWidget(self.permission_grid, 1)  # actions remain outside the permissions scroll
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
            item.setToolTip(item.text())
            item.setSizeHint(QSize(0, tokens.LAYOUT["settings_link_min"]))
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
        status = "نشط" if u.active else "موقوف"
        self.sub.setText(f"اسم الدخول: {ltr(u.username)} · {status}")
        self.role.setCurrentIndex(list(security.ROLES).index(u.role))
        editable = self.session.can("users.manage")
        perms = users.configured_permissions(self.session.con, u)
        self.new_btn.setEnabled(editable)
        self.role.setEnabled(editable)
        self.pw_btn.setEnabled(editable)
        for key, cb in self.checks.items():
            cb.setChecked(key in perms)
            cb.setEnabled(editable and not u.is_manager)
        self.discount_cap.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons if u.is_manager
                                           else QSpinBox.ButtonSymbols.UpDownArrows)
        self.discount_cap.setSpecialValueText("غير محدود" if u.is_manager else "")
        self.discount_cap.setValue(users.configured_permission_limit(self.session.con, u, "sales.discount") or 0)
        self.discount_cap.setEnabled(editable and not u.is_manager)
        self.active_btn.setText("إيقاف الحساب" if u.active else "تفعيل الحساب")
        self.active_btn.setEnabled(editable and u.id != self.session.user.id)
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
            self.reload(self.current.id)
            self.notice.show_message(str(e))
            return
        if self.current.id == self.session.user.id:
            self.session.refresh_user()
        self.reload(self.current.id)

    def _perm_changed(self, key: str, on: bool) -> None:
        if self._loading or not self.current or self.current.is_manager:
            return
        limit = self.discount_cap.value() if key == "sales.discount" else None
        try:
            users.set_permission(self.session.con, self.session.user, self.current.id, key, on, limit)
        except users.AuthError as e:
            self.reload(self.current.id)
            self.notice.show_message(str(e))
            return
        if self.current.id == self.session.user.id:
            self.reload(self.current.id)

    def new_user(self) -> None:
        if not self.session.can("users.manage"):
            self.reload()
            return
        dlg = NewUserDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(dlg.created.id)

    def reset_password(self) -> None:
        if not self.current or not self.session.can("users.manage"):
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
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.LAYOUT["pane_gap"])
        lay.addWidget(muted("يعرض سجل العمليات من الأحدث إلى الأقدم. استخدم تحميل الأقدم لعرض المزيد."))
        listing, inner = card("سجل العمليات", compact=True)
        self.model = AuditModel(session.con)
        self.view = _table(self.model)
        self.view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.columns = AdaptiveTableColumns(self.view, {0: 200, 1: 170, 2: 220, 3: 448}, {})
        self.model.modelReset.connect(self.columns.schedule)
        self.model.rowsInserted.connect(self.columns.schedule)
        inner.addWidget(self.view, 1)
        lay.addWidget(listing, 1)
        self.refresh_btn = QPushButton("تحديث")
        self.refresh_btn.clicked.connect(self.model.reload)
        self.more_btn = QPushButton("تحميل الأقدم")
        self.more_btn.clicked.connect(self.load_older)
        self.count = muted("")
        row = action_row(self.refresh_btn, self.more_btn)
        row.addWidget(self.count)
        lay.addLayout(row)
        self.model.modelReset.connect(self.update_count)
        self.model.rowsInserted.connect(self.update_count)
        self.update_count()

    def update_count(self) -> None:
        count = self.model.rowCount()
        self.count.setText(f"{count} سجل معروض")
        self.more_btn.setEnabled(count > 0)

    def load_older(self) -> None:
        if not self.model.load_more():
            self.count.setText(f"{self.model.rowCount()} سجل معروض · لا توجد سجلات أقدم")
            self.more_btn.setEnabled(False)


class ShortcutsTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.LAYOUT["pane_gap"])
        cards = []
        self.keys = {}
        descriptions = dict(SHORTCUTS)
        for title, keys in (("البحث والإضافة", ("F2", "Ctrl+N")),
                            ("المظهر والتنقل", ("Ctrl+B", "Ctrl+D")),
                            ("جلسة العمل", ("Ctrl+L", "Ctrl+Q"))):
            box, inner = card(title, compact=True)
            for key in keys:
                row = QWidget()
                row.setMinimumHeight(tokens.LAYOUT["settings_link_min"])
                line = QHBoxLayout(row)
                line.setContentsMargins(0, 0, 0, 0)
                key_label = kbd(key)
                key_label.setFixedWidth(90)
                key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.keys[key] = key_label
                desc = QLabel(descriptions[key])
                desc.setWordWrap(True)
                line.addWidget(key_label)
                line.addWidget(desc, 1)
                inner.addWidget(row)
            cards.append(box)
        self.cards = AdaptiveCards(cards, minimum_width=tokens.LAYOUT["settings_card_min"], max_columns=3)
        lay.addWidget(self.cards, 1)
        lay.addWidget(muted("هذه بعض الاختصارات العامة؛ لكل شاشة اختصاراتها حسب الوظائف المتاحة."))


class BackupTab(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.LAYOUT["pane_gap"])

        stats = QHBoxLayout()
        stats.setSpacing(tokens.LAYOUT["pane_gap"])
        self.stat_local = self._stat("آخر نسخة على الجهاز")
        self.stat_usb = self._stat("نسخة الفلاشة")
        self.stat_test = self._stat("آخر اختبار استعادة")
        for s in (self.stat_local, self.stat_usb, self.stat_test):
            stats.addWidget(s[0], 1)
        lay.addLayout(stats)
        self.health = Notice()
        lay.addWidget(self.health)
        self.backup_btn = primary_button("نسخ الآن", "save")
        self.backup_btn.clicked.connect(self.backup_now)
        self.usb_settings_btn = icon_button("مجلد الفلاشة", "usb")
        self.usb_settings_btn.clicked.connect(self.open_usb_settings)
        self.security_settings_btn = icon_button("الأمان والاسترداد", "shield")
        self.security_settings_btn.clicked.connect(self.open_security_settings)
        actions = action_row(self.backup_btn)
        actions.addWidget(self.usb_settings_btn)
        actions.addWidget(self.security_settings_btn)
        lay.addLayout(actions)
        self.notice = Notice()
        lay.addWidget(self.notice)

        listing, inner = card("النسخ المحفوظة", compact=True)
        self.model = BackupsModel()
        self.view = _table(self.model)
        self.view.setMinimumHeight(4 * tokens.LAYOUT["summary_table_row_height"])
        self.columns = AdaptiveTableColumns(self.view, {0: 4, 1: 2, 2: 2, 3: 2}, {})
        inner.addWidget(self.view, 1)
        self.test_btn = QPushButton("اختبار النسخة المختارة")
        self.test_btn.clicked.connect(self.test_selected)
        self.usb_btn = QPushButton("نسخ أحدث نسخة للفلاشة")
        self.usb_btn.clicked.connect(self.copy_usb)
        self.restore_btn = danger_button("استعادة النسخة المختارة")
        self.restore_btn.clicked.connect(self.restore_selected)
        lay.addWidget(listing, 1)
        footer = action_row(self.test_btn, self.usb_btn)
        footer.addWidget(self.restore_btn)
        lay.addLayout(footer)

        self.usb_dialog = Modal("مجلد الفلاشة", self, width=560)
        self.usb_dir = ltr_edit("مثال: E:\\MorvBackup")
        self.usb_dir.setText(settings.get(session.con, "backup.usb_dir", ""))
        self.pick_btn = QPushButton("اختيار")
        self.pick_btn.clicked.connect(self.pick_usb)
        self.usb_dialog.body.addWidget(field("مسار مجلد النسخ الخارجي", self.usb_dir))
        self.usb_dialog.body.addLayout(action_row(self.pick_btn))
        self.usb_dialog.body.addWidget(
            muted("فلاشتان بالتناوب: واحدة في المحل وأخرى في البيت، وبدّل بينهما أسبوعيًا."))
        self.save_usb_btn = self.usb_dialog.add_footer("حفظ", self.save_usb, "إغلاق")

        self.security_dialog = Modal("الأمان والاسترداد", self, width=460)
        self.pw_btn = QPushButton("تغيير كلمة مرور النسخ")
        self.rk_btn = QPushButton("مفتاح استرداد جديد")
        self.relink_btn = QPushButton("إعادة ربط هذا الجهاز")
        for button, handler in ((self.pw_btn, self.change_password), (self.rk_btn, self.new_recovery_key),
                                (self.relink_btn, self.relink)):
            button.clicked.connect(self.security_dialog.accept)
            button.clicked.connect(handler)
            self.security_dialog.body.addWidget(button)
        self.security_dialog.body.addWidget(
            muted("النسخ القديمة تبقى تُفتح بكلمة المرور التي أُخذت بها أو بمفتاح الاسترداد وقتها."))
        close = QPushButton("إغلاق")
        close.clicked.connect(self.security_dialog.reject)
        self.security_dialog.add_footer_widget(close)
        self.refresh()

    def open_usb_settings(self) -> None:
        self._refresh_permissions()
        self.usb_dir.setText(settings.get(self.session.con, "backup.usb_dir", ""))
        self.usb_dialog.notice.hide()
        self.usb_dialog.open()

    def open_security_settings(self) -> None:
        self._refresh_permissions()
        self.relink_btn.setVisible(backup.device_dek(self.session.con) is None)
        self.security_dialog.open()

    @staticmethod
    def _stat(title: str) -> tuple:
        box, inner = card(compact=True)
        inner.addWidget(muted(title))
        value = QLabel("—")
        value.setObjectName("cardTitle")
        sub = muted("")
        inner.addWidget(value)
        inner.addWidget(sub)
        return box, value, sub

    def _refresh_permissions(self) -> None:
        # The tab can outlive the role or permission it was built with.
        manager = users.is_active_manager(self.session.con, self.session.user)
        for widget in (self.usb_dir, self.pick_btn, self.save_usb_btn):
            widget.setEnabled(manager)
        self.pw_btn.setVisible(manager)
        self.rk_btn.setVisible(manager)
        self.restore_btn.setVisible(self.session.can("backup.restore"))

    def _allow_sensitive(self, *, restore: bool = False) -> bool:
        allowed = (self.session.can("backup.restore") if restore
                   else users.is_active_manager(self.session.con, self.session.user))
        if not allowed:
            self._refresh_permissions()
            self.notice.show_message("لا تملك صلاحية تنفيذ هذه العملية")
            if self.usb_dialog.isVisible():
                self.usb_dialog.notice.show_message("لا تملك صلاحية تنفيذ هذه العملية")
        return allowed

    def refresh(self) -> None:
        self._refresh_permissions()
        con = self.session.con
        last_ok = settings.get(con, "backup.last_ok_at", "")
        self.stat_local[1].setText(ltr(last_ok[:16]) if last_ok else "لا نسخة بعد")
        self.model.reload()
        n = len(self.model.items)
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
        self.columns.schedule()

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
        if not self._allow_sensitive():
            return
        parent = self.usb_dialog if self.usb_dialog.isVisible() else self
        d = QFileDialog.getExistingDirectory(parent, "مجلد النسخ على الفلاشة")
        if d and self._allow_sensitive():
            self.usb_dir.setText(d)

    def save_usb(self) -> None:
        if not self._allow_sensitive():
            return
        folder = self.usb_dir.text().strip()
        if folder and backup.same_drive_as_data(folder):
            parent = self.usb_dialog if self.usb_dialog.isVisible() else self
            if not ask(parent, "تنبيه", "هذا المجلد على نفس قرص الجهاز، فلو تعطّل الجهاز ضاعت النسخة معه. "
                                       "المقصود مجلد على فلاشة أو قرص خارجي. هل تحفظه على أي حال؟"):
                return
        if not self._allow_sensitive():
            return
        settings.put(self.session.con, "backup.usb_dir", folder)
        users.audit(self.session.con, self.session.user, "settings.usb", None, None, folder)
        self.notice.show_message("تم حفظ مجلد الفلاشة" + (" (على نفس القرص — مؤقتًا)" if folder and backup.same_drive_as_data(folder) else ""), "success")
        self.refresh()
        self.usb_dialog.accept()

    def change_password(self) -> None:
        if not self._allow_sensitive():
            return
        dlg = SecretDialog("تغيير كلمة مرور النسخ", "كلمة المرور الحالية أو مفتاح الاسترداد", new_password=True, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._allow_sensitive():
            return
        try:
            backup.change_backup_password(self.session.con, dlg.secret, dlg.new_secret)
        except (vault.VaultError, backup.BackupError) as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.password")
        self.notice.show_message("تم تغيير كلمة مرور النسخ. النسخ الجديدة تُفتح بها، والقديمة بالقديمة أو بمفتاح الاسترداد.", "success")

    def new_recovery_key(self) -> None:
        if not self._allow_sensitive():
            return
        dlg = SecretDialog("مفتاح استرداد جديد", "كلمة مرور النسخ الحالية", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._allow_sensitive():
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
        if not self._allow_sensitive(restore=True):
            return
        b = self._selected()
        if not b:
            return
        dlg = RestoreConfirmDialog(self.session, b, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._allow_sensitive(restore=True):
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
        self.cur = PasswordEdit()
        lay.addWidget(field(label, self.cur))
        if new_password:
            self.new = PasswordEdit()
            self.new2 = PasswordEdit()
            lay.addWidget(field("كلمة المرور الجديدة", self.new))
            lay.addWidget(field("تأكيدها", self.new2))
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
        self.login_pw = PasswordEdit()
        self.word = QLineEdit()
        self.word.setPlaceholderText("استعادة")
        lay.addWidget(field("كلمة مرور دخولك", self.login_pw))
        lay.addWidget(field("للتأكيد اكتب: استعادة", self.word))
        self.add_footer("حفظ نسخة أمان ثم الاستعادة", self.go).setObjectName("danger")

    def go(self) -> None:
        if not self.session.can("backup.restore"):
            self.notice.show_message("لا تملك صلاحية استعادة النسخة")
            return
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
