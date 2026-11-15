"""اختبارات المرحلة 3: فاتورة الشراء، الدفعات (DOT)، طبقات FIFO، القيود، والمطابقة. بيانات وهمية فقط."""
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from morv.core import costing, dot
from morv.infra import inventory, items, ledger, purchasing, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db, transaction
from morv.infra.inventory import StockError
from morv.infra.purchasing import PurchaseError, PurchaseLine

EXPECTED_0006_CHECKSUM = "a6d3207860e8c700d10f3802102ebb65679168656423d9d21d7da0dfc2c49107"   # بصمة النسخة الأولى (1511acb) المطبّقة على القواعد القائمة
TABLES = ("journal_entries", "journal_lines", "purchase_invoices", "purchase_lines", "stock_lots",
          "cost_layers", "stock_movements", "stock_issues", "cost_allocations")


class CoreTest(unittest.TestCase):
    """حسابات نقية: DOT وتقريب الشرائح وخطة FIFO."""

    def test_dot(self):
        """DOT أسبوع وسنة؛ الأقدم تصنيعًا يُرتَّب أولًا عبر حدود السنوات."""
        self.assertEqual(dot.parse_dot("٢٣٢٤"), "2324")
        for bad in ("0024", "5424", "232", "23245", "ab12", "", None):
            with self.assertRaises(dot.DotError, msg=str(bad)):
                dot.parse_dot(bad)
        self.assertLess(dot.mfg_key("5223"), dot.mfg_key("0124"))

    def test_slice_cost_rounding_no_drift(self):
        """طبقة 1000 قرش على 3 وحدات: التقريب بالقرش ومجموع الشرائح = 1000 تمامًا."""
        c, q, taken = 1000, 3, []
        for _ in range(3):
            t = costing.slice_cost(c, q, 1)
            taken.append(t)
            c, q = c - t, q - 1
        self.assertEqual(taken, [333, 334, 333])
        self.assertEqual((sum(taken), c, q), (1000, 0, 0))

    def test_slice_cost_invariants_exhaustive(self):
        """لكل تركيبة صغيرة: لا تكلفة سالبة، والمتبقي لا ينزل عن عدد الوحدات، والمجموع يطابق الأصل."""
        for total in range(1, 40):
            for qty in range(1, total + 1):
                for first in range(1, qty + 1):
                    t = costing.slice_cost(total, qty, first)
                    self.assertTrue(0 <= t <= total)
                    if first < qty:
                        self.assertGreaterEqual(total - t, qty - first)

    def test_plan_fifo_errors(self):
        """كمية أكبر من المتاح أو غير موجبة مرفوضة."""
        with self.assertRaises(costing.InsufficientStock):
            costing.plan_fifo([(1, 2, 200)], 3)
        with self.assertRaises(ValueError):
            costing.plan_fifo([(1, 2, 200)], 0)


class Base(unittest.TestCase):
    """تجهيز قاعدة مؤقتة بمدير وكاشير ومورد وأصناف وهمية."""

    def setUp(self):
        """تجهيز."""
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.path = self.dir / "m.db"
        self.con = open_main_db(self.path)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1")
        self.tire2 = self.item("tire", "TR-2")
        self.battery = self.item("battery", "BT-1")
        self.service = self.item("service", "SV-1")
        self.n = 0

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, kind, code):
        """صنف وهمي مباشرة بالجدول (بيانات اختبار فقط)."""
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,100,100,100)", (kind, code, code, code)).lastrowid

    def buy(self, item_id, qty, cost, dot_code="2324", by=None, **kw):
        """ترحيل فاتورة شراء بسطر واحد بمفتاح وبرقم مورد جديدين."""
        self.n += 1
        args = dict(request_key=f"key-{self.n:06d}", supplier_id=self.sup, supplier_invoice_no=f"S-{self.n}",
                    invoice_date="2026-11-01", lines=[PurchaseLine(item_id, qty, cost, dot_code)])
        args.update(kw)
        return purchasing.post_invoice(self.con, by or self.admin, **args)

    def lot_ids(self, item_id):
        """أرقام دفعات الصنف بترتيب الإنشاء."""
        return [r[0] for r in self.con.execute("SELECT id FROM stock_lots WHERE item_id=? ORDER BY id", (item_id,))]

    def counts(self):
        """عدد صفوف كل الجداول الدفترية وسجل مراجعة الشراء والصرف."""
        c = {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
        c["audit"] = self.con.execute("SELECT count(*) FROM audit_log WHERE action LIKE 'purchase.%' "
                                      "OR action LIKE 'stock.%' OR action LIKE 'journal.%'").fetchone()[0]
        return c

    def assertReconciled(self):
        """المطابقة الكاملة بلا أي فرق."""
        self.assertEqual(inventory.reconcile(self.con), [])


class MandatoryCaseTest(Base):
    """حالة الاختبار الإلزامية في قرار التكلفة."""

    def test_fifo_cost_differs_from_chosen_lot(self):
        """صرف إطار فعليًا من B تكلفته 1000 (FIFO)، والمتبقي فعليًا A=2 وB=1، والقيمة 3400."""
        self.buy(self.tire, 2, 100000, "2024")          # A
        self.buy(self.tire, 2, 120000, "2124")          # B
        a, b = self.lot_ids(self.tire)
        r = inventory.issue(self.con, self.admin, self.tire, 1, source_type="test", source_id=1, source_line_id=1,
                            picks={b: 1}, reason="طلب العميل دفعة أحدث")
        self.assertEqual(r.cost_total, 100000)
        self.assertEqual([(x[2], x[5], x[6]) for x in inventory.list_lots(self.con, self.tire)],
                         [("2024", 2, 100000), ("2124", 1, 120000)])   # تكلفة الشراء التاريخية لا تتغير
        self.assertEqual(dict(self.con.execute("SELECT id, qty_on_hand FROM stock_lots")), {a: 2, b: 1})
        layers = self.con.execute("SELECT qty_remaining, cost_remaining FROM cost_layers ORDER BY id").fetchall()
        self.assertEqual(layers, [(1, 100000), (2, 240000)])
        self.assertEqual(inventory.book_value(self.con), 340000)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 340000)
        self.assertEqual(ledger.account_balance(self.con, "5110"), 100000)
        self.assertReconciled()

    def test_reconcile_after_many_mixed_issues(self):
        """مطابقة بعد كل صرف في سلسلة مبيعات من دفعات مختلفة وأسعار مختلفة."""
        self.buy(self.tire, 3, 100000, "2024")
        self.buy(self.tire, 2, 120050, "3023")
        self.buy(self.tire, 4, 99999, "1524")
        lots = self.lot_ids(self.tire)
        plan = [(1, None), (2, {lots[2]: 2}), (1, {lots[1]: 1}), (3, None), (1, {lots[0]: 1}), (1, None)]
        total_cost = 0
        for i, (q, picks) in enumerate(plan, start=1):
            r = inventory.issue(self.con, self.admin, self.tire, q, source_type="test", source_id=i, source_line_id=1,
                                picks=picks, reason="اختيار العميل" if picks else None)
            total_cost += r.cost_total
            self.assertReconciled()
        self.assertEqual(inventory.on_hand(self.con, self.tire), 0)
        self.assertEqual(inventory.book_value(self.con), 0)
        self.assertEqual(total_cost, 3 * 100000 + 2 * 120050 + 4 * 99999)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 0)

    def test_default_issue_follows_oldest_dot_not_received_order(self):
        """الاقتراح الافتراضي الأقدم تصنيعًا ولو استُلم لاحقًا، وبلا حاجة لسبب."""
        self.buy(self.tire, 2, 110000, "0124")          # أحدث تصنيعًا، استُلم أولًا
        self.buy(self.tire, 2, 130000, "5223")          # أقدم تصنيعًا، استُلم لاحقًا
        newer, older = self.lot_ids(self.tire)
        self.assertEqual(inventory.suggest_lots(self.con, self.tire, 3), {older: 2, newer: 1})
        r = inventory.issue(self.con, self.cashier, self.tire, 2, source_type="t", source_id=1, source_line_id=1)
        self.assertEqual(r.lot_picks, ((older, 2),))
        self.assertEqual(r.cost_total, 220000)           # التكلفة FIFO من الاستلام الأول لا من الدفعة المصروفة
        self.assertReconciled()


class IssueRulesTest(Base):
    """قواعد الصرف: الصلاحية والسبب والمخزون السالب."""

    def setUp(self):
        """دفعتان للصنف."""
        super().setUp()
        self.buy(self.tire, 2, 100000, "2024")
        self.buy(self.tire, 2, 120000, "2124")
        self.a, self.b = self.lot_ids(self.tire)

    def test_override_needs_permission_and_reason(self):
        """دفعة غير الأقدم: الكاشير بلا صلاحية يُرفض، والمدير بلا سبب يُرفض، ولا يتغير شيء."""
        before = self.counts()
        with self.assertRaises(StockError):
            inventory.issue(self.con, self.cashier, self.tire, 1, source_type="t", source_id=1, source_line_id=1,
                            picks={self.b: 1}, reason="سبب")
        for reason in (None, "", "  ", "ab"):
            with self.assertRaises(StockError):
                inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=1, source_line_id=1,
                                picks={self.b: 1}, reason=reason)
        self.assertEqual(self.counts(), before)
        self.assertReconciled()

    def test_override_reason_is_recorded(self):
        """السبب والعلامة يُحفظان على حركة الصرف."""
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=1, source_line_id=1,
                        picks={self.b: 1}, reason="ضمان قديم")
        row = self.con.execute("SELECT picked_non_oldest, override_reason FROM stock_movements "
                               "WHERE kind='issue'").fetchone()
        self.assertEqual(row, (1, "ضمان قديم"))

    def test_picking_the_suggested_lot_is_not_an_override(self):
        """اختيار الدفعة المقترحة نفسها لا يحتاج سببًا ولا صلاحية."""
        inventory.issue(self.con, self.cashier, self.tire, 1, source_type="t", source_id=1, source_line_id=1, picks={self.a: 1})
        self.assertReconciled()

    def test_bad_picks_rejected(self):
        """دفعة صنف آخر، أو كمية أكبر من رصيدها، أو مجموع خاطئ، أو كمية غير موجبة."""
        self.buy(self.tire2, 5, 50000, "2024")
        other = self.lot_ids(self.tire2)[0]
        for picks in ({other: 1}, {self.a: 3}, {self.a: 1, self.b: 1}, {self.a: 0}, {self.a: True}):
            with self.assertRaises(StockError, msg=str(picks)):
                inventory.issue(self.con, self.admin, self.tire, 1 if len(picks) == 1 else 1, source_type="t",
                                source_id=1, source_line_id=1, picks=picks, reason="سبب")
        self.assertReconciled()

    def test_no_negative_stock(self):
        """صرف أكثر من المتاح مرفوض، والقيد في قاعدة البيانات يمنع الرصيد السالب حتى لو تجاوزه الكود."""
        before = self.counts()
        with self.assertRaises(StockError):
            inventory.issue(self.con, self.admin, self.tire, 5, source_type="t", source_id=1, source_line_id=1)
        self.assertEqual(self.counts(), before)
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.con):
            self.con.execute("UPDATE stock_lots SET qty_on_hand = -1 WHERE id=?", (self.a,))
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.con):
            self.con.execute("UPDATE cost_layers SET qty_remaining = -1, cost_remaining = 0 WHERE id=1")

    def test_bad_quantity_and_item(self):
        """كمية صفر أو سالبة أو عشرية، وصنف خدمة أو غير موجود."""
        for q in (0, -1, 1.5, True):
            with self.assertRaises(StockError, msg=str(q)):
                inventory.issue(self.con, self.admin, self.tire, q, source_type="t", source_id=1, source_line_id=1)
        for it in (self.service, 9999):
            with self.assertRaises(StockError):
                inventory.issue(self.con, self.admin, it, 1, source_type="t", source_id=1, source_line_id=1)

    def test_issue_requires_open_transaction_for_in_tx(self):
        """issue_in_tx خارج معاملة خطأ برمجي يُكشف."""
        with self.assertRaises(StockError):
            inventory.issue_in_tx(self.con, self.admin, self.tire, 1, source_type="t", source_id=1, source_line_id=1)

    def test_partial_failure_rolls_back_issue(self):
        """فشل في آخر خطوة من الصرف يلغي كل شيء: دفعات وطبقات وقيود وتخصيصات."""
        before = self.counts()
        snap = self.con.execute("SELECT id, qty_on_hand FROM stock_lots").fetchall()
        with mock.patch.object(inventory, "audit", side_effect=RuntimeError("عطل")), self.assertRaises(RuntimeError):
            inventory.issue(self.con, self.admin, self.tire, 3, source_type="t", source_id=1, source_line_id=1)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.con.execute("SELECT id, qty_on_hand FROM stock_lots").fetchall(), snap)
        self.assertFalse(self.con.in_transaction)
        self.assertReconciled()


class DuplicateIssueTest(Base):
    """منع صرف سطر المصدر نفسه مرتين، مع جواز عدة أسطر في المستند الواحد."""

    def setUp(self):
        """صنفان بمخزون كافٍ."""
        super().setUp()
        self.buy(self.tire, 10, 100000, "2024")
        self.buy(self.tire2, 10, 50000, "2024")

    def state(self):
        """لقطة: الأرصدة والقيمة وتكلفة المبيعات وعدد الصرفات."""
        return (inventory.on_hand(self.con, self.tire), inventory.book_value(self.con),
                ledger.account_balance(self.con, "5110"), self.counts())

    def test_same_source_line_cannot_issue_twice(self):
        """إعادة صرف نفس (المستند، السطر) تُرفض ولا تتكرر الكمية ولا تكلفة المبيعات، ولو تغيّرت الكمية أو الصنف."""
        inventory.issue(self.con, self.admin, self.tire, 2, source_type="sale", source_id=7, source_line_id=1)
        before = self.state()
        for item, q in ((self.tire, 2), (self.tire, 1), (self.tire2, 2)):
            with self.assertRaises(StockError, msg=f"{item}/{q}"):
                inventory.issue(self.con, self.admin, item, q, source_type="sale", source_id=7, source_line_id=1)
        self.assertEqual(self.state(), before)
        self.assertEqual(before[0], 8)
        self.assertEqual(before[2], 200000)
        self.assertReconciled()

    def test_several_lines_in_one_document_are_allowed(self):
        """أسطر مختلفة في المستند نفسه تُصرف كلها، وكل سطر مرة واحدة."""
        inventory.issue(self.con, self.admin, self.tire, 2, source_type="sale", source_id=7, source_line_id=1)
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="sale", source_id=7, source_line_id=2)
        inventory.issue(self.con, self.admin, self.tire2, 3, source_type="sale", source_id=7, source_line_id=3)
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="sale", source_id=8, source_line_id=1)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 6)
        self.assertEqual(inventory.on_hand(self.con, self.tire2), 7)
        self.assertEqual(ledger.account_balance(self.con, "5110"), 550000)   # 4×1,000 + 3×500
        with self.assertRaises(StockError):
            inventory.issue(self.con, self.admin, self.tire, 1, source_type="sale", source_id=7, source_line_id=2)
        self.assertReconciled()

    def test_source_reference_required(self):
        """مرجع المصدر ناقص أو غير صحيح مرفوض."""
        for kw in (dict(source_type="", source_id=1, source_line_id=1), dict(source_type="s", source_id=None,
                   source_line_id=1), dict(source_type="s", source_id=1, source_line_id="1")):
            with self.assertRaises(StockError, msg=str(kw)):
                inventory.issue(self.con, self.admin, self.tire, 1, **kw)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 10)

    def test_database_enforces_uniqueness_even_if_code_is_bypassed(self):
        """القيد الفريد في قاعدة البيانات يمنع التكرار حتى لو تخطى الكود الفحص."""
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="sale", source_id=7, source_line_id=1)
        with self.assertRaises(sqlite3.IntegrityError), transaction(self.con):
            self.con.execute("INSERT INTO stock_issues (item_id, qty, cost_total, source_type, source_id, "
                             "source_line_id, journal_entry_id) SELECT item_id, qty, cost_total, source_type, "
                             "source_id, source_line_id, 1 FROM stock_issues")


class PurchaseTest(Base):
    """فاتورة الشراء: الصحة، الرفض، التكرار، الفشل الجزئي."""

    def test_posting_creates_everything_consistently(self):
        """ترحيل فاتورة بسطرين: دفعات بمراجع وDOT، وطبقات، وحركات استلام، وقيد متوازن."""
        r = purchasing.post_invoice(
            self.con, self.admin, request_key="abcdef123", supplier_id=self.sup, supplier_invoice_no="INV-9",
            invoice_date="2026-11-02", notes="وهمي",
            lines=[PurchaseLine(self.tire, 4, 150050, "٢٣٢٤"), PurchaseLine(self.battery, 3, 200000)])
        self.assertEqual((r.number, r.total, r.replayed), ("PI-000001", 4 * 150050 + 3 * 200000, False))
        lots = self.con.execute("SELECT ref_no, dot, qty_received, qty_on_hand, purchase_unit_cost FROM stock_lots "
                                "ORDER BY id").fetchall()
        self.assertEqual(lots, [("PI-000001/1", "2324", 4, 4, 150050), ("PI-000001/2", None, 3, 3, 200000)])
        self.assertEqual(inventory.book_value(self.con), r.total)
        self.assertEqual(ledger.account_balance(self.con, "1410"), r.total)
        self.assertEqual(ledger.account_balance(self.con, "2110"), -r.total)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_movements WHERE kind='receipt'").fetchone()[0], 2)
        self.assertReconciled()

    def test_idempotent_replay(self):
        """إعادة نفس الطلب (نفس المفتاح والمحتوى) لا تكرر شيئًا، ومحتوى مختلف بنفس المفتاح مرفوض."""
        kw = dict(request_key="same-key-1", supplier_id=self.sup, supplier_invoice_no="X-1",
                  invoice_date="2026-11-02", lines=[PurchaseLine(self.tire, 2, 100000, "2024")])
        first = purchasing.post_invoice(self.con, self.admin, **kw)
        again = purchasing.post_invoice(self.con, self.admin, **kw)
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.counts()["purchase_invoices"], 1)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 2)
        kw["lines"] = [PurchaseLine(self.tire, 3, 100000, "2024")]
        with self.assertRaises(PurchaseError):
            purchasing.post_invoice(self.con, self.admin, **kw)
        self.assertReconciled()

    def test_same_supplier_invoice_number_rejected(self):
        """رقم فاتورة المورد نفسه لا يُرحَّل مرتين (بمفتاح جديد، وبحالة أحرف مختلفة)."""
        self.buy(self.tire, 1, 100000, supplier_invoice_no="AbC-1")
        with self.assertRaises(PurchaseError):
            self.buy(self.tire, 1, 100000, supplier_invoice_no="abc-1")
        self.assertEqual(self.counts()["purchase_invoices"], 1)

    def test_validation_rejections_write_nothing(self):
        """DOT ناقص أو خاطئ، كمية/تكلفة غير صحيحة، خدمة، مورد غائب أو موقوف، تاريخ مستقبلي: لا يُكتب شيء."""
        self.con.execute("UPDATE suppliers SET active=0 WHERE id=?", (self.sup,))
        inactive = self.sup
        self.con.execute("UPDATE suppliers SET active=1 WHERE id=?", (self.sup,))
        before = self.counts()
        cases = [
            dict(lines=[PurchaseLine(self.tire, 1, 100000, None)]),
            dict(lines=[PurchaseLine(self.tire, 1, 100000, "9999")]),
            dict(lines=[PurchaseLine(self.tire, 0, 100000, "2024")]),
            dict(lines=[PurchaseLine(self.tire, -1, 100000, "2024")]),
            dict(lines=[PurchaseLine(self.tire, 1, 0, "2024")]),
            dict(lines=[PurchaseLine(self.tire, 1.5, 100000, "2024")]),
            dict(lines=[PurchaseLine(self.tire, 1, 1000.5, "2024")]),
            dict(lines=[PurchaseLine(self.tire, True, 100000, "2024")]),
            dict(lines=[PurchaseLine(self.service, 1, 100000, None)]),
            dict(lines=[PurchaseLine(9999, 1, 100000, "2024")]),
            dict(lines=[]),
            dict(supplier_id=9999),
            dict(invoice_date="2999-01-01"),
            dict(invoice_date="2026-13-40"),
            dict(supplier_invoice_no="  "),
            dict(request_key="short"),
            dict(lines=[PurchaseLine(self.tire, 1, 100000, "2024"), PurchaseLine(self.tire, 1, 100000, "bad")]),
        ]
        for kw in cases:
            with self.assertRaises(PurchaseError, msg=str(kw)):
                self.buy(self.tire, 1, 100000, **kw)
        self.con.execute("UPDATE suppliers SET active=0 WHERE id=?", (inactive,))
        with self.assertRaises(PurchaseError):
            self.buy(self.tire, 1, 100000)
        self.assertEqual(self.counts(), before)

    def test_permission_required(self):
        """الكاشير بلا صلاحية الشراء لا يرحّل."""
        with self.assertRaises(PurchaseError):
            self.buy(self.tire, 1, 100000, by=self.cashier)
        self.assertEqual(self.counts()["purchase_invoices"], 0)

    def test_partial_failure_rolls_back_everything(self):
        """فشل عند السطر الثاني بعد كتابة الأول والقيد: لا فاتورة ولا دفعات ولا قيد ولا سجل مراجعة."""
        before = self.counts()
        real = purchasing._receive_line
        calls = []

        def flaky(*a, **k):
            """ينجح أول مرة ثم يفشل."""
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("عطل وهمي")
            return real(*a, **k)

        with mock.patch.object(purchasing, "_receive_line", flaky), self.assertRaises(RuntimeError):
            self.buy(self.tire, 1, 100000, lines=[PurchaseLine(self.tire, 1, 100000, "2024"),
                                                  PurchaseLine(self.battery, 1, 200000)])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.counts(), before)
        self.assertFalse(self.con.in_transaction)
        self.buy(self.tire, 1, 100000)         # الاتصال سليم ويقبل الترحيل بعد الفشل
        self.assertReconciled()

    def test_journal_failure_rolls_back(self):
        """فشل ترحيل القيد نفسه لا يترك فاتورة."""
        before = self.counts()
        with mock.patch.object(ledger, "post_entry", side_effect=ledger.LedgerError("عطل")), \
                self.assertRaises(PurchaseError):
            self.buy(self.tire, 1, 100000)
        self.assertEqual(self.counts(), before)

    def test_item_with_purchases_cannot_be_deleted(self):
        """صنف له شراء لا يُحذف."""
        self.buy(self.tire, 1, 100000)
        with self.assertRaises(items.ItemError):
            items.delete(self.con, self.admin, self.tire)

    def test_cost_pools_are_per_item(self):
        """طبقات صنفين مختلفين لا تختلط: الصرف من صنف لا يستهلك طبقة الآخر."""
        self.buy(self.tire, 1, 100000)
        self.buy(self.tire2, 1, 900000)
        r = inventory.issue(self.con, self.admin, self.tire2, 1, source_type="t", source_id=1, source_line_id=1)
        self.assertEqual(r.cost_total, 900000)
        self.assertEqual(inventory.book_value(self.con, self.tire), 100000)
        self.assertReconciled()

    def test_odd_costs_reconcile_to_the_piaster(self):
        """تكاليف وحدات غير مستديرة وكميات متعددة: القيمة الدفترية = القيد بالقرش دائمًا."""
        self.buy(self.tire, 3, 33333, "2024")
        self.buy(self.tire, 7, 1, "2124")
        for i in range(10):
            inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=i, source_line_id=1)
            self.assertReconciled()
        self.assertEqual(inventory.book_value(self.con), 0)


class ItemKindLockTest(Base):
    """نوع الصنف لا يتغير بعد وجود حركات، وبقية التعديلات تبقى مسموحة."""

    def battery_attrs(self, it):
        """تحويل كائن صنف إلى بطارية بحقولها الإلزامية."""
        it.kind, it.brand = "battery", "ACDelco"
        it.attrs = {"volt": "12V", "ah": "70", "cca": "640", "tech": "EFB", "box": "L3", "pol": "الموجب يمين",
                    "dims": "278×175×190"}

    def test_kind_change_blocked_after_purchase(self):
        """tire→service وtire→battery مرفوضان بعد الشراء، ولا يتغير نوع الصنف ولا حساب التكلفة."""
        self.buy(self.tire, 4, 100000, "2024")
        for target in ("service", "battery"):
            it = items.get(self.con, self.tire)
            if target == "battery":
                self.battery_attrs(it)
            else:
                it.kind, it.attrs = "service", {}
            with self.assertRaises(items.ItemError, msg=target):
                items.update(self.con, self.admin, it)
        self.assertEqual(items.get(self.con, self.tire).kind, "tire")
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="s", source_id=1, source_line_id=1)
        self.assertEqual((ledger.account_balance(self.con, "5110"), ledger.account_balance(self.con, "5120")),
                         (100000, 0))

    def test_kind_is_read_inside_the_transaction_not_before(self):
        """تغيير متزامن (نوع جديد + شراء) بين فتح المحرر والحفظ: التحديث يُرفض ولا يُعاد النوع القديم فوق الجديد."""
        from contextlib import contextmanager
        it = items.get(self.con, self.tire)             # حالة المحرر: tire
        it.brand, it.attrs = "Michelin", {"size": "205/55R16", "li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"}
        other, real, fired = open_db(self.path), items.transaction, []

        @contextmanager
        def racing(con):
            """قبل حجز الكتابة يُنفَّذ تغيير متزامن من اتصال آخر: نوع الصنف يصير بطارية ثم يُشترى."""
            if not fired:
                fired.append(1)
                other.execute("UPDATE items SET kind='battery' WHERE id=?", (self.tire,))
                purchasing.post_invoice(other, self.admin, request_key="race-kind-1", supplier_id=self.sup,
                                        supplier_invoice_no="RK-1", invoice_date="2026-11-01",
                                        lines=[PurchaseLine(self.tire, 1, 100000)])
            with real(con) as c:
                yield c
        try:
            with mock.patch.object(items, "transaction", racing), self.assertRaises(items.ItemError):
                items.update(self.con, self.admin, it)
        finally:
            other.close()
        self.assertEqual(self.con.execute("SELECT kind FROM items WHERE id=?", (self.tire,)).fetchone()[0], "battery")
        self.assertReconciled()

    def test_other_edits_deactivate_and_free_kind_change_without_documents(self):
        """تعديل الاسم والسعر وإيقاف الصنف مسموح بعد الشراء؛ وصنف بلا حركات يبقى تغيير نوعه كما كان."""
        self.buy(self.tire, 4, 100000, "2024")
        it = items.get(self.con, self.tire)
        it.name, it.price_retail, it.brand = "اسم جديد", 777, "Michelin"
        it.attrs = {"size": "205/55R16", "li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"}
        self.assertEqual(items.update(self.con, self.admin, it).price_retail, 777)
        items.set_active(self.con, self.admin, self.tire, False)
        items.set_active(self.con, self.admin, self.tire, True)
        fresh = items.get(self.con, self.tire2)         # بلا أي حركة
        fresh.kind, fresh.attrs = "service", {}
        self.assertEqual(items.update(self.con, self.admin, fresh).kind, "service")


class ReplayAfterChangeTest(Base):
    """إعادة طلب شراء سبق نجاحه تبقى تعيد الفاتورة الأصلية رغم تغيّر الأصناف أو المورد."""

    def kw(self):
        """طلب بسطرين ثابت."""
        return dict(request_key="req-replay-1", supplier_id=self.sup, supplier_invoice_no="A1",
                    invoice_date="2026-11-01", notes="n1",
                    lines=[PurchaseLine(self.tire, 2, 100000, "2024"), PurchaseLine(self.battery, 1, 200000)])

    def test_replay_after_item_deactivated_or_kind_changed_or_supplier_deactivated(self):
        """الإعادة تنجح بعد إيقاف الصنف أو تغيير نوعه أو إيقاف المورد، دون أي كتابة جديدة."""
        first = purchasing.post_invoice(self.con, self.admin, **self.kw())
        before = self.counts()
        self.con.execute("UPDATE items SET active=0 WHERE id=?", (self.battery,))
        self.con.execute("UPDATE suppliers SET active=0 WHERE id=?", (self.sup,))
        self.con.execute("UPDATE items SET kind='service' WHERE id=?", (self.battery,))
        again = purchasing.post_invoice(self.con, self.admin, **self.kw())
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.counts(), before)
        self.assertReconciled()

    def test_different_content_with_same_key_still_rejected_after_change(self):
        """نفس المفتاح بكمية أو DOT أو تاريخ أو رقم مورد مختلف يُرفض حتى بعد تغيّر الأصناف."""
        purchasing.post_invoice(self.con, self.admin, **self.kw())
        self.con.execute("UPDATE items SET active=0 WHERE id=?", (self.battery,))
        before = self.counts()
        variants = [dict(lines=[PurchaseLine(self.tire, 3, 100000, "2024"), PurchaseLine(self.battery, 1, 200000)]),
                    dict(lines=[PurchaseLine(self.tire, 2, 100000, "2124"), PurchaseLine(self.battery, 1, 200000)]),
                    dict(lines=[PurchaseLine(self.tire, 2, 100000, "2024")]),
                    dict(invoice_date="2026-11-02"), dict(supplier_invoice_no="A2")]
        for v in variants:
            with self.assertRaises(PurchaseError, msg=str(v)):
                purchasing.post_invoice(self.con, self.admin, **{**self.kw(), **v})
        self.assertEqual(self.counts(), before)

    def test_new_request_still_rejects_inactive_item(self):
        """الصنف الموقوف يبقى مرفوضًا في طلب جديد بمفتاح جديد."""
        self.con.execute("UPDATE items SET active=0 WHERE id=?", (self.tire,))
        with self.assertRaises(PurchaseError):
            purchasing.post_invoice(self.con, self.admin, **{**self.kw(), "request_key": "req-new-0002",
                                                             "supplier_invoice_no": "B2"})

    def test_notes_are_not_part_of_the_fingerprint(self):
        """قرار موثّق: تغيير الملاحظات وحدها لا يُعد محتوى مختلفًا، وتبقى ملاحظة الفاتورة الأصلية."""
        first = purchasing.post_invoice(self.con, self.admin, **self.kw())
        again = purchasing.post_invoice(self.con, self.admin, **{**self.kw(), "notes": "ملاحظة أخرى"})
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.con.execute("SELECT notes FROM purchase_invoices").fetchone()[0], "n1")


class ReconcileOneSidedTest(Base):
    """المطابقة تكشف غياب البيانات من أحد الجانبين (عبث خارج الخدمات: FK مُطفأ والمحفّزات محذوفة)."""

    def setUp(self):
        """صنفان: الأول عليه رصيد، والثاني صُرف كله."""
        super().setUp()
        self.buy(self.tire, 3, 100000, "2024")
        self.buy(self.tire2, 2, 50000, "2024")
        inventory.issue(self.con, self.admin, self.tire2, 2, source_type="s", source_id=1, source_line_id=1)
        self.assertReconciled()
        self.con.execute("PRAGMA foreign_keys=OFF")
        for (name,) in self.con.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            self.con.execute(f"DROP TRIGGER {name}")

    def test_lots_missing_while_layers_and_ledger_agree(self):
        """حذف دفعات صنف عليه رصيد (الطبقات والقيد متسقان) كان يمر بلا كشف؛ يُكشف الآن."""
        self.con.execute("DELETE FROM stock_lots WHERE item_id=?", (self.tire,))
        report = inventory.reconcile(self.con)
        self.assertTrue(any("رصيد الدفعات 0" in x for x in report), report)

    def test_lots_and_movements_both_missing(self):
        """حذف الدفعات وحركاتها معًا: يكشفه فقدان دفعة سطر الشراء ورصيد الصنف."""
        self.con.execute("DELETE FROM stock_movements WHERE item_id=?", (self.tire,))
        self.con.execute("DELETE FROM stock_lots WHERE item_id=?", (self.tire,))
        report = inventory.reconcile(self.con)
        self.assertTrue(any("سطر شراء" in x for x in report), report)
        self.assertTrue(any("رصيد الدفعات 0" in x for x in report), report)

    def test_layers_missing_for_fully_issued_item(self):
        """حذف طبقات صنف مصروف بالكامل: يكشفه سطر الشراء بلا طبقة وتخصيص بلا طبقة."""
        self.con.execute("DELETE FROM cost_layers WHERE item_id=?", (self.tire2,))
        report = inventory.reconcile(self.con)
        self.assertTrue(any("سطر شراء" in x for x in report), report)
        self.assertTrue(any("تخصيص" in x and "طبقته" in x for x in report), report)

    def test_item_identity_mismatch_only_the_new_check_catches(self):
        """دفعة وطبقة سطر شراء نُسبتا معًا إلى صنف آخر: أرصدة الأصناف والقيود تبقى متسقة، ولا يكشفها غير فحص الهوية."""
        line = self.con.execute("SELECT purchase_line_id FROM stock_lots WHERE item_id=?", (self.tire,)).fetchone()[0]
        self.con.execute("UPDATE stock_lots SET item_id=? WHERE purchase_line_id=?", (self.tire2, line))
        self.con.execute("UPDATE cost_layers SET item_id=? WHERE purchase_line_id=?", (self.tire2, line))
        report = inventory.reconcile(self.con)
        self.assertEqual(len(report), 2, report)
        self.assertTrue(all("≠ صنف سطر الشراء" in x for x in report), report)

    def test_item_identity_mismatch_on_each_side_detected(self):
        """اختلاف الهوية في الدفعة وحدها أو الطبقة وحدها يُكشف."""
        line = self.con.execute("SELECT purchase_line_id FROM stock_lots WHERE item_id=?", (self.tire,)).fetchone()[0]
        self.con.execute("UPDATE stock_lots SET item_id=? WHERE purchase_line_id=?", (self.tire2, line))
        self.assertTrue(any(x.startswith("دفعة") and "≠ صنف سطر الشراء" in x for x in inventory.reconcile(self.con)))
        self.con.execute("UPDATE stock_lots SET item_id=? WHERE purchase_line_id=?", (self.tire, line))
        self.con.execute("UPDATE cost_layers SET item_id=? WHERE purchase_line_id=?", (self.tire2, line))
        self.assertTrue(any(x.startswith("طبقة") and "≠ صنف سطر الشراء" in x for x in inventory.reconcile(self.con)))

    def test_orphan_movement_detected(self):
        """حركة بلا دفعة تُكشف."""
        self.con.execute("DELETE FROM stock_lots WHERE item_id=?", (self.tire2,))
        self.assertTrue(any("حركة" in x and "دفعتها" in x for x in inventory.reconcile(self.con)))


class UpgradeTest(unittest.TestCase):
    """الترقية 0007 فوق قاعدة بنسخة 0006 الأولى المطبّقة فعلًا: البيانات تبقى وتُعبَّأ الأعمدة الجديدة، وملف 0006 لم يتغير."""

    def test_upgrade_from_original_0006_keeps_data(self):
        """قاعدة على 0006 الأصلية فيها قيد وصرف قديمان، ثم 0007 و0008: لا فقد، تعبئة الأعمدة، حماية جديدة، وفاتورة جديدة تعمل."""
        from morv.infra.db import migrator
        d = Path(tempfile.mkdtemp())
        try:
            con = open_db(d / "old.db", create=True)
            allm = migrator.load_migrations()
            migrator.migrate(con, allm[:6])
            self.assertEqual(migrator.current_version(con), 6)
            con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
            item = con.execute("INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
                               "VALUES ('tire','TR-1','TR-1','TR-1',100,100,100)").lastrowid
            with transaction(con):
                e1 = con.execute("INSERT INTO journal_entries (entry_date, source_type, source_id) "
                                 "VALUES ('2026-11-01','legacy',1)").lastrowid
                con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,1,'1110',100,0)", (e1,))
                con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,2,'3100',0,100)", (e1,))
                e2 = con.execute("INSERT INTO journal_entries (entry_date, source_type, source_id) "
                                 "VALUES ('2026-11-02','legacy',2)").lastrowid
                con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,1,'1110',50,0)", (e2,))
                con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,2,'3100',0,30)", (e2,))
                con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,3,'4110',0,20)", (e2,))
                con.execute("INSERT INTO stock_issues (item_id, qty, cost_total, source_type, source_id, journal_entry_id) "
                            "VALUES (?,1,100,'legacy',1,?)", (item, e1))
            before = con.execute("SELECT id, entry_date, source_type, source_id FROM journal_entries ORDER BY id").fetchall()
            self.assertEqual(migrator.migrate(con, allm), [7, 8, 9, 10])
            self.assertEqual(con.execute("SELECT id, entry_date, source_type, source_id FROM journal_entries ORDER BY id").fetchall(), before)
            self.assertEqual(con.execute("SELECT status, line_count, total FROM journal_entries ORDER BY id").fetchall(),
                             [("posted", 2, 100), ("posted", 3, 50)])
            self.assertEqual(con.execute("SELECT id, source_line_id FROM stock_issues").fetchall(), [(1, 1)])
            self.assertEqual(ledger.account_balance(con, "1110"), 150)
            self.assertEqual(con.execute("SELECT count(*) FROM journal_lines").fetchone()[0], 5)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError), transaction(con):
                con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,4,'1110',1,0)", (e1,))
            with self.assertRaises(sqlite3.IntegrityError), transaction(con):
                con.execute("UPDATE journal_entries SET memo = 'x' WHERE id = ?", (e1,))
            with self.assertRaises(sqlite3.IntegrityError), transaction(con):
                con.execute("INSERT INTO stock_issues (item_id, qty, cost_total, source_type, source_id, source_line_id, journal_entry_id) "
                            "VALUES (?,1,1,'legacy',1,1,?)", (item, e2))
            admin = users.create_user(con, None, "admin", "المدير", "Admin12345", "manager")
            sup = con.execute("SELECT id FROM suppliers").fetchone()[0]
            purchasing.post_invoice(con, admin, request_key="key-up-1", supplier_id=sup, supplier_invoice_no="S-1",
                                    invoice_date="2026-11-03", lines=[PurchaseLine(item, 2, 100000, "2324")])
            self.assertEqual(ledger.account_balance(con, "1410"), 200000)
            self.assertEqual(ledger.unbalanced_entries(con), [])
            self.assertEqual(ledger.incomplete_entries(con), [])
            con.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_0006_file_unchanged_since_pr_head(self):
        """بصمة 0006 ثابتة كما طُبّقت على القواعد القائمة (1511acb)؛ أي تغيير لاحق يكون بترقية جديدة."""
        from morv.infra.db import migrator
        m6 = migrator.load_migrations()[5]
        self.assertEqual(m6.name, "purchasing")
        self.assertEqual(m6.checksum, EXPECTED_0006_CHECKSUM)


class ProtectionTest(Base):
    """حماية المستندات المرحّلة والقيود."""

    def setUp(self):
        """فاتورة وصرف مرحّلان."""
        super().setUp()
        self.buy(self.tire, 3, 100000, "2024")
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=1, source_line_id=1)

    def exec_fails(self, sql):
        """أمر كتابة يجب أن يُرفض من قاعدة البيانات نفسها."""
        with self.assertRaises(sqlite3.IntegrityError, msg=sql), transaction(self.con):
            self.con.execute(sql)

    def test_posted_documents_are_immutable(self):
        """لا تعديل ولا حذف في الفواتير والأسطر والقيود والحركات والتخصيصات والرؤوس."""
        for t in ("purchase_invoices", "purchase_lines", "journal_entries", "journal_lines", "stock_movements",
                  "cost_allocations", "stock_issues"):
            self.exec_fails(f"DELETE FROM {t}")
        self.exec_fails("UPDATE purchase_invoices SET total = 1")
        self.exec_fails("UPDATE purchase_lines SET qty = 99, line_total = 99 * unit_cost")
        self.exec_fails("UPDATE journal_lines SET debit = debit + 1 WHERE debit > 0")
        self.exec_fails("UPDATE stock_movements SET qty = -2 WHERE kind='issue'")
        self.exec_fails("UPDATE cost_allocations SET cost = 1")

    def test_lot_and_layer_fixed_fields(self):
        """بيانات الدفعة والطبقة الثابتة (DOT، التكلفة، الكمية المستلمة) لا تتغير، ولا حذف."""
        self.exec_fails("UPDATE stock_lots SET dot = '0199', mfg_key = 199")
        self.exec_fails("UPDATE stock_lots SET purchase_unit_cost = 1")
        self.exec_fails("UPDATE stock_lots SET qty_received = 50")
        self.exec_fails("UPDATE cost_layers SET cost_total = 1")
        self.exec_fails("DELETE FROM stock_lots")
        self.exec_fails("DELETE FROM cost_layers")

    def test_ledger_rules(self):
        """قيد غير متوازن، أو على حساب تجميعي، أو مبلغ غير صحيح، أو خارج معاملة: كله مرفوض."""
        with transaction(self.con):
            for lines in ([("1410", 100, 0), ("2110", 0, 99)], [("14", 100, 0), ("2110", 0, 100)],
                          [("1410", 100, 0)], [("1410", 100.0, 0), ("2110", 0, 100.0)],
                          [("1410", 100, 100), ("2110", 0, 0)], [("9999", 100, 0), ("2110", 0, 100)]):
                with self.assertRaises(ledger.LedgerError, msg=str(lines)):
                    self.con.execute("SAVEPOINT s")
                    try:
                        ledger.post_entry(self.con, self.admin, "2026-11-01", "t", 1, "m", lines)
                    finally:
                        self.con.execute("ROLLBACK TO s")
                        self.con.execute("RELEASE s")
        with self.assertRaises(ledger.LedgerError):
            ledger.post_entry(self.con, self.admin, "2026-11-01", "t", 1, "m", [("1410", 1, 0), ("2110", 0, 1)])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertReconciled()

    def test_no_extra_lines_after_entry_is_complete(self):
        """سطر إضافي على قيد مكتمل مرفوض من قاعدة البيانات، ولا يتغير إجمالي الحساب."""
        before = ledger.account_balance(self.con, "1410")
        self.exec_fails("INSERT INTO journal_lines (entry_id, line_no, account_code, debit, credit) "
                        "VALUES (1, 3, '1410', 5, 0)")
        self.exec_fails("INSERT INTO journal_lines (entry_id, line_no, account_code, debit, credit) "
                        "VALUES (1, 3, '2110', 0, 5)")
        self.assertEqual(ledger.account_balance(self.con, "1410"), before)
        self.assertReconciled()

    def raw_entry(self, lines, source_id, post=True):
        """بناء قيد بـ SQL مباشر داخل معاملة: رأس مفتوح ثم سطوره ثم (اختياريًا) الترحيل."""
        with transaction(self.con):
            eid = self.con.execute(
                "INSERT INTO journal_entries (entry_date, source_type, source_id, line_count, total, status) "
                "VALUES ('2026-11-01', 't', ?, 2, 100, 'open')", (source_id,)).lastrowid
            for no, (acc, d, c) in enumerate(lines, start=1):
                self.con.execute("INSERT INTO journal_lines (entry_id, line_no, account_code, debit, credit) "
                                 "VALUES (?,?,?,?,?)", (eid, no, acc, d, c))
            if post:
                self.con.execute("UPDATE journal_entries SET status='posted' WHERE id=?", (eid,))
        return eid

    def test_entry_must_balance_when_its_last_line_arrives(self):
        """قيد يُبنى ذريًا: آخر سطر غير موازن للإجمالي المعلن يُلغي كل شيء، والمتوازن يُقبل ويُرحَّل."""
        before = self.counts()
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_entry([("1410", 100, 0), ("2110", 0, 99)], 99)
        self.assertEqual(self.counts(), before)             # المعاملة كلها تراجعت
        self.raw_entry([("1410", 100, 0), ("2110", 0, 100)], 99)
        self.assertEqual(self.counts()["journal_entries"], before["journal_entries"] + 1)

    def test_incomplete_entry_cannot_be_posted_or_affect_balances(self):
        """قيد مفتوح بسطر من اثنين: لا يُرحَّل، ولو بقي مفتوحًا فلا يدخل الأرصدة ويكشفه reconcile."""
        before_balance = ledger.account_balance(self.con, "1410")
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw_entry([("1410", 100, 0)], 98)                        # محاولة ترحيل ناقص: تتراجع المعاملة
        eid = self.raw_entry([("1410", 100, 0)], 97, post=False)          # يبقى مفتوحًا ويُحفظ
        self.assertEqual(ledger.account_balance(self.con, "1410"), before_balance)
        self.assertEqual(ledger.open_entries(self.con), [eid])
        self.assertTrue(any("عالق" in x for x in inventory.reconcile(self.con)))
        self.exec_fails(f"UPDATE journal_entries SET status='posted' WHERE id={eid}")

    def test_posted_entry_is_sealed(self):
        """القيد المرحّل: لا سطور جديدة، ولا تعديل لأي عمود، ولا إدراج رأس مرحّل مباشرة."""
        self.exec_fails("INSERT INTO journal_lines (entry_id, line_no, account_code, debit, credit) "
                        "VALUES (1, 3, '1410', 5, 0)")
        self.exec_fails("UPDATE journal_entries SET memo = 'x' WHERE id = 1")
        self.exec_fails("UPDATE journal_entries SET total = 1 WHERE id = 1")
        self.exec_fails("UPDATE journal_entries SET status = 'open' WHERE id = 1")
        self.exec_fails("INSERT INTO journal_entries (entry_date, source_type, source_id, line_count, total) "
                        "VALUES ('2026-11-01', 't', 5, 2, 10)")
        self.assertEqual(ledger.open_entries(self.con), [])
        self.assertReconciled()

    def test_post_entry_leaves_no_open_entries(self):
        """البناء عبر post_entry يرحّل كل قيد، ولا يبقى مفتوح بعد النجاح ولا بعد الفشل."""
        self.assertEqual(ledger.open_entries(self.con), [])
        with self.assertRaises(ledger.LedgerError), transaction(self.con):
            ledger.post_entry(self.con, self.admin, "2026-11-01", "t", 1, "m", [("14", 1, 0), ("2110", 0, 1)])
        self.assertEqual(ledger.open_entries(self.con), [])

    def test_lot_and_layer_item_must_match_purchase_line(self):
        """إدراج دفعة أو طبقة بصنف يخالف سطر الشراء مرفوض من القاعدة، والإدراج المطابق عبر الشراء يعمل."""
        line = self.con.execute("SELECT id FROM purchase_lines").fetchone()[0]
        for sql in (
            "INSERT INTO stock_lots (ref_no, item_id, purchase_line_id, dot, mfg_key, received_date, qty_received, "
            f"qty_on_hand, purchase_unit_cost) VALUES ('X', {self.tire2}, {line}, '2024', 2420, '2026-11-01', 1, 1, 1)",
            "INSERT INTO cost_layers (item_id, purchase_line_id, received_date, qty_received, cost_total, "
            f"qty_remaining, cost_remaining) VALUES ({self.tire2}, {line}, '2026-11-01', 1, 1, 1, 1)",
        ):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql[:30]) as cm, transaction(self.con):
                self.con.execute(sql)
            self.assertIn("هوية الصنف", str(cm.exception))
        self.buy(self.tire2, 1, 100000, "2024")
        self.assertReconciled()

    def test_reconcile_detects_tampering(self):
        """لو عبث أحد بالرصيد (خارج الكود) تكشفه المطابقة."""
        self.con.execute("DROP TRIGGER cost_layers_fixed")
        self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - 1 WHERE id=1")
        self.assertTrue(any("رصيد الدفعات" in x for x in inventory.reconcile(self.con)))
        self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + 1 WHERE id=1")
        self.con.execute("UPDATE cost_layers SET cost_remaining = cost_remaining + 1 WHERE id=1")
        self.assertTrue(any("حساب المخزون" in x for x in inventory.reconcile(self.con)))


class ConcurrencyTest(Base):
    """تزامن حقيقي: اتصالات متعددة على نفس الملف."""

    def run_threads(self, fn, n):
        """تشغيل fn(i) في n خيوط معًا (حاجز) وجمع النتائج والأخطاء."""
        barrier, out = threading.Barrier(n), []

        def work(i):
            """خيط واحد باتصاله الخاص."""
            con = open_db(self.path)
            try:
                barrier.wait(timeout=10)
                out.append(("ok", fn(con, i)))
            except Exception as e:  # noqa: BLE001 — نجمع كل أنواع الرفض لفحصها
                out.append(("err", e))
            finally:
                con.close()

        ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        self.assertEqual(len(out), n)
        return out

    def test_same_request_key_posts_once(self):
        """ستة ترحيلات متزامنة بنفس المفتاح تنتج فاتورة واحدة والباقي إعادة."""
        def post(con, i):
            """ترحيل نفس الطلب."""
            return purchasing.post_invoice(con, self.admin, request_key="race-key-1", supplier_id=self.sup,
                                           supplier_invoice_no="R-1", invoice_date="2026-11-01",
                                           lines=[PurchaseLine(self.tire, 2, 100000, "2024")])
        out = self.run_threads(post, 6)
        self.assertEqual([k for k, _ in out].count("err"), 0, [v for k, v in out if k == "err"])
        self.assertEqual(sorted(v.replayed for _, v in out), [False] + [True] * 5)
        self.assertEqual(self.counts()["purchase_invoices"], 1)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 2)
        self.assertReconciled()

    def test_concurrent_issues_never_oversell(self):
        """خمس عمليات صرف متزامنة لوحدة من 3 متاحة: ثلاث تنجح واثنتان ترفضان، والمطابقة سليمة."""
        self.buy(self.tire, 3, 100000, "2024")

        def sell(con, i):
            """صرف وحدة."""
            return inventory.issue(con, self.admin, self.tire, 1, source_type="t", source_id=i, source_line_id=1)
        out = self.run_threads(sell, 5)
        oks = [v for k, v in out if k == "ok"]
        errs = [v for k, v in out if k == "err"]
        self.assertEqual((len(oks), len(errs)), (3, 2), errs)
        self.assertTrue(all(isinstance(e, StockError) for e in errs), errs)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 0)
        self.assertEqual(len({r.issue_id for r in oks}), 3)
        self.assertReconciled()

    def test_same_source_line_issues_once_under_concurrency(self):
        """أربع عمليات صرف متزامنة لنفس (المستند، السطر): واحدة تنجح والباقي يُرفض، والكمية تُخصم مرة."""
        self.buy(self.tire, 10, 100000, "2024")

        def sell(con, i):
            """صرف نفس سطر المصدر."""
            return inventory.issue(con, self.admin, self.tire, 3, source_type="sale", source_id=5, source_line_id=1)
        out = self.run_threads(sell, 4)
        errs = [v for k, v in out if k == "err"]
        self.assertEqual(([k for k, _ in out].count("ok"), len(errs)), (1, 3), errs)
        self.assertTrue(all(isinstance(e, StockError) for e in errs), errs)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 7)
        self.assertEqual(ledger.account_balance(self.con, "5110"), 300000)
        self.assertReconciled()


if __name__ == "__main__":
    unittest.main()
