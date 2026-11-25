"""Shared invoice topbar, navigation and single customer search tests."""
from unittest.mock import patch

from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class InvoiceTopbarTest(PosFixture):

    def settle(self):
        from PySide6.QtTest import QTest
        self.app.processEvents()
        QTest.qWait(50)
        self.app.processEvents()

    def window(self, page):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose

        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1920, 1040)
        win.show()
        win.go_key(page)
        self.settle()
        return win

    def test_single_customer_search_field(self):
        p = self.page()
        self.assertIsNone(getattr(p, "mobile", None))
        self.assertIn("الموبايل", p.customer.placeholderText())
        self.assertTrue(p.customer.isVisibleTo(p))
        self.assertTrue(p.tier_combo.isVisibleTo(p))

    def test_pos_header_moves_into_topbar(self):
        win = self.window("pos")
        p = win.pages["pos"]

        self.assertEqual(win._topbar.height(), 60)
        self.assertTrue(win._detail_host.isAncestorOf(p.cancel_btn))
        self.assertTrue(p.cancel_btn.isVisible())
        self.assertEqual(win.title.text(), "نقطة البيع")

        win.go_key("home")
        self.settle()

        self.assertTrue(win._detail_host.isHidden())
        self.assertTrue(p.isAncestorOf(p.cancel_btn))
        self.assertFalse(p.cancel_btn.isVisible())

    def test_pos_header_fallback_preserves_focus(self):
        from PySide6.QtCore import QSize

        win = self.window("pos")
        p = win.pages["pos"]
        button = p.cancel_btn

        self.assertTrue(win._detail_host.isAncestorOf(button))
        button.setFocus()
        self.settle()
        self.assertIs(win.focusWidget(), button)

        with patch.object(win, "_detail_available_size",
                          return_value=QSize(0, 0)):
            win._header_timer.start(0)
            self.settle()

            self.assertTrue(p.isAncestorOf(button))
            self.assertTrue(button.isVisible())
            self.assertIs(win.focusWidget(), button)

        win._header_timer.start(0)
        self.settle()

        self.assertTrue(win._detail_host.isAncestorOf(button))
        self.assertIs(win.focusWidget(), button)

    def test_purchase_header_returns_with_form(self):
        from PySide6.QtWidgets import QPushButton

        win = self.window("purchases")
        stack = win.pages["purchases"]

        self.assertTrue(win._detail_host.isHidden())
        stack.open_form()
        self.settle()

        p = stack.form_page
        button = next(
            b for b in p.findChildren(QPushButton)
            if b.text() == "رجوع للقائمة"
        ) if win._detail_host.isHidden() else next(
            b for b in win._detail_host.findChildren(QPushButton)
            if b.text() == "رجوع للقائمة"
        )

        self.assertTrue(win._detail_host.isAncestorOf(button))
        self.assertTrue(button.isVisible())

        button.click()
        self.settle()

        self.assertIs(stack.currentWidget(), stack.list_page)
        self.assertIsNone(stack.form_page)
        self.assertTrue(win._detail_host.isHidden())
