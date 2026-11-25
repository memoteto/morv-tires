"""Shared invoice search and keyboard regression tests."""
from morv.core.text import norm_ar

from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class InvoiceSearchTest(PosFixture):
    def setUp(self):
        super().setUp()
        self.ids = {}
        for kind, code, name, barcode in (
            ("tire", "TR-QA", "إطار ألف", "12345678"),
            ("battery", "BT-QA", "بطارية باء", "87654321"),
            ("service", "SV-QA", "خدمة جيم", "11223344"),
        ):
            self.ids[kind] = self.con.execute(
                "INSERT INTO items "
                "(kind,code,name,name_norm,barcode,price_retail,price_wholesale,price_super) "
                "VALUES (?,?,?,?,?,10000,10000,10000)",
                (kind, code, name, norm_ar(name), barcode)
            ).lastrowid

    def invoice(self, purchase):
        if not purchase:
            return self.page()
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.widgets import dispose
        stack = PurchasesPage(self.session)
        self.addCleanup(dispose, stack, self.app)
        stack.resize(1366, 768)
        stack.open_form()
        stack.show()
        self.app.processEvents()
        return stack.form_page

    def key(self, widget, name):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.keyClick(widget, getattr(Qt.Key, "Key_" + name))
        self.app.processEvents()

    def query(self, form, text):
        form.item_search.setFocus()
        form.item_search.setText(text)

    def test_exact_barcode(self):
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "12345678")
                self.key(f.item_search, "Return")
                self.assertEqual(
                    [r["item_id"] for r in f._rows],
                    [self.ids["tire"]])
                self.assertEqual(f.item_search.text(), "")

    def test_arabic_digits(self):
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "١٢٣٤٥٦٧٨")
                self.key(f.item_search, "Return")
                self.assertEqual(
                    [r["item_id"] for r in f._rows],
                    [self.ids["tire"]])

    def test_partial_search_does_not_add(self):
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "بطارية")
                f._do_item_search()
                self.assertEqual(f._rows, [])
                self.assertGreater(f._item_popup._layout.count(), 1)

    def test_arrow_selection(self):
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "بطارية")
                f._do_item_search()
                self.key(f.item_search, "Down")
                self.key(f.item_search, "Return")
                self.assertEqual(
                    [r["item_id"] for r in f._rows],
                    [self.ids["battery"]])

    def test_escape_closes_results(self):
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "بطارية")
                f._do_item_search()
                self.key(f.item_search, "Escape")
                self.assertFalse(f._item_popup.isVisible())
                self.assertEqual(f._rows, [])

    def test_purchase_rejects_service_barcode(self):
        f = self.invoice(True)
        self.query(f, "11223344")
        self.key(f.item_search, "Return")
        self.assertEqual(f._rows, [])

    def test_inactive_barcode_is_not_added(self):
        self.con.execute(
            "UPDATE items SET active=0 WHERE id=?",
            (self.ids["tire"],))
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "12345678")
                self.key(f.item_search, "Return")
                self.assertEqual(f._rows, [])

    def test_purchase_shortcuts(self):
        from PySide6.QtGui import QShortcut
        f = self.invoice(True)
        shortcuts = {s.key().toString()
                     for s in f.findChildren(QShortcut)}
        self.assertTrue({"F2", "F9"} <= shortcuts, shortcuts)


    def test_ambiguous_barcode_is_not_added(self):
        self.con.execute(
            "UPDATE items SET code='12345678' WHERE id=?",
            (self.ids["battery"],))
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "12345678")
                self.key(f.item_search, "Return")
                self.assertEqual(f._rows, [])

    def test_escape_cancels_pending_search(self):
        from PySide6.QtTest import QTest
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                f = self.invoice(purchase)
                self.query(f, "بطارية")
                self.key(f.item_search, "Escape")
                QTest.qWait(300)
                self.assertFalse(f._item_popup.isVisible())

    def test_purchase_shortcuts_actually_work(self):
        f = self.invoice(True)
        f.stack.activateWindow()
        f.notes.setFocus()
        self.app.processEvents()
        self.key(f.notes, "F2")
        self.assertTrue(f.item_search.hasFocus())
        self.key(f.item_search, "F9")
        self.assertIn("اختر المورد", f.notice.text())
