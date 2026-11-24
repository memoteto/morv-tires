"""Backup settings preserve access, selection and visible table space across layouts."""
import unittest

from tests.qt_app import require_qt
from tests import test_ui_settings_hub


@require_qt()
class BackupLayoutTest(unittest.TestCase):
    settle = test_ui_settings_hub.SettingsHubTest.settle

    def setUp(self):
        from PySide6.QtCore import Qt
        test_ui_settings_hub.SettingsHubTest.setUp(self)
        self.addCleanup(self.app.setLayoutDirection, self.app.layoutDirection())
        self.app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    def choose_first_backup(self, tab):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        index = tab.model.index(0, 0)
        self.assertTrue(index.isValid())
        tab.view.clearSelection()
        tab.view.scrollTo(index)
        self.settle()
        point = tab.view.visualRect(index).center()
        self.assertTrue(tab.view.viewport().rect().contains(point))
        self.assertEqual(tab.view.indexAt(point), index)
        # selectRow() can fail in RTL even when clicking the rendered cell works.
        QTest.mouseClick(tab.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(tab.view.selectionModel().selectedRows(), [index])
        selected = tab._selected()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.path, tab.model.items[0].path)

    def test_backup_layout_and_permissions(self):
        from PySide6.QtCore import QPoint, QRect
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
            for width in (1074, 801, 1628):
                tab.resize(width, 482)
                self.settle()
                self.assertGreaterEqual(tab.view.height(), 144)
                self.assertGreaterEqual(tab.view.width(), tab.width() - 32)
                self.assertEqual(tab.view.horizontalScrollBar().maximum(), 0)
                for button in (tab.backup_btn, tab.usb_settings_btn, tab.security_settings_btn,
                               tab.test_btn, tab.usb_btn):
                    self.assertTrue(button.isVisible())
                    self.assertTrue(tab.rect().contains(QRect(button.mapTo(tab, QPoint()), button.size())))
                self.assertGreater(tab.test_btn.y(), tab.view.mapTo(tab, QPoint()).y())
                self.assertEqual(tab.restore_btn.isVisible(), user.role == "manager")
                if user.role == "manager":
                    self.assertLess(tab.restore_btn.x(), tab.usb_btn.x())
                self.assertFalse(tab.usb_dialog.isVisible())
                self.assertFalse(tab.security_dialog.isVisible())
                self.choose_first_backup(tab)
                tab.usb_settings_btn.click()
                self.settle()
                self.assertTrue(tab.usb_dialog.isVisible())
                self.assertTrue(tab.usb_dir.isVisible())
                self.assertEqual(tab.usb_dir.isEnabled(), user.role == "manager")
                self.assertEqual(tab.save_usb_btn.isEnabled(), user.role == "manager")
                tab.usb_dialog.reject()
                tab.security_settings_btn.click()
                self.settle()
                self.assertTrue(tab.security_dialog.isVisible())
                self.assertEqual(tab.pw_btn.isVisible(), user.role == "manager")
                self.assertEqual(tab.rk_btn.isVisible(), user.role == "manager")
                self.assertFalse(tab.relink_btn.isVisible())
                tab.security_dialog.reject()
                self.settle()
                self.assertEqual(tab._selected().path, tab.model.items[0].path)
            tab.hide()

    def test_usb_dialog_cancel_and_save(self):
        from pathlib import Path
        from unittest.mock import patch
        from morv.infra import settings
        from morv.ui.settings_page import BackupTab
        from morv.ui.widgets import dispose

        tab = BackupTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.show()
        tab.usb_settings_btn.click()
        folder = str(Path(self.tmp.name) / "test-usb")
        tab.usb_dir.setText(folder)
        tab.usb_dialog.reject()
        self.assertFalse(settings.get(self.con, "backup.usb_dir", ""))
        tab.usb_settings_btn.click()
        self.assertEqual(tab.usb_dir.text(), "")
        tab.usb_dir.setText(folder)
        with patch("morv.ui.settings_page.backup.same_drive_as_data", return_value=True), patch(
                "morv.ui.settings_page.ask", return_value=False):
            tab.save_usb_btn.click()
        self.assertTrue(tab.usb_dialog.isVisible())
        self.assertFalse(settings.get(self.con, "backup.usb_dir", ""))
        with patch("morv.ui.settings_page.backup.same_drive_as_data", return_value=True), patch(
                "morv.ui.settings_page.ask", return_value=True):
            tab.save_usb_btn.click()
        self.assertFalse(tab.usb_dialog.isVisible())
        self.assertEqual(settings.get(self.con, "backup.usb_dir"), folder)

    def test_backup_and_test_refresh_the_real_model(self):
        from morv.core import security
        from morv.infra import backup, settings
        from morv.ui.settings_page import BackupTab
        from morv.ui.widgets import dispose

        backup.setup_encryption(self.con, "TestBackup123", security.new_recovery_key())
        tab = BackupTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.resize(1074, 636)
        tab.show()
        self.settle()
        self.assertEqual(tab.model.rowCount(), 0)
        tab.backup_btn.click()
        self.assertEqual(tab.model.rowCount(), 1)
        self.assertIn("1 نسخة محفوظة", tab.stat_local[2].text())
        self.choose_first_backup(tab)
        tab.test_btn.click()
        self.assertTrue(settings.get(self.con, "backup.last_test_at", ""))
        self.assertIn("نجح الاختبار", tab.notice.text())
