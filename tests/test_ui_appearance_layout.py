"""Appearance preview and shortcut cards preserve RTL geometry and saved preferences."""
import unittest

from tests.qt_app import require_qt
from tests import test_ui_settings_hub


@require_qt()
class AppearanceLayoutTest(unittest.TestCase):
    settle = test_ui_settings_hub.SettingsHubTest.settle

    def setUp(self):
        from PySide6.QtCore import Qt
        test_ui_settings_hub.SettingsHubTest.setUp(self)
        self.addCleanup(self.app.setLayoutDirection, self.app.layoutDirection())
        self.addCleanup(self.app.setFont, self.app.font())
        self.addCleanup(self.app.setPalette, self.app.palette())
        self.addCleanup(self.app.setStyleSheet, self.app.styleSheet())
        self.app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    def test_preview_layout_and_unsaved_font(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QLabel
        from morv.infra import settings
        from morv.ui import theme
        from morv.ui.settings_page import AppearanceTab
        from morv.ui.widgets import dispose

        theme.apply(self.app, False, theme.DEFAULT_FONT, theme.DEFAULT_SIZE)
        tab = AppearanceTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.show()
        self.assertFalse(any(label.text().startswith("الخطان") for label in tab.findChildren(QLabel)))
        for width, columns in ((1074, 2), (600, 1), (1628, 2)):
            tab.resize(width, 550)
            self.settle()
            self.assertEqual(tab.panes.columns, columns)
            options = tab.options.mapTo(tab, QPoint())
            preview = tab.preview.mapTo(tab, QPoint())
            if columns == 2:
                self.assertGreaterEqual(options.x(), preview.x() + tab.preview.width())
            else:
                self.assertLess(options.y(), preview.y())
            self.assertEqual(tab.panes.horizontalScrollBar().maximum(), 0)
        before = settings.get(self.con, "ui.font_family", theme.DEFAULT_FONT)
        old_size = settings.get(self.con, "ui.font_size", str(theme.DEFAULT_SIZE))
        tab.family.setCurrentText("Cairo")
        tab.size.setCurrentIndex(tab.size.findData(18))
        self.settle()
        for widget in (tab.sample, tab.sample_input, tab.sample_table):
            self.assertEqual(widget.font().family(), "Cairo")
            self.assertEqual(widget.font().pixelSize(), 18)
        self.assertEqual(self.app.font().pixelSize(), theme.DEFAULT_SIZE)
        self.assertEqual(settings.get(self.con, "ui.font_family", theme.DEFAULT_FONT), before)
        self.assertEqual(settings.get(self.con, "ui.font_size", str(theme.DEFAULT_SIZE)), old_size)

    def test_apply_and_theme_keep_preview(self):
        from morv.infra import settings
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose

        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.show()
        win.go_key("settings")
        page = win.pages["settings"]
        self.assertTrue(page.open_page("appearance"))
        tab = page.appearance_tab
        tab.family.setCurrentText("Cairo")
        tab.size.setCurrentIndex(tab.size.findData(16))
        tab.apply_btn.click()
        self.assertEqual(settings.get(self.con, "ui.font_family"), "Cairo")
        self.assertEqual(settings.get(self.con, "ui.font_size"), "16")
        self.assertEqual(self.app.font().pixelSize(), 16)
        tab.family.setCurrentText("IBM Plex Sans Arabic")
        tab.size.setCurrentIndex(tab.size.findData(18))
        self.settle()
        self.assertEqual(tab.sample.font().pixelSize(), 18)
        for _ in range(2):
            win.toggle_theme()
            self.settle()
            self.assertEqual(tab.family.currentText(), "IBM Plex Sans Arabic")
            self.assertEqual(tab.size.currentData(), 18)
            for widget in (tab.sample, tab.sample_input, tab.sample_table):
                self.assertEqual(widget.font().pixelSize(), 18)
                self.assertEqual(widget.font().family(), "IBM Plex Sans Arabic")
            self.assertEqual(self.app.font().pixelSize(), 16)
            self.assertEqual(self.app.font().family(), "Cairo")
            self.assertEqual(settings.get(self.con, "ui.font_size"), "16")
            self.assertEqual(settings.get(self.con, "ui.font_family"), "Cairo")

    def test_shortcuts_reflow_without_changing_keys(self):
        from morv.ui.settings_page import SHORTCUTS, ShortcutsTab
        from morv.ui.widgets import dispose

        tab = ShortcutsTab()
        self.addCleanup(dispose, tab, self.app)
        tab.show()
        for width, columns in ((1628, 3), (800, 2), (450, 1)):
            tab.resize(width, 550)
            self.settle()
            self.assertEqual(tab.cards.columns, columns)
            self.assertEqual(tab.cards.horizontalScrollBar().maximum(), 0)
            self.assertEqual(set(tab.keys), {key for key, _ in SHORTCUTS})
