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
        self.assertTrue(page.back.isHidden())
        self.assertEqual(len(page.hub.cards), 4)
        self.assertEqual(set(page.destinations), set(page.links))
        self.assertEqual(len(page.links), 10)
        self.assertTrue(all(not btn.icon().isNull() for btn in page.links.values()))
        page.links["shop"].click()
        self.assertEqual(page.current_key, "shop")
        page.shop_tab.name.setText("Unsaved example")
        page.back.click()
        self.assertIsNone(page.current_key)
        self.assertTrue(page.back.isHidden())
        for key in page.destinations:
            self.assertTrue(page.open_page(key))
            self.assertEqual(page.current_key, key)
            self.assertIs(page.stack.currentWidget(), page.destinations[key])
            self.assertFalse(page.back.isHidden())
            self.assertEqual(page.title.text(), page.title_for(key))
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
        self.assertEqual(set(page.destinations), {"shop", "printing", "item_fields", "backup", "appearance", "shortcuts"})
        self.assertFalse(page.open_page("users"))
        self.assertTrue(page.open_page("shop"))
        self.assertTrue(page.open_page("printing"))
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", False)
        page.show_hub()
        self.assertFalse(page.open_page("shop"))
        self.assertFalse(page.open_page("printing"))

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
        buttons[0].setFocus()
        for width, expected in ((900, 2), (450, 1), (900, 2)):
            area.resize(width, 300)
            self.settle()
            self.assertEqual(area.columns, expected)
            self.assertEqual(tuple(cards), area.cards)
            for index, box in enumerate(cards):
                self.assertIs(area.grid.itemAtPosition(index // expected, index % expected).widget(), box)
            self.assertIs(area.focusWidget(), buttons[0])
            self.assertFalse(area.horizontalScrollBar().isVisible())
            for a, b in combinations(cards, 2):
                self.assertFalse(a.geometry().intersects(b.geometry()))
            for box, button in zip(cards, buttons, strict=True):
                self.assertTrue(box.rect().contains(button.geometry()))

    def test_hub_geometry_at_reference_sizes(self):
        from itertools import combinations
        from PySide6.QtCore import QPoint, QRect, QSize, Qt
        from PySide6.QtTest import QTest
        from morv.ui import icons, tokens
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
                        # Match the shell's minimum for the simulated logical screen.
                        win.setMinimumSize(min(1024, w - 40), min(640, h - 80))
                        win.resize(w, h - 40)
                        self.settle()
                        with self.subTest(font=font, dark=dark, collapsed=collapsed, size=(w, h)):
                            self.assertEqual((w, h - 40), (win.width(), win.height()))
                            hub = page.hub
                            self.assertFalse(hub.horizontalScrollBar().isVisible())
                            self.assertLessEqual(hub.content.minimumSizeHint().width(), hub.viewport().width())
                            for a, b in combinations(hub.cards, 2):
                                self.assertFalse(a.geometry().intersects(b.geometry()))
                            rects = [QRect(btn.mapTo(hub.content, QPoint()), btn.size())
                                     for btn in page.links.values()]
                            for a, b in combinations(rects, 2):
                                self.assertFalse(a.intersects(b))
                            for rect in rects:
                                self.assertTrue(hub.content.rect().contains(rect))
                            for btn in page.links.values():
                                self.assertGreaterEqual(btn.height(), btn.minimumSizeHint().height())
                                self.assertGreaterEqual(btn.width(), btn.minimumSizeHint().width())
                            self.assertTrue(page.open_page("backup"))
                            self.settle()
                            back = page.back
                            title = page.title
                            header = page.header
                            back_rect = QRect(back.mapTo(header, QPoint()), back.size())
                            title_rect = QRect(title.mapTo(header, QPoint()), title.size())
                            self.assertTrue(back.isVisible())
                            hosted = win._detail_host.isAncestorOf(header)
                            self.assertEqual(page.isAncestorOf(header), not hosted)
                            self.assertEqual(win._detail_host.isHidden(), not hosted)
                            self.assertEqual(win.title.text(), "الإعدادات")
                            topbar = win.title.parentWidget()
                            self.assertEqual(topbar.height(), 60)
                            if hosted:
                                header_rect = QRect(header.mapTo(topbar, QPoint()), header.size())
                                base_rect = QRect(win.title.mapTo(topbar, QPoint()), win.title.size())
                                health_rect = QRect(win.health_pill.mapTo(topbar, QPoint()), win.health_pill.size())
                                self.assertTrue(topbar.rect().contains(header_rect))
                                self.assertLess(header_rect.right(), base_rect.left())
                                self.assertGreater(header_rect.left(), health_rect.right())
                            else:
                                header_rect = QRect(header.mapTo(page, QPoint()), header.size())
                                self.assertTrue(page.rect().contains(header_rect))
                                self.assertLess(header_rect.bottom(), page.stack.y())
                            if w == 1093 and font >= 17 and not collapsed:
                                self.assertFalse(hosted)
                            if w == 1920:
                                self.assertTrue(hosted)
                            self.assertGreaterEqual(title.height(), title.heightForWidth(title.width()))
                            self.assertEqual(back.text(), "")
                            self.assertEqual(back.toolTip(), "الرجوع إلى جميع الإعدادات")
                            self.assertEqual(back.accessibleName(), back.toolTip())
                            self.assertEqual(back.iconSize(), QSize(18, 18))
                            expected = icons.icon("collapse", tokens.ICON_MUTED, 18)
                            self.assertEqual(back.icon().pixmap(18, 18).toImage(),
                                             expected.pixmap(18, 18).toImage())
                            self.assertGreaterEqual(back.width(), 38)
                            self.assertGreaterEqual(back.height(), 38)
                            self.assertEqual(back_rect.right(), header.rect().right())
                            self.assertEqual(back_rect.left() - title_rect.right() - 1, 8)
                            self.assertTrue(header.rect().contains(back_rect))
                            self.assertTrue(header.rect().contains(title_rect))
                            self.assertFalse(back_rect.intersects(title_rect))
                            back.setFocus()
                            self.assertIs(win.focusWidget(), back)
                            QTest.keyClick(back, Qt.Key.Key_Space)
                            self.settle()
                            self.assertIsNone(page.current_key)
                            self.assertTrue(back.isHidden())
                            self.assertIs(page.focusWidget(), page.links["backup"])
                            self.assertTrue(page.isAncestorOf(header))
                            self.assertTrue(win._detail_host.isHidden())

    def test_detail_header_returns_to_page_and_shop_uses_the_space(self):
        from PySide6.QtCore import QPoint, QSize
        from PySide6.QtWidgets import QLabel
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1366, 728)
        win.show()
        win.go_key("settings")
        page = win.pages["settings"]
        self.settle()
        hub_top = page.stack.y()
        self.assertTrue(page.open_page("shop"))
        self.settle()
        self.assertLess(page.stack.y(), hub_top)
        tab = page.shop_tab
        tab.name.setText("Draft")
        self.assertEqual(tab.logo.size(), QSize(80, 80))
        self.assertEqual(tab.tax_registered.text(), "المحل مسجل ضريبيًا")
        self.assertFalse(hasattr(tab, "thanks"))
        self.assertFalse(tab.area.isAncestorOf(tab.save_btn))
        self.assertTrue(tab.footer.isAncestorOf(tab.save_btn))
        self.assertLessEqual(tab.save_btn.mapTo(tab, QPoint()).y() + tab.save_btn.height(), tab.height())
        self.assertTrue(page.open_page("printing"))
        self.settle()
        printing = page.printing_tab
        self.assertFalse(any("التصميم مناسب للطابعات" in label.text()
                             for label in printing.findChildren(QLabel)))
        self.assertFalse(printing.area.isAncestorOf(printing.save_btn))
        self.assertTrue(printing.footer.isAncestorOf(printing.save_btn))
        self.assertLessEqual(printing.save_btn.mapTo(printing, QPoint()).y() + printing.save_btn.height(),
                             printing.height())
        self.assertGreater(printing.save_btn.mapTo(printing, QPoint()).y(), printing.area.geometry().bottom())
        win.go_key("home")
        self.settle()
        self.assertTrue(win._detail_host.isHidden())
        self.assertTrue(page.isAncestorOf(page.header))
        self.assertEqual(win.title.text(), "الرئيسية")
        win.go_key("settings")
        self.settle()
        self.assertIsNone(page.current_key)
        self.assertTrue(page.open_page("shop"))
        self.assertEqual(tab.name.text(), "Draft")
        win.open_backup()
        self.settle()
        self.assertEqual(page.current_key, "backup")
        self.assertTrue(win._detail_host.isAncestorOf(page.header))
        page.back.click()
        self.settle()
        self.assertIsNone(page.current_key)
        self.assertTrue(win._detail_host.isHidden())

    def test_header_reflows_on_resize_sidebar_and_font_without_losing_focus(self):
        from PySide6.QtCore import QPoint, QRect, Qt
        from PySide6.QtTest import QTest
        from morv.ui import theme
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        self.addCleanup(theme.apply, self.app)
        win.setMinimumSize(1024, 534)
        win.resize(1920, 1040)
        win.show()
        if win.collapsed:
            win.toggle_sidebar()
        win.set_font(theme.DEFAULT_FONT, 18)
        win.go_key("settings")
        page = win.pages["settings"]
        self.assertTrue(page.open_page("backup"))
        self.settle()
        header, back = page.header, page.back
        for active in (False, True):
            self.app.setActiveWindow(win if active else None)
            self.settle()
            self.assertEqual(win.isActiveWindow(), active)
            back.setFocus()
            self.assertIs(win.focusWidget(), back, "Focus must be established before resizing")
            self.assertEqual(back.hasFocus(), active)
            for width, height, hosted in ((1920, 1040, True), (1093, 574, False), (1920, 1040, True)):
                with self.subTest(active=active, width=width, height=height):
                    win.resize(width, height)
                    self.settle()
                    self.assertEqual((width, height), (win.width(), win.height()))
                    self.assertEqual(win._detail_host.isAncestorOf(header), hosted)
                    self.assertIs(page.back, back)
                    self.assertEqual(win.isActiveWindow(), active)
                    self.assertIs(win.focusWidget(), back)
                    self.assertEqual(back.hasFocus(), active)
                    self.assertEqual(page.current_key, "backup")
                    self.assertGreaterEqual(page.title.height(), page.title.heightForWidth(page.title.width()))
        win.resize(1093, 574)
        self.settle()
        self.assertTrue(page.isAncestorOf(header))
        win.toggle_sidebar()
        self.settle()
        self.assertTrue(win._detail_host.isAncestorOf(header))
        win.toggle_sidebar()
        self.settle()
        self.assertTrue(page.isAncestorOf(header))
        win.set_font(theme.DEFAULT_FONT, 13)
        self.settle()
        self.assertTrue(win._detail_host.isAncestorOf(header))
        win.set_font(theme.DEFAULT_FONT, 18)
        self.settle()
        self.assertTrue(page.isAncestorOf(header))
        for dark in (False, True):
            if win.dark != dark:
                win.toggle_theme()
            for route in page.destinations:
                self.assertTrue(page.open_page(route))
                self.settle()
                title_rect = QRect(page.title.mapTo(header, QPoint()), page.title.size())
                back_rect = QRect(back.mapTo(header, QPoint()), back.size())
                with self.subTest(dark=dark, route=route):
                    self.assertEqual(page.title.text(), page.title_for(route))
                    self.assertGreaterEqual(page.title.height(), page.title.heightForWidth(page.title.width()))
                    self.assertTrue(header.rect().contains(title_rect))
                    self.assertTrue(header.rect().contains(back_rect))
                    self.assertFalse(title_rect.intersects(back_rect))
                    self.assertTrue(page.isAncestorOf(header) or win._detail_host.isAncestorOf(header))
        back.setFocus()
        QTest.keyClick(back, Qt.Key.Key_Space)
        self.settle()
        self.assertIsNone(page.current_key)
        self.assertTrue(win._detail_host.isHidden())
