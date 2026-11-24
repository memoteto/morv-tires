"""Independent settings forms preserve drafts, printer choice and accessible save actions."""
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.qt_app import get_app, require_qt


@require_qt()
class ShopSettingsTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import shop, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"MORV_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.con = open_main_db(Path(tmp.name) / "test.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "Admin", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        self.original = shop.Shop(name="Original", phone="01000000000", paper="r80",
                                  printer="Unavailable test printer", thanks="Original thanks")
        shop.save(self.con, self.admin, self.original)

    def settle(self):
        for _ in range(12):
            self.app.processEvents()

    def make_page(self):
        from morv.ui.settings_page import SettingsPage
        from morv.ui.widgets import dispose
        page = SettingsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        page.resize(1074, 636)
        page.show()
        self.settle()
        return page

    def test_independent_ui_drafts_and_preview(self):
        from morv.infra import shop
        from morv.ui import print_doc
        page = self.make_page()
        identity, printing = page.shop_tab, page.printing_tab
        self.assertTrue(page.open_page("shop"))
        identity.name.setText("New identity")
        self.assertTrue(page.open_page("printing"))
        printing.thanks.setText("New thanks")
        printing.sizes.setCurrentIndex(printing.sizes.findData("a5"))
        self.assertEqual(printing.collect().name, "Original")
        with patch.object(print_doc, "preview_image", wraps=print_doc.preview_image) as draw:
            printing._draw_preview()
            self.assertEqual(draw.call_args.args[0].name, "Original")
            self.assertEqual(draw.call_args.args[0].thanks, "New thanks")
            self.assertEqual(draw.call_args.args[2].key, "a5")
        printing.save_btn.click()
        self.assertEqual(shop.load(self.con), replace(self.original, thanks="New thanks", paper="a5"))
        self.assertTrue(page.open_page("shop"))
        self.assertEqual(identity.name.text(), "New identity")
        identity.save_btn.click()
        self.assertEqual(shop.load(self.con).name, "New identity")
        self.assertEqual(shop.load(self.con).thanks, "New thanks")
        printing.thanks.setText("Unsaved printing")
        self.assertTrue(page.open_page("printing"))
        self.assertEqual(printing.collect().name, "New identity")
        self.assertEqual(printing.thanks.text(), "Unsaved printing")
        self.assertEqual(shop.load(self.con).thanks, "New thanks")
        self.assertEqual(printing.printer.currentData(), self.original.printer)

    def test_missing_printer_and_paper_options(self):
        from morv.infra import shop
        from morv.ui import print_doc
        with patch("PySide6.QtPrintSupport.QPrinterInfo") as printers:
            printers.availablePrinterNames.return_value = []
            page = self.make_page()
            self.assertTrue(page.open_page("printing"))
            printing = page.printing_tab
            self.assertEqual(printing.printer.currentData(), self.original.printer)
            self.assertIn("غير متاحة", printing.printer.currentText())
            with patch.object(print_doc, "to_printer") as send:
                printing.trial_print()
                send.assert_not_called()
            self.assertIn("غير متاحة", printing.notice.text())
            for key in shop.PAPERS:
                printing.sizes.setCurrentIndex(printing.sizes.findData(key))
                printing.save()
                self.assertEqual(shop.load(self.con).paper, key)
                self.assertEqual(shop.load(self.con).printer, self.original.printer)
                self.assertFalse(printing.page.isNull())
            printing.printer.setCurrentIndex(0)
            printing.save()
            self.assertEqual(shop.load(self.con).printer, "")

    def test_trial_print_uses_saved_identity_and_print_draft(self):
        from morv.ui import print_doc
        page = self.make_page()
        page.shop_tab.name.setText("Unsaved identity")
        printing = page.printing_tab
        printing.thanks.setText("Draft thanks")
        printing.printer.setCurrentIndex(0)
        with patch("PySide6.QtPrintSupport.QPrinterInfo") as printers, \
                patch("PySide6.QtPrintSupport.QPrinter"), patch.object(print_doc, "to_printer") as send:
            printers.defaultPrinter.return_value.isNull.return_value = False
            printers.defaultPrinter.return_value.printerName.return_value = "Test"
            printing.trial_print()
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0].name, "Original")
            self.assertEqual(send.call_args.args[0].thanks, "Draft thanks")

    def test_logo_updates_immediately_and_requires_permission(self):
        from PySide6.QtGui import QImage
        from morv.infra import shop, users
        from morv.ui.session import Session
        from morv.ui.settings_page import SettingsPage
        from morv.ui.widgets import dispose
        source = Path(os.environ["MORV_DATA_DIR"]) / "example.png"
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(0xFF336699)
        self.assertTrue(image.save(str(source)))
        page = self.make_page()
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            page.shop_tab.pick_logo()
        self.assertIsNotNone(shop.Shop().logo_path)
        self.assertFalse(page.shop_tab.logo.pixmap().isNull())
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", True)
        other = SettingsPage(Session(self.con, cashier))
        self.addCleanup(dispose, other, self.app)
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", False)
        other.shop_tab.remove_logo()
        self.assertIsNotNone(shop.Shop().logo_path)
        page.shop_tab.remove_logo()
        self.assertIsNone(shop.Shop().logo_path)

    def test_geometry_and_fixed_save_at_reference_sizes(self):
        from itertools import combinations
        from PySide6.QtCore import QPoint, QRect
        from morv.ui import theme
        from morv.ui.adaptive_layout import AdaptiveFields
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
                    for width, height in ((1920, 1080), (1093, 614), (1366, 768)):
                        win.setMinimumSize(min(1024, width - 40), min(640, height - 80))
                        win.resize(width, height - 40)
                        for key in ("shop", "printing"):
                            self.assertTrue(page.open_page(key))
                            self.settle()
                            tab = page.destinations[key]
                            area = tab.area
                            with self.subTest(font=font, dark=dark, collapsed=collapsed,
                                              size=(width, height), route=key):
                                self.assertEqual((width, height - 40), (win.width(), win.height()))
                                self.assertFalse(area.horizontalScrollBar().isVisible())
                                self.assertLessEqual(area.content.minimumSizeHint().width(), area.viewport().width())
                                for first, second in combinations(area.cards, 2):
                                    self.assertFalse(first.geometry().intersects(second.geometry()))
                                for card in area.cards:
                                    self.assertTrue(area.content.rect().contains(card.geometry()))
                                for grid in tab.findChildren(AdaptiveFields):
                                    for first, second in combinations(grid.fields, 2):
                                        self.assertFalse(first.geometry().intersects(second.geometry()))
                                    for field_widget in grid.fields:
                                        self.assertTrue(grid.rect().contains(field_widget.geometry()))
                                        self.assertGreaterEqual(field_widget.height(),
                                                                field_widget.minimumSizeHint().height())
                                button = tab.save_btn
                                rect = QRect(button.mapTo(tab, QPoint()), button.size())
                                self.assertTrue(button.isVisible())
                                self.assertTrue(tab.rect().contains(rect))
                                self.assertGreater(rect.top(), area.geometry().bottom())
                                area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
                                self.assertEqual(QRect(button.mapTo(tab, QPoint()), button.size()), rect)
                                if key == "printing":
                                    preview = QRect(tab.preview.mapTo(tab.area.content, QPoint()), tab.preview.size())
                                    trial = QRect(tab.trial_btn.mapTo(tab.area.content, QPoint()), tab.trial_btn.size())
                                    self.assertFalse(preview.intersects(trial))
