"""Stock valuation, expenses, collections, purchases and sale returns reports."""
from morv.core.sales_rules import Payment
from morv.infra import customer_receipts, ledger, reports, sale_returns
from morv.infra.db.connection import transaction
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_reports_batch3 import _Shop


def lines(table):
    return [cells for kind, cells in table.rows if kind == "line"]


class StockValuationTest(_Shop):
    def test_values_come_from_layers_with_oldest_dot_and_tie_to_1410(self):
        t = reports.stock_valuation(self.con, "2026-11-30")
        rows = {c[0]: c for c in lines(t)}
        self.assertEqual(rows["TR-1"][2:], ("1", 120000, 120000, "2124", "2"))   # 3 of 4 sold, oldest layer first
        self.assertEqual(rows["TR-2"][2:5], ("1", 90000, 90000))
        self.assertEqual(t.rows[-1][1][3], 210000)
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_sold_out_items_disappear_and_sound_returns_come_back(self):
        self.sell("2026-11-11", [SaleLineIn(self.tire, 1, 200000)], [Payment("cash", 200000, received=200000)])
        self.assertEqual([c[0] for c in lines(reports.stock_valuation(self.con, "2026-11-30"))], ["TR-2"])
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 1, "sound")], reason="عيب في الصنف", return_date="2026-11-12")
        t = reports.stock_valuation(self.con, "2026-11-30")
        self.assertEqual({c[0] for c in lines(t)}, {"TR-1", "TR-2"})
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_defective_stock_is_shown_apart_from_the_valued_total(self):
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 1, "defective")], reason="عيب في الصنف",
                                 return_date="2026-11-12")
        t = reports.stock_valuation(self.con, "2026-11-30")
        self.assertIn("معيب بانتظار المورد 1430", t.subtitle)
        self.assertIn("يطابق الدفتر", t.subtitle)


class ExpensesTest(_Shop):
    def spend(self, day, account, amount, n):
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "treasury_movement", 900 + n, "مصروف وهمي",
                              [(account, amount, 0), ("1110", 0, amount)])

    def test_expense_accounts_in_range_with_shares_and_no_cost_of_sales(self):
        self.spend("2026-11-05", "5220", 30000, 1)
        self.spend("2026-11-06", "5210", 70000, 2)
        self.spend("2026-12-06", "5210", 99999, 3)
        t = reports.expenses(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual([(c[0], c[2], c[3]) for c in lines(t)], [("5210", 70000, "70.0%"), ("5220", 30000, "30.0%")])
        self.assertEqual(t.rows[-1][1][2], 100000)

    def test_empty_period_gives_a_zero_total(self):
        self.assertEqual(reports.expenses(self.con, "2026-11-01", "2026-11-30").rows[-1][1][2], 0)


class CollectionsTest(_Shop):
    def test_receipts_per_day_split_by_method_and_tied_to_the_ledger(self):
        self.sell("2026-11-10", [SaleLineIn(self.service, 1, 100000)], [Payment("credit", 100000)], customer=self.cust)
        first = customer_receipts.post_receipt(self.con, self.admin, request_key=self.key("rc"), customer_id=self.cust,
                                               amount=40000, method="cash", receipt_date="2026-11-12")
        customer_receipts.post_receipt(self.con, self.admin, request_key=self.key("rc"), customer_id=self.cust,
                                       amount=20000, method="transfer", receipt_date="2026-11-12")
        t = reports.collections(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual(lines(t), [("2026-11-12", 60000, 0, 0, 60000, 40000, 20000)])
        self.assertIn("يطابق الدفتر", t.subtitle)
        customer_receipts.reverse_receipt(self.con, self.admin, request_key=self.key("rv"), receipt_id=first.id,
                                          reason="خطأ")
        net = reports.collections(self.con, "2026-11-01", "2026-12-31")
        self.assertEqual(net.rows[-1][1][4], 20000)
        self.assertIn("يطابق الدفتر", net.subtitle)


class PurchasesTest(_Shop):
    def test_suppliers_with_their_items_and_the_2110_tie(self):
        t = reports.purchases(self.con, "2026-11-01", "2026-11-30")
        kinds = [k for k, _c in t.rows]
        self.assertEqual(kinds, ["head", "line", "line", "grand"])
        rows = {c[1]: c for k, c in t.rows if k == "line"}
        self.assertEqual((rows["TR-1"][2], rows["TR-1"][3], rows["TR-1"][4]), ("4", 440000, 110000))
        self.assertEqual(t.rows[-1][1][3], 530000)
        self.assertIn("يطابق الدفتر", t.subtitle)
        self.assertEqual([k for k, _c in reports.purchases(self.con, "2026-12-01", "2026-12-31").rows], ["grand"])


class ReturnsTest(_Shop):
    def test_by_reason_and_by_item_sound_against_defective_with_the_4190_tie(self):
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 1, "sound")], reason="مقاس خاطئ", return_date="2026-11-12")
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 2, "defective")], reason="عيب في الصنف",
                                 return_date="2026-11-13")
        t = reports.returns(self.con, "2026-11-01", "2026-11-30")
        got = {c[0]: c[1:] for c in lines(t)}
        self.assertEqual(got["عيب في الصنف"], ("1", "0", "2", 400000))
        self.assertEqual(got["مقاس خاطئ"], ("1", "1", "0", 200000))
        self.assertEqual(got["TR-1 · صنف TR-1"], ("2", "1", "2", 600000))
        self.assertIn("يطابق الدفتر", t.subtitle)
        self.assertEqual([k for k, _c in t.rows].count("total"), 2)

    def test_no_returns_in_range_builds_two_empty_sections(self):
        t = reports.returns(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual(lines(t), [])
        self.assertIn("يطابق الدفتر", t.subtitle)
