"""اختبارات المرحلة 4أ: فاتورة البيع (قيد الإيراد، الصرف FIFO، التحصيل، الصلاحيات، الذرية، المطابقة). بيانات وهمية فقط."""
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.infra import customers, inventory, items, ledger, purchasing, selling, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db, transaction
from morv.infra.purchasing import PurchaseLine
from morv.infra.selling import SaleError, SaleLine

TABLES = ("sales", "sale_lines", "sale_payments", "journal_entries", "journal_lines", "stock_issues",
          "stock_movements", "cost_allocations")
TIRE_RETAIL, TIRE_WHOLESALE, TIRE_SUPER = 645000, 612000, 595000     # 6,450.00 / 6,120.00 / 5,950.00 جنيه بالقرش
BATTERY_PRICE, SERVICE_PRICE = 300000, 50000


class Base(unittest.TestCase):
    """قاعدة مؤقتة: مدير وكاشير وأصناف وعملاء وهميون ومخزون إطارات بطبقتين."""

    def setUp(self):
        """تجهيز: إطار بدفعتين (2 × 5,000.00 بـ DOT 2324 ثم 2 × 5,200.00 بـ DOT 2224) وبطارية 3 وخدمة."""
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "m.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1", TIRE_RETAIL, TIRE_WHOLESALE, TIRE_SUPER)
        self.battery = self.item("battery", "BT-1", BATTERY_PRICE, BATTERY_PRICE, BATTERY_PRICE)
        self.service = self.item("service", "SV-1", SERVICE_PRICE, SERVICE_PRICE, SERVICE_PRICE)
        self.cust_retail = self.customer("01000000011", "retail", 0)
        self.cust_whole = self.customer("01000000012", "wholesale", 1000000)
        self.n = 0
        self.buy(self.tire, 2, 500000, "2324")
        self.buy(self.tire, 2, 520000, "2224")
        self.buy(self.battery, 3, 200000, "")

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, kind, code, retail, wholesale, super_):
        """صنف وهمي مباشرة بالجدول."""
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?)", (kind, code, code, code, retail, wholesale, super_)).lastrowid

    def customer(self, mobile, level, limit):
        """عميل وهمي بفئة سعر وحد آجل."""
        return self.con.execute("INSERT INTO customers (name, name_norm, mobile, price_level, credit_limit) "
                                "VALUES (?,?,?,?,?)", (f"ع{mobile}", f"ع{mobile}", mobile, level, limit)).lastrowid

    def buy(self, item_id, qty, cost, dot_code):
        """ترحيل فاتورة شراء بسطر واحد."""
        self.n += 1
        return purchasing.post_invoice(
            self.con, self.admin, request_key=f"buy-key-{self.n:06d}", supplier_id=self.sup,
            supplier_invoice_no=f"S-{self.n}", invoice_date="2026-11-01",
            lines=[PurchaseLine(item_id, qty, cost, dot_code or None)])

    def sell(self, lines, payments=None, customer=None, by=None, key=None, **kw):
        """ترحيل فاتورة بيع بمفتاح جديد ما لم يُعطَ."""
        self.n += 1
        return selling.post_sale(self.con, by or self.admin, request_key=key or f"sale-key-{self.n:06d}",
                                 customer_id=customer, sale_date="2026-11-05", lines=lines,
                                 payments=payments if payments is not None else {}, **kw)

    def bal(self, code):
        """رصيد حساب (مدين − دائن) بالقرش."""
        return ledger.account_balance(self.con, code)

    def counts(self):
        """عدد صفوف الجداول الدفترية وسجل المراجعة."""
        c = {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
        c["audit"] = self.con.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        return c

    def assertClean(self):
        """مطابقة المخزون والمبيعات والقيود بلا فروق."""
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(selling.reconcile_sales(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])


class GoldenCasesTest(Base):
    """حالات ذهبية بأرقام محسوبة باليد."""

    def test_g1_cash_sale_fifo_cost_across_two_layers(self):
        """4 إطارات نقدًا بـ 6,450.00: إيراد 25,800.00 وتكلفة FIFO من طبقتين = 20,400.00 والمخزون صفر."""
        r = self.sell([SaleLine(self.tire, 4)], {"cash": 2580000})
        self.assertEqual((r.total, r.paid, r.credit, r.replayed, r.warnings), (2580000, 2580000, 0, False, ()))
        self.assertEqual(r.number, "SI-000001")
        self.assertEqual(self.bal("1110"), 2580000)
        self.assertEqual(self.bal("4110"), -2580000)
        self.assertEqual(self.bal("5110"), 2040000)          # 2×500000 + 2×520000
        self.assertEqual(self.bal("1410"), 3 * 200000)       # بقيت بطاريات الشراء فقط
        self.assertEqual(inventory.on_hand(self.con, self.tire), 0)
        self.assertEqual(self.con.execute("SELECT cost_total FROM sale_lines").fetchone()[0], 2040000)
        self.assertClean()

    def test_g2_mixed_payment_three_kinds_no_double_revenue(self):
        """إطار جملة + بطارية + خدمة = 9,620.00: نقدي 3,000 وبطاقة 2,950 وآجل 3,670؛ الإيراد 4110/4120/4130 مرة واحدة."""
        r = self.sell([SaleLine(self.tire, 1), SaleLine(self.battery, 1), SaleLine(self.service, 1)],
                      {"cash": 300000, "card": 295000}, customer=self.cust_whole)
        self.assertEqual((r.total, r.paid, r.credit), (962000, 595000, 367000))     # 6,120 + 3,000 + 500
        self.assertEqual(self.bal("1110"), 300000)
        self.assertEqual(self.bal("1220"), 295000)
        self.assertEqual(self.bal("1310"), 367000)
        self.assertEqual(self.bal("4110"), -612000)
        self.assertEqual(self.bal("4120"), -300000)
        self.assertEqual(self.bal("4130"), -50000)
        rev = -(self.bal("4110") + self.bal("4120") + self.bal("4130"))
        self.assertEqual(rev, r.total)
        # الخدمة بلا صرف ولا تكلفة؛ المنتجان لهما صرف واحد لكل منهما
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_issues WHERE source_type='sale'").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT issue_id, cost_total FROM sale_lines WHERE item_kind='service'").fetchone(),
                         (None, 0))
        self.assertEqual(self.bal("5120"), 200000)
        self.assertClean()

    def test_g3_price_level_default_and_override_rules(self):
        """السعر الافتراضي حسب فئة العميل؛ تغييره يحتاج sales.price_override ويُسجَّل."""
        self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_WHOLESALE}, customer=self.cust_whole)
        self.assertEqual(self.con.execute("SELECT list_price, unit_price FROM sale_lines").fetchone(),
                         (TIRE_WHOLESALE, TIRE_WHOLESALE))
        self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL})            # بلا عميل = قطاعي
        self.assertEqual(self.con.execute("SELECT unit_price FROM sale_lines ORDER BY id DESC").fetchone()[0], TIRE_RETAIL)
        before = self.counts()
        with self.assertRaises(SaleError):                                    # الكاشير بلا صلاحية تغيير السعر
            self.sell([SaleLine(self.tire, 1, 600000)], {"cash": 600000}, by=self.cashier)
        self.assertEqual(self.counts(), before)
        self.sell([SaleLine(self.tire, 1, 600000)], {"cash": 600000})        # المدير يملكها
        row = self.con.execute("SELECT detail FROM audit_log WHERE action='sale.price_override'").fetchone()
        self.assertIn(f"{TIRE_RETAIL} ← 600000", row[0])
        self.assertClean()

    def test_g4_below_cost_warns_and_logs_without_blocking(self):
        """بيع الإطار بـ 4,000.00 وتكلفته 5,000.00: تنبيه وتسجيل والفاتورة تُرحَّل بالأرقام المُدخلة."""
        r = self.sell([SaleLine(self.tire, 1, 400000)], {"cash": 400000})
        self.assertEqual(len(r.warnings), 1)
        self.assertIn("تحت التكلفة", r.warnings[0])
        self.assertEqual(self.bal("4110"), -400000)
        self.assertEqual(self.bal("5110"), 500000)
        self.assertEqual(self.con.execute("SELECT count(*) FROM audit_log WHERE action='sale.below_cost'").fetchone()[0], 1)
        self.assertClean()

    def test_g4b_service_never_below_cost(self):
        """الخدمة بلا تكلفة فلا تنبيه مهما كان سعرها."""
        r = self.sell([SaleLine(self.service, 1, 100)], {"cash": 100})
        self.assertEqual(r.warnings, ())

    def test_g5_credit_warnings_limit_zero_and_exceed(self):
        """حد صفر = لا آجل (تنبيه)، وتجاوز الحد تنبيه، وداخل الحد بلا تنبيه، والفاتورة تُرحَّل في كل الحالات."""
        r = self.sell([SaleLine(self.battery, 1)], {}, customer=self.cust_retail)
        self.assertIn("غير مصرّح", r.warnings[0])
        self.assertEqual(self.bal("1310"), BATTERY_PRICE)
        r = self.sell([SaleLine(self.battery, 1)], {}, customer=self.cust_whole)       # 300,000 ≤ حد 1,000,000
        self.assertEqual(r.warnings, ())
        r = self.sell([SaleLine(self.tire, 2)], {}, customer=self.cust_whole)          # 300,000 + 1,224,000 > حد
        self.assertIn("يتجاوز", r.warnings[0])
        self.assertEqual(selling.customer_outstanding(self.con, self.cust_whole), BATTERY_PRICE + 2 * TIRE_WHOLESALE)
        self.assertClean()

    def test_g9_dot_pick_independent_of_fifo_cost(self):
        """الاقتراح الأقدم تصنيعًا (2224، اشتُري ثانيًا) والتكلفة FIFO من أقدم طبقة (5,000.00) لا من دفعته (5,200.00)."""
        lots = {d: i for i, d in con_rows(self.con, "SELECT id, dot FROM stock_lots WHERE item_id=?", self.tire)}
        self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL})
        self.assertEqual(self.bal("5110"), 500000)
        moved = con_rows(self.con, "SELECT lot_id, qty, picked_non_oldest FROM stock_movements WHERE kind='issue'")
        self.assertEqual(moved, [(lots["2224"], -1, 0)])

    def test_g9b_non_oldest_pick_needs_permission_and_reason(self):
        """اختيار دفعة غير الأقدم: الكاشير مرفوض، والمدير بسبب مقبول، والتكلفة تبقى FIFO وتُسجَّل الحركة."""
        lots = {d: i for i, d in con_rows(self.con, "SELECT id, dot FROM stock_lots WHERE item_id=?", self.tire)}
        pick = {lots["2324"]: 1}
        before = self.counts()
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.tire, 1, picks=pick, reason="طلب العميل")], {"cash": TIRE_RETAIL}, by=self.cashier)
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.tire, 1, picks=pick)], {"cash": TIRE_RETAIL})          # بلا سبب
        self.assertEqual(self.counts(), before)
        self.sell([SaleLine(self.tire, 1, picks=pick, reason="طلب العميل")], {"cash": TIRE_RETAIL})
        self.assertEqual(self.bal("5110"), 500000)
        self.assertEqual(con_rows(self.con, "SELECT lot_id, picked_non_oldest FROM stock_movements WHERE kind='issue'"),
                         [(lots["2324"], 1)])
        self.assertClean()


def con_rows(con, sql, *args):
    """صفوف استعلام كقائمة tuples."""
    return [tuple(r) for r in con.execute(sql, args).fetchall()]


class AtomicityTest(Base):
    """الذرية ومنع التكرار والتحقق."""

    def test_g6_insufficient_stock_rolls_back_everything(self):
        """السطر الأول ينجح والثاني يفوق المخزون: لا فاتورة ولا قيود ولا حركات، والمخزون كما كان."""
        before, stock = self.counts(), inventory.on_hand(self.con, self.tire)
        with self.assertRaises(SaleError) as cm:
            self.sell([SaleLine(self.tire, 1), SaleLine(self.battery, 4)], {"cash": 1}, customer=self.cust_whole)
        self.assertIn("السطر 2", str(cm.exception))
        self.assertEqual(self.counts(), before)
        self.assertEqual(inventory.on_hand(self.con, self.tire), stock)
        self.assertClean()

    def test_g7_replay_returns_original_without_new_rows(self):
        """إعادة نفس الطلب تعيد الأصلية بلا قيود جديدة، ومحتوى مختلف بنفس المفتاح مرفوض."""
        first = self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL}, key="same-key-0001")
        before = self.counts()
        again = self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL}, key="same-key-0001")
        self.assertTrue(again.replayed)
        self.assertEqual((again.id, again.number, again.total), (first.id, first.number, first.total))
        self.assertEqual(self.counts(), before)
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.tire, 2)], {"cash": TIRE_RETAIL}, key="same-key-0001")
        self.assertEqual(self.counts(), before)

    def test_g10_credit_without_customer_rejected(self):
        """آجل بلا عميل مرفوض كليًا، وعميل موقوف مرفوض."""
        before = self.counts()
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.tire, 1)], {"cash": 1000})
        customers.set_active(self.con, self.admin, self.cust_whole, False)
        before = self.counts()
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL}, customer=self.cust_whole)
        self.assertEqual(self.counts(), before)

    def test_g11_overpayment_and_bad_amounts_rejected(self):
        """المحصَّل أكبر من الإجمالي، أو مبلغ غير موجب أو عشري، أو وسيلة مجهولة: مرفوض بلا أثر."""
        before = self.counts()
        for pays in ({"cash": TIRE_RETAIL + 1}, {"cash": 0}, {"cash": -1}, {"cash": 1.5}, {"visa": 5}):
            with self.assertRaises(SaleError, msg=str(pays)):
                self.sell([SaleLine(self.tire, 1)], pays)
        self.assertEqual(self.counts(), before)

    def test_bad_lines_and_items_rejected(self):
        """فاتورة بلا أسطر، كمية أو سعر غير صالح، صنف موقوف أو غير موجود، تاريخ غير صالح، مفتاح قصير."""
        before = self.counts()
        bad = [[], [SaleLine(self.tire, 0)], [SaleLine(self.tire, 1, 0)], [SaleLine(self.tire, 1.5)],
               [SaleLine(9999, 1)], [SaleLine(self.tire, 1, 10.5)]]
        for lines in bad:
            with self.assertRaises(SaleError, msg=str(lines)):
                self.sell(lines, {})
        items.set_active(self.con, self.admin, self.battery, False)
        before = self.counts()
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.battery, 1)], {"cash": 1})
        with self.assertRaises(SaleError):
            selling.post_sale(self.con, self.admin, request_key="short", customer_id=None, sale_date="2026-11-05",
                              lines=[SaleLine(self.tire, 1)], payments={"cash": TIRE_RETAIL})
        with self.assertRaises(SaleError):
            selling.post_sale(self.con, self.admin, request_key="date-key-01", customer_id=None, sale_date="غير",
                              lines=[SaleLine(self.tire, 1)], payments={"cash": TIRE_RETAIL})
        self.assertEqual(self.counts(), before)

    def test_g12_service_creates_no_stock_movement(self):
        """فاتورة خدمة فقط: إيراد 4130 بلا صرف ولا حركة مخزون ولا قيد تكلفة."""
        before_moves = self.con.execute("SELECT count(*) FROM stock_movements").fetchone()[0]
        self.sell([SaleLine(self.service, 2)], {"cash": 2 * SERVICE_PRICE})
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_movements").fetchone()[0], before_moves)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_issues WHERE source_type='sale'").fetchone()[0], 0)
        self.assertEqual(self.bal("4130"), -2 * SERVICE_PRICE)
        self.assertClean()


class PermissionAndGuardTest(Base):
    """الصلاحيات في الـ backend وحماية المستندات المرحّلة."""

    def test_cashier_can_post_manager_default_but_not_without_permission(self):
        """الكاشير يرث sales.post؛ سحبها يمنعه؛ ولا ترحيل بلا مستخدم."""
        self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL}, by=self.cashier)
        users.set_permission(self.con, self.admin, self.cashier.id, "sales.post", False)
        with self.assertRaises(SaleError):
            self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL}, by=self.cashier)
        with self.assertRaises(SaleError):
            selling.post_sale(self.con, None, request_key="nouser-key-1", customer_id=None, sale_date="2026-11-05",
                              lines=[SaleLine(self.tire, 1)], payments={"cash": TIRE_RETAIL})

    def test_posted_documents_are_immutable(self):
        """التعديل والحذف على الفاتورة وأسطرها ودفعاتها مرفوض من القاعدة، ولا سطر ولا دفعة زائدة."""
        r = self.sell([SaleLine(self.tire, 1)], {"cash": TIRE_RETAIL})
        for sql in ("UPDATE sales SET total = total + 1", "DELETE FROM sales", "UPDATE sale_lines SET qty = 5",
                    "DELETE FROM sale_lines", "UPDATE sale_payments SET amount = 1", "DELETE FROM sale_payments"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.con.execute(sql)
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("INSERT INTO sale_lines (sale_id, line_no, item_id, item_kind, qty, list_price, unit_price, "
                             "line_total, cost_total) VALUES (?,2,?,'service',1,5,5,5,0)", (r.id, self.service))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("INSERT INTO sale_payments (sale_id, method, amount) VALUES (?, 'card', 5)", (r.id,))

    def test_cannot_delete_customer_or_item_with_sales(self):
        """عميل أو صنف مرتبط بفاتورة بيع لا يُحذف (يُوقف فقط)."""
        self.sell([SaleLine(self.service, 1)], {}, customer=self.cust_whole)
        with self.assertRaises(customers.CustomerError):
            customers.delete(self.con, self.admin, self.cust_whole)
        with self.assertRaises(items.ItemError):
            items.delete(self.con, self.admin, self.service)
        self.assertTrue(customers.has_documents(self.con, self.cust_whole))
        self.assertTrue(items.has_documents(self.con, self.service))
        self.assertFalse(customers.has_documents(self.con, self.cust_retail))

    def test_preview_cost_is_read_only_and_matches_issue(self):
        """معاينة التكلفة لا تكتب شيئًا وتطابق التكلفة الفعلية؛ ولا مخزون كافٍ = None."""
        before = self.counts()
        self.assertEqual(selling.preview_cost(self.con, self.tire, 3), 2 * 500000 + 520000)
        self.assertIsNone(selling.preview_cost(self.con, self.tire, 5))
        self.assertEqual(self.counts(), before)
        self.sell([SaleLine(self.tire, 3)], {"cash": 3 * TIRE_RETAIL})
        self.assertEqual(self.bal("5110"), 2 * 500000 + 520000)

    def test_reconcile_sales_detects_orphan_sale_issue(self):
        """صرف بمصدر 'sale' بلا سطر فاتورة يكشفه reconcile_sales."""
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="sale", source_id=999, source_line_id=1)
        self.assertEqual([k for k, _ in selling.reconcile_sales(self.con)], ["orphan_sale_issue"])


class UpgradeTest(unittest.TestCase):
    """الترقية 0009 فوق قاعدة بنسخة 0008: البيانات تبقى والكاشير الحالي يأخذ sales.post."""

    def test_upgrade_from_0008_keeps_data_and_grants_cashier(self):
        """قاعدة على 0008 فيها كاشير وقيد قديم، ثم 0009: لا فقد، جداول البيع تظهر، والكاشير يرث الصلاحية."""
        from morv.infra.db import migrator
        d = Path(tempfile.mkdtemp())
        try:
            con = open_db(d / "old.db", create=True)
            allm = migrator.load_migrations()
            migrator.migrate(con, allm[:8])
            self.assertEqual(migrator.current_version(con), 8)
            admin = users.create_user(con, None, "admin", "المدير", "Admin12345", "manager")
            users.create_user(con, admin, "cash1", "كاشير", "Cash12345", "cashier")
            con.execute("DELETE FROM user_permissions WHERE perm = 'sales.post'")     # كما كان قبل الصلاحية الجديدة
            with transaction(con):
                ledger.post_entry(con, admin, "2026-11-01", "legacy", 1, "قيد قديم", [("1110", 100, 0), ("3100", 0, 100)])
            migrator.migrate(con, allm)
            self.assertEqual(migrator.current_version(con), 9)
            self.assertEqual(con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM users").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM user_permissions p JOIN users u ON u.id = p.user_id "
                                         "WHERE p.perm='sales.post' AND u.role='cashier'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM sales").fetchone()[0], 0)
            con.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class RandomizedTest(Base):
    """آلاف العمليات العشوائية تنتهي بمطابقة كاملة."""

    def test_g13_random_sales_reconcile(self):
        """300 فاتورة عشوائية (أنواع وكميات وأسعار ودفعات وآجل): مطابقة المخزون والمبيعات والقيود وتطابق الإيراد."""
        self.buy(self.tire, 800, 480000, "2424")
        self.buy(self.battery, 800, 210000, "")
        rnd = random.Random(7)
        placed = 0
        for _ in range(300):
            lines = [SaleLine(rnd.choice((self.tire, self.battery, self.service)), rnd.randint(1, 3),
                              rnd.choice((None, None, rnd.randint(10000, 900000)))) for _ in range(rnd.randint(1, 3))]
            probe = 0
            for ln in lines:
                price = ln.unit_price or selling.default_price(self.con, ln.item_id, self.cust_whole)
                probe += price * ln.qty
            pays, left = {}, probe
            for m in rnd.sample(("cash", "transfer", "card"), rnd.randint(0, 3)):
                amt = rnd.randint(1, max(1, left // 2)) if left > 1 else 0
                if amt:
                    pays[m], left = amt, left - amt
            selling.post_sale(self.con, self.admin, request_key=f"rnd-key-{placed:06d}", customer_id=self.cust_whole,
                              sale_date="2026-11-05", lines=lines, payments=pays)
            placed += 1
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 300)
        total = self.con.execute("SELECT sum(total) FROM sales").fetchone()[0]
        self.assertEqual(-(self.bal("4110") + self.bal("4120") + self.bal("4130")), total)
        self.assertClean()


if __name__ == "__main__":
    unittest.main()
