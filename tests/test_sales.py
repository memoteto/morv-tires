"""اختبارات فاتورة البيع: الإيراد وFIFO والخصم والدفع والآجل والصلاحيات. بيانات وهمية فقط."""
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
    """جهاز وهمي: مدير وكاشير، مورد، صنفان للإطارات، بطارية، خدمة، وعميلان."""

    def setUp(self):
        """قاعدة جديدة وبيانات وهمية، ثم استلام مخزون بدفعتين بتكلفتين مختلفتين."""
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
        self.cust = self.customer("01000000011", 500000)       # حد ائتمان 5000 جنيه
        self.nocredit = self.customer("01000000012", 0)
        self.purchase(self.tire, 2, 100000, "2024", "k-buy-0001")
        self.purchase(self.tire, 2, 120000, "2124", "k-buy-0002")
        self.n = 0

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, kind, code):
        """صنف وهمي بالجدول مباشرة."""
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id", (kind, code, code, code.lower(), 20000, 18000, 16000)).fetchone()[0]

    def customer(self, mobile, limit):
        """عميل وهمي بحد ائتمان معين."""
        return self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) VALUES ('عميل وهمي','عميل وهمي',?,?) "
            "RETURNING id", (mobile, limit)).fetchone()[0]

    def purchase(self, item_id, qty, cost, dot, key):
        """استلام مخزون عبر محرك الشراء الفعلي."""
        purchasing.post_invoice(self.con, self.admin, request_key=key, supplier_id=self.sup,
                                supplier_invoice_no=f"S-{key}", invoice_date="2026-11-01",
                                lines=[PurchaseLine(item_id, qty, cost, dot)])

    def post(self, lines, payments, **kw):
        """ترحيل فاتورة بمفتاح جديد لكل استدعاء."""
        self.n += 1
        kw.setdefault("customer_id", None)
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", lines=lines,
                               payments=payments, sale_date="2026-11-10", **kw)

    def counts(self):
        """عدد الصفوف في الجداول المتأثرة، لإثبات أن الرفض لم يكتب شيئًا."""
        return tuple(self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("sales", "sale_lines", "sale_payments", "journal_entries", "stock_issues"))

    def balance(self, code):
        """رصيد حساب مدين-دائن بالقرش."""
        return self.con.execute("SELECT coalesce(sum(debit - credit), 0) FROM journal_lines WHERE account_code=?",
                                (code,)).fetchone()[0]

    def test_cash_sale_uses_fifo_cost_and_balances(self):
        """بيع 3 إطارات نقدًا: التكلفة من الأقدم (2×1000 + 1×1200)، والقيد متوازن، والمخزون يتسق."""
        r = self.post([SaleLineIn(self.tire, 3, 20000)], [Payment("cash", 60000, received=60000)])
        self.assertEqual(r.total, 60000)
        self.assertEqual(self.balance("4110"), -60000)
        self.assertEqual(self.balance("5110"), 2 * 100000 + 120000)
        self.assertEqual(self.balance("1410"), 2 * 100000 + 2 * 120000 - 2 * 100000 - 120000)
        self.assertEqual(self.balance("1110"), 60000)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(self.con.execute("SELECT coalesce(sum(debit),0)-coalesce(sum(credit),0) FROM journal_lines").fetchone()[0], 0)

    def test_service_line_has_no_stock_issue(self):
        """الخدمة تُحسب إيرادًا بلا تكلفة صرف."""
        before = self.counts()
        self.post([SaleLineIn(self.service, 1, 15000)], [Payment("card", 15000, reference="POS-7")])
        self.assertEqual(self.balance("4130"), -15000)
        self.assertEqual(self.counts()[4], before[4])

    def test_invoice_discount_splits_exactly(self):
        """خصم فاتورة 100 قرش على سطرين يتوزع بالكامل، وينعكس على الإجمالي والقيد."""
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
        """مجموع الدفع الخاطئ، ووسيلتان متكررتان، والنقد الأقل من المطلوب: كلها ترفض ولا يُكتب شيء."""
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
        """المستلم أكبر من المطلوب: يُحفظ المستلم للحساب، والقيد يسجل المطلوب فقط."""
        r = self.post([SaleLineIn(self.service, 1, 20000)], [Payment("cash", 20000, received=50000)])
        row = self.con.execute("SELECT amount, received FROM sale_payments WHERE sale_id=?", (r.id,)).fetchone()
        self.assertEqual(tuple(row), (20000, 50000))
        self.assertEqual(self.balance("1110"), 20000)

    def test_credit_needs_customer_and_limit(self):
        """الآجل: عميل الزيارة مرفوض، وعميل بحد صفر مرفوض، وتجاوز الحد مرفوض، وضمن الحد يُقبل."""
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
        """طلب كمية أكبر من المخزون بعد سطر خدمة صحيح: لا فاتورة ولا قيد ولا صرف."""
        before, lots = self.counts(), self.con.execute("SELECT qty_on_hand FROM stock_lots ORDER BY id").fetchall()
        with self.assertRaises(SaleError):
            self.post([SaleLineIn(self.service, 1, 5000), SaleLineIn(self.tire, 99, 20000)],
                      [Payment("cash", 5000 + 99 * 20000, received=10**7)])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.con.execute("SELECT qty_on_hand FROM stock_lots ORDER BY id").fetchall(), lots)
        self.assertFalse(self.con.in_transaction)

    def test_replay_and_key_reuse(self):
        """نفس المفتاح بنفس المحتوى يعيد الفاتورة دون تكرار، وبمحتوى مختلف يُرفض."""
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
        """بلا صلاحية sales.post يُرفض؛ اختيار دفعة غير الأقدم للكاشير يُرفض، وللمدير بلا سبب يُرفض."""
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

    def test_posted_sale_is_immutable(self):
        """الفاتورة المرحّلة وسطورها ودفعاتها لا تُعدَّل ولا تُحذف."""
        r = self.post([SaleLineIn(self.service, 1, 1000)], [Payment("cash", 1000, received=1000)])
        for stmt in ("UPDATE sales SET total=1 WHERE id=?", "DELETE FROM sales WHERE id=?",
                     "UPDATE sale_lines SET qty=2 WHERE sale_id=?", "DELETE FROM sale_payments WHERE sale_id=?"):
            with self.subTest(stmt=stmt), self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(stmt, (r.id,))


if __name__ == "__main__":
    unittest.main()
