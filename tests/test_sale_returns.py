"""اختبارات مرتجع البيع: القيمة والتكلفة وعودة المخزون والمعيب والجزئي والصلاحيات. بيانات وهمية فقط."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import costing
from morv.core.sales_rules import Payment
from morv.infra import inventory, purchasing, sale_returns, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnError, ReturnLineIn
from morv.infra.sales import SaleLineIn


class SaleReturnsTest(unittest.TestCase):
    """جهاز وهمي: مدير وكاشير، مورد، إطار بدفعتين بتكلفتين، خدمة، وعميل بحد ائتمان."""

    def setUp(self):
        """قاعدة جديدة، استلام مخزون، ثم فاتورة بيع نقدية لثلاثة إطارات."""
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "r.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) "
                         "VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1")
        self.service = self.item("service", "SV-1")
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',500000) RETURNING id").fetchone()[0]
        self.buy(2, 100000, "2024", "k-buy-0001")
        self.buy(2, 120000, "2124", "k-buy-0002")
        self.n = 0
        self.sale = self.sell([SaleLineIn(self.tire, 3, 200000)], [Payment("cash", 600000, received=600000)])

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, kind, code):
        """صنف وهمي بالجدول مباشرة."""
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id",
            (kind, code, code, code.lower(), 200000, 180000, 160000)).fetchone()[0]

    def buy(self, qty, cost, dot, key):
        """استلام مخزون عبر محرك الشراء الفعلي."""
        purchasing.post_invoice(self.con, self.admin, request_key=key, supplier_id=self.sup,
                                supplier_invoice_no=f"S-{key}", invoice_date="2026-11-01",
                                lines=[PurchaseLine(self.tire, qty, cost, dot)])

    def sell(self, lines, payments, **kw):
        """ترحيل فاتورة بيع بمفتاح جديد."""
        self.n += 1
        kw.setdefault("customer_id", None)
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", lines=lines,
                               payments=payments, sale_date="2026-11-10", **kw)

    def ret(self, lines, by=None, key=None, **kw):
        """ترحيل مرتجع بمفتاح جديد."""
        self.n += 1
        kw.setdefault("reason", "عيب في الصنف")
        kw.setdefault("return_date", "2026-11-12")
        return sale_returns.post_return(self.con, by or self.admin, request_key=key or f"ret-key-{self.n:04d}",
                                        sale_id=self.sale.id, lines=lines, **kw)

    def line_id(self, no=1):
        """رقم سطر الفاتورة."""
        return self.con.execute("SELECT id FROM sale_lines WHERE sale_id=? AND line_no=?",
                                (self.sale.id, no)).fetchone()[0]

    def balance(self, code):
        """رصيد حساب مدين-دائن بالقرش."""
        return self.con.execute("SELECT coalesce(sum(debit - credit), 0) FROM journal_lines WHERE account_code=?",
                                (code,)).fetchone()[0]

    def counts(self):
        """عدد الصفوف في جداول المرتجع والدفتر، لإثبات أن الرفض لم يكتب شيئًا."""
        return tuple(self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("sale_returns", "sale_return_lines", "journal_entries", "stock_movements"))

    # ---------- النجاح ----------
    def test_sound_return_restores_stock_and_cost(self):
        """إرجاع إطار سليم: الرصيد يعود، والقيمة في المردودات، والتكلفة تعود للمخزون، والجرد متسق."""
        before = inventory.on_hand(self.con, self.tire)
        value_before = inventory.book_value(self.con, self.tire)
        posted = self.ret([ReturnLineIn(self.line_id(), 1, "sound")])
        self.assertEqual(posted.number, "SR-000001")
        self.assertEqual(posted.total, 200000)
        self.assertEqual(inventory.on_hand(self.con, self.tire), before + 1)
        # الوحدة المرتجعة هي الأولى المصروفة: تكلفتها 1000 جنيه من الدفعة الأقدم
        self.assertEqual(inventory.book_value(self.con, self.tire), value_before + 100000)
        self.assertEqual(self.balance("4190"), 200000)
        self.assertEqual(self.balance("1110"), 600000 - 200000)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_defective_return_goes_to_defect_account_not_stock(self):
        """إرجاع إطار معيب: لا يعود للرصيد ولا لطبقته، والتكلفة تذهب لحساب المخزون المعيب."""
        before = inventory.on_hand(self.con, self.tire)
        value_before = inventory.book_value(self.con, self.tire)
        self.ret([ReturnLineIn(self.line_id(), 1, "defective")])
        self.assertEqual(inventory.on_hand(self.con, self.tire), before)
        self.assertEqual(inventory.book_value(self.con, self.tire), value_before)
        self.assertEqual(self.balance("1430"), 100000)
        self.assertEqual(self.balance("5110"), 320000 - 100000)   # تكلفة البيع 3 إطارات ناقص المرتجع
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_partial_returns_sum_to_whole_line(self):
        """ثلاثة مرتجعات جزئية: مجموع قيمها وتكاليفها يساوي السطر كاملًا بالضبط بلا فرق قرش."""
        for _ in range(3):
            self.ret([ReturnLineIn(self.line_id(), 1, "sound")])
        row = self.con.execute("SELECT sum(value), sum(cost) FROM sale_return_lines").fetchone()
        self.assertEqual(row[0], 600000)
        self.assertEqual(row[1], 320000)        # 2×1000 + 1×1200 جنيه
        self.assertEqual(self.balance("5110"), 0)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_credit_refund_frees_customer_limit(self):
        """مرتجع على الحساب يخفض المشغول من حد ائتمان العميل فيسمح ببيع آجل جديد."""
        sale = self.sell([SaleLineIn(self.service, 1, 500000)], [Payment("credit", 500000)],
                         customer_id=self.cust)
        self.n += 1
        sale_returns.post_return(self.con, self.admin, request_key=f"ret-key-{self.n:04d}", sale_id=sale.id,
                                 lines=[ReturnLineIn(self.con.execute(
                                     "SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0], 1)],
                                 reason="إلغاء الخدمة", return_date="2026-11-12")
        self.assertEqual(self.balance("1310"), 0)
        self.sell([SaleLineIn(self.service, 1, 500000)], [Payment("credit", 500000)], customer_id=self.cust)

    def test_replayed_key_returns_same_document(self):
        """إعادة الطلب بالمفتاح نفسه تعيد المرتجع الأصلي ولا تكرر الإرجاع."""
        first = self.ret([ReturnLineIn(self.line_id(), 1, "sound")], key="same-return-key")
        again = sale_returns.post_return(self.con, self.admin, request_key="same-return-key", sale_id=self.sale.id,
                                         lines=[ReturnLineIn(self.line_id(), 1, "sound")],
                                         reason="عيب في الصنف", return_date="2026-11-12")
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 1)

    # ---------- الرفض ----------
    def test_more_than_sold_is_refused(self):
        """إرجاع أكثر من المباع مرفوض ولا يكتب شيئًا."""
        before = self.counts()
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 4, "sound")])
        self.assertEqual(self.counts(), before)

    def test_second_return_cannot_exceed_remainder(self):
        """بعد إرجاع وحدتين لا يُقبل إرجاع وحدتين أخريين."""
        self.ret([ReturnLineIn(self.line_id(), 2, "sound")])
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 2, "sound")])

    def test_short_reason_is_refused(self):
        """سبب أقل من ثلاثة أحرف مرفوض."""
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 1)], reason="لا")

    def test_bad_condition_is_refused(self):
        """حالة قطعة غير معروفة مرفوضة."""
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 1, "broken")])

    def test_cashier_cannot_post_return(self):
        """الكاشير بلا صلاحية مرتجع، والرفض لا يكتب شيئًا."""
        before = self.counts()
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 1)], by=self.cashier)
        self.assertEqual(self.counts(), before)

    def test_line_of_another_sale_is_refused(self):
        """سطر من فاتورة أخرى مرفوض في هذا المرتجع."""
        other = self.sell([SaleLineIn(self.service, 1, 50000)], [Payment("cash", 50000, received=50000)])
        foreign = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (other.id,)).fetchone()[0]
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(foreign, 1)])

    def test_duplicate_sale_line_in_one_return_is_refused(self):
        """تكرار سطر الفاتورة داخل مرتجع واحد مرفوض."""
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 1), ReturnLineIn(self.line_id(), 1)])

    def test_return_before_sale_date_is_refused(self):
        """تاريخ مرتجع قبل تاريخ الفاتورة مرفوض."""
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 1)], return_date="2026-11-09")

    def test_refund_method_must_match_invoice(self):
        """طريقة رد ليست من وسائل دفع الفاتورة مرفوضة."""
        with self.assertRaises(ReturnError):
            self.ret([ReturnLineIn(self.line_id(), 1)], refund_method="card")

    def test_split_payment_needs_explicit_refund_method(self):
        """فاتورة بوسيلتين تحتاج تحديد طريقة الرد صراحة."""
        sale = self.sell([SaleLineIn(self.service, 1, 100000)],
                         [Payment("cash", 60000, received=60000), Payment("card", 40000)])
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        self.n += 1
        with self.assertRaises(ReturnError):
            sale_returns.post_return(self.con, self.admin, request_key=f"ret-key-{self.n:04d}", sale_id=sale.id,
                                     lines=[ReturnLineIn(line, 1)], reason="إلغاء", return_date="2026-11-12")

    def test_rebuilt_tables_keep_their_protection(self):
        """الترقية 0010 تعيد بناء الحركات والتخصيصات: حمايتها من التعديل والحذف تبقى بعدها."""
        import sqlite3
        for sql in ("UPDATE cost_allocations SET qty = 1", "DELETE FROM cost_allocations",
                    "UPDATE stock_movements SET qty = 1", "DELETE FROM stock_movements"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.con.execute(sql)

    def test_posted_return_cannot_be_edited_or_deleted(self):
        """المرتجع المرحّل لا يُعدَّل ولا يُحذف حتى بـ SQL مباشر."""
        self.ret([ReturnLineIn(self.line_id(), 1)])
        import sqlite3
        for sql in ("UPDATE sale_returns SET total = 1", "DELETE FROM sale_returns",
                    "UPDATE sale_return_lines SET qty = 9", "DELETE FROM sale_return_lines"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql)


class TakeUnitsTest(unittest.TestCase):
    """اقتطاع الوحدات النقي: المجموع دقيق مهما تكرر الاقتطاع."""

    def test_repeated_takes_sum_exactly(self):
        """اقتطاع وحدة بوحدة من جزأين بقيمة لا تقبل القسمة يعطي مجموعًا مساويًا للقيمة."""
        parts = [("a", 3, 1000), ("b", 2, 333)]
        total = 0
        for i in range(5):
            total += sum(c for _, _, c in costing.take_units(parts, i, 1))
        self.assertEqual(total, 1333)

    def test_take_beyond_available_is_refused(self):
        """اقتطاع أكثر من المتاح مرفوض."""
        with self.assertRaises(costing.InsufficientStock):
            costing.take_units([("a", 2, 100)], 1, 2)


if __name__ == "__main__":
    unittest.main()
