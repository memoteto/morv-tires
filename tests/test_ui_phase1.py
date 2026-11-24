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
        shop.save(self.con, self.admin, shop.Shop(name="مورف للإطارات", phone="01000000000"))
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
        self.assertEqual(len(admin_page.destinations), 10)
        self.assertIsNotNone(admin_page.fields_tab)
        p = SettingsPage(self.session(k))
        self.assertEqual(len(p.destinations), 3)               # appearance, shortcuts and backup only
        self.assertIsNone(p.shop_tab)                     # never sees shop data at all
        self.assertFalse(p.open_page("shop"))
        self.assertIsNone(p.printing_tab)
        self.assertFalse(p.open_page("printing"))
        self.assertTrue(p.open_page("backup"))
        self.assertFalse(p.backup_tab.restore_btn.isVisibleTo(p))
        self.assertFalse(p.backup_tab.pw_btn.isVisibleTo(p))      # the backup password is for a manager only
        self.assertFalse(p.backup_tab.usb_dir.isEnabled())
        self.assertTrue(p.backup_tab.backup_btn.isEnabled())

    def test_users_tab_grouped_permissions_and_current_discount_display(self):
        from PySide6.QtWidgets import QLabel
        from morv.core import security
        from morv.infra import users
        from morv.ui.settings_page import SettingsPage
        self.make_admin()
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "sales.discount", True, 5)
        page = SettingsPage(self.session())
        tab = page.users_tab
        before = users.audit_rows(self.con)
        self.assertEqual(set(tab.checks), set(security.PERMISSIONS))
        self.assertEqual(len(tab.permission_grid.cards), 4)
        for key, check in tab.checks.items():
            self.assertEqual(check.text(), security.PERMISSIONS[key])
            self.assertEqual(sum(card.isAncestorOf(check) for card in tab.permission_grid.cards), 1)
        labels = [label.text() for label in tab.findChildren(QLabel)]
        self.assertFalse(any("كل عملية حساسة" in label for label in labels))
        self.assertFalse(any("المدير يملكها" in label for label in labels))
        tab.reload(self.admin.id)
        self.assertEqual(tab.discount_cap.text(), "غير محدود")
        self.assertFalse(tab.discount_cap.isEnabled())
        self.assertTrue(all(check.isChecked() and not check.isEnabled() for check in tab.checks.values()))
        tab.reload(cashier.id)
        self.assertEqual(tab.discount_cap.specialValueText(), "")
        self.assertEqual(tab.discount_cap.value(), 5)
        self.assertTrue(tab.discount_cap.isEnabled())
        self.assertTrue(tab.checks["sales.discount"].isChecked())
        self.assertEqual(users.audit_rows(self.con), before)

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

    def test_existing_backup_tab_refuses_usb_save_after_self_demotion(self):
        from unittest.mock import patch
        from morv.infra import settings, users
        from morv.ui.settings_page import BackupTab
        self.make_admin()
        users.create_user(self.con, self.admin, "boss2", "Second manager", "Boss12345", "manager")
        tab = BackupTab(self.session())
        self.assertTrue(tab.usb_dir.isEnabled())
        before = settings.get(self.con, "backup.usb_dir")
        users.set_role(self.con, self.admin, self.admin.id, "cashier")
        self.assertFalse(users.is_active_manager(self.con, self.admin))
        audit_before = users.audit_rows(self.con)
        tab.usb_dir.setText(str(self.tmp / "fake-usb"))
        with patch("morv.ui.settings_page.backup.same_drive_as_data", return_value=False):
            tab.save_usb()
        self.assertEqual(settings.get(self.con, "backup.usb_dir"), before)
        self.assertEqual(users.audit_rows(self.con), audit_before)

    def test_existing_backup_tab_refuses_key_actions_after_demotion(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QDialog
        from morv.infra import users
        from morv.ui.settings_page import BackupTab
        self.make_admin()
        second = users.create_user(self.con, self.admin, "boss2", "Second manager", "Boss12345", "manager")
        tab = BackupTab(self.session())
        users.set_role(self.con, second, self.admin.id, "cashier")
        audit_before = users.audit_rows(self.con)
        for action, target in ((tab.change_password, "change_backup_password"),
                               (tab.new_recovery_key, "regenerate_recovery_key")):
            with (
                self.subTest(action=target),
                patch("morv.ui.settings_page.SecretDialog") as dialog,
                patch("morv.ui.settings_page.ShowKeyDialog"),
                patch("morv.ui.settings_page.backup." + target) as operation,
            ):
                dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
                dialog.return_value.secret = PW_BACKUP
                dialog.return_value.new_secret = "FakeNewSecret2026"
                action()
                operation.assert_not_called()
        self.assertEqual(users.audit_rows(self.con), audit_before)

    def test_existing_backup_tab_refuses_revoked_restore(self):
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from PySide6.QtWidgets import QDialog
        from morv.infra import users
        from morv.ui.settings_page import BackupTab
        self.make_admin()
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "backup.restore", True)
        session = self.session(cashier)
        session.on_restore = Mock()
        tab = BackupTab(session)
        fake = SimpleNamespace(path=self.tmp / "never-restore.morvbak", at=datetime.now())
        users.set_permission(self.con, self.admin, cashier.id, "backup.restore", False)
        self.assertFalse(session.can("backup.restore"))
        audit_before = users.audit_rows(self.con)
        with patch.object(tab, "_selected", return_value=fake), patch(
            "morv.ui.settings_page.RestoreConfirmDialog"
        ) as dialog:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.secret = None
            tab.restore_selected()
        session.on_restore.assert_not_called()
        self.assertEqual(users.audit_rows(self.con), audit_before)

    def test_backup_restore_rechecks_permission_after_confirmation(self):
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from PySide6.QtWidgets import QDialog
        from morv.infra import users
        from morv.ui.settings_page import BackupTab
        self.make_admin()
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "backup.restore", True)
        session = self.session(cashier)
        session.on_restore = Mock()
        tab = BackupTab(session)
        fake = SimpleNamespace(path=self.tmp / "never-restore.morvbak", at=datetime.now())

        def confirm_and_revoke():
            users.set_permission(self.con, self.admin, cashier.id, "backup.restore", False)
            return QDialog.DialogCode.Accepted

        with patch.object(tab, "_selected", return_value=fake), patch(
            "morv.ui.settings_page.RestoreConfirmDialog"
        ) as dialog:
            dialog.return_value.secret = None
            dialog.return_value.exec.side_effect = confirm_and_revoke
            tab.restore_selected()
        self.assertFalse(session.can("backup.restore"))
        session.on_restore.assert_not_called()

    def test_backup_restore_keeps_delegated_permission(self):
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from PySide6.QtWidgets import QDialog
        from morv.infra import users
        from morv.ui.settings_page import BackupTab
        self.make_admin()
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "backup.restore", True)
        session = self.session(cashier)
        session.on_restore = Mock()
        tab = BackupTab(session)
        fake = SimpleNamespace(path=self.tmp / "never-restore.morvbak", at=datetime.now())
        with patch.object(tab, "_selected", return_value=fake), patch(
            "morv.ui.settings_page.RestoreConfirmDialog"
        ) as dialog:
            dialog.return_value.secret = None
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            tab.restore_selected()
        session.on_restore.assert_called_once_with(str(fake.path), None)

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
        # A local copy alone leaves the shop one disk failure away from losing everything
        self.assertEqual(w.health_pill.objectName(), "pillWarn")
        self.assertIn("خارج الجهاز", w.health_pill.toolTip())
        w.open_backup()
        self.assertEqual(w.title.text(), "الإعدادات")
        page = w.pages["settings"]
        self.assertEqual(page.current_key, "backup")
        self.assertIs(page.stack.currentWidget(), page.backup_tab)


if __name__ == "__main__":
    unittest.main()
