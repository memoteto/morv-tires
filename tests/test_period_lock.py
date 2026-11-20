"""Period lock: refusal at the ledger and the database, preconditions, unlock rules, immutability."""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from morv.infra import cash_closing, income_statement, ledger, period_lock, safe_count, treasury, users
from morv.infra.cash_closing import ClosingError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction
from morv.infra.period_lock import LockError

D = [(date.today() - timedelta(days=n)).isoformat() for n in range(0, 6)]   # D[0] today ... D[5]


class PeriodLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.n = 0
        self.post(D[4], "1110", "4110", 10_000)        # till activity four days ago, not yet closed

    def key(self):
        self.n += 1
        return f"lock-key-{self.n:04d}"

    def post(self, day, debit, credit, amount):
        self.n += 1
        with transaction(self.con):
            return ledger.post_entry(self.con, self.admin, day, "test", 800 + self.n, "قيد وهمي",
                                     [(debit, amount, 0), (credit, 0, amount)])

    def close_till(self, day):
        book = cash_closing.book_snapshot(self.con)[1]
        cash_closing.close_day(self.con, self.admin, request_key=self.key(), closing_date=day, counted=book,
                               expected_book=book)

    def lock(self, through=D[3], by=None, key=None, memo=None):
        return period_lock.lock_period(self.con, by or self.admin, request_key=key or self.key(),
                                       through_date=through, memo=memo)

    def test_lock_needs_the_till_closed_first(self):
        with self.assertRaises(LockError) as ctx:
            self.lock()
        self.assertIn("لم يُقفل", str(ctx.exception))
        self.close_till(D[4])
        done = self.lock()
        self.assertEqual(ledger.locked_through(self.con), D[3])
        self.assertFalse(done.replayed)

    def test_postings_on_or_before_the_lock_date_are_refused_and_later_ones_pass(self):
        self.close_till(D[4])
        self.lock(D[3])
        with self.assertRaises(ledger.LedgerError):
            self.post(D[3], "1110", "4110", 100)
        with self.assertRaises(ledger.LedgerError):
            self.post(D[5], "1110", "4110", 100)
        self.post(D[2], "1110", "4110", 100)
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries WHERE entry_date <= ?",
                                          (D[3],)).fetchone()[0], 1)

    def test_the_database_refuses_even_a_raw_insert(self):
        self.close_till(D[4])
        self.lock(D[3])
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.con):
                self.con.execute("INSERT INTO journal_entries (entry_date, source_type, source_id, line_count, total, "
                                 "status) VALUES (?, 'raw', 1, 2, 5, 'open')", (D[3],))

    def test_locked_period_statement_does_not_change(self):
        self.close_till(D[4])
        before = income_statement.build(self.con, D[5], D[3]).net_result
        self.lock(D[3])
        with self.assertRaises(ledger.LedgerError):
            self.post(D[4], "1110", "4110", 5_000)
        self.assertEqual(income_statement.build(self.con, D[5], D[3]).net_result, before)

    def test_movement_closing_and_safe_count_inside_the_period_are_refused(self):
        self.close_till(D[4])
        self.lock(D[3])
        with self.assertRaises(treasury.TreasuryError):
            treasury.post_movement(self.con, self.admin, request_key=self.key(), kind="expense", amount=100,
                                   memo="مصروف", money_account="1110", other_account="5210", movement_date=D[3])
        with self.assertRaises(ClosingError):
            cash_closing.close_day(self.con, self.admin, request_key=self.key(), closing_date=D[3], counted=10_000,
                                   expected_book=10_000)
        with self.assertRaises(ClosingError):
            safe_count.count_safe(self.con, self.admin, request_key=self.key(), count_date=D[3], counted=0,
                                  expected_book=0)

    def test_lock_rules(self):
        self.close_till(D[4])
        with self.assertRaises(LockError):
            self.lock(D[0])                              # today cannot be locked
        with self.assertRaises(LockError):
            self.lock((date.today() + timedelta(days=1)).isoformat())
        with self.assertRaises(LockError):
            self.lock(by=self.cashier)
        self.lock(D[3])
        with self.assertRaises(LockError):
            self.lock(D[3])                              # not later than the current lock
        with self.assertRaises(LockError):
            self.lock(D[4])
        self.lock(D[2])                                  # moving forward is fine
        self.assertEqual(ledger.locked_through(self.con), D[2])

    def test_a_skipped_day_between_two_closings_blocks_the_lock(self):
        self.post(D[3], "1110", "4110", 500)               # till activity on a day nobody closed
        self.close_till(D[4])
        self.close_till(D[2])
        with self.assertRaises(LockError) as ctx:
            self.lock(D[1])
        self.assertIn(D[3], str(ctx.exception))
        self.assertEqual(cash_closing.unclosed_days(self.con), 1)       # the dashboard uses the same rule

    def test_days_before_the_first_closing_do_not_block(self):
        self.post(D[5], "1110", "4110", 700)
        self.close_till(D[4])                              # this first closing counted all earlier cash
        self.assertEqual(self.lock(D[3]).through_date, D[3])

    def test_movement_posted_after_the_closing_blocks_until_the_next_closing(self):
        self.close_till(D[4])
        self.close_till(D[3])
        self.post(D[4], "1110", "4110", 300)               # discovered later, dated inside the period
        with self.assertRaises(LockError) as ctx:
            self.lock(D[3])
        self.assertIn("أقفل الصندوق مرة أخرى", str(ctx.exception))
        self.close_till(D[2])                              # the safe path: the next closing counts it
        self.assertEqual(self.lock(D[3]).through_date, D[3])

    def test_the_closing_difference_entry_itself_is_not_a_late_movement(self):
        book = cash_closing.book_snapshot(self.con)[1]
        cash_closing.close_day(self.con, self.admin, request_key=self.key(), closing_date=D[4], counted=book - 100,
                               expected_book=book, memo="عجز")
        self.assertEqual(self.lock(D[3]).through_date, D[3])

    def test_open_entry_blocks_the_lock(self):
        self.close_till(D[4])
        with transaction(self.con):
            self.con.execute("INSERT INTO journal_entries (entry_date, source_type, source_id, line_count, total, "
                             "status) VALUES (?, 'raw', 1, 2, 5, 'open')", (D[3],))
        with self.assertRaises(LockError):
            self.lock(D[3])

    def test_replay_and_changed_payload(self):
        self.close_till(D[4])
        first = self.lock(D[3], key="fixed-key-0001")
        again = self.lock(D[3], key="fixed-key-0001")
        self.assertTrue(again.replayed)
        self.assertEqual(again.id, first.id)
        with self.assertRaises(LockError):
            self.lock(D[2], key="fixed-key-0001")

    def test_unlock_needs_reason_and_voids_only_the_newest(self):
        self.close_till(D[4])
        first = self.lock(D[3])
        second = self.lock(D[2])
        with self.assertRaises(LockError):
            period_lock.unlock_period(self.con, self.admin, request_key=self.key(), lock_id=first.id, memo="x")
        with self.assertRaises(LockError):
            period_lock.unlock_period(self.con, self.admin, request_key=self.key(), lock_id=second.id, memo="")
        period_lock.unlock_period(self.con, self.admin, request_key=self.key(), lock_id=second.id, memo="تصحيح")
        self.assertEqual(ledger.locked_through(self.con), D[3])
        period_lock.unlock_period(self.con, self.admin, request_key=self.key(), lock_id=first.id, memo="تصحيح")
        self.assertIsNone(ledger.locked_through(self.con))
        self.post(D[3], "1110", "4110", 100)             # postings are possible again

    def test_locks_are_immutable(self):
        self.close_till(D[4])
        self.lock()
        for sql in ("UPDATE period_locks SET through_date = '2000-01-01'", "DELETE FROM period_locks"):
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.con):
                    self.con.execute(sql)


if __name__ == "__main__":
    unittest.main()
