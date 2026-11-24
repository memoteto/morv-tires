"""Settings routes and adaptive cards preserve permissions, state and geometry."""
import os
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class SettingsHubTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_dir = os.environ.get("MORV_DATA_DIR")
        os.environ["MORV_DATA_DIR"] = self.tmp.name
        if old_dir is None:
            self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        else:
            self.addCleanup(os.environ.__setitem__, "MORV_DATA_DIR", old_dir)
        self.con = open_main_db(Path(self.tmp.name) / "audit.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "Admin", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()

    def settle(self):
        for _ in range(8):
            self.app.processEvents()

    def test_routes_and_unsaved_state(self):
        from morv.ui.settings_page import SettingsPage
        from morv.ui.widgets import dispose
        page = SettingsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        page.resize(1074, 636)
        page.show()
        self.settle()
        self.assertIsNone(page.current_key)
        self.assertEqual(len(page.hub.cards), 4)
        self.assertEqual(set(page.destinations), set(page.links))
        self.assertEqual(len(page.links), 9)
        page.links["shop"].click()
        self.assertEqual(page.current_key, "shop")
        page.shop_tab.name.setText("Unsaved example")
        page.back.click()
        self.assertIsNone(page.current_key)
        for key in page.destinations:
            self.assertTrue(page.open_page(key))
            self.assertEqual(page.current_key, key)
            page.show_hub()
        self.assertTrue(page.open_page("shop"))
        self.assertEqual(page.shop_tab.name.text(), "Unsaved example")
        self.assertFalse(page.open_page("unknown"))
        self.assertEqual(page.current_key, "shop")

    def test_cashier_custom_permission(self):
        from morv.infra import users
        from morv.ui.session import Session
        from morv.ui.settings_page import SettingsPage
        from morv.ui.widgets import dispose
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cashier12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", True)
        page = SettingsPage(Session(self.con, cashier))
        self.addCleanup(dispose, page, self.app)
        self.assertEqual(set(page.destinations), {"shop", "item_fields", "backup", "appearance", "shortcuts"})
        self.assertFalse(page.open_page("users"))
        self.assertTrue(page.open_page("shop"))
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", False)
        page.show_hub()
        self.assertFalse(page.open_page("shop"))

    def test_adaptive_grid_reflows_without_losing_widgets(self):
        from itertools import combinations
        from PySide6.QtWidgets import QPushButton
        from morv.ui.adaptive_layout import AdaptiveCards
        from morv.ui.widgets import card, dispose
        cards = []
        buttons = []
        for i in range(4):
            box, layout = card()
            button = QPushButton(str(i))
            layout.addWidget(button)
            cards.append(box)
            buttons.append(button)
        area = AdaptiveCards(cards, minimum_width=320)
        self.addCleanup(dispose, area, self.app)
        area.show()
        for width, expected in ((900, 2), (450, 1), (900, 2)):
            area.resize(width, 300)
            self.settle()
            self.assertEqual(area.columns, expected)
            self.assertEqual(tuple(cards), area.cards)
            self.assertFalse(area.horizontalScrollBar().isVisible())
            for a, b in combinations(cards, 2):
                self.assertFalse(a.geometry().intersects(b.geometry()))
            for box, button in zip(cards, buttons, strict=True):
                self.assertTrue(box.rect().contains(button.geometry()))

    def test_hub_geometry_at_reference_sizes(self):
        from itertools import combinations
        from PySide6.QtCore import QPoint, QRect
        from morv.ui import theme
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        self.addCleanup(theme.apply, self.app)
        win.show()
        win.go_key("settings")
        page = win.pages["settings"]
        for font in theme.FONT_SIZES:
            win.set_font(theme.DEFAULT_FONT, font)
            for dark in (False, True):
                if win.dark != dark:
                    win.toggle_theme()
                for collapsed in (False, True):
                    if win.collapsed != collapsed:
                        win.toggle_sidebar()
                    for w, h in ((1920, 1080), (1093, 614), (1366, 768)):
                        win.resize(w, h - 40)
                        self.settle()
                        with self.subTest(font=font, dark=dark, collapsed=collapsed, size=(w, h)):
                            hub = page.hub
                            self.assertFalse(hub.horizontalScrollBar().isVisible())
                            self.assertLessEqual(hub.content.minimumSizeHint().width(), hub.viewport().width())
                            for a, b in combinations(hub.cards, 2):
                                self.assertFalse(a.geometry().intersects(b.geometry()))
                            rects = [QRect(btn.mapTo(hub.content, QPoint()), btn.size())
                                     for btn in page.links.values()]
                            for a, b in combinations(rects, 2):
                                self.assertFalse(a.intersects(b))
                            for btn in page.links.values():
                                self.assertGreaterEqual(btn.height(), btn.minimumSizeHint().height())
                                self.assertGreaterEqual(btn.width(), btn.minimumSizeHint().width())
