"""Backup settings preserve access, selection and visible table space across layouts."""
import unittest

from tests.qt_app import require_qt
from tests import test_ui_settings_hub


@require_qt()
class BackupLayoutTest(unittest.TestCase):
    setUp = test_ui_settings_hub.SettingsHubTest.setUp
    settle = test_ui_settings_hub.SettingsHubTest.settle

    def test_backup_layout_and_permissions(self):
        from morv.core import security
        from morv.infra import backup, users
        from morv.ui.session import Session
        from morv.ui.settings_page import BackupTab
        from morv.ui.widgets import dispose

        backup.setup_encryption(self.con, "TestBackup123", security.new_recovery_key())
        backup.create_backup(self.con, "manual")
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cashier12345", "cashier")
        for user in (self.admin, cashier):
            tab = BackupTab(Session(self.con, user))
            self.addCleanup(dispose, tab, self.app)
            tab.show()
            for width, expected in ((1074, 2), (801, 1), (1628, 2)):
                tab.resize(width, 482)
                self.settle()
                self.assertEqual(tab.panes.columns, expected)
                self.assertGreaterEqual(tab.view.height(), 144)
                self.assertEqual(tab.view.horizontalScrollBar().maximum(), 0)
                self.assertEqual(tab.panes.horizontalScrollBar().maximum(), 0)
                self.assertTrue(tab.backup_btn.isVisible())
                self.assertEqual(tab.restore_btn.isVisible(), user.role == "manager")
                self.assertEqual(tab.pw_btn.isVisible(), user.role == "manager")
                self.assertEqual(tab.rk_btn.isVisible(), user.role == "manager")
                self.assertEqual(tab.usb_dir.isEnabled(), user.role == "manager")
                tab.view.selectRow(0)
                self.assertEqual(tab._selected().path, tab.model.items[0].path)
            tab.hide()

    def test_backup_and_test_refresh_the_real_model(self):
        from morv.core import security
        from morv.infra import backup, settings
        from morv.ui.settings_page import BackupTab
        from morv.ui.widgets import dispose

        backup.setup_encryption(self.con, "TestBackup123", security.new_recovery_key())
        tab = BackupTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        self.assertEqual(tab.model.rowCount(), 0)
        tab.backup_now()
        self.assertEqual(tab.model.rowCount(), 1)
        self.assertIn("1 نسخة محفوظة", tab.stat_local[2].text())
        tab.view.selectRow(0)
        tab.test_selected()
        self.assertTrue(settings.get(self.con, "backup.last_test_at", ""))
        self.assertIn("نجح الاختبار", tab.notice.text())
