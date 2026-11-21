"""Offscreen phase 1 screens: setup wizard, login, settings, backup notice."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt

PW_LOGIN = "Admin12345"
PW_BACKUP = "Morv-Backup-2026"
LONG_LOGIN = "OwnerLogin2026xx"   # 16 characters, to test that the backup password may not equal the login one


@require_qt()
class _Base(unittest.TestCase):
    def setUp(self):
        from morv.infra.db.bootstrap import open_main_db
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.app = get_app()

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_admin(self):
        from morv.core import security
        from morv.infra import backup, shop, users
        self.admin = users.create_user(self.con, None, "admin", "المدير", PW_LOGIN, "manager")
        shop.save(self.con, self.admin, "مورف للإطارات", "01000000000", "")
        self.rk = security.new_recovery_key()
        backup.setup_encryption(self.con, PW_BACKUP, self.rk)
        return self.admin

    def session(self, user=None):
        from morv.ui.session import Session
        return Session(self.con, user or self.admin)


class WizardTest(_Base):
    def test_new_shop_full_flow(self):
        from morv.infra import backup, users
        from morv.ui.setup_wizard import SetupWizard
        w = SetupWizard(self.con)
        self.assertEqual(w.order[w.index], "choice")
        w.next()
        self.assertEqual(w.order[w.index], "shop")
        w.next()                                   # incomplete
        self.assertIn("اسم المحل", w.notice.text())
        w.shop_name.setText("مورف للإطارات")
        w.shop_phone.setText("٠١٠٠٠٠٠٠٠٠٠")         # Arabic digits are accepted
        w.next()
        self.assertEqual(w.order[w.index], "admin")
        w.admin_name.setText("صاحب المحل")
        w.admin_pw.setText(LONG_LOGIN)
        w.admin_pw2.setText("different1")
        w.next()
        self.assertIn("غير متطابقتين", w.notice.text())
        w.admin_pw2.setText(LONG_LOGIN)
        w.next()
        self.assertEqual(w.order[w.index], "backup")
        w.bk_pw.setText("short1")
        w.bk_pw2.setText("short1")
        w.next()
        self.assertIn("12", w.notice.text())
        w.bk_pw.setText(LONG_LOGIN)                # the login password is refused here, and is long enough to pass the length check
        w.bk_pw2.setText(LONG_LOGIN)
        w.next()
        self.assertIn("مختلفة", w.notice.text())
        w.bk_pw.setText(PW_BACKUP)
        w.bk_pw2.setText(PW_BACKUP)
        w.next()
        self.assertEqual(w.order[w.index], "key")
        self.assertEqual(w.key_box.text(), w.recovery_key)
        w.key_confirm.setText("ABCD-EFGH")
        w.next()
        self.assertTrue(w.notice.text())
        w.key_confirm.setText(w.recovery_key.lower().replace("-", " "))
        w.next()
        self.assertEqual(w.order[w.index], "review")
        self.assertIn("مورف للإطارات", w.review.text())
        w.next()                                   # finish
        self.assertEqual(w.result_kind, "new")
        self.assertEqual(users.count_users(self.con), 1)
        self.assertTrue(backup.is_encryption_ready(self.con))
        self.assertEqual(len(backup.list_backups()), 1)
        self.assertEqual(users.login(self.con, "admin", LONG_LOGIN).role, "manager")

    def test_restore_branch_validates_secret(self):
        from morv.infra import backup, vault
        from morv.ui.setup_wizard import SetupWizard
        self.make_admin()
        src = backup.create_backup(self.con, "manual").path
        other = Path(tempfile.mkdtemp())
        try:
            os.environ["MORV_DATA_DIR"] = str(other)
            from morv.infra.db.bootstrap import open_main_db
            con2 = open_main_db(other / "morv_tires.db")
            w = SetupWizard(con2)
            w._pick_mode("restore")
            self.assertEqual(w.order, ["choice", "restore"])
            w.next()
            w.next()
            self.assertIn("اختر ملف", w.notice.text())
            w.set_restore_file(str(src))
            self.assertIn("نسخة من", w.restore_info.text())
            w.restore_pw.setText("wrong-secret-11")
            w.next()
            self.assertIn("غير صحيح", w.notice.text())
            w.restore_pw.setText(self.rk)
            w.next()
            self.assertEqual(w.result_kind, "restore")
            self.assertEqual(w.restore_path, str(src))
            con2.close()
            # the real swap, as the app does it
            backup.restore_file(other / "morv_tires.db", w.restore_path, secret=w.restore_secret)
            con2 = open_main_db(other / "morv_tires.db")
            from morv.infra import users
            self.assertEqual(users.login(con2, "admin", PW_LOGIN).display_name, "المدير")
            con2.close()
        finally:
            os.environ["MORV_DATA_DIR"] = str(self.tmp)
            shutil.rmtree(other, ignore_errors=True)
        del vault


class LoginTest(_Base):
    def test_login_dialog(self):
        from morv.infra import users
        from morv.ui.login import LoginDialog
        self.make_admin()
        k = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        users.set_active(self.con, self.admin, k.id, False)
        d = LoginDialog(self.con)
        self.assertEqual(d.combo.count(), 1)
        self.assertIn("المدير", d.combo.itemText(0))
        d.password.setText("wrong")
        d.try_login()
        self.assertIn("غير صحيحة", d.notice.text())
        self.assertIsNone(d.user)
        d.password.setText(PW_LOGIN)
        d.try_login()
        self.assertEqual(d.user.username, "admin")


def _ancestors(widget):
    while widget is not None:
        yield widget
        widget = widget.parentWidget()


class SettingsTest(_Base):
    def test_tabs_by_permission(self):
        from morv.infra import users
        from morv.ui.settings_page import SettingsPage
        self.make_admin()
        k = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        admin_page = SettingsPage(self.session())        # kept in a variable, or Qt takes it and its tabs away
        self.assertEqual(admin_page.tabs.count(), 9)     # shop, users, log, fields, partners, opening, appearance, shortcuts, backup
        self.assertIsNotNone(admin_page.fields_tab)
        p = SettingsPage(self.session(k))
        self.assertEqual(p.tabs.count(), 3)               # appearance, shortcuts and backup only
        self.assertIsNone(p.shop_tab)                     # never sees shop data at all
        self.assertFalse(p.backup_tab.restore_btn.isVisibleTo(p))
        self.assertFalse(p.backup_tab.pw_btn.isVisibleTo(p))      # the backup password is for a manager only
        self.assertFalse(p.backup_tab.usb_dir.isEnabled())
        self.assertTrue(p.backup_tab.backup_btn.isEnabled())

    def test_permissions_scroll_so_the_action_buttons_stay_visible(self):
        from PySide6.QtWidgets import QScrollArea
        from morv.ui.settings_page import SettingsPage
        self.make_admin()
        page = SettingsPage(self.session())          # kept alive, or Qt deletes the tabs
        tab = page.users_tab
        box = next(iter(tab.checks.values())).parentWidget()
        self.assertTrue(any(isinstance(w, QScrollArea) for w in _ancestors(box)))
        self.assertFalse(any(isinstance(w, QScrollArea) for w in _ancestors(tab.pw_btn)))

    def test_users_tab_actions(self):
        from morv.infra import users
        from morv.ui.settings_page import NewUserDialog, SettingsPage
        self.make_admin()
        page = SettingsPage(self.session())
        tab = page.users_tab
        dlg = NewUserDialog(self.session())
        dlg.name.setText("كريم")
        dlg.username.setText("karim")
        dlg.password.setText("Karim12345")
        dlg.save()
        self.assertIsNotNone(dlg.created)
        tab.reload(dlg.created.id)
        self.assertEqual(tab.current.username, "karim")
        tab.checks["backup.restore"].setChecked(True)
        self.assertTrue(users.has_permission(self.con, dlg.created, "backup.restore"))
        tab.reload(self.admin.id)
        tab.role.setCurrentIndex(1)               # turning the last manager into a cashier
        self.assertIn("مدير واحد", tab.notice.text())
        self.assertEqual(users.get_user(self.con, self.admin.id).role, "manager")

    def test_backup_tab(self):
        from morv.ui.settings_page import SettingsPage
        self.make_admin()
        page = SettingsPage(self.session())
        tab = page.backup_tab
        self.assertEqual(tab.model.rowCount(), 0)
        tab.backup_now()
        self.assertEqual(tab.model.rowCount(), 1)
        self.assertIn("تمت النسخة", tab.notice.text())
        # selectRow on a hidden RTL table picks a padding-width column and fails; select through the model
        from PySide6.QtCore import QItemSelectionModel
        flags = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        tab.view.selectionModel().setCurrentIndex(tab.model.index(0, 0), flags)
        tab.test_selected()
        self.assertIn("نجح", tab.notice.text())
        self.assertNotEqual(tab.stat_test[1].text(), "لم يُجرَ بعد")

    def test_shop_tab_save(self):
        from morv.infra import shop
        from morv.ui.settings_page import SettingsPage
        self.make_admin()
        page = SettingsPage(self.session())
        tab = page.shop_tab
        tab.phone.setText("123")
        tab.save()
        self.assertIn("الهاتف", tab.notice.text())
        tab.phone.setText("01111111111")
        tab.address.setText("0")
        tab.save()
        self.assertIn("العنوان", tab.notice.text())
        tab.address.setText("")
        tab.save()
        self.assertEqual(shop.load(self.con).phone, "01111111111")


class AppearanceTest(_Base):
    def test_font_applies_and_persists(self):
        from morv.infra import settings
        from morv.ui import theme
        from morv.ui.shell import MainWindow
        self.make_admin()
        self.assertIn("IBM Plex Sans Arabic", theme.available_fonts())
        self.assertIn("Cairo", theme.available_fonts())
        w = MainWindow(self.session(), self.app)
        w.health_timer.stop()
        from morv.ui.widgets import dispose
        self.addCleanup(dispose, w, self.app)
        w.set_font("Cairo", 16)
        self.assertEqual(self.app.font().pixelSize(), 16)
        self.assertEqual(self.app.font().family(), "Cairo")
        self.assertEqual(settings.get(self.con, "ui.font_size"), "16")
        w2 = MainWindow(self.session(), self.app)
        w2.health_timer.stop()
        from morv.ui.widgets import dispose
        self.addCleanup(dispose, w2, self.app)
        self.assertEqual(self.app.font().family(), "Cairo")
        w.set_font(theme.DEFAULT_FONT, theme.DEFAULT_SIZE)


class ShellHealthTest(_Base):
    def test_pill_and_open_backup(self):
        from morv.infra import backup
        from morv.ui.shell import MainWindow
        self.make_admin()
        w = MainWindow(self.session(), self.app)
        w.health_timer.stop()
        from morv.ui.widgets import dispose
        self.addCleanup(dispose, w, self.app)
        self.assertEqual(w.health_pill.objectName(), "pillDanger")
        backup.run_scheduled(self.con)
        w.refresh_health()
        self.assertEqual(w.health_pill.objectName(), "pillOk")
        w.open_backup()
        self.assertEqual(w.title.text(), "الإعدادات")
        page = w.pages["settings"]
        self.assertIs(page.tabs.currentWidget(), page.backup_tab)


if __name__ == "__main__":
    unittest.main()
