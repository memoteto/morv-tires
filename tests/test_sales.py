"""Sale-invoice tests: revenue, FIFO, discount, payment, credit and permissions."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path


from morv.infra import inventory, purchasing, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine
from morv.infra.sales import SaleError, SaleLineIn
from morv.core.sales_rules import Payment


class SalesTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "s.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1")
        self.battery = self.item("battery", "BT-1")
        self.service = self.item("service", "SV-1")
        self.cust = self.customer("01000000011", 500000)       # a credit limit of 5000
        self.nocredit = self.customer("01000000012", 0)
        self.purchase(self.tire, 2, 100000, "2024", "k-buy-0001")
        self.purchase(self.tire, 2, 120000, "2124", "k-buy-0002")
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, kind, code):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id", (kind, code, code, code.lower(), 20000, 18000, 16000)).fetchone()[0]

    def customer(self, mobile, limit):
        return self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) VALUES ('عميل وهمي','عميل وهمي',?,?) "
            "RETURNING id", (mobile, limit)).fetchone()[0]

    def purchase(self, item_id, qty, cost, dot, key):
        purchasing.post_invoice(self.con, self.admin, request_key=key, supplier_id=self.sup,
                                supplier_invoice_no=f"S-{key}", invoice_date="2026-11-01",
                                lines=[PurchaseLine(item_id, qty, cost, dot)])

    def post(self, lines, payments, **kw):
        self.n += 1
        kw.setdefault("customer_id", None)
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", lines=lines,
                               payments=payments, sale_date="2026-11-10", **kw)

    def counts(self):
        return tuple(self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("sales", "sale_lines", "sale_payments", "journal_entries", "stock_issues"))

    def balance(self, code):
        return self.con.execute("SELECT coalesce(sum(debit - credit), 0) FROM journal_lines WHERE account_code=?",
                                (code,)).fetchone()[0]

    def test_cash_sale_uses_fifo_cost_and_balances(self):
        r = self.post([SaleLineIn(self.tire, 3, 20000)], [Payment("cash", 60000, received=60000)])
        self.assertEqual(r.total, 60000)
        self.assertEqual(self.balance("4110"), -60000)
        self.assertEqual(self.balance("5110"), 2 * 100000 + 120000)
        self.assertEqual(self.balance("1410"), 2 * 100000 + 2 * 120000 - 2 * 100000 - 120000)
        self.assertEqual(self.balance("1110"), 60000)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(self.con.execute("SELECT coalesce(sum(debit),0)-coalesce(sum(credit),0) FROM journal_lines").fetchone()[0], 0)

    def test_service_line_has_no_stock_issue(self):
        before = self.counts()
        self.post([SaleLineIn(self.service, 1, 15000)], [Payment("card", 15000, reference="POS-7")])
        self.assertEqual(self.balance("4130"), -15000)
        self.assertEqual(self.counts()[4], before[4])

    def test_invoice_discount_splits_exactly(self):
        self.con.execute("INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
                         "VALUES ('service','SV-2','خدمة','sv2',1,1,1)")
        s2 = self.con.execute("SELECT id FROM items WHERE code='SV-2'").fetchone()[0]
        r = self.post([SaleLineIn(self.service, 1, 10001), SaleLineIn(s2, 1, 10000)],
                      [Payment("cash", 19900, received=19900)], invoice_discount=101)
        self.assertEqual(r.total, 20001 - 101)
        lines = self.con.execute("SELECT net_total, invoice_share FROM sale_lines ORDER BY line_no").fetchall()
        self.assertEqual(sum(n for n, _ in lines), r.total)
        self.assertEqual(sum(s for _, s in lines), 101)

    def test_payment_rules_reject_without_writing(self):
        before = self.counts()
        bad = [
            ([Payment("cash", 19999, received=19999)], "مجموع الدفع"),
            ([Payment("cash", 10000, received=10000), Payment("cash", 10000, received=10000)], "تتكرر"),
            ([Payment("cash", 20000, received=19000)], "أقل من المطلوب"),
            ([Payment("card", 10000), Payment("transfer", 5000), Payment("wallet", 5000)], "وسيلتان"),
            ([Payment("card", 20000, received=20000)], "للنقد فقط"),
        ]
        for payments, msg in bad:
            with self.subTest(msg=msg), self.assertRaisesRegex(SaleError, msg):
                self.post([SaleLineIn(self.service, 1, 20000)], payments)
        self.assertEqual(self.counts(), before)

    def test_cash_change_is_stored_not_posted(self):
        r = self.post([SaleLineIn(self.service, 1, 20000)], [Payment("cash", 20000, received=50000)])
        row = self.con.execute("SELECT amount, received FROM sale_payments WHERE sale_id=?", (r.id,)).fetchone()
        self.assertEqual(tuple(row), (20000, 50000))
        self.assertEqual(self.balance("1110"), 20000)

    def test_credit_needs_customer_and_limit(self):
        line = [SaleLineIn(self.service, 1, 300000)]
        with self.assertRaisesRegex(SaleError, "يحتاج عميلًا"):
            self.post(line, [Payment("credit", 300000)])
        with self.assertRaisesRegex(SaleError, "حد ائتمان صفر"):
            self.post(line, [Payment("credit", 300000)], customer_id=self.nocredit)
        with self.assertRaisesRegex(SaleError, "يتجاوز"):
            self.post([SaleLineIn(self.service, 1, 600000)], [Payment("credit", 600000)], customer_id=self.cust)
        self.post(line, [Payment("credit", 300000)], customer_id=self.cust)
        with self.assertRaisesRegex(SaleError, "يتجاوز"):
            self.post([SaleLineIn(self.service, 1, 300000)], [Payment("credit", 300000)], customer_id=self.cust)
        self.assertEqual(self.balance("1310"), 300000)

    def test_insufficient_stock_rolls_back_everything(self):
        before, lots = self.counts(), self.con.execute("SELECT qty_on_hand FROM stock_lots ORDER BY id").fetchall()
        with self.assertRaises(SaleError):
            self.post([SaleLineIn(self.service, 1, 5000), SaleLineIn(self.tire, 99, 20000)],
                      [Payment("cash", 5000 + 99 * 20000, received=10**7)])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.con.execute("SELECT qty_on_hand FROM stock_lots ORDER BY id").fetchall(), lots)
        self.assertFalse(self.con.in_transaction)

    def test_replay_and_key_reuse(self):
        args = dict(customer_id=None, lines=[SaleLineIn(self.service, 1, 9000)],
                    payments=[Payment("cash", 9000, received=9000)], sale_date="2026-11-10")
        first = sales.post_sale(self.con, self.admin, request_key="same-key-001", **args)
        again = sales.post_sale(self.con, self.admin, request_key="same-key-001", **args)
        self.assertEqual((again.id, again.replayed), (first.id, True))
        with self.assertRaisesRegex(SaleError, "محتوى مختلف"):
            sales.post_sale(self.con, self.admin, request_key="same-key-001", customer_id=None,
                            lines=[SaleLineIn(self.service, 2, 9000)], payments=[Payment("cash", 18000, received=18000)],
                            sale_date="2026-11-10")
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 1)

    def test_permission_and_pick_override(self):
        self.con.execute("UPDATE user_permissions SET allowed=0 WHERE user_id=? AND perm='sales.post'", (self.cashier.id,))
        with self.assertRaisesRegex(SaleError, "صلاحية"):
            sales.post_sale(self.con, self.cashier, request_key="perm-key-0001", customer_id=None,
                            lines=[SaleLineIn(self.service, 1, 1000)], payments=[Payment("cash", 1000, received=1000)])
        newer = self.con.execute("SELECT id FROM stock_lots ORDER BY id DESC LIMIT 1").fetchone()[0]
        with self.assertRaisesRegex(SaleError, "سبب|صلاحية"):
            self.post([SaleLineIn(self.tire, 1, 20000, picks={newer: 1})], [Payment("cash", 20000, received=20000)])
        with self.assertRaisesRegex(SaleError, "سبب"):
            self.post([SaleLineIn(self.tire, 1, 20000, picks={newer: 1}, pick_reason=None)],
                      [Payment("cash", 20000, received=20000)])

    def cashier_sale(self, price, discount=0, qty=1, item=None):
        self.n += 1
        return sales.post_sale(self.con, self.cashier, request_key=f"cash-key-{self.n:04d}", customer_id=None,
                               lines=[SaleLineIn(item or self.tire, qty, price, line_discount=discount)],
                               payments=[Payment("cash", qty * price - discount, received=qty * price - discount)],
                               sale_date="2026-11-10")

    def cheap_item(self):
        item = self.item("tire", "TR-2")
        self.purchase(item, 10, 5000, "2525", "k-buy-0003")
        return item

    def test_cashier_cannot_sell_off_tier_price(self):
        item = self.cheap_item()
        self.cashier_sale(18000, item=item)                       # a wholesale tier price is allowed
        with self.assertRaisesRegex(SaleError, "السعر"):
            self.cashier_sale(1, item=item)

    def test_cashier_discount_needs_permission_and_cap(self):
        item = self.cheap_item()
        with self.assertRaisesRegex(SaleError, "خصم"):
            self.cashier_sale(20000, discount=100, item=item)
        users.set_permission(self.con, self.admin, self.cashier.id, "sales.discount", True, 5)
        self.cashier_sale(20000, discount=1000, item=item)        # exactly 5%
        with self.assertRaisesRegex(SaleError, "يتجاوز"):
            self.cashier_sale(20000, discount=1001, item=item)

    def test_below_cost_needs_permission_and_rolls_back(self):
        users.set_permission(self.con, self.admin, self.cashier.id, "sales.price_override", True)
        before = self.counts()
        with self.assertRaisesRegex(SaleError, "التكلفة"):
            self.cashier_sale(20000)                   # cost is 1000.00 per tire (100000 piasters)
        self.assertEqual(self.counts(), before)
        users.set_permission(self.con, self.admin, self.cashier.id, "sales.below_cost", True)
        self.cashier_sale(20000)
        row = self.con.execute("SELECT detail FROM audit_log WHERE action='sale.post' ORDER BY id DESC").fetchone()
        self.assertIn("بأقل من التكلفة", row[0])

    def test_manager_price_override_is_audited(self):
        self.post([SaleLineIn(self.tire, 1, 150000)], [Payment("cash", 150000, received=150000)])
        row = self.con.execute("SELECT detail FROM audit_log WHERE action='sale.post' ORDER BY id DESC").fetchone()
        self.assertIn("سعر معدّل", row[0])

    def test_posted_sale_is_immutable(self):
        r = self.post([SaleLineIn(self.service, 1, 1000)], [Payment("cash", 1000, received=1000)])
        for stmt in ("UPDATE sales SET total=1 WHERE id=?", "DELETE FROM sales WHERE id=?",
                     "UPDATE sale_lines SET qty=2 WHERE sale_id=?", "DELETE FROM sale_payments WHERE sale_id=?"):
            with self.subTest(stmt=stmt), self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(stmt, (r.id,))


if __name__ == "__main__":
    unittest.main()
