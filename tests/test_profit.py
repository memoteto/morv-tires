"""Profit shares and distribution: locked-period rule, reserve, loss carry-forward, reversal, immutability."""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from morv.infra import cash_closing, ledger, period_lock, profit, treasury, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction
from morv.infra.profit import ProfitError

D = [(date.today() - timedelta(days=n)).isoformat() for n in range(0, 9)]


class ProfitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.a = treasury.add_partner(self.con, self.admin, "أحمد").id
        self.b = treasury.add_partner(self.con, self.admin, "بهاء").id
        self.n = 0
        self.post(D[8], "1110", "4110", 100_000)      # revenue
        self.post(D[8], "5110", "1410", 40_000)       # cost of sales
        self.post(D[8], "5210", "1110", 10_000)       # rent  -> net result 50,000
        self.close_and_lock(D[8], D[6])

    def key(self):
        self.n += 1
        return f"profit-key-{self.n:04d}"

    def post(self, day, debit, credit, amount):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "test", 900 + self.n, "قيد وهمي",
                              [(debit, amount, 0), (credit, 0, amount)])

    def close_and_lock(self, close_day, through):
        book = cash_closing.book_snapshot(self.con)[1]
        cash_closing.close_day(self.con, self.admin, request_key=self.key(), closing_date=close_day, counted=book,
                               expected_book=book)
        period_lock.lock_period(self.con, self.admin, request_key=self.key(), through_date=through)

    def shares(self, a=6000, b=4000, day=D[8], by=None):
        return profit.set_shares(self.con, by or self.admin, request_key=self.key(), effective_from=day,
                                 shares=[(self.a, a), (self.b, b)], memo="محضر اعتماد وهمي")

    def dist(self, amount, through=D[6], by=None, key=None, memo="توزيع"):
        return profit.distribute(self.con, by or self.admin, request_key=key or self.key(), through_date=through,
                                 amount=amount, memo=memo)

    def test_needs_a_locked_period_and_approved_shares(self):
        with self.assertRaises(ProfitError):
            profit.preview(self.con, D[5])                 # beyond the lock
        with self.assertRaises(ProfitError) as ctx:
            profit.preview(self.con, D[6])
        self.assertIn("حصص", str(ctx.exception))
        self.shares()
        self.assertEqual(profit.preview(self.con, D[6]).max_amount, 50_000)

    def test_unlocked_ledger_cannot_be_distributed(self):
        period_lock.unlock_period(self.con, self.admin, request_key=self.key(),
                                  lock_id=period_lock.latest_lock(self.con)[0], memo="x")
        self.shares()
        with self.assertRaises(ProfitError):
            self.dist(1_000)

    def test_reserve_and_exact_split_touch_equity_only(self):
        self.shares()
        profit.set_reserve_bp(self.con, self.admin, 1000)         # 10% of the period result
        p = profit.preview(self.con, D[6])
        self.assertEqual((p.reserve_amount, p.max_amount), (5_000, 45_000))
        cash_before = [ledger.account_balance(self.con, c) for c in ("1110", "1210", "1220", "1230", "1130")]
        done = self.dist(45_001 - 1)
        self.assertEqual(treasury.partner_balance(self.con, self.a), 27_000)
        self.assertEqual(treasury.partner_balance(self.con, self.b), 18_000)
        self.assertEqual(ledger.account_balance(self.con, "3250"), -5_000)
        self.assertEqual(ledger.account_balance(self.con, "3200"), 50_000)
        self.assertEqual([ledger.account_balance(self.con, c) for c in ("1110", "1210", "1220", "1230", "1130")],
                         cash_before)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(done.amount, 45_000)

    def test_amount_limits_and_permission(self):
        self.shares()
        for bad in (0, -5, 50_001):
            with self.assertRaises(ProfitError):
                self.dist(bad)
        with self.assertRaises(ProfitError):
            self.dist(100, by=self.cashier)
        with self.assertRaises(ProfitError):
            self.dist(100, memo="")
        self.assertEqual(self.dist(30_000).amount, 30_000)

    def test_partial_distribution_then_the_rest_in_a_later_period(self):
        self.shares()
        self.dist(30_000)                                           # 20,000 of the profit stays undistributed
        self.post(D[5], "1110", "4110", 8_000)                      # next period earns 8,000
        self.close_and_lock(D[5], D[4])
        p = profit.preview(self.con, D[4])
        self.assertEqual((p.from_date, p.period_net, p.max_amount), (D[5], 8_000, 28_000))
        self.dist(28_000, through=D[4])
        self.assertEqual(profit.latest_distribution(self.con)[2], D[4])

    def test_losses_of_earlier_periods_are_absorbed_before_distributing(self):
        self.shares()
        self.dist(50_000)
        self.post(D[5], "5210", "1110", 20_000)                     # a losing period
        self.close_and_lock(D[5], D[4])
        self.assertEqual(profit.preview(self.con, D[4]).max_amount, 0)
        self.post(D[3], "1110", "4110", 26_000)                     # then a profitable one
        self.close_and_lock(D[3], D[2])
        p = profit.preview(self.con, D[2])
        # No distribution was made for the losing period, so the open period spans both: -20,000 + 26,000
        self.assertEqual((p.from_date, p.period_net, p.cumulative_net, p.max_amount), (D[5], 6_000, 56_000, 6_000))

    def test_replay_and_changed_payload(self):
        self.shares()
        first = self.dist(10_000, key="fixed-key-0001")
        again = self.dist(10_000, key="fixed-key-0001")
        self.assertTrue(again.replayed)
        self.assertEqual(again.id, first.id)
        self.assertEqual(treasury.partner_balance(self.con, self.a), 6_000)
        with self.assertRaises(ProfitError):
            self.dist(20_000, key="fixed-key-0001")

    def test_reverse_only_the_newest_and_reopen_the_amount(self):
        self.shares()
        first = self.dist(20_000)
        self.post(D[5], "1110", "4110", 1_000)
        self.close_and_lock(D[5], D[4])
        second = self.dist(1_000, through=D[4])
        with self.assertRaises(ProfitError):
            profit.reverse_distribution(self.con, self.admin, request_key=self.key(), distribution_id=first.id, memo="x")
        profit.reverse_distribution(self.con, self.admin, request_key=self.key(), distribution_id=second.id, memo="خطأ")
        profit.reverse_distribution(self.con, self.admin, request_key=self.key(), distribution_id=first.id, memo="خطأ")
        self.assertEqual(treasury.partner_balance(self.con, self.a), 0)
        self.assertEqual(ledger.account_balance(self.con, "3200"), 0)
        self.assertEqual(profit.preview(self.con, D[6]).max_amount, 50_000)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_stopped_partner_blocks_distribution_until_new_shares(self):
        self.shares()
        treasury.set_partner_active(self.con, self.admin, self.b, False)
        with self.assertRaises(ProfitError):
            self.dist(1_000)

    def test_share_set_rules(self):
        with self.assertRaises(ProfitError):
            self.shares(a=6000, b=3000)                              # not 100%
        with self.assertRaises(ProfitError):
            self.shares(by=self.cashier)
        self.shares()
        with self.assertRaises(ProfitError):
            self.shares(day=D[8])                                    # not after the newest set
        self.dist(1_000)
        with self.assertRaises(ProfitError):
            self.shares(day=D[6])                                    # reaches into a distributed period
        self.shares(a=5000, b=5000, day=D[5])                        # a later one is fine
        self.assertEqual(profit.shares_effective(self.con, D[7])[2][0][2], 6000)
        self.assertEqual(profit.shares_effective(self.con, D[5])[2][0][2], 5000)

    def test_sole_owner_is_a_single_partner_with_all_the_profit(self):
        solo = tempfile.TemporaryDirectory()
        self.addCleanup(solo.cleanup)
        con = open_main_db(Path(solo.name) / "morv_tires.db")
        self.addCleanup(con.close)
        admin = users.create_user(con, None, "admin", "المدير", "Admin12345", "manager")
        owner = treasury.add_partner(con, admin, "المالك").id
        with transaction(con):
            ledger.post_entry(con, admin, D[8], "test", 1, "بيع", [("1110", 7_001, 0), ("4110", 0, 7_001)])
        book = cash_closing.book_snapshot(con)[1]
        cash_closing.close_day(con, admin, request_key="solo-key-0001", closing_date=D[8], counted=book, expected_book=book)
        period_lock.lock_period(con, admin, request_key="solo-key-0002", through_date=D[6])
        profit.set_shares(con, admin, request_key="solo-key-0003", effective_from=D[8], shares=[(owner, 10_000)],
                          memo="مالك وحيد")
        profit.distribute(con, admin, request_key="solo-key-0004", through_date=D[6], amount=7_001, memo="توزيع")
        self.assertEqual(treasury.partner_balance(con, owner), 7_001)

    def test_documents_are_immutable(self):
        self.shares()
        self.dist(1_000)
        for table in ("profit_share_sets", "profit_share_lines", "profit_distributions",
                      "profit_distribution_lines"):
            for sql in (f"UPDATE {table} SET rowid = rowid", f"DELETE FROM {table}"):
                with self.assertRaises(sqlite3.IntegrityError):
                    with transaction(self.con):
                        self.con.execute(sql)

    def test_reserve_setting_validation(self):
        with self.assertRaises(ProfitError):
            profit.set_reserve_bp(self.con, self.admin, 10_001)
        with self.assertRaises(ProfitError):
            profit.set_reserve_bp(self.con, self.cashier, 100)
        self.assertEqual(profit.reserve_bp(self.con), 0)


if __name__ == "__main__":
    unittest.main()
