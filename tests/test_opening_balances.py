"""Opening balances: balance check, one entry per line, sub-ledgers, statements, reports and reversal."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import (customer_receipts, customer_statement, inventory, ledger, opening_balances, purchasing, reports,
                        sales, stock_adjustments, treasury, users)
from morv.infra.db.bootstrap import open_main_db
from morv.infra.opening_balances import OpeningError, OpeningLine
from morv.infra.sales import SaleLineIn


class OpeningBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "o.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.p1 = treasury.add_partner(self.con, self.admin, "الشريك الأول")
        self.p2 = treasury.add_partner(self.con, self.admin, "الشريك الثاني")
        self.cust = self.con.execute("INSERT INTO customers (name, name_norm, mobile, credit_limit, credit_days) "
                                     "VALUES ('عميل وهمي','عميل وهمي','01000000011',500000,30) RETURNING id").fetchone()[0]
        self.sup = self.con.execute("INSERT INTO suppliers (name, name_norm, mobile, payment_days) "
                                    "VALUES ('مورد وهمي','مورد وهمي','01000000001',15) RETURNING id").fetchone()[0]
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def lines(self):
        # assets 500000 cash + 100000 customer; liabilities 200000 supplier; equity 400000
        return [OpeningLine("account", 500000, account="1110"),
                OpeningLine("customer", 100000, customer_id=self.cust),
                OpeningLine("supplier", 200000, supplier_id=self.sup),
                OpeningLine("partner_capital", 250000, partner_id=self.p1.id),
                OpeningLine("partner_capital", 100000, partner_id=self.p2.id),
                OpeningLine("partner_current", 50000, partner_id=self.p2.id)]

    def post(self, lines=None, date="2026-11-01", by=None):
        self.n += 1
        return opening_balances.post_opening(self.con, by or self.admin, request_key=f"open-key-{self.n:04d}",
                                             opening_date=date, lines=lines or self.lines())

    def bal(self, code):
        return ledger.account_balance(self.con, code)

    def _service(self):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','SV-1','sv-1',450000,450000,450000) RETURNING id").fetchone()[0]


class OpeningTest(OpeningBase):
    def test_posts_one_entry_per_line_and_clears_the_transitional_account(self):
        r = self.post()
        self.assertEqual(self.con.execute("SELECT count(*) FROM opening_balance_lines WHERE opening_id=?",
                                          (r.id,)).fetchone()[0], 6)
        self.assertEqual(self.bal("3500"), 0)
        self.assertEqual((self.bal("1110"), self.bal("1310"), self.bal("2110")), (500000, 100000, -200000))
        self.assertEqual((self.bal(self.p1.capital_account), self.bal(self.p2.capital_account)), (-250000, -100000))
        self.assertEqual(self.bal(self.p2.account), -50000)       # a credit: the shop owes the partner
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_subledgers_statement_and_reports_follow_the_opening(self):
        self.post()
        self.assertEqual(customer_receipts.customer_balance(self.con, self.cust), 100000)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 200000)
        st = customer_statement.statement(self.con, self.cust)
        self.assertEqual((st.closing, len(st.rows), st.rows[0].text), (100000, 1, "رصيد افتتاحي"))
        aging = reports.credit_aging(self.con, "2026-11-20")
        self.assertEqual(aging.rows[-1][1][-1], 100000)
        sup = reports.supplier_balances(self.con, "2026-11-20")
        self.assertEqual(sup.rows[-1][1][1], 200000)
        self.assertEqual(reports.supplier_aging(self.con, "2026-11-20").rows[-1][1][-1], 200000)

    def test_credit_limit_counts_the_opening_debt(self):
        self.post()
        with self.assertRaisesRegex(sales.SaleError, "ائتمان"):
            sales.post_sale(self.con, self.admin, request_key="credit-key-001", customer_id=self.cust,
                            lines=[SaleLineIn(self._service(), 1, 450000)], payments=[Payment("credit", 450000)],
                            sale_date="2026-11-10")

    def test_unbalanced_is_refused_with_the_difference_and_preview_writes_nothing(self):
        bad = self.lines()[:-1]                                    # drop the partner current: 50000 short
        view = opening_balances.preview(self.con, self.admin, bad)
        self.assertEqual(view.difference, 50000)
        with self.assertRaisesRegex(OpeningError, "غير متوازنة"):
            self.post(bad)
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM opening_balances").fetchone()[0], 0)

    def test_line_rules(self):
        cases = [OpeningLine("account", 100, account="1310"), OpeningLine("account", 0, account="1110"),
                 OpeningLine("partner_capital", -5, partner_id=self.p1.id),
                 OpeningLine("customer", 100, customer_id=99999)]
        for ln in cases:
            with self.assertRaises(OpeningError):
                self.post([ln])
        with self.assertRaisesRegex(OpeningError, "مكرر"):
            self.post([OpeningLine("account", 1, account="1110"), OpeningLine("account", 2, account="1110")])

    def test_replay_and_changed_payload(self):
        kw = dict(request_key="same-key-0001", opening_date="2026-11-01")
        a = opening_balances.post_opening(self.con, self.admin, lines=self.lines(), **kw)
        b = opening_balances.post_opening(self.con, self.admin, lines=self.lines(), **kw)
        self.assertEqual((a.id, b.replayed), (b.id, True))
        with self.assertRaisesRegex(OpeningError, "بمحتوى مختلف"):
            opening_balances.post_opening(self.con, self.admin, lines=self.lines()[:3], **kw)

    def test_cashier_is_refused(self):
        with self.assertRaisesRegex(OpeningError, "صلاحية"):
            self.post(by=self.cashier)
        with self.assertRaisesRegex(OpeningError, "صلاحية"):
            opening_balances.preview(self.con, self.cashier, self.lines())

    def test_only_one_live_opening_and_it_must_precede_other_documents(self):
        r = self.post()
        with self.assertRaisesRegex(OpeningError, "ساري"):
            self.post()
        opening_balances.reverse_opening(self.con, self.admin, request_key="rev-key-0001", opening_id=r.id,
                                         reason="خطأ في الإدخال")
        sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=None,
                        lines=[SaleLineIn(self._service(), 1, 450000)], payments=[Payment("cash", 450000, received=450000)],
                        sale_date="2026-11-05")
        with self.assertRaisesRegex(OpeningError, "أسبق"):
            self.post(date="2026-11-10")

    def test_reversal_zeroes_everything_and_is_allowed_only_before_other_documents(self):
        r = self.post()
        rev = opening_balances.reverse_opening(self.con, self.admin, request_key="rev-key-0002", opening_id=r.id,
                                               reason="خطأ في الإدخال")
        for code in ("1110", "1310", "2110", "3500", self.p1.capital_account, self.p2.account):
            self.assertEqual(self.bal(code), 0, code)
        self.assertEqual((customer_receipts.customer_balance(self.con, self.cust),
                          treasury.supplier_owed(self.con, self.sup)), (0, 0))
        self.assertEqual(customer_statement.statement(self.con, self.cust).closing, 0)
        with self.assertRaisesRegex(OpeningError, "معكوس"):
            opening_balances.reverse_opening(self.con, self.admin, request_key="rev-key-0003", opening_id=r.id,
                                             reason="مرة ثانية")
        self.assertTrue(rev.number.startswith("OB-"))
        r2 = self.post()                                           # a corrected opening can be entered now
        sales.post_sale(self.con, self.admin, request_key="sale-key-0002", customer_id=None,
                        lines=[SaleLineIn(self._service(), 1, 450000)], payments=[Payment("cash", 450000, received=450000)],
                        sale_date="2026-11-12")
        with self.assertRaisesRegex(OpeningError, "مستندات أخرى"):
            opening_balances.reverse_opening(self.con, self.admin, request_key="rev-key-0004", opening_id=r2.id,
                                             reason="متأخر")

    def test_posted_opening_is_immutable(self):
        r = self.post()
        for sql in ("UPDATE opening_balances SET note='x' WHERE id=?", "DELETE FROM opening_balances WHERE id=?"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql, (r.id,))


class OpeningInventoryTest(OpeningBase):
    def setUp(self):
        super().setUp()
        self.tire = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-1','TR-1','tr-1',50000,50000,50000) RETURNING id").fetchone()[0]

    def stock_lines(self):
        return [OpeningLine("inventory", item_id=self.tire, qty=10, unit_cost=30000, dot="2024"),
                OpeningLine("inventory", item_id=self.tire, qty=4, unit_cost=32000, dot="2224")]   # 300000 + 128000

    def with_stock(self):
        # equity grows by the stock value, so the balance equation still holds
        return self.lines() + [OpeningLine("partner_capital", 428000, partner_id=self.p1.id)] + self.stock_lines()

    def post_stock(self):
        base = [ln for ln in self.lines() if not (ln.kind == "partner_capital" and ln.partner_id == self.p1.id)]
        return self.post(base + [OpeningLine("partner_capital", 250000 + 428000, partner_id=self.p1.id)]
                         + self.stock_lines())

    def test_inventory_creates_lots_layers_and_ties_to_1410(self):
        self.post_stock()
        self.assertEqual(inventory.on_hand(self.con, self.tire), 14)
        self.assertEqual(self.bal("1410"), 428000)
        self.assertEqual(inventory.book_value(self.con), 428000)
        self.assertEqual(inventory.reconcile(self.con), [])
        dots = {r[0] for r in self.con.execute("SELECT dot FROM stock_lots WHERE item_id=?", (self.tire,))}
        self.assertEqual(dots, {"2024", "2224"})

    def test_opening_cost_feeds_fifo_sales_and_counts_as_a_recorded_cost(self):
        self.post_stock()
        sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=None,
                        lines=[SaleLineIn(self.tire, 3, 50000)], payments=[Payment("cash", 150000, received=150000)],
                        sale_date="2026-11-05")
        self.assertEqual(self.bal("5110"), 90000)                       # three at the oldest layer's 30000
        self.assertEqual(purchasing.last_cost(self.con, self.tire), 32000)
        g = stock_adjustments.post_adjustment(
            self.con, self.admin, request_key="adj-key-0001", adjust_date="2026-11-06", reason="جرد",
            lines=[stock_adjustments.AdjustLine(self.tire, "gain", 1, dot="2024")])
        self.assertEqual(g.gain_total, 30000)                           # cost from the opening lot of that DOT
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_reversal_removes_the_lots(self):
        r = self.post_stock()
        opening_balances.reverse_opening(self.con, self.admin, request_key="rev-key-0009", opening_id=r.id,
                                         reason="خطأ في الإدخال")
        self.assertEqual((inventory.on_hand(self.con, self.tire), self.bal("1410"), inventory.book_value(self.con)),
                         (0, 0, 0))
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_inventory_line_rules(self):
        bad = [dict(qty=0, unit_cost=1), dict(qty=1, unit_cost=0), dict(qty=1, unit_cost=1, dot="9999"),
               dict(qty=1, unit_cost=1, item_id=self._service())]
        for kw in bad:
            kw.setdefault("item_id", self.tire)
            with self.assertRaises(OpeningError):
                self.post([OpeningLine("inventory", **kw)])
        same = OpeningLine("inventory", item_id=self.tire, qty=1, unit_cost=5, dot="2024")
        with self.assertRaisesRegex(OpeningError, "مكرر"):
            self.post([same, same])

    def test_an_item_with_stock_cannot_change_kind_or_be_deleted(self):
        from morv.infra import items
        self.post_stock()
        self.assertTrue(items.has_documents(self.con, self.tire))


class OpeningScreenHelpersTest(OpeningBase):
    def test_choices_cover_every_line_kind(self):
        for kind in ("account", "customer", "supplier", "partner_capital", "partner_current", "inventory"):
            with self.subTest(kind=kind):
                got = opening_balances.choices(self.con, kind)
                if kind != "inventory":
                    self.assertTrue(got)
        self.assertEqual(opening_balances.choices(self.con, "retained"), [])
        self.assertIn("1110", [k for k, _ in opening_balances.choices(self.con, "account")])

    def test_current_follows_post_and_reversal(self):
        self.assertIsNone(opening_balances.current(self.con))
        posted = self.post()
        live = opening_balances.current(self.con)
        self.assertEqual((live[0], live[3]), (posted.id, 6))
        opening_balances.reverse_opening(self.con, self.admin, request_key="rev-key-0001",
                                         opening_id=posted.id, reason="خطأ إدخال")
        self.assertIsNone(opening_balances.current(self.con))
