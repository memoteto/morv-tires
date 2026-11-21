"""Customer deposit tests: take, refund, apply on debt, reversals, credit-limit headroom and report ties."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import customer_deposits as cd
from morv.infra import customer_receipts, customer_statement, ledger, reports, sale_returns, sales, users
from morv.infra.customer_deposits import DepositError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleError, SaleLineIn


class CustomerDepositsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "cd.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.clerk = users.create_user(self.con, self.admin, "clerk", "موظف", "Clerk12345", "cashier")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',50000,50000,50000) RETURNING id").fetchone()[0]
        self.cust = self.customer("01000000011", 0)
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def customer(self, mobile, limit):
        return self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي',?,?) RETURNING id", (mobile, limit)).fetchone()[0]

    def key(self):
        self.n += 1
        return f"dep-key-{self.n:04d}"

    def doc(self, kind, amount, method="cash", day="2026-02-05", customer=None, **kw):
        return cd.post_document(self.con, kw.pop("by", self.admin), request_key=kw.pop("key", self.key()),
                                customer_id=customer or self.cust, kind=kind, amount=amount,
                                method=None if kind == "apply" else method, doc_date=day)

    def credit_sale(self, amount, day="2026-02-01"):
        return sales.post_sale(self.con, self.admin, request_key=self.key(), customer_id=self.cust,
                               lines=[SaleLineIn(self.service, 1, amount)], payments=[Payment("credit", amount)],
                               sale_date=day)

    def held(self):
        return customer_receipts.deposit_balance(self.con, self.cust)

    def test_deposit_is_a_liability_not_income_and_refund_returns_it(self):
        self.doc("deposit", 80000, "transfer")
        self.assertEqual((ledger.account_balance(self.con, "2340"), ledger.account_balance(self.con, "1210")),
                         (-80000, 80000))
        self.doc("refund", 30000, "cash")
        self.assertEqual(self.held(), 50000)
        self.assertEqual(ledger.account_balance(self.con, "1110"), -30000)
        self.assertEqual(ledger.account_balance(self.con, "4110"), 0)

    def test_refund_and_apply_are_capped_by_the_held_deposit_and_the_debt(self):
        self.doc("deposit", 50000)
        with self.assertRaises(DepositError):
            self.doc("refund", 50001)
        with self.assertRaises(DepositError):
            self.doc("apply", 1000)                                    # no debt yet
        self.credit_sale(30000)
        with self.assertRaises(DepositError):
            self.doc("apply", 30001)
        self.doc("apply", 30000)
        self.assertEqual((self.held(), customer_receipts.customer_balance(self.con, self.cust)), (20000, 0))
        self.assertEqual(ledger.account_balance(self.con, "1310"), 0)

    def test_credit_limit_zero_customer_can_buy_on_credit_only_up_to_the_deposit(self):
        with self.assertRaises(SaleError):
            self.credit_sale(10000)
        self.doc("deposit", 40000)
        self.credit_sale(40000)
        with self.assertRaises(SaleError):
            self.credit_sale(10000, day="2026-02-02")
        self.doc("apply", 40000)
        self.assertEqual(customer_receipts.customer_balance(self.con, self.cust), 0)

    def test_statement_shows_the_application_and_ties_to_1310(self):
        self.doc("deposit", 50000)
        self.credit_sale(40000)
        self.doc("apply", 40000)
        st = customer_statement.statement(self.con, self.cust)
        self.assertEqual([(r.debit, r.credit) for r in st.rows], [(40000, 0), (0, 40000)])
        self.assertEqual(customer_statement.unassigned(self.con), [])

    def test_reversals_restore_balances_once_and_guard_consumed_deposits(self):
        dep = self.doc("deposit", 50000)
        self.credit_sale(30000)
        applied = self.doc("apply", 30000)
        with self.assertRaises(DepositError):
            cd.reverse_document(self.con, self.admin, request_key=self.key(), document_id=dep.id, reason="خطأ")
        cd.reverse_document(self.con, self.admin, request_key=self.key(), document_id=applied.id, reason="خطأ")
        self.assertEqual((self.held(), customer_receipts.customer_balance(self.con, self.cust)), (50000, 30000))
        with self.assertRaises(DepositError):
            cd.reverse_document(self.con, self.admin, request_key=self.key(), document_id=applied.id, reason="مرة ثانية")
        cd.reverse_document(self.con, self.admin, request_key=self.key(), document_id=dep.id, reason="خطأ")
        self.assertEqual(self.held(), 0)
        self.assertEqual(ledger.account_balance(self.con, "2340"), 0)

    def test_reversing_a_refund_puts_the_deposit_back(self):
        self.doc("deposit", 50000)
        refund = self.doc("refund", 20000)
        cd.reverse_document(self.con, self.admin, request_key=self.key(), document_id=refund.id, reason="خطأ")
        self.assertEqual(self.held(), 50000)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 50000)

    def test_replay_changed_payload_permission_and_validation(self):
        first = self.doc("deposit", 50000, key="same-key-0001")
        self.assertTrue(self.doc("deposit", 50000, key="same-key-0001").replayed)
        with self.assertRaises(DepositError):
            self.doc("deposit", 60000, key="same-key-0001")
        self.assertEqual((self.held(), first.number[:8]), (50000, "CD-2026-"))
        for bad in (0, -1, 5.5):
            with self.assertRaises(DepositError):
                self.doc("deposit", bad)
        with self.assertRaises(DepositError):
            self.doc("deposit", 1000, "credit")
        with self.assertRaises(DepositError):
            self.doc("deposit", 1000, day="2999-01-01")
        with self.assertRaises(DepositError):
            self.doc("deposit", 1000, by=self.clerk)
        with self.assertRaises(DepositError):
            self.doc("deposit", 1000, customer=9999)

    def test_money_refund_of_a_deposit_settled_invoice_uses_the_applied_cash(self):
        self.doc("deposit", 50000)
        sale = self.credit_sale(50000)
        self.doc("apply", 50000)
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key=self.key(), sale_id=sale.id,
                                 lines=[ReturnLineIn(line, 1)], reason="إلغاء الخدمة", refund_method="cash",
                                 return_date="2026-02-20")
        self.assertEqual((ledger.account_balance(self.con, "1310"), ledger.account_balance(self.con, "1110")), (0, 0))
        applied = cd.history(self.con, self.cust)[0]
        with self.assertRaises(DepositError):
            cd.reverse_document(self.con, self.admin, request_key=self.key(), document_id=applied[0], reason="خطأ")

    def test_credit_aging_and_collections_include_deposits(self):
        self.doc("deposit", 60000, "cash", day="2026-02-10")
        self.credit_sale(60000)
        self.doc("apply", 30000, day="2026-02-12")
        aging = reports.credit_aging(self.con, "2026-02-28")
        self.assertEqual([c[-1] for k, c in aging.rows if k == "line"], [30000])
        self.assertIn("يطابق الدفتر", aging.subtitle)
        coll = reports.collections(self.con, "2026-02-01", "2026-02-28")
        self.assertEqual([c[3] for k, c in coll.rows if k == "line"], [60000])
        self.assertIn("يطابق الدفتر", coll.subtitle)

    def test_documents_are_immutable(self):
        doc = self.doc("deposit", 50000)
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE customer_deposits SET amount=1 WHERE id=?", (doc.id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM customer_deposits WHERE id=?", (doc.id,))
