"""Interest recognition reversal tests: ledger effect, latest-month rule, re-recognition and refusals."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installments, interest_recognition, interest_reversal, interest_review, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.interest_reversal import ReversalError
from morv.infra.sales import SaleLineIn

R = interest_review


class InterestReversalTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="morv_irevsl_"))
        self.con = open_main_db(self.folder / "test.db")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "irs_admin", "مدير وهمي", "Admin12345", "manager")
        self.clerk = users.create_user(self.con, self.admin, "irs_clerk", "موظف", "Clerk12345", "cashier")
        customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) VALUES ('عميل وهمي','عميل وهمي','01000000051',0) "
            "RETURNING id").fetchone()[0]
        service = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','IRS-SVC','خدمة وهمية','خدمه وهميه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key="irs-sale-0001", customer_id=customer,
                               lines=[SaleLineIn(service, 1, 1200000)], payments=[Payment("installment", 1200000)],
                               sale_date="2025-07-10")
        self.contract = installments.post_contract(
            self.con, self.admin, request_key="irs-contract-0001", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2025-08-13", national_id="29001011201234", down_method="cash",
            contract_date="2025-07-13")
        self.n = 0

    def recognise(self, period, entry_date):
        return interest_recognition.recognise_month(self.con, self.admin, contract_id=self.contract.id,
                                                    period=period, entry_date=entry_date)

    def reverse(self, rec, by=None, key=None, reason="تاريخ خاطئ"):
        self.n += 1
        return interest_reversal.reverse(self.con, by or self.admin, request_key=key or f"irs-rev-{self.n:04d}",
                                         recognition_id=rec.id, reason=reason)

    def balance(self, code):
        return ledger.account_balance(self.con, code)

    def test_reversal_restores_the_ledger_and_the_month_can_be_recognised_again(self):
        rec = self.recognise("2025-07", "2025-09-15")
        deferred, income = self.balance("2410"), self.balance("4140")
        rev = self.reverse(rec)
        self.assertEqual(rev.amount, rec.amount)
        self.assertEqual((self.balance("2410"), self.balance("4140")), (deferred - rec.amount, income + rec.amount))   # debit-positive: 2410 is credit-normal
        self.assertEqual(interest_reversal.reversible(self.con), [])
        self.assertEqual([(i.period, i.status) for i in R.review(self.con, "2025-07-31")], [("2025-07", R.READY)])
        again = self.recognise("2025-07", "2025-07-31")
        self.assertNotEqual(again.id, rec.id)
        self.assertEqual((self.balance("2410"), self.balance("4140")), (deferred, income))
        self.assertEqual(self.con.execute("SELECT generation FROM interest_recognitions WHERE id=?",
                                          (again.id,)).fetchone()[0], 2)
        self.assertEqual(R.review(self.con, "2025-07-31"), [])

    def test_a_misdated_month_is_fixed_by_reverse_and_repost(self):
        rec = self.recognise("2025-07", "2025-09-15")
        self.assertEqual([(i.period, i.status) for i in R.review(self.con, "2025-07-31")], [("2025-07", R.MISDATED)])
        self.reverse(rec)
        self.recognise("2025-07", "2025-07-31")
        self.assertEqual(R.review(self.con, "2025-07-31"), [])

    def test_only_the_latest_month_of_a_contract_can_be_reversed(self):
        first = self.recognise("2025-07", "2025-07-31")
        second = self.recognise("2025-08", "2025-08-31")
        with self.assertRaises(ReversalError):
            self.reverse(first)
        self.assertEqual([r[2] for r in interest_reversal.reversible(self.con)], ["2025-08"])
        self.reverse(second)
        self.reverse(first)
        self.assertEqual(self.balance("4140"), 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM active_interest_recognitions").fetchone()[0], 0)

    def test_replay_conflict_double_reversal_and_validation(self):
        rec = self.recognise("2025-07", "2025-07-31")
        a = self.reverse(rec, key="irs-same-0001")
        b = self.reverse(rec, key="irs-same-0001")
        self.assertTrue(b.replayed and a.id == b.id)
        with self.assertRaises(ReversalError):
            self.reverse(rec, key="irs-same-0001", reason="سبب آخر")
        with self.assertRaises(ReversalError):
            self.reverse(rec)                                   # already reversed under a new key
        rec2 = self.recognise("2025-07", "2025-07-31")
        for kw in (dict(reason="x"), dict(key="short")):
            with self.assertRaises(ReversalError):
                self.reverse(rec2, **kw)
        with self.assertRaises(ReversalError):
            interest_reversal.reverse(self.con, self.admin, request_key="irs-miss-0001", recognition_id=99999,
                                      reason="غير موجود")
        self.assertEqual(self.con.execute("SELECT count(*) FROM interest_recognition_reversals").fetchone()[0], 1)

    def test_permission_and_immutability(self):
        rec = self.recognise("2025-07", "2025-07-31")
        with self.assertRaises(ReversalError):
            self.reverse(rec, by=self.clerk)
        self.reverse(rec)
        for sql in ("UPDATE interest_recognition_reversals SET reason='xxx'", "DELETE FROM interest_recognition_reversals",
                    "DELETE FROM interest_recognitions"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql)

    def test_contract_with_a_return_is_refused(self):
        rec = self.recognise("2025-07", "2025-07-31")
        sale_id, entry = self.con.execute("SELECT id, journal_entry_id FROM sales").fetchone()
        self.con.execute(
            "INSERT INTO sale_returns (number, request_key, payload_hash, sale_id, return_date, total, cost_total, "
            "reason, refund_method, journal_entry_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("IRS-RET-001", "irs-return-test", "legacy", sale_id, "2025-07-20", 0, 0, "synthetic", "installment", entry))
        with self.assertRaises(ReversalError):
            self.reverse(rec)
        self.assertEqual(interest_reversal.reversible(self.con), [])


if __name__ == "__main__":
    unittest.main()
