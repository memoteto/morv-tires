"""معالج التشغيل الأول: يظهر مرة واحدة عندما لا يوجد مستخدمون في القاعدة.

أول شاشة تعطي اختيارين:
- «محل جديد»: بيانات المحل ← حساب المدير ← كلمة مرور النسخ ← مفتاح الاسترداد ← مراجعة.
  عند الإنهاء تُنشأ البيانات في القاعدة وتُؤخذ أول نسخة مشفّرة.
- «استعادة من نسخة احتياطية»: اختيار ملف .mtb وكلمة المرور أو مفتاح الاسترداد.
  المعالج يتحقق من الملف والسر فقط، والاستبدال الفعلي يقوم به التطبيق بعد إغلاق القاعدة
  (انظر morv.app.main) لأن الملف لا يُستبدل وهو مفتوح.

مفتاح الاسترداد يظهر مرة واحدة، ولا يُحفظ في أي مكان، ولا يُكمل المعالج حتى يُعاد إدخاله كاملًا.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from morv import APP_NAME
from morv.core import security
from morv.infra import backup, settings, shop, users, vault
from morv.ui.widgets import Notice, PasswordEdit, form, ltr_edit, muted, primary_button

STEPS_NEW = ["البداية", "بيانات المحل", "حساب المدير", "كلمة مرور النسخ", "مفتاح الاسترداد", "مراجعة وإنهاء"]
STEPS_RESTORE = ["البداية", "ملف النسخة"]


class SetupWizard(QDialog):
    """نافذة المعالج. بعد exec(): result_kind يكون 'new' أو 'restore'، وعند الاستعادة
    restore_path و restore_secret جاهزان للتطبيق."""

    def __init__(self, con, parent=None):
        """بناء الخطوات."""
        super().__init__(parent)
        self.con = con
        self.setWindowTitle(f"{APP_NAME} — الإعداد لأول مرة")
        self.setMinimumSize(900, 600)
        self.result_kind = None
        self.restore_path = None
        self.restore_secret = None
        self.mode = "new"
        self.recovery_key = security.new_recovery_key()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.side = QWidget()
        self.side.setObjectName("wizSide")
        self.side.setFixedWidth(240)
        self.side_lay = QVBoxLayout(self.side)
        self.side_lay.setContentsMargins(16, 20, 16, 20)
        brand = QLabel(APP_NAME)
        brand.setObjectName("wizBrand")
        self.side_lay.addWidget(brand)
        self.side_lay.addWidget(muted("إعداد البرنامج لأول مرة"))
        self.side_lay.addSpacing(12)
        self.step_labels: list[QLabel] = []
        self.side_lay.addStretch(1)
        root.addWidget(self.side)

        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(28, 24, 28, 20)
        col.setSpacing(14)
        self.stack = QStackedWidget()
        col.addWidget(self.stack, 1)
        self.notice = Notice()
        col.addWidget(self.notice)
        foot = QHBoxLayout()
        self.back_btn = QPushButton("السابق")
        self.back_btn.clicked.connect(self.back)
        self.next_btn = primary_button("التالي")
        self.next_btn.clicked.connect(self.next)
        self.step_text = muted("")
        foot.addWidget(self.back_btn)
        foot.addWidget(self.step_text, 1)
        foot.addWidget(self.next_btn)
        col.addLayout(foot)
        root.addWidget(main, 1)

        self.pages = {
            "choice": self._page_choice(), "shop": self._page_shop(), "admin": self._page_admin(),
            "backup": self._page_backup(), "key": self._page_key(), "review": self._page_review(),
            "restore": self._page_restore(),
        }
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.order = ["choice"]
        self.index = 0
        self._set_mode("new")
        self._show()

    # ---------- الخطوات ----------
    def _set_mode(self, mode: str) -> None:
        """تحديد المسار (جديد أو استعادة) وإعادة بناء قائمة الخطوات في الجانب."""
        self.mode = mode
        self.order = ["choice", "shop", "admin", "backup", "key", "review"] if mode == "new" else ["choice", "restore"]
        names = STEPS_NEW if mode == "new" else STEPS_RESTORE
        for lbl in self.step_labels:
            self.side_lay.removeWidget(lbl)
            lbl.deleteLater()
        self.step_labels = []
        for i, name in enumerate(names):
            lbl = QLabel(f"{i + 1}. {name}")
            self.side_lay.insertWidget(3 + i, lbl)
            self.step_labels.append(lbl)

    def _show(self) -> None:
        """عرض الخطوة الحالية وتحديث الجانب والأزرار."""
        key = self.order[self.index]
        self.stack.setCurrentWidget(self.pages[key])
        for i, lbl in enumerate(self.step_labels):
            lbl.setObjectName("wizStepDone" if i < self.index else "wizStepCur" if i == self.index else "wizStep")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        self.back_btn.setVisible(self.index > 0)
        self.step_text.setText(f"الخطوة {self.index + 1} من {len(self.order)}")
        last = self.index == len(self.order) - 1
        self.next_btn.setText("إنهاء الإعداد وبدء العمل" if (last and self.mode == "new")
                              else "استعادة" if last else "التالي")
        self.notice.show_message("")
        if key == "review":
            self._fill_review()
        if key == "key":
            self.key_box.setText(self.recovery_key)

    def back(self) -> None:
        """خطوة للخلف."""
        if self.index > 0:
            self.index -= 1
            self._show()

    def next(self) -> None:
        """التحقق من الخطوة الحالية ثم التقدم، أو الإنهاء في آخر خطوة."""
        key = self.order[self.index]
        error = getattr(self, f"_validate_{key}")()
        if error:
            self.notice.show_message(error)
            return
        if self.index == len(self.order) - 1:
            self._finish()
            return
        self.index += 1
        self._show()

    # ---------- الصفحات ----------
    def _page_choice(self) -> QWidget:
        """الاختيار بين محل جديد واستعادة."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        h = QLabel("مرحبًا بك في Morv Tires")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("هل هذا محل جديد على البرنامج، أم تستعيد بيانات محفوظة من جهاز سابق؟"))
        self.btn_new = QPushButton("محل جديد — أبدأ الإعداد من الصفر")
        self.btn_restore = QPushButton("استعادة من نسخة احتياطية — من فلاشة أو جهاز سابق")
        for b, m in ((self.btn_new, "new"), (self.btn_restore, "restore")):
            b.setCheckable(True)
            b.setObjectName("choice")           # المختار يتلون من theme.py
            b.setMinimumHeight(56)
            b.clicked.connect(lambda _=False, mode=m: self._pick_mode(mode))
            lay.addWidget(b)
        self.btn_new.setChecked(True)
        lay.addStretch(1)
        return w

    def _pick_mode(self, mode: str) -> None:
        """اختيار المسار."""
        self.btn_new.setChecked(mode == "new")
        self.btn_restore.setChecked(mode == "restore")
        self._set_mode(mode)
        self._show()

    def _validate_choice(self) -> str | None:
        """لا شيء يُتحقق منه."""
        return None

    def _page_shop(self) -> QWidget:
        """بيانات المحل."""
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("بيانات المحل")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("تظهر في ترويسة الفواتير والإيصالات. يمكن تعديلها لاحقًا من الإعدادات."))
        f = form()
        self.shop_name = QLineEdit()
        self.shop_name.setPlaceholderText("مثال: مورف للإطارات والبطاريات")
        self.shop_phone = ltr_edit("01xxxxxxxxx")
        self.shop_addr = QLineEdit()
        self.shop_addr.setPlaceholderText("المدينة — الشارع")
        f.addRow("اسم المحل *", self.shop_name)
        f.addRow("رقم الهاتف *", self.shop_phone)
        f.addRow("العنوان", self.shop_addr)
        lay.addLayout(f)
        lay.addStretch(1)
        return w

    def _validate_shop(self) -> str | None:
        """فحص اسم المحل والهاتف."""
        try:
            shop.validate(self.shop_name.text(), self.shop_phone.text())
        except shop.ShopError as e:
            return str(e)
        return None

    def _page_admin(self) -> QWidget:
        """حساب المدير."""
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("حساب المدير")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("المدير يملك كل الصلاحيات، ومنه تُنشأ حسابات الكاشير لاحقًا. لا تشارك كلمة المرور."))
        f = form()
        self.admin_name = QLineEdit()
        self.admin_name.setPlaceholderText("الاسم الذي يظهر على الفواتير")
        self.admin_user = ltr_edit("admin")
        self.admin_user.setText("admin")
        self.admin_pw = PasswordEdit()
        self.admin_pw2 = PasswordEdit()
        self.admin_strength = muted("8 أحرف على الأقل، ويُفضّل خلط حروف وأرقام")
        self.admin_pw.edit.textChanged.connect(self._strength)
        f.addRow("اسمك *", self.admin_name)
        f.addRow("اسم الدخول *", self.admin_user)
        f.addRow("كلمة المرور *", self.admin_pw)
        f.addRow("", self.admin_strength)
        f.addRow("تأكيد كلمة المرور *", self.admin_pw2)
        lay.addLayout(f)
        lay.addStretch(1)
        return w

    def _strength(self, text: str) -> None:
        """عرض قوة كلمة المرور أثناء الكتابة."""
        s = security.password_strength(text)
        self.admin_strength.setText(["", "ضعيفة", "متوسطة", "قوية", "قوية جدًا"][s] if text else
                                    "8 أحرف على الأقل، ويُفضّل خلط حروف وأرقام")

    def _validate_admin(self) -> str | None:
        """فحص حساب المدير."""
        if not self.admin_name.text().strip():
            return "الاسم مطلوب"
        try:
            users._check_username(self.admin_user.text())
        except users.AuthError as e:
            return str(e)
        if len(self.admin_pw.text()) < security.MIN_LOGIN_PASSWORD:
            return f"كلمة المرور {security.MIN_LOGIN_PASSWORD} أحرف على الأقل"
        if self.admin_pw.text() != self.admin_pw2.text():
            return "كلمتا المرور غير متطابقتين"
        return None

    def _page_backup(self) -> QWidget:
        """كلمة مرور النسخ ومجلد الفلاشة."""
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("كلمة مرور النسخ الاحتياطي")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("كل النسخ الاحتياطية مشفّرة، على الجهاز وعلى الفلاشة. هذه الكلمة تفتحها على أي جهاز، "
                            "وهي غير كلمة مرور الدخول. 12 حرفًا على الأقل، فيها حروف وأرقام."))
        f = form()
        self.bk_pw = PasswordEdit()
        self.bk_pw2 = PasswordEdit()
        usb_row = QHBoxLayout()
        self.usb_dir = ltr_edit("اختياري: مجلد على فلاشة USB")
        pick = QPushButton("اختيار")
        pick.clicked.connect(self._pick_usb)
        usb_row.addWidget(self.usb_dir, 1)
        usb_row.addWidget(pick)
        f.addRow("كلمة مرور النسخ *", self.bk_pw)
        f.addRow("تأكيدها *", self.bk_pw2)
        f.addRow("مجلد الفلاشة", usb_row)
        lay.addLayout(f)
        lay.addWidget(muted("النسخ اليومي تلقائي على الجهاز. الفلاشة تُنسخ إليها عند الطلب وعند إغلاق البرنامج إن كانت موصولة، "
                            "ويذكّرك البرنامج إن مرّ أسبوع بلا نسخة عليها."))
        lay.addStretch(1)
        return w

    def _pick_usb(self) -> None:
        """اختيار مجلد الفلاشة."""
        d = QFileDialog.getExistingDirectory(self, "مجلد النسخ على الفلاشة")
        if d:
            self.usb_dir.setText(d)

    def _validate_backup(self) -> str | None:
        """فحص كلمة مرور النسخ."""
        err = security.check_backup_password(self.bk_pw.text())
        if err:
            return err
        if self.bk_pw.text() != self.bk_pw2.text():
            return "كلمتا المرور غير متطابقتين"
        if self.bk_pw.text() == self.admin_pw.text():
            return "اجعل كلمة مرور النسخ مختلفة عن كلمة مرور الدخول"
        return None

    def _page_key(self) -> QWidget:
        """عرض مفتاح الاسترداد وإعادة إدخاله."""
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("مفتاح الاسترداد")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("هذا المفتاح يفتح النسخ الاحتياطية لو نسيت كلمة مرور النسخ أو تعطّل الجهاز. "
                            "يظهر مرة واحدة فقط ولا يُحفظ في أي مكان."))
        self.key_box = QLabel()
        self.key_box.setObjectName("keybox")
        self.key_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.key_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.key_box)
        warn = Notice()
        warn.show_message("اكتبه بخط يدك وضعه في مظروف مغلق في مكان آمن خارج المحل، بعيدًا عن الفلاشة. "
                          "لو ضاع المفتاح ونُسيت كلمة المرور معًا، لا يمكن لأحد — ولا نحن — فتح النسخ.", "warn")
        lay.addWidget(warn)
        f = form()
        self.key_confirm = ltr_edit("أعد كتابة المفتاح كاملًا للتأكيد")
        f.addRow("للتأكيد، اكتب المفتاح *", self.key_confirm)
        lay.addLayout(f)
        lay.addStretch(1)
        return w

    def _validate_key(self) -> str | None:
        """المفتاح المُعاد إدخاله يجب أن يطابق المعروض."""
        try:
            if security.normalize_recovery_key(self.key_confirm.text()) != self.recovery_key:
                return "المفتاح غير مطابق — راجع ما كتبته"
        except ValueError as e:
            return str(e)
        return None

    def _page_review(self) -> QWidget:
        """المراجعة قبل الإنهاء."""
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("مراجعة وإنهاء")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("راجع البيانات. يمكن تعديل كل شيء لاحقًا من الإعدادات، ما عدا مفتاح الاسترداد الذي لا يُعرض مرة أخرى."))
        self.review = QLabel()
        self.review.setWordWrap(True)
        self.review.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.review)
        lay.addStretch(1)
        return w

    def _fill_review(self) -> None:
        """ملء جدول المراجعة من الحقول."""
        rows = [("اسم المحل", self.shop_name.text()), ("الهاتف", self.shop_phone.text()),
                ("المدير", f"{self.admin_name.text()} · {self.admin_user.text()}"),
                ("النسخ الاحتياطي", "يومي، مشفّر" + (f" + فلاشة: {self.usb_dir.text()}" if self.usb_dir.text() else " — بلا فلاشة")),
                ("مفتاح الاسترداد", "تم التأكيد")]
        self.review.setText("<table cellpadding='6'>" + "".join(
            f"<tr><td style='color:#888'>{k}</td><td><b>{v}</b></td></tr>" for k, v in rows) + "</table>")

    def _validate_review(self) -> str | None:
        """لا شيء."""
        return None

    def _page_restore(self) -> QWidget:
        """اختيار ملف النسخة والسر."""
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("استعادة من نسخة احتياطية")
        h.setObjectName("pageTitle")
        lay.addWidget(h)
        lay.addWidget(muted("اختر ملف النسخة (.mtb) من الفلاشة، ثم اكتب كلمة مرور النسخ أو مفتاح الاسترداد. "
                            "ستعود البيانات كما كانت وقت أخذ النسخة."))
        f = form()
        row = QHBoxLayout()
        self.restore_file = ltr_edit("")
        self.restore_file.setReadOnly(True)
        pick = QPushButton("اختيار الملف")
        pick.clicked.connect(self._pick_restore)
        row.addWidget(self.restore_file, 1)
        row.addWidget(pick)
        self.restore_info = muted("")
        self.restore_pw = PasswordEdit("كلمة مرور النسخ أو مفتاح الاسترداد")
        f.addRow("ملف النسخة *", row)
        f.addRow("", self.restore_info)
        f.addRow("كلمة المرور أو المفتاح *", self.restore_pw)
        lay.addLayout(f)
        lay.addStretch(1)
        return w

    def _pick_restore(self) -> None:
        """اختيار ملف .mtb وعرض تاريخه."""
        p, _ = QFileDialog.getOpenFileName(self, "ملف النسخة الاحتياطية", "", "نسخة Morv Tires (*.mtb)")
        if p:
            self.set_restore_file(p)

    def set_restore_file(self, path: str) -> None:
        """تعيين ملف الاستعادة (من الحوار أو من الاختبارات) وقراءة رأسه."""
        self.restore_file.setText(path)
        try:
            hdr = vault.read_header(path)
            self.restore_info.setText(f"نسخة من {hdr.get('created_at', '?')} · {hdr.get('db_detail', '')}")
        except (OSError, vault.VaultError) as e:
            self.restore_info.setText(str(e))

    def _validate_restore(self) -> str | None:
        """التحقق أن الملف نسخة صحيحة وأن السر يفتحها، دون كتابة أي شيء."""
        p = self.restore_file.text()
        if not p or not Path(p).exists():
            return "اختر ملف النسخة"
        try:
            hdr = vault.read_header(p)
            vault.unlock(hdr["keyring"], self.restore_pw.text())
        except (OSError, KeyError, vault.VaultError) as e:
            return str(e) if str(e) else "الملف ليس نسخة صالحة"
        return None

    # ---------- الإنهاء ----------
    def _finish(self) -> None:
        """تنفيذ الإعداد: محل جديد يُكتب في القاعدة الآن؛ الاستعادة تُترك للتطبيق."""
        if self.mode == "restore":
            self.result_kind = "restore"
            self.restore_path = self.restore_file.text()
            self.restore_secret = self.restore_pw.text()
            self.accept()
            return
        try:
            shop.save(self.con, self.shop_name.text(), self.shop_phone.text(), self.shop_addr.text())
            admin = users.create_user(self.con, None, self.admin_user.text(), self.admin_name.text(),
                                      self.admin_pw.text(), "manager")
            backup.setup_encryption(self.con, self.bk_pw.text(), self.recovery_key)
            if self.usb_dir.text().strip():
                settings.put(self.con, "backup.usb_dir", self.usb_dir.text().strip())
            users.audit(self.con, admin, "setup.done", None, None, "اكتمل الإعداد الأول")
            backup.create_backup(self.con, "manual", username=admin.username)
        except (users.AuthError, shop.ShopError, backup.BackupError) as e:
            self.notice.show_message(str(e))
            return
        self.result_kind = "new"
        self.accept()
