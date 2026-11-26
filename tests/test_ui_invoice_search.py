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

    def result_labels(self, form, query):
        from PySide6.QtWidgets import QLabel

        self.query(form, query)
        form._do_item_search()
        self.app.processEvents()
        layout = form._item_popup._layout
        self.assertEqual(layout.count(), 2, "SETUP: one matching item and the new-item action")
        button = layout.itemAt(0).widget()
        return [(label.objectName(), label.text().replace("\u2066", "").replace("\u2069", ""))
                for label in button.findChildren(QLabel)]

    def set_tire_spec(self, name):
        self.con.execute("UPDATE items SET name=?, name_norm=? WHERE id=?",
                         (name, norm_ar(name), self.ids["tire"]))
        self.con.execute("INSERT INTO item_attrs (item_id, field_key, value) VALUES (?, 'size', ?)",
                         (self.ids["tire"], "205/60R16"))

    def receive_stock(self, quantities=(3, 5)):
        from morv.infra import purchasing, suppliers

        supplier = suppliers.create(self.con, self.session.user,
                                    suppliers.Supplier(None, "مورد تجريبي", "01012345678"))
        purchasing.post_invoice(
            self.con, self.session.user, request_key="search-stock-fixture",
            supplier_id=supplier.id, supplier_invoice_no="SEARCH-1", invoice_date="2026-11-01",
            lines=[purchasing.PurchaseLine(self.ids["tire"], qty, 80000, dot)
                   for qty, dot in zip(quantities, ("2324", "2424"), strict=True)])

    def test_search_does_not_repeat_tire_size_in_name(self):
        name = "إطار شيلد 205/60R16"
        self.set_tire_spec(name)
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                labels = self.result_labels(self.invoice(purchase), "205/60R16")
                self.assertEqual(sum(text.count("205/60R16") for _, text in labels), 1,
                                 "SEARCH_DUPLICATE_SIZE")
                self.assertIn(name, [text for _, text in labels])

    def test_search_keeps_size_when_name_has_no_size(self):
        self.set_tire_spec("إطار شيلد")
        for purchase in (False, True):
            with self.subTest(purchase=purchase):
                labels = self.result_labels(self.invoice(purchase), "205/60R16")
                self.assertEqual(sum(text.count("205/60R16") for _, text in labels), 1,
                                 "SEARCH_MISSING_SIZE")

    def test_sale_search_shows_active_tier_price_matching_invoice(self):
        from morv.core.money import fmt, parse_money

        self.con.execute("UPDATE items SET price_retail=130000, price_wholesale=110050, "
                         "price_super=100025 WHERE id=?", (self.ids["tire"],))
        form = self.invoice(False)
        for tier, expected in (("retail", 130000), ("wholesale", 110050), ("super", 100025)):
            with self.subTest(tier=tier):
                form._set_tier(tier)
                labels = self.result_labels(form, "TR-QA")
                self.assertTrue(any(fmt(expected) in text for _, text in labels),
                                "SEARCH_TIER_PRICE")
                self.key(form.item_search, "Down")
                self.key(form.item_search, "Return")
                row = form._rows[-1]
                self.assertEqual(parse_money(row["price_edit"].text()), expected)
                form._remove_row(row)

    def test_sale_search_stock_is_sum_of_lots_and_warns_below_minimum(self):
        from morv.infra import inventory

        self.receive_stock()
        self.assertEqual(inventory.on_hand(self.con, self.ids["tire"]), 8)
        form = self.invoice(False)
        for minimum, status in ((0, "pillOk"), (8, "pillOk"), (9, "pillWarn")):
            with self.subTest(minimum=minimum):
                self.con.execute("UPDATE items SET min_qty=? WHERE id=?", (minimum, self.ids["tire"]))
                labels = self.result_labels(form, "TR-QA")
                self.assertTrue(any(obj == status and "8" in text for obj, text in labels),
                                "SEARCH_STOCK_BALANCE_STATUS")
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_sale_search_zero_stock_is_explicit(self):
        labels = self.result_labels(self.invoice(False), "BT-QA")
        self.assertTrue(any(obj == "pillDanger" and "0" in text for obj, text in labels),
                        "SEARCH_ZERO_STOCK")

    def test_service_search_shows_price_without_stock(self):
        labels = self.result_labels(self.invoice(False), "SV-QA")
        self.assertTrue(any("100.00" in text for _, text in labels), "SEARCH_SERVICE_PRICE")
        self.assertFalse(any(obj in ("pillOk", "pillWarn", "pillDanger") for obj, _ in labels),
                         "SEARCH_SERVICE_HAS_STOCK_BADGE")

    def test_purchase_search_does_not_show_sale_price(self):
        labels = self.result_labels(self.invoice(True), "TR-QA")
        self.assertFalse(any("100.00" in text for _, text in labels), "SEARCH_PURCHASE_SALE_PRICE")

    def test_sale_search_stock_reads_are_batched(self):
        form = self.invoice(False)
        self.receive_stock()
        self.con.execute("UPDATE items SET name='إطار اختبار', name_norm=? WHERE id=?",
                         (norm_ar("إطار اختبار"), self.ids["tire"]))
        self.con.execute("UPDATE items SET name='بطارية اختبار', name_norm=? WHERE id=?",
                         (norm_ar("بطارية اختبار"), self.ids["battery"]))
        self.query(form, "اختبار")
        statements = []
        self.con.set_trace_callback(statements.append)
        try:
            form._do_item_search()
        finally:
            self.con.set_trace_callback(None)
        self.assertEqual(form._item_popup._layout.count(), 3)
        stock_reads = [sql for sql in statements if "stock_lots" in sql.lower()]
        self.assertEqual(len(stock_reads), 1, "SEARCH_BATCHED_STOCK")
        self.assertFalse(any("select * from items where id" in sql.lower()
                             for sql in statements), "SEARCH_PRICE_N_PLUS_ONE")
