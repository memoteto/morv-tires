"""Sales profit by item, instalment aging and partner statement reports."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import income_statement, installments, ledger, purchasing, reports, sale_returns, sales, treasury, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "r.db")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(self.con.close)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.n = 0

    def key(self, prefix="k"):
        self.n += 1
        return f"{prefix}-key-{self.n:04d}"

    def item(self, kind, code):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id",
            (kind, code, f"صنف {code}", code.lower(), 200000, 180000, 160000)).fetchone()[0]


class SalesProfitTest(_Base):
    def setUp(self):
        super().setUp()
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1")
        for qty, cost, dot in ((2, 100000, "2024"), (2, 120000, "2124")):
            purchasing.post_invoice(self.con, self.admin, request_key=self.key("buy"), supplier_id=sup,
                                    supplier_invoice_no=self.key("S"), invoice_date="2026-11-01",
                                    lines=[PurchaseLine(self.tire, qty, cost, dot)])
        self.sale = sales.post_sale(self.con, self.admin, request_key=self.key("sale"), customer_id=None,
                                    lines=[SaleLineIn(self.tire, 3, 200000)],
                                    payments=[Payment("cash", 600000, received=600000)], sale_date="2026-11-10")

    def row(self, table):
        return next(cells for kind, cells in table.rows if kind == "line")

    def test_profit_uses_the_real_cost_layers_and_matches_the_income_statement(self):
        t = reports.sales_profit(self.con, "2026-11-01", "2026-11-30")
        code, _name, qty, sales_, returns, cost, profit, margin = self.row(t)
        self.assertEqual((code, qty, sales_, returns, cost, profit), ("TR-1", "3", 600000, 0, 320000, 280000))
        self.assertEqual(margin, "46.7%")
        gross = income_statement.build(self.con, "2026-11-01", "2026-11-30").gross_profit
        self.assertEqual(t.rows[-1][1][6], gross)

    def test_returns_reduce_sales_quantity_and_cost(self):
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key("ret"), sale_id=self.sale.id,
                                 lines=[ReturnLineIn(line, 1, "sound")], reason="عيب في الصنف",
                                 return_date="2026-11-12")
        t = reports.sales_profit(self.con, "2026-11-01", "2026-11-30")
        _c, _n, qty, sales_, returns, cost, profit, _m = self.row(t)
        self.assertEqual((qty, sales_, returns, cost, profit), ("2", 600000, 200000, 220000, 180000))
        gross = income_statement.build(self.con, "2026-11-01", "2026-11-30").gross_profit
        self.assertEqual(t.rows[-1][1][6], gross)

    def test_period_outside_the_sale_is_empty_apart_from_the_total(self):
        t = reports.sales_profit(self.con, "2026-12-01", "2026-12-31")
        self.assertEqual([k for k, _c in t.rows], ["grand"])
        self.assertEqual(t.rows[0][1][7], "")

    def test_margin_text_rounds_from_integers(self):
        self.assertEqual(reports._bp_text(1, 3), "33.3%")
        self.assertEqual(reports._bp_text(-1, 3), "-33.3%")
        self.assertEqual(reports._bp_text(5, 0), "")


class AgingTest(_Base):
    def setUp(self):
        super().setUp()
        customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) VALUES ('عميل وهمي','عميل وهمي','01000000041',0) "
            "RETURNING id").fetchone()[0]
        service = self.item("service", "IR-SVC")
        sale = sales.post_sale(self.con, self.admin, request_key=self.key("sale"), customer_id=customer,
                               lines=[SaleLineIn(service, 1, 1200000)], payments=[Payment("installment", 1200000)],
                               sale_date="2025-07-10")
        self.contract = installments.post_contract(
            self.con, self.admin, request_key=self.key("con"), sale_id=sale.id, down=200000, rate_bp=1500, months=10,
            first_due="2025-08-13", national_id="29001011201234", down_method="cash", contract_date="2025-07-13")

    def pay(self, day, amount=115000):
        installments.collect_payment(self.con, self.admin, request_key=self.key("pay"), contract_id=self.contract.id,
                                     amount=amount, method="cash", payment_date=day)

    def buckets(self, end):
        t = reports.installment_aging(self.con, end)
        return t, next(cells for kind, cells in t.rows if kind == "line")

    def test_rows_fall_into_buckets_by_days_past_due(self):
        t, cells = self.buckets("2025-09-20")
        self.assertEqual(cells[0], self.contract.number)
        not_due, d30, d60, d90, over90, total = cells[2:]
        self.assertEqual((d30, d60, d90, over90), (115000, 115000, 0, 0))      # 7 days and 38 days late
        self.assertEqual(not_due, 115000 * 8)
        self.assertEqual(total, 115000 * 10)
        self.assertEqual(t.rows[-1][1][-1], total)

    def test_payments_after_the_date_do_not_reduce_it_and_those_before_do(self):
        self.pay("2025-09-01")
        _t, before = self.buckets("2025-08-31")
        self.assertEqual(before[-1], 115000 * 10)
        self.assertEqual(before[3], 115000)                                  # 18 days late on 31 August
        _t, after = self.buckets("2025-09-20")
        self.assertEqual(after[-1], 115000 * 9)

    def test_nothing_before_the_contract_exists(self):
        t = reports.installment_aging(self.con, "2025-07-01")
        self.assertEqual([k for k, _c in t.rows], ["grand"])


class PartnerStatementTest(_Base):
    def test_statement_shows_the_partner_account_and_capital(self):
        partner = treasury.add_partner(self.con, self.admin, "أحمد")
        for day, debit, credit, amount in (("2026-11-01", "1110", partner.account, 50_000),
                                          ("2026-11-05", partner.account, "1110", 20_000),
                                          ("2026-11-06", "1110", partner.capital_account, 100_000)):
            self.n += 1
            with transaction(self.con):
                ledger.post_entry(self.con, self.admin, day, "test", 900 + self.n, "قيد وهمي",
                                  [(debit, amount, 0), (credit, 0, amount)])
        t = reports.partner_statement(self.con, partner.account, "2026-11-01", "2026-11-30")
        self.assertEqual(t.rows[-1][1][5], 30_000)            # shop owes the partner 30,000
        self.assertIn("1,000.00", t.subtitle)                  # capital
        self.assertIn("أحمد", t.title)
        self.assertEqual(reports.param_choices(self.con, "partner"), [(partner.account, "أحمد")])

    def test_unknown_partner_account_is_refused(self):
        with self.assertRaises(reports.ReportError):
            reports.partner_statement(self.con, "1110", "2026-11-01", "2026-11-30")


if __name__ == "__main__":
    unittest.main()
