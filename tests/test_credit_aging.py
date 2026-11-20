"""Credit-sale aging report: due dates, oldest-first receipts, returns, as-of dates and ledger tie."""
from morv.core.sales_rules import Payment
from morv.infra import customer_receipts, reports, sale_returns, sales
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_reports_batch2 import _Base


class CreditAgingTest(_Base):
    def setUp(self):
        super().setUp()
        self.service = self.item("service", "SV-1")
        self.cust = self.customer("01000000011", 30)
        self.n_sale = 0

    def customer(self, mobile, days):
        return self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit, credit_days) "
            "VALUES ('عميل وهمي','عميل وهمي',?,5000000,?) RETURNING id", (mobile, days)).fetchone()[0]

    def credit(self, day, amount, customer=None):
        return sales.post_sale(self.con, self.admin, request_key=self.key("sale"), customer_id=customer or self.cust,
                               lines=[SaleLineIn(self.service, 1, amount)], payments=[Payment("credit", amount)],
                               sale_date=day)

    def receive(self, amount, day):
        return customer_receipts.post_receipt(self.con, self.admin, request_key=self.key("rcpt"),
                                              customer_id=self.cust, amount=amount, method="cash", receipt_date=day)

    def line(self, table):
        return [cells for kind, cells in table.rows if kind == "line"]

    def test_invoices_age_from_sale_date_plus_credit_days(self):
        self.credit("2026-09-01", 100000)      # due 10-01: 50 days late on 11-20 -> 31-60
        self.credit("2026-11-10", 50000)       # due 12-10: not due
        t = reports.credit_aging(self.con, "2026-11-20")
        self.assertEqual(self.line(t), [("عميل وهمي", 50000, 0, 100000, 0, 0, 150000)])
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_receipts_settle_the_oldest_invoice_first(self):
        self.credit("2026-09-01", 100000)
        self.credit("2026-11-10", 50000)
        self.receive(120000, "2026-11-12")
        t = reports.credit_aging(self.con, "2026-11-20")
        self.assertEqual(self.line(t), [("عميل وهمي", 30000, 0, 0, 0, 0, 30000)])
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_as_of_date_ignores_later_receipts_and_invoices(self):
        self.credit("2026-09-01", 100000)
        self.receive(100000, "2026-11-12")
        self.credit("2026-11-15", 20000)
        before = reports.credit_aging(self.con, "2026-11-11")
        self.assertEqual(self.line(before), [("عميل وهمي", 0, 0, 100000, 0, 0, 100000)])
        after = reports.credit_aging(self.con, "2026-11-20")
        self.assertEqual(self.line(after), [("عميل وهمي", 20000, 0, 0, 0, 0, 20000)])

    def test_credit_return_reduces_its_own_invoice(self):
        old = self.credit("2026-09-01", 100000)
        self.credit("2026-11-10", 50000)
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (old.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=old.id,
                                 lines=[ReturnLineIn(line, 1)], reason="إلغاء", return_date="2026-11-12")
        t = reports.credit_aging(self.con, "2026-11-20")
        self.assertEqual(self.line(t), [("عميل وهمي", 50000, 0, 0, 0, 0, 50000)])
        self.assertIn("يطابق الدفتر", t.subtitle)

    def test_reversed_receipt_reopens_the_debt_and_empty_report_is_zero(self):
        self.assertEqual(reports.credit_aging(self.con, "2026-11-20").rows[-1][1][-1], 0)
        self.credit("2026-09-01", 100000)
        doc = self.receive(100000, "2026-11-12")
        customer_receipts.reverse_receipt(self.con, self.admin, request_key=self.key("rev"), receipt_id=doc.id,
                                          reason="خطأ")
        t = reports.credit_aging(self.con, "2999-01-01")
        self.assertEqual(t.rows[-1][1][-1], 100000)
        self.assertIn("يطابق الدفتر", t.subtitle)
