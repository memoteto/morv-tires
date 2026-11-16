"""Instalment payment-collection tests: allocation across rows, the entry, and refusals."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installment_view, installments, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.installments import ContractError
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class PaymentTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "ip.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        self.n = 0
        sale = sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=self.cust,
                               lines=[SaleLineIn(self.service, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-11-10")
        self.contract = installments.post_contract(
            self.con, self.admin, request_key="ic-key-0001", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-12-13", national_id=NID, down_method="cash", contract_date="2026-11-13")
        self.rows = self.con.execute("SELECT id, no, amount FROM installment_rows WHERE contract_id=? ORDER BY no",
                                     (self.contract.id,)).fetchall()

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def pay(self, by=None, key=None, **kw):
        self.n += 1
        kw.setdefault("contract_id", self.contract.id)
        kw.setdefault("amount", self.rows[0][2])
        kw.setdefault("method", "cash")
        kw.setdefault("payment_date", "2026-11-14")
        return installments.collect_payment(self.con, by or self.admin, request_key=key or f"ip-key-{self.n:04d}",
                                            **kw)

    def balance(self, code):
        return ledger.account_balance(self.con, code)

    # ---------- Accepted ----------
    def test_exact_row_payment_books_cash_against_receivable(self):
        before = self.balance("1320")
        posted = self.pay()
        self.assertEqual(posted.number, "IP-2026-000001")
        self.assertEqual(self.balance("1110"), 200000 + self.rows[0][2])   # down payment + this collection
        self.assertEqual(self.balance("1320"), before - self.rows[0][2])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_payment_allocates_oldest_row_first(self):
        self.pay(amount=self.rows[0][2])
        remaining = installments.remaining_rows(self.con, self.contract.id)
        self.assertEqual([r[0] for r in remaining], [r[0] for r in self.rows[1:]])

    def test_partial_payment_leaves_remainder_on_same_row(self):
        half = self.rows[0][2] // 2
        self.pay(amount=half)
        remaining = installments.remaining_rows(self.con, self.contract.id)
        self.assertEqual(remaining[0][0], self.rows[0][0])
        self.assertEqual(remaining[0][4], self.rows[0][2] - half)

    def test_payment_spanning_two_rows_splits_correctly(self):
        self.pay(amount=self.rows[0][2] + 1000)
        remaining = installments.remaining_rows(self.con, self.contract.id)
        self.assertEqual(remaining[0][0], self.rows[1][0])
        self.assertEqual(remaining[0][4], self.rows[1][2] - 1000)

    def test_replayed_key_returns_the_same_payment(self):
        first = self.pay(key="same-payment-key")
        again = self.pay(key="same-payment-key")
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.con.execute("SELECT count(*) FROM installment_payments").fetchone()[0], 1)

    # ---------- Refused ----------
    def test_overpayment_beyond_remaining_is_refused(self):
        total_remaining = sum(r[2] for r in self.rows)
        before = self.balance("1320")
        with self.assertRaises(ContractError):
            self.pay(amount=total_remaining + 1)
        self.assertEqual(self.balance("1320"), before)

    def test_cashier_cannot_collect(self):
        with self.assertRaises(ContractError):
            self.pay(by=self.cashier)

    def test_payment_before_contract_date_is_refused(self):
        with self.assertRaises(ContractError):
            self.pay(payment_date="2026-11-01")

    def test_bad_method_is_refused(self):
        with self.assertRaises(ContractError):
            self.pay(method="credit")

    def test_zero_amount_is_refused(self):
        with self.assertRaises(ContractError):
            self.pay(amount=0)

    def test_fully_paid_contract_refuses_further_payment(self):
        self.pay(amount=sum(r[2] for r in self.rows))
        with self.assertRaises(ContractError):
            self.pay(key="extra-key", amount=1)

    def test_posted_payment_cannot_be_edited_or_deleted(self):
        self.pay()
        for sql in ("UPDATE installment_payments SET amount = 1", "DELETE FROM installment_payments",
                    "UPDATE installment_payment_lines SET amount = 1", "DELETE FROM installment_payment_lines"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.con.execute(sql)


class CollectionViewTest(PaymentTest):
    """The Collect screen's summary and per-row status, built on top of collect_payment."""

    def test_fresh_contract_has_no_overdue_and_next_due_is_first_row(self):
        due_date = self.con.execute("SELECT due_date FROM installment_rows WHERE id=?", (self.rows[0][0],)).fetchone()[0]
        v = installment_view.collection_view(self.con, self.contract.id, today="2026-11-14")
        self.assertEqual((v.paid_total, v.overdue_count, v.overdue_amount), (0, 0, 0))
        self.assertEqual(v.next_due, (due_date, self.rows[0][2]))
        self.assertEqual(v.rows[0].status, "لم يحن")

    def test_paid_row_is_removed_from_overdue_and_marked_paid(self):
        self.pay(amount=self.rows[0][2])
        v = installment_view.collection_view(self.con, self.contract.id, today="2027-01-01")
        self.assertEqual(v.rows[0].status, "مدفوع")
        self.assertEqual(v.overdue_count, 0)   # row 2 (2027-01-13) is not yet due on 2027-01-01

    def test_unpaid_past_due_row_counts_as_overdue(self):
        v = installment_view.collection_view(self.con, self.contract.id, today="2026-12-20")
        self.assertEqual(v.overdue_count, 1)
        self.assertEqual(v.overdue_amount, self.rows[0][2])
        self.assertIn("متأخر", v.rows[0].status)

    def test_partial_payment_marks_row_partial_overdue(self):
        self.pay(amount=self.rows[0][2] // 2)
        v = installment_view.collection_view(self.con, self.contract.id, today="2027-02-01")
        self.assertTrue(v.rows[0].status.startswith("جزئي ·"))


if __name__ == "__main__":
    unittest.main()
