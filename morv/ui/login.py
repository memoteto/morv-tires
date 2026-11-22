"""Login screen: pick a user and type the password."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from morv import APP_NAME, VERSION
from morv.core import security
from morv.core.text import ltr
from morv.infra import backup, shop, users
from morv.ui.modal import Modal
from morv.ui.widgets import field, form, muted, Notice, PasswordEdit, primary_button


class LoginDialog(QDialog):
    def __init__(self, con, parent=None):
        super().__init__(parent)
        self.con = con
        self.user = None
        self.setWindowTitle(f"{APP_NAME} — تسجيل الدخول")
        self.setFixedWidth(440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 30, 30, 22)
        outer.setSpacing(14)

        shop_name = shop.load(con).name or APP_NAME
        logo = QLabel((shop_name.strip()[:1] or "M").upper())
        logo.setObjectName("loginLogo")
        logo.setFixedSize(56, 56)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row = QHBoxLayout()
        logo_row.addStretch(1)
        logo_row.addWidget(logo)
        logo_row.addStretch(1)
        outer.addLayout(logo_row)

        name = QLabel(shop_name)
        name.setObjectName("pageTitle")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(name)
        sub = QLabel("اختر المستخدم وأدخل كلمة المرور")   # No wrapping: it used to overlap on two lines
        sub.setObjectName("muted")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(sub)

        f = form()
        self.combo = QComboBox()
        self.users = [u for u in users.list_users(con) if u.active]
        for u in self.users:
            self.combo.addItem(f"{u.display_name} — {security.ROLES[u.role]}", u.id)
        self.password = PasswordEdit()
        self.password.edit.returnPressed.connect(self.try_login)
        f.addRow(field("المستخدم", self.combo))
        f.addRow(field("كلمة المرور", self.password))
        outer.addLayout(f)

        self.notice = Notice()
        outer.addWidget(self.notice)
        self.login_btn = primary_button("دخول")
        self.login_btn.setMinimumHeight(48)
        self.login_btn.clicked.connect(self.try_login)
        outer.addWidget(self.login_btn)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        outer.addWidget(line)
        foot = QHBoxLayout()
        level, text = backup.health(con)
        foot.addWidget(muted(("النسخ الاحتياطي سليم · " if level == "ok" else "") + text))
        foot.addStretch(1)
        foot.addWidget(muted(ltr(f"v{VERSION}")))
        outer.addLayout(foot)
        self.password.edit.setFocus()

    def try_login(self) -> None:
        if self.combo.currentIndex() < 0:
            self.notice.show_message("لا يوجد مستخدم نشط")
            return
        username = self.users[self.combo.currentIndex()].username
        try:
            user = users.login(self.con, username, self.password.text())
        except users.AuthError as e:
            self.notice.show_message(str(e))
            self.password.clear()
            self.password.edit.setFocus()
            return
        if user.must_change_pw:
            dlg = ChangePasswordDialog(self.con, user, self.password.text(), forced=True, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                self.password.clear()
                return
            user = users.get_user(self.con, user.id)
        self.user = user
        self.accept()


class ChangePasswordDialog(Modal):
    def __init__(self, con, user, current: str | None = None, forced: bool = False, parent=None):
        super().__init__("تغيير كلمة المرور", parent, width=440)
        self.con, self.user, self.current = con, user, current
        lay = self.body
        lay.addWidget(muted("يجب تغيير كلمة المرور المؤقتة قبل المتابعة." if forced else
                            f"تغيير كلمة مرور {user.display_name}."))
        f = form()
        self.old = PasswordEdit()
        self.new = PasswordEdit()
        self.new2 = PasswordEdit()
        if current is None:
            f.addRow(field("كلمة المرور الحالية", self.old))
        f.addRow(field("الجديدة", self.new))
        f.addRow(field("تأكيدها", self.new2))
        lay.addLayout(f)
        self.add_footer("حفظ", self.save)

    def save(self) -> None:
        if self.new.text() != self.new2.text():
            self.notice.show_message("كلمتا المرور غير متطابقتين")
            return
        try:
            old = self.current if self.current is not None else self.old.text()
            users.change_own_password(self.con, self.user, old, self.new.text())
        except users.AuthError as e:
            self.notice.show_message(str(e))
            return
        self.accept()
