"""صفحة الإعدادات بتبويباتها: المحل، المستخدمون والصلاحيات، سجل العمليات، الاختصارات، النسخ الاحتياطي.

كل تبويب كلاس مستقل يأخذ الجلسة. التبويبات التي تحتاج صلاحية تُعرض للمخوَّل فقط.
لا SQL هنا؛ كل شيء عبر morv.infra.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QTableView, QTabWidget, QVBoxLayout, QWidget)

from morv.core import security
from morv.core.text import ltr
from morv.infra import backup, settings, shop, users, vault
from morv.ui.login import ChangePasswordDialog
from morv.ui.models import AuditModel, BackupsModel
from morv.ui.widgets import (Notice, PasswordEdit, ask, card, danger_button, form, info, ltr_edit, muted,
                             primary_button)

SHORTCUTS = [("Ctrl+B", "طي الشريط الجانبي أو فتحه"), ("Ctrl+D", "التبديل بين الوضع الفاتح والداكن"),
             ("Ctrl+L", "تسجيل الخروج"), ("Ctrl+Q", "إغلاق البرنامج")]


def _table(model) -> QTableView:
    """جدول موحّد: صف كامل عند الاختيار، بلا تحرير، وآخر عمود يتمدد."""
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
    """حاوية التبويبات."""

    def __init__(self, session):
        """بناء التبويبات حسب الصلاحيات."""
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        self.tabs = QTabWidget()
        self.shop_tab = None
        if session.can("settings.edit"):      # الكاشير لا يرى بيانات المحل أصلًا (قرار المالك)
            self.shop_tab = ShopTab(session)
            self.tabs.addTab(self.shop_tab, "المحل")
        if session.can("users.manage"):
            self.users_tab = UsersTab(session)
            self.tabs.addTab(self.users_tab, "المستخدمون والصلاحيات")
            self.audit_tab = AuditTab(session)
            self.tabs.addTab(self.audit_tab, "سجل العمليات")
        self.tabs.addTab(ShortcutsTab(), "الاختصارات")
        self.backup_tab = BackupTab(session)
        self.tabs.addTab(self.backup_tab, "النسخ الاحتياطي")
        lay.addWidget(self.tabs)


class ShopTab(QWidget):
    """بيانات المحل والشعار."""

    def __init__(self, session):
        """تحميل البيانات الحالية في الحقول."""
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        box, inner = card("بيانات المحل")
        inner.addWidget(muted("تظهر في ترويسة الفواتير والإيصالات."))
        f = form()
        data = shop.load(session.con)
        self.name = QLineEdit(data.name)
        self.phone = ltr_edit("01xxxxxxxxx")
        self.phone.setText(data.phone)
        self.address = QLineEdit(data.address)
        f.addRow("اسم المحل *", self.name)
        f.addRow("رقم الهاتف *", self.phone)
        f.addRow("العنوان", self.address)
        logo_row = QHBoxLayout()
        self.logo = QLabel()
        self.logo.setFixedSize(96, 96)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pick = QPushButton("اختيار شعار")
        pick.clicked.connect(self.pick_logo)
        remove = QPushButton("إزالة")
        remove.clicked.connect(self.remove_logo)
        logo_row.addWidget(self.logo)
        logo_row.addWidget(pick)
        logo_row.addWidget(remove)
        logo_row.addStretch(1)
        f.addRow("الشعار", logo_row)
        inner.addLayout(f)
        self.notice = Notice()
        inner.addWidget(self.notice)
        editable = session.can("settings.edit")
        save = primary_button("حفظ")
        save.clicked.connect(self.save)
        save.setEnabled(editable)
        for w in (self.name, self.phone, self.address, pick, remove):
            w.setEnabled(editable)
        inner.addWidget(save, 0, Qt.AlignmentFlag.AlignLeading)
        lay.addWidget(box)
        lay.addStretch(1)
        self._show_logo()

    def _show_logo(self) -> None:
        """عرض الشعار الحالي إن وُجد."""
        p = shop.Shop().logo_path
        if p:
            self.logo.setPixmap(QPixmap(str(p)).scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation))
        else:
            self.logo.setPixmap(QPixmap())
            self.logo.setText("لا شعار")

    def pick_logo(self) -> None:
        """اختيار صورة الشعار ونسخها إلى مجلد البيانات."""
        p, _ = QFileDialog.getOpenFileName(self, "صورة الشعار", "", "صور (*.png *.jpg *.jpeg)")
        if p:
            shop.save_logo(p)
            self._show_logo()

    def remove_logo(self) -> None:
        """حذف الشعار."""
        shop.remove_logo()
        self._show_logo()

    def save(self) -> None:
        """حفظ البيانات بعد فحصها."""
        try:
            shop.save(self.session.con, self.name.text(), self.phone.text(), self.address.text())
        except shop.ShopError as e:
            self.notice.show_message(str(e))
            return
        users.audit(self.session.con, self.session.user, "shop.save")
        self.notice.show_message("تم الحفظ", "success")


class UsersTab(QWidget):
    """قائمة المستخدمين وتفاصيل المختار وصلاحياته."""

    def __init__(self, session):
        """بناء القائمة والتفاصيل."""
        super().__init__()
        self.session = session
        self.current: users.User | None = None
        row = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setObjectName("userList")
        self.list.currentRowChanged.connect(self._select)
        new = primary_button("مستخدم جديد")
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
        for key, label in security.PERMISSIONS.items():
            cb = QCheckBox(label)
            cb.toggled.connect(lambda on, k=key: self._perm_changed(k, on))
            self.checks[key] = cb
            inner.addWidget(cb)
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
        inner.addStretch(1)
        row.addWidget(self.detail, 2)
        self._loading = False
        self.reload()

    def reload(self, select_id: int | None = None) -> None:
        """إعادة تحميل القائمة واختيار مستخدم."""
        self.users = users.list_users(self.session.con)
        self.list.blockSignals(True)
        self.list.clear()
        for u in self.users:
            item = QListWidgetItem(f"{u.display_name} — {security.ROLES[u.role]}" + ("" if u.active else " (موقوف)"))
            item.setData(Qt.ItemDataRole.UserRole, u.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        idx = next((i for i, u in enumerate(self.users) if u.id == select_id), 0)
        self.list.setCurrentRow(idx)
        self._select(idx)

    def _select(self, row: int) -> None:
        """عرض تفاصيل المستخدم المختار."""
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
        self.active_btn.setText("إيقاف الحساب" if u.active else "تفعيل الحساب")
        self.active_btn.setEnabled(u.id != self.session.user.id)
        self.notice.show_message("")
        self._loading = False

    def _role_changed(self, idx: int) -> None:
        """تغيير الدور من القائمة."""
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
        """تعديل صلاحية واحدة."""
        if self._loading or not self.current or self.current.is_manager:
            return
        users.set_permission(self.session.con, self.session.user, self.current.id, key, on)

    def new_user(self) -> None:
        """حوار مستخدم جديد."""
        dlg = NewUserDialog(self.session, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.reload(dlg.created.id)

    def reset_password(self) -> None:
        """تعيين كلمة مرور مؤقتة يُلزم المستخدم بتغييرها."""
        if not self.current:
            return
        dlg = TempPasswordDialog(self.session, self.current, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.notice.show_message("تم تعيين كلمة مرور مؤقتة، وسيُطلب تغييرها عند أول دخول", "success")

    def toggle_active(self) -> None:
        """إيقاف الحساب أو تفعيله."""
        if not self.current:
            return
        try:
            users.set_active(self.session.con, self.session.user, self.current.id, not self.current.active)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.reload(self.current.id)


class NewUserDialog(QDialog):
    """حوار إنشاء مستخدم."""

    def __init__(self, session, parent=None):
        """الحقول: الاسم، اسم الدخول، كلمة مرور مؤقتة، الدور."""
        super().__init__(parent)
        self.session = session
        self.created = None
        self.setWindowTitle("مستخدم جديد")
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
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
        self.notice = Notice()
        lay.addWidget(self.notice)
        save = primary_button("حفظ")
        save.clicked.connect(self.save)
        lay.addWidget(save)

    def save(self) -> None:
        """إنشاء المستخدم."""
        try:
            self.created = users.create_user(self.session.con, self.session.user, self.username.text(),
                                             self.name.text(), self.password.text(), self.role.currentData(),
                                             must_change_pw=True)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class TempPasswordDialog(QDialog):
    """حوار كلمة مرور مؤقتة لمستخدم آخر."""

    def __init__(self, session, target, parent=None):
        """حقل واحد."""
        super().__init__(parent)
        self.session, self.target = session, target
        self.setWindowTitle(f"كلمة مرور مؤقتة — {target.display_name}")
        self.setFixedWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        f = form()
        self.password = PasswordEdit()
        f.addRow("كلمة المرور المؤقتة", self.password)
        lay.addLayout(f)
        self.notice = Notice()
        lay.addWidget(self.notice)
        save = primary_button("حفظ")
        save.clicked.connect(self.save)
        lay.addWidget(save)

    def save(self) -> None:
        """الحفظ مع إلزام التغيير."""
        try:
            users.set_password(self.session.con, self.session.user, self.target.id, self.password.text(), must_change=True)
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class AuditTab(QWidget):
    """سجل العمليات."""

    def __init__(self, session):
        """جدول وزر تحميل المزيد."""
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
    """قائمة اختصارات لوحة المفاتيح الحالية؛ كل شاشة جديدة تضيف اختصاراتها هنا."""

    def __init__(self):
        """بناء الجدول."""
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
    """النسخ الاحتياطي: الحالة، والعمليات، والقائمة، والفلاشة، والمفاتيح، والاستعادة."""

    def __init__(self, session):
        """بناء الأجزاء."""
        super().__init__()
        self.session = session
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        # بطاقات الحالة
        stats = QHBoxLayout()
        self.stat_local = self._stat("آخر نسخة على الجهاز")
        self.stat_usb = self._stat("نسخة الفلاشة")
        self.stat_test = self._stat("آخر اختبار استعادة")
        for s in (self.stat_local, self.stat_usb, self.stat_test):
            stats.addWidget(s[0], 1)
        lay.addLayout(stats)
        self.health = Notice()
        lay.addWidget(self.health)

        # الأزرار الرئيسية
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

        # القائمة
        self.model = BackupsModel()
        self.view = _table(self.model)
        lay.addWidget(self.view, 1)

        # الفلاشة والمفاتيح
        bottom = QHBoxLayout()
        usb_box, usb_inner = card("مجلد الفلاشة")
        urow = QHBoxLayout()
        self.usb_dir = ltr_edit("مثال: E:\\MorvBackup")
        self.usb_dir.setText(settings.get(session.con, "backup.usb_dir", ""))
        pick = QPushButton("اختيار")
        pick.clicked.connect(self.pick_usb)
        save = QPushButton("حفظ")
        save.clicked.connect(self.save_usb)
        # تغيير مجلد الفلاشة وكلمة مرور النسخ للمدير فقط؛ الكاشير ينسخ ويختبر فقط
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
        """بطاقة حالة صغيرة: (البطاقة، عنوان القيمة، النص الفرعي)."""
        box, inner = card()
        inner.addWidget(muted(title))
        value = QLabel("—")
        value.setObjectName("cardTitle")
        sub = muted("")
        inner.addWidget(value)
        inner.addWidget(sub)
        return box, value, sub

    def refresh(self) -> None:
        """تحديث البطاقات والتنبيه والقائمة."""
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
        """النسخة المختارة في الجدول."""
        idx = self.view.currentIndex()
        if not idx.isValid():
            self.notice.show_message("اختر نسخة من القائمة أولًا")
            return None
        return self.model.items[idx.row()]

    def backup_now(self) -> None:
        """نسخة يدوية."""
        try:
            r = backup.create_backup(self.session.con, "manual", username=self.session.user.username)
            self.notice.show_message(f"تمت النسخة: {r.path.name} · {r.detail}", "success")
        except backup.BackupError as e:
            self.notice.show_message(str(e))
        self.refresh()

    def test_selected(self) -> None:
        """اختبار استعادة النسخة المختارة في مكان معزول."""
        b = self._selected()
        if not b:
            return
        r = backup.restore_test(self.session.con, b.path, username=self.session.user.username)
        self.notice.show_message(("نجح الاختبار: " if r.ok else "فشل الاختبار: ") + r.detail, "success" if r.ok else "error")
        self.refresh()

    def copy_usb(self) -> None:
        """نسخ أحدث نسخة إلى الفلاشة."""
        try:
            r = backup.copy_to_usb(self.session.con, username=self.session.user.username)
            self.notice.show_message(f"نُسخت إلى الفلاشة: {r.path.name}", "success")
        except backup.BackupError as e:
            self.notice.show_message(str(e))
        self.refresh()

    def pick_usb(self) -> None:
        """اختيار مجلد الفلاشة."""
        d = QFileDialog.getExistingDirectory(self, "مجلد النسخ على الفلاشة")
        if d:
            self.usb_dir.setText(d)

    def save_usb(self) -> None:
        """حفظ مجلد الفلاشة، مع تحذير إن كان على قرص النظام نفسه (لا يحمي من تعطّل الجهاز)."""
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
        """تغيير كلمة مرور النسخ."""
        dlg = SecretDialog("تغيير كلمة مرور النسخ", "كلمة المرور الحالية أو مفتاح الاسترداد", new_password=True, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup.change_backup_password(self.session.con, dlg.secret, dlg.new_secret)
        except vault.VaultError as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.password")
        self.notice.show_message("تم تغيير كلمة مرور النسخ. النسخ الجديدة تُفتح بها، والقديمة بالقديمة أو بمفتاح الاسترداد.", "success")

    def new_recovery_key(self) -> None:
        """مفتاح استرداد جديد (مدير فقط) يُعرض مرة واحدة."""
        dlg = SecretDialog("مفتاح استرداد جديد", "كلمة مرور النسخ الحالية", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        key = security.new_recovery_key()
        try:
            backup.regenerate_recovery_key(self.session.con, dlg.secret, key)
        except vault.VaultError as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.recovery_key")
        ShowKeyDialog(key, self).exec()
        self.notice.show_message("أُنشئ مفتاح استرداد جديد. المفتاح القديم يفتح النسخ القديمة فقط.", "success")

    def relink(self) -> None:
        """إعادة حفظ مفتاح الجهاز بعد ضياعه."""
        dlg = SecretDialog("إعادة ربط الجهاز", "كلمة مرور النسخ أو مفتاح الاسترداد", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            backup.relink_device(self.session.con, dlg.secret)
        except vault.VaultError as e:
            info(self, "خطأ", str(e), error=True)
            return
        users.audit(self.session.con, self.session.user, "backup.relink")
        self.notice.show_message("أُعيد ربط الجهاز، وعاد النسخ التلقائي يعمل", "success")
        self.refresh()

    def restore_selected(self) -> None:
        """استعادة النسخة المختارة بعد تأكيد مزدوج، ثم يعيد التطبيق تشغيل نفسه."""
        b = self._selected()
        if not b:
            return
        dlg = RestoreConfirmDialog(self.session, b, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        users.audit(self.session.con, self.session.user, "backup.restore", None, None, b.path.name)
        self.session.on_restore(str(b.path), dlg.secret)


class SecretDialog(QDialog):
    """حوار يطلب سرًا (وكلمة مرور جديدة اختياريًا)."""

    def __init__(self, title: str, label: str, new_password: bool = False, parent=None):
        """بناء الحقول."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(440)
        self.secret = ""
        self.new_secret = ""
        self.with_new = new_password
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        f = form()
        self.cur = PasswordEdit()
        f.addRow(label, self.cur)
        if new_password:
            self.new = PasswordEdit()
            self.new2 = PasswordEdit()
            f.addRow("كلمة المرور الجديدة", self.new)
            f.addRow("تأكيدها", self.new2)
        lay.addLayout(f)
        self.notice = Notice()
        lay.addWidget(self.notice)
        ok = primary_button("متابعة")
        ok.clicked.connect(self.go)
        lay.addWidget(ok)

    def go(self) -> None:
        """التحقق المحلي ثم القبول."""
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


class ShowKeyDialog(QDialog):
    """عرض مفتاح استرداد جديد مرة واحدة مع إعادة الإدخال للتأكيد."""

    def __init__(self, key: str, parent=None):
        """بناء الحوار."""
        super().__init__(parent)
        self.key = key
        self.setWindowTitle("مفتاح الاسترداد الجديد")
        self.setFixedWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
        box = QLabel(key)
        box.setObjectName("keybox")
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(box)
        lay.addWidget(muted("اكتبه بخط يدك وضعه في مظروف مغلق خارج المحل. لن يُعرض مرة أخرى."))
        self.confirm = ltr_edit("أعد كتابة المفتاح للتأكيد")
        lay.addWidget(self.confirm)
        self.notice = Notice()
        lay.addWidget(self.notice)
        ok = primary_button("كتبته وتأكدت منه")
        ok.clicked.connect(self.go)
        lay.addWidget(ok)

    def go(self) -> None:
        """لا يُغلق إلا بمطابقة المفتاح."""
        try:
            if security.normalize_recovery_key(self.confirm.text()) != self.key:
                raise ValueError("المفتاح غير مطابق")
        except ValueError as e:
            self.notice.show_message(str(e))
            return
        self.accept()


class RestoreConfirmDialog(QDialog):
    """تأكيد الاستعادة: كلمة مرور الدخول للمستخدم الحالي + كتابة كلمة «استعادة»."""

    def __init__(self, session, b, parent=None):
        """بناء الحوار."""
        super().__init__(parent)
        self.session = session
        self.secret = None
        self.setWindowTitle(f"استعادة نسخة {b.at.strftime('%Y-%m-%d %H:%M')}")
        self.setFixedWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 20)
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
        self.notice = Notice()
        lay.addWidget(self.notice)
        go = danger_button("حفظ نسخة أمان ثم الاستعادة")
        go.clicked.connect(self.go)
        lay.addWidget(go)

    def go(self) -> None:
        """التحقق من كلمة المرور والكلمة."""
        if self.word.text().strip() != "استعادة":
            self.notice.show_message("اكتب كلمة «استعادة» للتأكيد")
            return
        try:
            users.login(self.session.con, self.session.user.username, self.login_pw.text())
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.secret = None   # النسخة المحلية تُفتح بمفتاح الجهاز؛ لا تحتاج سرًا
        self.accept()
