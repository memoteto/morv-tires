"""Safe count: book snapshot, own difference accounts, one per date, cancel, immutability, dashboard alert."""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from morv.infra import cash_closing, dashboard_view, ledger, safe_count, users
from morv.infra.cash_closing import ClosingError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


class SafeCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.n = 0
        self.post("1130", "1110", 80_000, YESTERDAY)

    def post(self, debit, credit, amount, day=TODAY):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "treasury_movement", 500 + self.n, "حركة وهمية",
                              [(debit, amount, 0), (credit, 0, amount)])

    def key(self):
        self.n += 1
        return f"safe-key-{self.n:04d}"

    def count(self, day=TODAY, counted=80_000, book=None, memo=None, key=None, by=None):
        if book is None:
            book = cash_closing.book_snapshot(self.con, "1130")[1]
        return safe_count.count_safe(self.con, by or self.admin, request_key=key or self.key(), count_date=day,
                                     counted=counted, expected_book=book, memo=memo)

    def test_exact_count_posts_nothing(self):
        before = self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0]
        self.assertEqual(self.count().difference, 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], before)

    def test_shortage_and_overage_use_the_safe_accounts_not_the_till_ones(self):
        with self.assertRaises(ClosingError):
            self.count(counted=79_000)
        self.count(YESTERDAY, counted=79_000, memo="نقص")
        self.assertEqual(ledger.account_balance(self.con, "5292"), 1000)
        self.assertEqual(ledger.account_balance(self.con, "1130"), 79_000)
        self.count(TODAY, counted=80_000, memo="زيادة")
        self.assertEqual(ledger.account_balance(self.con, "2330"), -1000)
        self.assertEqual(ledger.account_balance(self.con, "5291"), 0)
        self.assertEqual(ledger.account_balance(self.con, "2320"), 0)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_movement_during_the_count_forces_a_recount(self):
        seen = cash_closing.book_snapshot(self.con, "1130")[1]
        self.post("1210", "1130", 5_000)
        with self.assertRaises(ClosingError):
            self.count(book=seen)

    def test_one_live_count_per_date_dates_move_forward_and_cashier_refused(self):
        self.count(YESTERDAY)
        with self.assertRaises(ClosingError):
            self.count(YESTERDAY)
        with self.assertRaises(ClosingError):
            self.count(by=self.cashier)
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.con):
                self.con.execute(
                    "INSERT INTO safe_counts (number, request_key, payload_hash, kind, count_date, cutoff_line_id, "
                    "book_balance, counted, difference, user_id) "
                    "VALUES ('X','raw-key-0001','h','count',?,0,0,0,0,?)", (YESTERDAY, self.admin.id))

    def test_replay_and_changed_payload(self):
        first = self.count(counted=79_000, memo="نقص", key="fixed-key-0001")
        again = self.count(counted=79_000, memo="نقص", key="fixed-key-0001", book=80_000)
        self.assertTrue(again.replayed)
        self.assertEqual(again.id, first.id)
        with self.assertRaises(ClosingError):
            self.count(counted=70_000, memo="نقص", key="fixed-key-0001", book=80_000)

    def test_cancel_reverses_both_directions_and_only_the_newest(self):
        older = self.count(YESTERDAY, counted=79_000, memo="نقص")
        newer = self.count(TODAY, counted=79_000)
        with self.assertRaises(ClosingError):
            safe_count.cancel_count(self.con, self.admin, request_key=self.key(), count_id=older.id, memo="x")
        safe_count.cancel_count(self.con, self.admin, request_key=self.key(), count_id=newer.id, memo="خطأ")
        safe_count.cancel_count(self.con, self.admin, request_key=self.key(), count_id=older.id, memo="خطأ")
        self.assertEqual(ledger.account_balance(self.con, "5292"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1130"), 80_000)
        over = self.count(counted=81_000, memo="زيادة")
        safe_count.cancel_count(self.con, self.admin, request_key=self.key(), count_id=over.id, memo="خطأ")
        self.assertEqual(ledger.account_balance(self.con, "2330"), 0)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_counts_are_immutable(self):
        self.count()
        for sql in ("UPDATE safe_counts SET counted = 1", "DELETE FROM safe_counts"):
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.con):
                    self.con.execute(sql)

    def test_dashboard_alert_when_never_counted_or_stale_and_none_when_recent_or_empty(self):
        alerts = [a for a in dashboard_view.get(self.con).alerts if a.kind == "safe_count"]
        self.assertEqual((len(alerts), alerts[0].count, alerts[0].amount), (1, -1, 80_000))
        self.count(YESTERDAY)
        self.assertFalse([a for a in dashboard_view.get(self.con).alerts if a.kind == "safe_count"])
        later = (date.today() + timedelta(days=8)).isoformat()
        stale = [a for a in dashboard_view.get(self.con, today=later).alerts if a.kind == "safe_count"]
        self.assertEqual(stale[0].count, 9)

    def test_no_alert_for_an_empty_safe(self):
        self.post("1110", "1130", 80_000)
        self.assertFalse([a for a in dashboard_view.get(self.con).alerts if a.kind == "safe_count"])


if __name__ == "__main__":
    unittest.main()
