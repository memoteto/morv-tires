"""Customer receipt tests: posting, limits, reversal, statement, credit limit and immutability."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import customer_receipts as cr
from morv.infra import customer_statement, ledger, period_lock, sales, users
from morv.infra.customer_receipts import ReceiptError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.sales import SaleError, SaleLineIn


class CustomerReceiptsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "cr.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.clerk = users.create_user(self.con, self.admin, "clerk", "موظف", "Clerk12345", "cashier")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',50000,50000,50000) RETURNING id").fetchone()[0]
        self.cust = self.customer("01000000011", 200000)
        self.other = self.customer("01000000012", 200000)
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def customer(self, mobile, limit):
        return self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي',?,?) RETURNING id", (mobile, limit)).fetchone()[0]

    def credit_sale(self, amount, customer=None, day="2026-02-01"):
        self.n += 1
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}",
                               customer_id=customer or self.cust, lines=[SaleLineIn(self.service, 1, amount)],
                               payments=[Payment("credit", amount)], sale_date=day)

    def receipt(self, amount, key="rcpt-key-0001", method="cash", day="2026-02-10", customer=None):
        return cr.post_receipt(self.con, self.admin, request_key=key, customer_id=customer or self.cust,
                               amount=amount, method=method, receipt_date=day)

    def test_receipt_reduces_the_debt_and_the_ledger_agrees(self):
        self.credit_sale(100000)
        doc = self.receipt(40000, method="transfer")
        self.assertTrue(doc.number.startswith("CR-2026-"))
        self.assertEqual(cr.customer_balance(self.con, self.cust), 60000)
        self.assertEqual(ledger.account_balance(self.con, "1310"), 60000)
        self.assertEqual(ledger.account_balance(self.con, "1210"), 40000)

    def test_replay_returns_the_same_document_and_changed_payload_is_refused(self):
        self.credit_sale(100000)
        first = self.receipt(40000)
        again = self.receipt(40000)
        self.assertEqual((first.id, again.replayed), (again.id, True))
        with self.assertRaises(ReceiptError):
            self.receipt(30000)
        self.assertEqual(cr.customer_balance(self.con, self.cust), 60000)

    def test_overpaying_and_no_debt_are_refused(self):
        with self.assertRaises(ReceiptError):
            self.receipt(1000)
        self.credit_sale(100000)
        with self.assertRaises(ReceiptError):
            self.receipt(100001)
        self.assertEqual(ledger.account_balance(self.con, "1310"), 100000)

    def test_other_customers_debt_is_not_collectable(self):
        self.credit_sale(100000)
        with self.assertRaises(ReceiptError):
            self.receipt(1000, customer=self.other)

    def test_invalid_input_and_permission(self):
        self.credit_sale(100000)
        for bad in (0, -5, 10.5):
            with self.assertRaises(ReceiptError):
                self.receipt(bad, key=f"bad-key-{abs(int(bad))}-x")
        with self.assertRaises(ReceiptError):
            self.receipt(1000, method="credit")
        with self.assertRaises(ReceiptError):
            self.receipt(1000, day="2999-01-01")
        with self.assertRaises(ReceiptError):
            self.receipt(1000, day="2026-01-01")
        with self.assertRaises(ReceiptError):
            cr.post_receipt(self.con, self.clerk, request_key="clerk-key-01", customer_id=self.cust,
                            amount=1000, method="cash")

    def test_reversal_restores_the_debt_once(self):
        self.credit_sale(100000)
        doc = self.receipt(40000)
        rev = cr.reverse_receipt(self.con, self.admin, request_key="rev-key-0001", receipt_id=doc.id, reason="خطأ")
        self.assertEqual(rev.amount, 40000)
        self.assertEqual(cr.customer_balance(self.con, self.cust), 100000)
        with self.assertRaises(ReceiptError):
            cr.reverse_receipt(self.con, self.admin, request_key="rev-key-0002", receipt_id=doc.id, reason="مرة ثانية")
        with self.assertRaises(ReceiptError):
            cr.reverse_receipt(self.con, self.admin, request_key="rev-key-0003", receipt_id=rev.id, reason="عكس العكس")
        with self.assertRaises(ReceiptError):
            cr.reverse_receipt(self.con, self.admin, request_key="rev-key-0004", receipt_id=doc.id, reason="")

    def test_statement_shows_receipts_and_reversals_for_the_right_customer(self):
        self.credit_sale(100000)
        self.credit_sale(30000, customer=self.other)
        doc = self.receipt(40000)
        cr.reverse_receipt(self.con, self.admin, request_key="rev-key-0001", receipt_id=doc.id, reason="خطأ")
        st = customer_statement.statement(self.con, self.cust)
        self.assertEqual([(r.debit, r.credit) for r in st.rows], [(100000, 0), (0, 40000), (40000, 0)])
        self.assertEqual(st.closing, 100000)
        self.assertEqual(customer_statement.statement(self.con, self.other).closing, 30000)
        self.assertEqual(customer_statement.unassigned(self.con), [])

    def test_receipt_frees_the_credit_limit_and_reversal_takes_it_back(self):
        self.credit_sale(150000)
        with self.assertRaises(SaleError):
            self.credit_sale(100000)
        doc = self.receipt(100000)
        self.credit_sale(100000)
        self.assertEqual(cr.customer_balance(self.con, self.cust), 150000)
        cr.reverse_receipt(self.con, self.admin, request_key="rev-key-0001", receipt_id=doc.id, reason="خطأ")
        with self.assertRaises(SaleError):
            self.credit_sale(50000)

    def test_receipt_into_a_locked_period_is_refused(self):
        self.credit_sale(100000)
        period_lock.lock_period(self.con, self.admin, request_key="lock-key-0001", through_date="2026-02-28")
        with self.assertRaises(ReceiptError):
            self.receipt(1000)
        self.assertEqual(cr.history(self.con, self.cust), [])

    def test_documents_are_immutable(self):
        self.credit_sale(100000)
        doc = self.receipt(40000)
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE customer_receipts SET amount=1 WHERE id=?", (doc.id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM customer_receipts WHERE id=?", (doc.id,))


if __name__ == "__main__":
    unittest.main()
