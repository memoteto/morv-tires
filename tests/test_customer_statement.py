"""Customer statement tests: opening balance, period, running balance and ledger agreement."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import customer_statement, ledger, purchasing, sale_returns, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn


class CustomerStatementTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "st.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) "
                         "VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-1','إطار','اطار',200000,200000,200000) RETURNING id").fetchone()[0]
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',50000,50000,50000) RETURNING id").fetchone()[0]
        self.cust = self.customer("01000000011")
        self.other = self.customer("01000000012")
        purchasing.post_invoice(self.con, self.admin, request_key="buy-key-0001", supplier_id=self.sup,
                                supplier_invoice_no="S-1", invoice_date="2026-01-01",
                                lines=[PurchaseLine(self.tire, 10, 100000, "2024")])
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def customer(self, mobile):
        return self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي',?,5000000) RETURNING id", (mobile,)).fetchone()[0]

    def credit_sale(self, day, amount, customer=None):
        self.n += 1
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}",
                               customer_id=customer or self.cust, lines=[SaleLineIn(self.service, 1, amount)],
                               payments=[Payment("credit", amount)], sale_date=day)

    def test_credit_sales_build_a_running_balance(self):
        self.credit_sale("2026-02-01", 100000)
        self.credit_sale("2026-03-01", 50000)
        st = customer_statement.statement(self.con, self.cust)
        self.assertEqual([r.balance for r in st.rows], [100000, 150000])
        self.assertEqual((st.debit_total, st.credit_total, st.closing), (150000, 0, 150000))

    def test_return_on_account_shows_as_credit(self):
        sale = self.credit_sale("2026-02-01", 100000)
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key="ret-key-0001", sale_id=sale.id,
                                 lines=[ReturnLineIn(line, 1)], reason="إلغاء الخدمة", return_date="2026-02-05")
        st = customer_statement.statement(self.con, self.cust)
        self.assertEqual([(r.debit, r.credit) for r in st.rows], [(100000, 0), (0, 100000)])
        self.assertEqual(st.closing, 0)

    def test_period_moves_what_is_before_it_into_the_opening_balance(self):
        self.credit_sale("2026-02-01", 100000)
        self.credit_sale("2026-05-01", 50000)
        st = customer_statement.statement(self.con, self.cust, "2026-04-01", "2026-06-01")
        self.assertEqual(st.opening, 100000)
        self.assertEqual([r.date for r in st.rows], ["2026-05-01"])
        self.assertEqual((st.debit_total, st.closing), (50000, 150000))

    def test_movements_after_the_period_do_not_move_the_closing_balance(self):
        self.credit_sale("2026-02-01", 100000)
        self.credit_sale("2026-09-01", 70000)
        st = customer_statement.statement(self.con, self.cust, "2026-01-01", "2026-03-01")
        self.assertEqual((len(st.rows), st.closing), (1, 100000))

    def test_one_customer_never_sees_another(self):
        self.credit_sale("2026-02-01", 100000)
        self.credit_sale("2026-02-02", 70000, customer=self.other)
        self.assertEqual(customer_statement.statement(self.con, self.cust).closing, 100000)
        self.assertEqual(customer_statement.statement(self.con, self.other).closing, 70000)

    def test_cash_sale_never_touches_the_customer_account(self):
        self.n += 1
        sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", customer_id=self.cust,
                        lines=[SaleLineIn(self.service, 1, 40000)],
                        payments=[Payment("cash", 40000, received=40000)], sale_date="2026-02-01")
        self.assertEqual(customer_statement.statement(self.con, self.cust).rows, ())

    def test_balances_add_up_to_the_ledger_account(self):
        """Every movement on 1310 must belong to a customer, or a statement would quietly lose it."""
        self.credit_sale("2026-02-01", 100000)
        self.credit_sale("2026-02-02", 70000, customer=self.other)
        total = sum(customer_statement.statement(self.con, c).closing for c in (self.cust, self.other))
        self.assertEqual(total, ledger.account_balance(self.con, customer_statement.CUSTOMER_ACCOUNT))
        self.assertEqual(customer_statement.unassigned(self.con), [])

    def test_bad_or_reversed_dates_are_refused(self):
        for a, b in (("2026-13-99", ""), ("", "not-a-date"), ("2026-06-01", "2026-01-01")):
            with self.assertRaises(ValueError):
                customer_statement.statement(self.con, self.cust, a, b)

    def test_unknown_customer_is_refused(self):
        with self.assertRaises(LookupError):
            customer_statement.statement(self.con, 9999)


if __name__ == "__main__":
    unittest.main()
