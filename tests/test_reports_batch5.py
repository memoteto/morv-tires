"""Supplier aging and item card report tests: buckets, settlement order, ties to the ledger and stock."""
import unittest

from morv.infra import inventory, purchase_returns, reports, sales, treasury
from morv.core.sales_rules import Payment
from morv.infra.purchase_returns import ReturnLine
from morv.infra.reports import ReportError
from morv.infra.sales import SaleLineIn
from tests.test_purchasing import Base


class SupplierAgingTest(Base):
    def setUp(self):
        super().setUp()
        self.con.execute("UPDATE suppliers SET payment_days = 30")

    def pay(self, amount, day="2026-11-15"):
        self.n += 1
        return treasury.post_movement(self.con, self.admin, request_key=f"pay-key-{self.n:04d}", kind="supplier_payment",
                                      amount=amount, memo="سداد", money_account="1110", supplier_id=self.sup,
                                      movement_date=day, confirmed=True)

    def aging(self, end="2026-11-20"):
        return reports.build(self.con, "supplier_aging", "2026-01-01", end)

    def test_buckets_follow_due_date_and_payments_settle_the_oldest(self):
        self.buy(self.tire, 1, 100000, invoice_date="2026-09-01")        # due 2026-10-01: 50 days late
        self.buy(self.tire2, 1, 50000, invoice_date="2026-11-10")        # due 2026-12-10: not yet due
        t = self.aging()
        line = [r[1] for r in t.rows if r[0] == "line"][0]
        self.assertEqual((line[1], line[3], line[-1]), (50000, 100000, 150000))     # not due, 31-60 days, total
        self.assertIn("يطابق الدفتر", t.subtitle)
        self.pay(30000)
        line = [r[1] for r in self.aging().rows if r[0] == "line"][0]
        self.assertEqual((line[1], line[3], line[-1]), (50000, 70000, 120000))      # the oldest invoice took it
        self.assertIn("يطابق الدفتر", self.aging().subtitle)

    def test_overpayment_shows_as_our_credit_and_still_ties(self):
        self.buy(self.tire, 1, 100000, invoice_date="2026-11-01")
        self.pay(130000)
        t = self.aging()
        line = [r[1] for r in t.rows if r[0] == "line"][0]
        self.assertEqual((line[-2], line[-1]), (-30000, -30000))
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_returns_reduce_the_open_amount(self):
        self.buy(self.tire, 2, 100000, invoice_date="2026-11-01")
        line_id = self.con.execute("SELECT id FROM purchase_lines").fetchone()[0]
        purchase_returns.post_return(self.con, self.admin, request_key="ret-key-0001", supplier_id=self.sup,
                                     lines=[ReturnLine(line_id, 1)], reason="عيب تصنيع", return_date="2026-11-10")
        t = self.aging()
        self.assertEqual([r[1][-1] for r in t.rows if r[0] == "grand"], [100000])
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_return_settlement_differing_from_book_value_still_ties(self):
        # full return of a 200000 invoice settled for only 180000: 20000 stays payable (matches
        # supplier_balances/treasury.supplier_owed), not zero as book-value aggregation would show.
        self.buy(self.tire, 2, 100000, invoice_date="2026-11-01")
        line_id = self.con.execute("SELECT id FROM purchase_lines").fetchone()[0]
        purchase_returns.post_return(self.con, self.admin, request_key="ret-key-0002", supplier_id=self.sup,
                                     lines=[ReturnLine(line_id, 2)], reason="فحص الجودة", return_date="2026-11-10",
                                     settlement_amount=180000, settlement_reason="خصم المورد قيمة الفحص")
        t = self.aging()
        self.assertEqual([r[1][-1] for r in t.rows if r[0] == "grand"], [20000])
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 20000)
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_date_filter_ignores_later_documents(self):
        self.buy(self.tire, 1, 100000, invoice_date="2026-11-01")
        self.assertEqual(self.aging("2026-10-31").rows[-1][1][-1], 0)


class ItemCardTest(Base):
    def card(self, item=None, start="2026-01-01", end="2026-12-31"):
        return reports.build(self.con, "item_card", start, end, str(item if item is not None else self.tire))

    def test_movements_running_balance_and_tie(self):
        self.buy(self.tire, 4, 100000)
        line = self.con.execute("SELECT id FROM purchase_lines").fetchone()[0]
        sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=None, sale_date="2026-11-10",
                        lines=[SaleLineIn(self.tire, 1, 150000)], payments=[Payment("cash", 150000, received=150000)])
        purchase_returns.post_return(self.con, self.admin, request_key="ret-key-0001", supplier_id=self.sup,
                                     lines=[ReturnLine(line, 2)], reason="عيب تصنيع", return_date="2026-11-12")
        t = self.card()
        lines = [r[1] for r in t.rows if r[0] == "line"]
        self.assertEqual([x[1] for x in lines], ["استلام شراء", "صرف", "مرتجع للمورد"])
        self.assertEqual([x[5] for x in lines], ["4", "3", "1"])
        self.assertEqual(t.rows[-1][1][4], "1")
        for _kind, cells in t.rows:
            self.assertEqual(len(cells), len(t.headers))
        self.assertIn("يطابق رصيد الدفعات", t.subtitle)
        self.assertTrue(lines[1][3].startswith("S"))              # the sale number is shown as the reference
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_opening_balance_and_period_edges(self):
        self.buy(self.tire, 3, 100000)
        t = self.card(start="2099-01-01", end="2099-01-02")        # after every movement
        self.assertEqual(t.rows[0][1][4], "3")
        self.assertEqual(t.rows[-1][1][4], "3")
        self.assertEqual([r for r in t.rows if r[0] == "line"], [])
        self.assertIn("يطابق رصيد الدفعات", t.subtitle)
        past = self.card(start="2000-01-01", end="2000-01-02")
        self.assertEqual(past.rows[-1][1][4], "0")
        self.assertIn("يطابق رصيد الدفعات", past.subtitle)

    def test_only_stocked_items_and_choices(self):
        for bad in (self.service, "", "x", 9999):
            with self.assertRaises(ReportError):
                self.card(bad)
        ids = [v for v, _l in reports.param_choices(self.con, "item_card")]
        self.assertIn(str(self.tire), ids)
        self.assertNotIn(str(self.service), ids)


class JournalLabelsTest(unittest.TestCase):
    def test_new_document_sources_have_arabic_labels(self):
        for source in ("customer_deposit", "purchase_return", "interest_recognition_reversal"):
            self.assertIn(source, reports.SOURCE_TEXT)


if __name__ == "__main__":
    unittest.main()
