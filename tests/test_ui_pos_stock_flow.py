"""POS integration tests from item selection to posted stock, FIFO and ledger entries."""
from morv.core.text import norm_ar
from morv.infra import customer_receipts, inventory, ledger, purchasing, suppliers

from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class PosStockFlowTest(PosFixture):
    def setUp(self):
        super().setUp()
        self.ids = {}
        for kind, code, name, barcode, price in (
            ("tire", "TR-FLOW", "إطار تجريبي 205/60R16", "12345678", 150000),
            ("battery", "BT-FLOW", "بطارية تجريبية 70 أمبير", "87654321", 200000),
            ("service", "SV-FLOW", "خدمة تجريبية", "11223344", 25000),
        ):
            self.ids[kind] = self.con.execute(
                "INSERT INTO items (kind,code,name,name_norm,barcode,price_retail,price_wholesale,price_super) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (kind, code, name, norm_ar(name), barcode, price, price, price)).lastrowid
        supplier = suppliers.create(self.con, self.session.user,
                                    suppliers.Supplier(None, "مورد تجريبي", "01012345678"))
        # Physical DOT order deliberately differs from financial FIFO order.
        for key, lines in (
            ("pos-flow-buy-1", [purchasing.PurchaseLine(self.ids["tire"], 2, 80000, "2424"),
                                purchasing.PurchaseLine(self.ids["battery"], 2, 120000)]),
            ("pos-flow-buy-2", [purchasing.PurchaseLine(self.ids["tire"], 3, 90000, "2324")]),
        ):
            purchasing.post_invoice(self.con, self.session.user, request_key=key,
                                    supplier_id=supplier.id, supplier_invoice_no=key,
                                    invoice_date="2026-11-01", lines=lines)
        self.assertEqual(inventory.book_value(self.con), 670000)
        self.assertEqual(inventory.reconcile(self.con), [])

    def key(self, field, name):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        QTest.keyClick(field, getattr(Qt.Key, "Key_" + name))
        self.app.processEvents()

    def add_item(self, form, kind, query, qty, *, barcode=False):
        search = form.item_search
        count = len(form._rows)
        search.setFocus()
        search.setText(query)
        if not barcode:
            form._do_item_search()
            self.assertEqual(form._item_popup._layout.count(), 2)
            self.key(search, "Down")
        self.key(search, "Return")
        self.assertEqual(len(form._rows), count + 1)
        row = form._rows[-1]
        self.assertEqual(row["item_id"], self.ids[kind])
        row["qty_spin"].setValue(qty)
        return row

    def snapshot(self):
        tables = ("sales", "sale_lines", "sale_payments", "journal_entries", "journal_lines",
                  "stock_issues", "stock_lots", "stock_movements", "cost_layers",
                  "cost_allocations", "audit_log")
        return {t: self.con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}

    def assert_balances(self, expected):
        for code, amount in expected.items():
            self.assertEqual(ledger.account_balance(self.con, code), amount, code)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertFalse(self.con.in_transaction)

    def test_mixed_invoice_split_payment_discounts_dot_and_fifo(self):
        form = self.page()
        tire = self.add_item(form, "tire", "12345678", 3, barcode=True)
        self.add_item(form, "battery", "BT-FLOW", 1)
        self.add_item(form, "service", "SV-FLOW", 2)
        tire["disc_edit"].setText("100.00")
        form.invoice_disc.setText("69.00")
        self.assertEqual(form.total_minor(), 683100)
        form.split_btn.setChecked(True)
        form.pay2_method.setCurrentIndex(form.pay2_method.findData("card"))
        form.pay1_amount.setText("3000.00")
        form.pay2_amount.setText("3831.00")
        form.pay1_received.setText("3100.00")
        form.pay2_ref.setText("QA-CARD")
        form.post_btn.click()
        self.assertIsNotNone(form.posted, form.notice.text())
        sid = form.posted.id
        self.assertEqual(form.posted.total, 683100)
        self.assertEqual(self.con.execute(
            "SELECT subtotal,line_discount,invoice_discount,total FROM sales WHERE id=?", (sid,)
        ).fetchone(), (700000, 10000, 6900, 683100))
        self.assertEqual(self.con.execute(
            "SELECT item_id,qty,unit_price,line_discount,invoice_share,net_total "
            "FROM sale_lines WHERE sale_id=? ORDER BY line_no", (sid,)).fetchall(), [
                (self.ids["tire"], 3, 150000, 10000, 4400, 435600),
                (self.ids["battery"], 1, 200000, 0, 2000, 198000),
                (self.ids["service"], 2, 25000, 0, 500, 49500),
            ])
        self.assertEqual(self.con.execute(
            "SELECT method,amount,received,reference FROM sale_payments WHERE sale_id=? ORDER BY method", (sid,)
        ).fetchall(), [("card", 383100, None, "QA-CARD"), ("cash", 300000, 310000, None)])
        self.assertEqual(self.con.execute(
            "SELECT dot,qty_on_hand FROM stock_lots WHERE item_id=? ORDER BY id", (self.ids["tire"],)
        ).fetchall(), [("2424", 2), ("2324", 0)])
        self.assertEqual(self.con.execute(
            "SELECT qty_remaining,cost_remaining FROM cost_layers WHERE item_id=? ORDER BY id", (self.ids["tire"],)
        ).fetchall(), [(0, 0), (2, 180000)])
        self.assertEqual(self.con.execute(
            "SELECT item_id,qty,cost_total FROM stock_issues WHERE source_type='sale' AND source_id=? ORDER BY id",
            (sid,)).fetchall(), [(self.ids["tire"], 3, 250000), (self.ids["battery"], 1, 120000)])
        self.assertEqual(inventory.on_hand(self.con, self.ids["battery"]), 1)
        self.assertEqual(inventory.on_hand(self.con, self.ids["service"]), 0)
        self.assertEqual(inventory.book_value(self.con), 300000)
        self.assert_balances({"1110": 300000, "1220": 383100, "4110": -435600, "4120": -198000,
                              "4130": -49500, "5110": 250000, "5120": 120000, "1410": 300000})
        self.assertFalse(form._rows)
        self.assertEqual(form.pay1_method.currentData(), "cash")

    def test_short_stock_rolls_back_prior_line_then_retry_posts_once(self):
        form = self.page()
        self.add_item(form, "tire", "TR-FLOW", 1)
        battery = self.add_item(form, "battery", "BT-FLOW", 3)
        form.pay1_received.setText("7500.00")
        before = self.snapshot()
        key = form._request_key
        form.post_btn.click()
        self.assertIsNone(form.posted)
        self.assertIn("الرصيد غير كاف", form.notice.text())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(form._request_key, key)
        self.assertEqual(len(form._rows), 2)
        self.assertTrue(form.post_btn.isEnabled())
        self.assertFalse(self.con.in_transaction)
        battery["qty_spin"].setValue(2)
        form.pay1_received.setText("5500.00")
        form.post_btn.click()
        self.assertIsNotNone(form.posted, form.notice.text())
        self.assertEqual(form.posted.total, 550000)
        self.assertEqual(inventory.on_hand(self.con, self.ids["tire"]), 4)
        self.assertEqual(inventory.on_hand(self.con, self.ids["battery"]), 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT request_key FROM sales").fetchone()[0], key)
        self.assertNotEqual(form._request_key, key)
        after = self.snapshot()
        form.post_btn.click()
        self.assertEqual(self.snapshot(), after)
        self.assert_balances({"1110": 550000, "5110": 80000, "5120": 240000, "1410": 350000})

    def test_partial_credit_sale_posts_debt_and_stock_cost(self):
        cid = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل تجريبي','عميل تجريبي','01000000021',200000) RETURNING id").fetchone()[0]
        form = self.page()
        form.customer.setText("01000000021")
        form.customer.accept_query()
        self.assertEqual(form._customer[0], cid)
        self.add_item(form, "tire", "TR-FLOW", 2)
        self.add_item(form, "service", "SV-FLOW", 1)
        self.assertEqual(form.total_minor(), 325000)
        form.split_btn.setChecked(True)
        form.pay2_method.setCurrentIndex(form.pay2_method.findData("credit"))
        form.pay1_amount.setText("1250.00")
        form.pay1_received.setText("1250.00")
        form.post_btn.click()
        self.assertIsNotNone(form.posted, form.notice.text())
        self.assertEqual(self.con.execute("SELECT customer_id,total FROM sales").fetchone(), (cid, 325000))
        self.assertEqual(self.con.execute(
            "SELECT method,amount,received FROM sale_payments ORDER BY method"
        ).fetchall(), [("cash", 125000, 125000), ("credit", 200000, None)])
        self.assertEqual(customer_receipts.customer_balance(self.con, cid), 200000)
        self.assertEqual(inventory.on_hand(self.con, self.ids["tire"]), 3)
        self.assert_balances({"1110": 125000, "1310": 200000, "4110": -300000,
                              "4130": -25000, "5110": 160000, "1410": 510000})
