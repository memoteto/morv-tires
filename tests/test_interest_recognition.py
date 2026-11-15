"""Monthly interest recognition tests: equal split, one month at a time, never twice."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installments, interest_recognition as rec, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.interest_recognition import RecognitionError
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class RecognitionTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "ir.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key="ir-sale-key-1", customer_id=self.cust,
                               lines=[SaleLineIn(item, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-01-05")
        # First instalment in February, so the recognised months are all in the past
        self.contract = installments.post_contract(
            self.con, self.admin, request_key="ir-contract-key-1", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-02-10", national_id=NID, down_method="cash", contract_date="2026-01-06")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def counts(self):
        return tuple(self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("interest_recognitions", "interest_recognition_rows", "journal_entries"))

    def test_a_month_moves_its_share_from_deferred_to_income(self):
        """Equal split: 1500 of markup over 10 months is 150 a month, as the signed schedule says."""
        before = ledger.account_balance(self.con, "2410")
        posted = rec.recognise(self.con, self.admin, period="2026-02")
        self.assertEqual((posted.number, posted.amount, posted.rows), ("IR-2026-02", 15000, 1))
        self.assertEqual(ledger.account_balance(self.con, "2410"), before + 15000)
        self.assertEqual(ledger.account_balance(self.con, "4140"), -15000)

    def test_every_month_together_recognises_the_whole_markup(self):
        total = 0
        for m in range(2, 12):
            total += rec.recognise(self.con, self.admin, period=f"2026-{m:02d}").amount
        self.assertEqual(total, 150000)
        self.assertEqual(ledger.account_balance(self.con, "2410"), 0)
        self.assertEqual(ledger.account_balance(self.con, "4140"), -150000)

    def test_the_last_month_carries_the_piaster_difference(self):
        amounts = [rec.recognise(self.con, self.admin, period=f"2026-{m:02d}").amount for m in range(2, 12)]
        self.assertEqual(amounts[:9], [15000] * 9)
        self.assertEqual(amounts[-1], 150000 - 15000 * 9)

    def test_a_month_is_never_recognised_twice(self):
        first = rec.recognise(self.con, self.admin, period="2026-02")
        again = rec.recognise(self.con, self.admin, period="2026-02")
        self.assertEqual((again.id, again.replayed, again.amount), (first.id, True, first.amount))
        self.assertEqual(self.counts()[0], 1)

    def test_a_month_with_nothing_to_recognise_is_refused(self):
        before = self.counts()
        with self.assertRaises(RecognitionError):
            rec.recognise(self.con, self.admin, period="2026-01")
        self.assertEqual(self.counts(), before)

    def test_a_future_month_is_refused(self):
        later = date.today().replace(day=1).isoformat()[:4]
        with self.assertRaises(RecognitionError):
            rec.recognise(self.con, self.admin, period=f"{int(later) + 1}-01")

    def test_a_bad_period_is_refused(self):
        for bad in ("2026", "2026-13", "سبتمبر", ""):
            with self.assertRaises(RecognitionError, msg=bad):
                rec.recognise(self.con, self.admin, period=bad)

    def test_cashier_cannot_post_the_entry(self):
        before = self.counts()
        with self.assertRaises(RecognitionError):
            rec.recognise(self.con, self.cashier, period="2026-02")
        self.assertEqual(self.counts(), before)

    def test_pending_shows_what_a_month_still_owes(self):
        self.assertEqual(rec.period_total(self.con, "2026-02"), 15000)
        rec.recognise(self.con, self.admin, period="2026-02")
        self.assertEqual(rec.period_total(self.con, "2026-02"), 0)
        self.assertEqual(rec.period_total(self.con, "2026-03"), 15000)

    def test_a_zero_interest_contract_has_nothing_to_recognise(self):
        self.assertEqual(rec.period_total(self.con, "2027-05"), 0)

    def test_posted_entry_cannot_be_edited_or_deleted(self):
        rec.recognise(self.con, self.admin, period="2026-02")
        for sql in ("UPDATE interest_recognitions SET amount = 1", "DELETE FROM interest_recognitions",
                    "UPDATE interest_recognition_rows SET amount = 1", "DELETE FROM interest_recognition_rows"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.con.execute(sql)


if __name__ == "__main__":
    unittest.main()
