"""Interest review: what is unrecognised, beyond the engine or mis-dated; wired into the period lock."""
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installments, interest_recognition, interest_review, period_lock, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.sales import SaleLineIn

R = interest_review


class InterestReviewTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="morv_irev_"))
        self.con = open_main_db(self.folder / "test.db")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "irev_admin", "مدير وهمي", "Admin12345", "manager")
        customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) VALUES ('عميل وهمي','عميل وهمي','01000000041',0) "
            "RETURNING id").fetchone()[0]
        service = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','IR-SVC','خدمة وهمية','خدمه وهميه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key="irev-sale-0001", customer_id=customer,
                               lines=[SaleLineIn(service, 1, 1200000)], payments=[Payment("installment", 1200000)],
                               sale_date="2025-07-10")
        self.contract = installments.post_contract(
            self.con, self.admin, request_key="irev-contract-0001", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2025-08-13", national_id="29001011201234", down_method="cash",
            contract_date="2025-07-13")

    def pay(self, day, key, amount=115000):
        installments.collect_payment(self.con, self.admin, request_key=key, contract_id=self.contract.id,
                                     amount=amount, method="cash", payment_date=day)

    def recognise(self, period, entry_date):
        return interest_recognition.recognise_month(self.con, self.admin, contract_id=self.contract.id,
                                                    period=period, entry_date=entry_date)

    def test_unrecognised_months_are_ready_and_chain_without_posting(self):
        self.pay("2025-08-13", "irev-pay-0001")
        items = R.review(self.con, "2025-08-31")
        self.assertEqual([(i.period, i.status) for i in items], [("2025-07", R.READY), ("2025-08", R.READY)])
        self.assertTrue(all(i.amount > 0 for i in items))
        self.assertEqual(self.con.execute("SELECT count(*) FROM interest_recognitions").fetchone()[0], 0)

    def test_a_late_installment_puts_the_month_beyond_the_engine_with_its_reason_and_amount(self):
        items = R.review(self.con, "2025-08-31")          # the 13 August installment is unpaid at month end
        self.assertEqual([(i.period, i.status) for i in items], [("2025-07", R.READY), ("2025-08", R.BLOCKED)])
        self.assertIn("التحصيلات", items[1].reason)
        self.assertGreater(items[1].amount, 0)

    def test_recognised_months_leave_the_review_and_misdated_ones_are_reported(self):
        self.recognise("2025-07", "2025-08-01")           # July interest dated in August
        self.assertEqual(R.review(self.con, "2025-07-31")[0].status, R.MISDATED)
        self.assertEqual([i.status for i in R.review(self.con, "2025-08-01") if i.period == "2025-07"], [])

    def test_month_end_dating_is_never_misdated(self):
        self.recognise("2025-07", "2025-07-31")
        self.assertEqual(R.review(self.con, "2025-07-31"), [])

    def test_the_review_never_writes(self):
        before = self.con.execute("SELECT (SELECT count(*) FROM journal_entries), (SELECT count(*) FROM audit_log)").fetchone()
        R.review(self.con, "2026-06-30")
        self.assertEqual(self.con.execute(
            "SELECT (SELECT count(*) FROM journal_entries), (SELECT count(*) FROM audit_log)").fetchone(), before)

    def test_lock_blockers_name_the_contract_month_amount_and_reason(self):
        text = "\n".join(period_lock.blockers(self.con, "2025-08-31"))
        self.assertIn("لم يُعترف بها", text)
        self.assertIn(self.contract.number, text)
        self.assertIn("2025-07", text)
        self.assertIn("خارج نطاق الاعتراف الآلي", text)
        self.assertIn("التحصيلات", text)

    def test_blockers_clear_once_everything_is_recognised_at_month_end(self):
        self.pay("2025-08-13", "irev-pay-0002")
        self.recognise("2025-07", "2025-07-31")
        self.recognise("2025-08", "2025-08-31")
        text = "\n".join(period_lock.blockers(self.con, "2025-08-31"))
        self.assertNotIn("تقسيط", text)
        self.assertNotIn("الاعتراف الآلي", text)

    def test_months_after_the_lock_date_are_not_reviewed(self):
        self.assertEqual(R.review(self.con, "2025-06-30"), [])


if __name__ == "__main__":
    unittest.main()


    def test_post_ready_posts_at_month_end_and_stops_a_contract_at_its_first_refusal(self):
        items = R.review(self.con, "2025-08-31")          # July ready, August refused (late installment)
        result = R.post_ready(self.con, self.admin, items)
        self.assertEqual(result.posted, 1)
        self.assertEqual(result.failed, ())
        dates = [r[0] for r in self.con.execute(
            "SELECT e.entry_date FROM interest_recognitions r JOIN journal_entries e ON e.id=r.journal_entry_id")]
        self.assertEqual(dates, ["2025-07-31"])
        self.assertEqual([i.status for i in R.review(self.con, "2025-08-31")], [R.BLOCKED])

    def test_post_ready_chains_months_in_order_and_is_not_misdated(self):
        self.pay("2025-08-13", "irev-pay-0002")
        result = R.post_ready(self.con, self.admin, R.review(self.con, "2025-08-31"))
        self.assertEqual(result.posted, 2)
        self.assertEqual(R.review(self.con, "2025-08-31"), [])

    def test_post_ready_reports_a_refusal_without_raising(self):
        items = R.review(self.con, "2025-07-31")
        self.recognise("2025-07", "2025-07-31")           # already posted, so the replay by the screen must fail
        result = R.post_ready(self.con, self.admin, items)
        self.assertEqual(result.posted, 0)
        self.assertEqual(len(result.failed), 1)
