"""Bank reconciliation tests: frozen program balance, difference rules, cancel, immutability, no entries."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.infra import bank_reconciliation as br
from morv.infra import ledger, period_lock, users
from morv.infra.bank_reconciliation import ReconciliationError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction


class BankReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "br.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.clerk = users.create_user(self.con, self.admin, "clerk", "موظف", "Clerk12345", "cashier")
        self.n = 0
        self.post("2026-10-05", "1210", 500000)
        self.post("2026-10-20", "1210", 200000)
        self.post("2026-10-10", "1230", 90000)

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def post(self, day, account, amount):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "test", self.n, "قيد وهمي",
                              [(account, amount, 0), ("3250", 0, amount)])

    def key(self):
        self.n += 1
        return f"rec-key-{self.n:04d}"

    def rec(self, statement, day="2026-10-31", account="1210", expected=None, **kw):
        expected = br.program_balance(self.con, account, day) if expected is None else expected
        return br.reconcile(self.con, self.admin, request_key=kw.pop("key", self.key()), account=account,
                            statement_date=day, statement_balance=statement, expected_program=expected, **kw)

    def test_program_balance_is_taken_up_to_the_date_only(self):
        self.assertEqual(br.program_balance(self.con, "1210", "2026-10-10"), 500000)
        self.assertEqual(br.program_balance(self.con, "1210", "2026-10-31"), 700000)
        self.assertEqual(br.program_balance(self.con, "1230", "2026-10-31"), 90000)

    def test_matching_record_needs_no_reason_and_posts_no_entry(self):
        before = self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0]
        doc = self.rec(700000, reference="كشف أكتوبر")
        self.assertEqual((doc.difference, doc.number[:8]), (0, "BR-2026-"))
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], before)

    def test_difference_is_signed_and_needs_a_reason(self):
        with self.assertRaises(ReconciliationError):
            self.rec(695000)
        self.assertEqual(self.rec(695000, memo="رسوم بنكية لم تُسجَّل").difference, -5000)
        self.assertEqual(self.rec(710000, day="2026-10-25", account="1210", memo="إيداع في الطريق").difference,
                         710000 - 700000)

    def test_program_balance_is_frozen_and_drift_is_reported(self):
        self.rec(700000)
        self.assertFalse(br.drifted(self.con, "1210"))
        self.post("2026-10-15", "1210", 1000)
        self.assertTrue(br.drifted(self.con, "1210"))
        self.assertEqual(br.latest(self.con, "1210")[4], 700000)

    def test_changed_balance_since_the_dialog_was_opened_is_refused(self):
        seen = br.program_balance(self.con, "1210", "2026-10-31")
        self.post("2026-10-15", "1210", 1000)
        with self.assertRaises(ReconciliationError):
            self.rec(700000, expected=seen)
        self.assertEqual(br.history(self.con, "1210"), [])

    def test_validation_permission_and_one_live_record_per_date(self):
        for bad in ({"account": "1110"}, {"account": "1130"}, {"day": "2999-01-01"}):
            with self.assertRaises(ReconciliationError):
                self.rec(0, **{"memo": "x", **bad})
        with self.assertRaises(ReconciliationError):
            br.reconcile(self.con, self.clerk, request_key=self.key(), account="1210",
                         statement_date="2026-10-31", statement_balance=700000, expected_program=700000)
        self.rec(700000)
        with self.assertRaises(ReconciliationError):
            self.rec(700000)

    def test_replay_and_changed_payload(self):
        first = self.rec(700000, key="same-key-0001")
        self.assertTrue(self.rec(700000, key="same-key-0001").replayed)
        with self.assertRaises(ReconciliationError):
            self.rec(690000, key="same-key-0001", memo="x")
        self.assertEqual(len(br.history(self.con, "1210")), 1)
        self.assertEqual(first.id, br.latest(self.con, "1210")[0])

    def test_cancel_only_the_newest_live_record_and_then_reconcile_again(self):
        old = self.rec(700000, day="2026-10-25")
        new = self.rec(700000, day="2026-10-31")
        with self.assertRaises(ReconciliationError):
            br.cancel(self.con, self.admin, request_key=self.key(), reconciliation_id=old.id, memo="خطأ")
        with self.assertRaises(ReconciliationError):
            br.cancel(self.con, self.admin, request_key=self.key(), reconciliation_id=new.id, memo="")
        br.cancel(self.con, self.admin, request_key=self.key(), reconciliation_id=new.id, memo="خطأ إدخال")
        self.assertEqual(br.latest(self.con, "1210")[0], old.id)
        self.assertEqual(self.rec(700000, day="2026-10-31").difference, 0)

    def test_gaps_name_each_account_with_a_reason_and_clear_when_reconciled(self):
        gaps = br.gaps(self.con, "2026-10-31")
        self.assertEqual(len(gaps), 2)
        self.assertTrue(all("لم تُطابَق قط" in g for g in gaps))
        self.assertEqual(br.gaps(self.con, "2026-10-01"), [])
        self.rec(700000, day="2026-10-20")
        self.rec(90000, day="2026-10-10", account="1230")
        self.assertEqual(br.gaps(self.con, "2026-10-31"), [])
        self.post("2026-10-28", "1210", 1000)
        self.assertIn("تسبق آخر حركة", br.gaps(self.con, "2026-10-31")[0])
        self.post("2026-10-05", "1230", 1000)
        self.assertIn("تغيّر رصيد البرنامج", br.gaps(self.con, "2026-10-31")[1])

    def test_unsettled_difference_is_reported_and_warnings_never_block_a_lock(self):
        self.rec(695000, day="2026-10-20", memo="رسوم")
        self.rec(90000, day="2026-10-10", account="1230")
        self.assertIn("فرق غير مسوّى", br.gaps(self.con, "2026-10-20")[0])
        self.assertEqual(period_lock.warnings(self.con, "2026-10-31"), br.gaps(self.con, "2026-10-31"))
        self.assertEqual(period_lock.blockers(self.con, "2026-10-31"), [])
        period_lock.lock_period(self.con, self.admin, request_key=self.key(), through_date="2026-10-31")

    def test_records_are_immutable(self):
        doc = self.rec(700000)
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE bank_reconciliations SET statement_balance=1 WHERE id=?", (doc.id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM bank_reconciliations WHERE id=?", (doc.id,))


if __name__ == "__main__":
    unittest.main()
