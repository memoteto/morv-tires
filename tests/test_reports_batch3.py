"""Journal, daily sales, top items, slow-moving items and supplier balance reports."""
import unittest

from morv.core.sales_rules import Payment
from morv.infra import ledger, purchasing, reports, sale_returns, sales, treasury
from morv.infra.db.connection import transaction
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_reports_batch2 import _Base


class _Shop(_Base):
    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire, self.idle_tire = self.item("tire", "TR-1"), self.item("tire", "TR-2")
        self.service = self.item("service", "SV-1")
        self.cust = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) VALUES ('عميل وهمي','عميل وهمي','01000000011',500000) "
            "RETURNING id").fetchone()[0]
        for item, qty, cost, dot in ((self.tire, 2, 100000, "2024"), (self.tire, 2, 120000, "2124"),
                                     (self.idle_tire, 1, 90000, "2024")):
            purchasing.post_invoice(self.con, self.admin, request_key=self.key("buy"), supplier_id=self.sup,
                                    supplier_invoice_no=self.key("S"), invoice_date="2026-11-01",
                                    lines=[PurchaseLine(item, qty, cost, dot)])
        self.sale = self.sell("2026-11-10", [SaleLineIn(self.tire, 3, 200000)], [Payment("cash", 600000, received=600000)])

    def sell(self, day, lines, payments, customer=None):
        return sales.post_sale(self.con, self.admin, request_key=self.key("sale"), customer_id=customer,
                               lines=lines, payments=payments, sale_date=day)


class JournalTest(_Shop):
    def test_entries_come_with_their_lines_and_balance(self):
        t = reports.journal(self.con, "2026-11-01", "2026-11-30")
        heads = [cells for kind, cells in t.rows if kind == "head"]
        self.assertTrue(any(h[1].startswith("بيع #") for h in heads))
        self.assertTrue(any(h[1].startswith("فاتورة شراء #") for h in heads))
        total = t.rows[-1][1]
        self.assertEqual(total[4], total[5])
        self.assertGreater(total[4], 0)

    def test_screen_limit_truncates_but_the_full_export_does_not(self):
        original = reports.JOURNAL_LIMIT
        reports.JOURNAL_LIMIT = 3
        self.addCleanup(setattr, reports, "JOURNAL_LIMIT", original)
        short = reports.journal(self.con, "2026-11-01", "2026-11-30")
        full = reports.journal(self.con, "2026-11-01", "2026-11-30", full=True)
        self.assertIn("أول", short.subtitle)
        self.assertLess(len(short.rows), len(full.rows))
        self.assertNotIn("أول", full.subtitle)

    def test_dates_outside_the_range_are_empty(self):
        self.assertEqual([k for k, _c in reports.journal(self.con, "2027-01-01", "2027-01-31").rows], ["grand"])


class DailySalesTest(_Shop):
    def test_days_split_payment_classes_and_net_of_returns(self):
        self.sell("2026-11-10", [SaleLineIn(self.tire, 1, 200000)], [Payment("credit", 200000)], customer=self.cust)
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 1, "sound")], reason="عيب في الصنف", return_date="2026-11-12")
        t = reports.daily_sales(self.con, "2026-11-01", "2026-11-30")
        rows = {cells[0]: cells for kind, cells in t.rows if kind == "line"}
        self.assertEqual(rows["2026-11-10"][1:], ("2", 800000, 0, 800000, 600000, 0, 200000))
        self.assertEqual(rows["2026-11-12"][1:5], ("0", 0, 200000, -200000))
        grand = t.rows[-1][1]
        self.assertEqual((grand[2], grand[3], grand[4]), (800000, 200000, 600000))


class TopItemsTest(_Shop):
    def test_items_rank_by_net_sales_value_with_shares(self):
        self.sell("2026-11-11", [SaleLineIn(self.service, 1, 100000)], [Payment("cash", 100000, received=100000)])
        t = reports.top_items(self.con, "2026-11-01", "2026-11-30")
        lines = [cells for kind, cells in t.rows if kind == "line"]
        self.assertEqual([(c[0], c[1], c[4]) for c in lines], [("1", "TR-1", 600000), ("2", "SV-1", 100000)])
        self.assertEqual(lines[0][5], "85.7%")
        self.assertEqual(t.rows[-1][1][4], 700000)

    def test_returns_reduce_the_ranking_value(self):
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 3, "sound")], reason="عيب في الصنف", return_date="2026-11-12")
        t = reports.top_items(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual([k for k, _c in t.rows], ["grand"])           # fully returned: nothing left to rank


class SlowMovingTest(_Shop):
    def names(self, end, days):
        t = reports.slow_moving(self.con, end, days)
        return [cells[0] for kind, cells in t.rows if kind == "line"]

    def test_threshold_uses_last_sale_or_first_receipt(self):
        # TR-1 last sold 10 Nov (40 days before 20 Dec); TR-2 never sold, received 1 Nov (49 days)
        self.assertEqual(self.names("2026-12-20", "30"), ["TR-2", "TR-1"])
        self.assertEqual(self.names("2026-12-20", "45"), ["TR-2"])
        self.assertEqual(self.names("2026-12-20", "60"), [])

    def test_sold_out_items_and_services_never_appear(self):
        self.sell("2026-11-11", [SaleLineIn(self.service, 1, 100000)], [Payment("cash", 100000, received=100000)])
        self.assertNotIn("SV-1", self.names("2027-06-01", "30"))
        self.sell("2026-11-12", [SaleLineIn(self.idle_tire, 1, 200000)], [Payment("cash", 200000, received=200000)])
        self.assertNotIn("TR-2", self.names("2027-06-01", "30"))

    def test_value_comes_from_the_cost_layers_and_bad_days_are_refused(self):
        t = reports.slow_moving(self.con, "2026-12-20", "30")
        self.assertEqual(t.rows[-1][1][3], 120000 + 90000)             # one TR-1 unit left (1,200) + TR-2 (900)
        with self.assertRaises(reports.ReportError):
            reports.slow_moving(self.con, "2026-12-20", "")


class SupplierBalancesTest(_Shop):
    def test_balance_is_invoices_less_payments_and_ties_to_the_ledger(self):
        t = reports.supplier_balances(self.con, "2026-12-31")
        self.assertEqual(t.rows[0][1][1:], (530000, 0, 530000))
        self.assertIn("يطابق الدفتر", t.subtitle)
        moved = treasury.post_movement(self.con, self.admin, request_key=self.key("pay"), kind="supplier_payment",
                                       amount=100000, memo="سداد", money_account="1230", supplier_id=self.sup,
                                       movement_date="2026-11-15", confirmed=True)
        t = reports.supplier_balances(self.con, "2026-12-31")
        self.assertEqual(t.rows[0][1][1:], (530000, 100000, 430000))
        self.assertIn("يطابق الدفتر", t.subtitle)
        self.assertEqual(reports.supplier_balances(self.con, "2026-11-14").rows[0][1][3], 530000)   # before the payment
        treasury.reverse_movement(self.con, self.admin, request_key=self.key("rev"), movement_id=moved.id, memo="خطأ")
        self.assertEqual(reports.supplier_balances(self.con, "2099-01-01").rows[0][1][3], 530000)

    def test_a_ledger_difference_is_flagged(self):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, "2026-11-20", "test", 900, "تسوية خارج المستندات",
                              [("2110", 5000, 0), ("1110", 0, 5000)])
        self.assertIn("تحذير", reports.supplier_balances(self.con, "2026-12-31").subtitle)


if __name__ == "__main__":
    unittest.main()
