"""Daily cash closing: book snapshot, differences, one closing per date, cancel, immutability, warnings."""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from morv.infra import cash_closing, dashboard_view, ledger, treasury, users
from morv.infra.cash_closing import ClosingError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction

TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


class CashClosingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.n = 0
        self.cash(YESTERDAY, 50_000)

    def key(self):
        self.n += 1
        return f"close-key-{self.n:04d}"

    def cash(self, day, amount):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "sale", 1000 + self.n, "بيع وهمي",
                              [("1110", amount, 0), ("4110", 0, amount)])

    def close(self, day=TODAY, counted=50_000, book=None, memo=None, key=None, by=None):
        if book is None:
            book = cash_closing.book_snapshot(self.con)[1]
        return cash_closing.close_day(self.con, by or self.admin, request_key=key or self.key(), closing_date=day,
                                      counted=counted, expected_book=book, memo=memo)

    def test_exact_count_posts_no_entry(self):
        before = self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0]
        done = self.close()
        self.assertEqual(done.difference, 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], before)
        self.assertEqual(cash_closing.last_closed_date(self.con), TODAY)

    def test_shortage_needs_a_reason_and_posts_to_5291(self):
        with self.assertRaises(ClosingError):
            self.close(counted=49_000)
        done = self.close(counted=49_000, memo="خطأ في الباقي")
        self.assertEqual(done.difference, -1000)
        self.assertEqual(ledger.account_balance(self.con, "5291"), 1000)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 49_000)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_overage_goes_to_a_liability_not_revenue(self):
        self.close(counted=52_500, memo="زيادة غير معروفة")
        self.assertEqual(ledger.account_balance(self.con, "2320"), -2500)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 52_500)

    def test_book_changed_while_counting_forces_a_recount(self):
        seen = cash_closing.book_snapshot(self.con)[1]
        self.cash(TODAY, 700)
        with self.assertRaises(ClosingError):
            self.close(book=seen)
        self.assertIsNone(cash_closing.last_closed_date(self.con))

    def test_sale_after_closing_is_not_blocked_and_joins_next_cycle(self):
        self.close()
        self.cash(TODAY, 300)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 50_300)
        self.assertEqual(cash_closing.unclosed_days(self.con), 0)   # same date as the closing: already covered

    def test_one_live_closing_per_date_and_dates_move_forward(self):
        self.close(YESTERDAY)
        with self.assertRaises(ClosingError):
            self.close(YESTERDAY)
        with self.assertRaises(ClosingError):
            self.close((date.today() - timedelta(days=2)).isoformat())
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.con):
                self.con.execute(
                    "INSERT INTO cash_closings (number, request_key, payload_hash, kind, closing_date, "
                    "cutoff_line_id, book_balance, counted, difference, user_id) "
                    "VALUES ('X','raw-key-0001','h','close',?,0,0,0,0,?)", (YESTERDAY, self.admin.id))

    def test_future_date_negative_count_and_cashier_are_refused(self):
        with self.assertRaises(ClosingError):
            self.close((date.today() + timedelta(days=1)).isoformat())
        with self.assertRaises(ClosingError):
            self.close(counted=-1)
        with self.assertRaises(ClosingError):
            self.close(by=self.cashier)

    def test_replay_returns_the_same_closing_and_a_changed_payload_is_refused(self):
        first = self.close(counted=49_000, memo="سبب", key="fixed-key-0001")
        again = self.close(counted=49_000, memo="سبب", key="fixed-key-0001", book=50_000)
        self.assertTrue(again.replayed)
        self.assertEqual(again.id, first.id)
        with self.assertRaises(ClosingError):
            self.close(counted=48_000, memo="سبب", key="fixed-key-0001", book=50_000)
        self.assertEqual(self.con.execute("SELECT count(*) FROM cash_closings").fetchone()[0], 1)

    def test_cancel_reverses_the_difference_and_reopens_the_date(self):
        first = self.close(counted=49_000, memo="سبب")
        with self.assertRaises(ClosingError):
            cash_closing.cancel_closing(self.con, self.admin, request_key=self.key(), closing_id=first.id, memo="")
        cash_closing.cancel_closing(self.con, self.admin, request_key=self.key(), closing_id=first.id, memo="عدّ خاطئ")
        self.assertEqual(ledger.account_balance(self.con, "5291"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 50_000)
        self.assertIsNone(cash_closing.last_closed_date(self.con))
        self.assertEqual(self.close().difference, 0)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_cancelling_an_overage_also_nets_to_zero(self):
        first = self.close(counted=52_500, memo="زيادة")
        cash_closing.cancel_closing(self.con, self.admin, request_key=self.key(), closing_id=first.id, memo="خطأ")
        self.assertEqual(ledger.account_balance(self.con, "2320"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 50_000)

    def test_only_the_newest_closing_can_be_cancelled(self):
        older = self.close(YESTERDAY)
        self.close(TODAY)
        with self.assertRaises(ClosingError):
            cash_closing.cancel_closing(self.con, self.admin, request_key=self.key(), closing_id=older.id, memo="x")

    def test_closings_are_immutable(self):
        self.close()
        for sql in ("UPDATE cash_closings SET counted = 1", "DELETE FROM cash_closings"):
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction(self.con):
                    self.con.execute(sql)

    def test_unclosed_days_counts_days_with_till_activity_after_the_last_closing(self):
        two_ago = (date.today() - timedelta(days=2)).isoformat()
        self.cash(two_ago, 100)
        self.assertEqual(cash_closing.unclosed_days(self.con), 2)   # two days ago and yesterday
        self.close(YESTERDAY, counted=50_100)
        self.assertEqual(cash_closing.unclosed_days(self.con), 0)

    def test_movement_dated_in_a_closed_period_needs_confirmation(self):
        self.close(TODAY)
        with self.assertRaises(treasury.NeedsConfirmation) as ctx:
            treasury.post_movement(self.con, self.admin, request_key=self.key(), kind="expense", amount=100,
                                   memo="مصروف", money_account="1110", other_account="5210",
                                   movement_date=YESTERDAY)
        self.assertTrue(any("مُقفلة" in w for w in ctx.exception.warnings))

    def test_movement_dated_on_the_closing_day_itself_needs_no_confirmation(self):
        self.close(TODAY)
        done = treasury.post_movement(self.con, self.admin, request_key=self.key(), kind="expense", amount=100,
                                      memo="مصروف", money_account="1110", other_account="5210")
        self.assertFalse(done.replayed)

    def test_dashboard_reports_last_closing_and_unclosed_days(self):
        view = dashboard_view.get(self.con)
        self.assertIsNone(view.last_closing)
        self.assertEqual(view.unclosed_days, 1)
        self.close(counted=49_000, memo="سبب")
        view = dashboard_view.get(self.con)
        self.assertEqual(view.last_closing[0], TODAY)
        self.assertEqual(view.last_closing[2], -1000)

    def test_accounts_exist_and_are_postable(self):
        rows = dict(self.con.execute("SELECT code, postable FROM accounts WHERE code IN ('5291','2320')").fetchall())
        self.assertEqual(rows, {"5291": 1, "2320": 1})


class HandoverTest(unittest.TestCase):
    """Closing plus surplus handover in one transaction; reuses the closing fixtures."""
    key, cash = CashClosingTest.key, CashClosingTest.cash

    def setUp(self):
        CashClosingTest.setUp(self)
        self.partner = treasury.add_partner(self.con, self.admin, "أحمد").id

    def hand(self, handover=40_000, counted=50_000, key=None, **kw):
        book = cash_closing.book_snapshot(self.con)[1]
        return treasury.close_and_hand_over(
            self.con, self.admin, request_key=key or self.key(), closing_date=TODAY, counted=counted,
            expected_book=book, memo=kw.pop("memo", None), handover=handover, **kw)

    def test_partner_handover_leaves_the_float_and_posts_a_withdrawal(self):
        self.hand(partner_id=self.partner, confirmed=True)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 10_000)
        self.assertEqual(treasury.partner_balance(self.con, self.partner), -40_000)
        self.assertEqual(cash_closing.last_closed_date(self.con), TODAY)

    def test_bank_handover_is_a_transfer(self):
        self.hand(bank_account="1210")
        self.assertEqual(ledger.account_balance(self.con, "1210"), 40_000)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 10_000)

    def test_handover_to_the_safe_keeps_the_money_in_the_total(self):
        from morv.infra import dashboard_view
        self.hand(bank_account="1130")
        self.assertEqual(ledger.account_balance(self.con, "1130"), 40_000)
        view = dashboard_view.get(self.con)
        self.assertEqual(view.cash_balance, 50_000)
        self.assertEqual(dict(view.account_balances)["1130"], 40_000)

    def test_safe_can_be_deposited_to_a_bank_with_a_plain_transfer(self):
        self.hand(bank_account="1130")
        treasury.post_movement(self.con, self.admin, request_key=self.key(), kind="transfer", amount=15_000,
                               memo="إيداع بنكي", money_account="1130", other_account="1210")
        self.assertEqual(ledger.account_balance(self.con, "1130"), 25_000)
        self.assertEqual(ledger.account_balance(self.con, "1210"), 15_000)

    def test_existing_custody_account_is_untouched_and_not_a_money_account(self):
        self.assertNotIn("1120", treasury.MONEY_ACCOUNTS)
        self.assertEqual(self.con.execute("SELECT name FROM accounts WHERE code='1120'").fetchone()[0], "العهد")

    def test_unconfirmed_warning_undoes_the_closing_too(self):
        with self.assertRaises(treasury.NeedsConfirmation):
            self.hand(partner_id=self.partner)        # withdrawal above the partner balance needs confirmation
        self.assertIsNone(cash_closing.last_closed_date(self.con))
        self.assertEqual(ledger.account_balance(self.con, "1110"), 50_000)

    def test_difference_is_posted_before_the_handover(self):
        self.hand(counted=49_000, handover=49_000, memo="عجز", bank_account="1210")
        self.assertEqual(ledger.account_balance(self.con, "5291"), 1000)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 0)

    def test_bad_requests_write_nothing(self):
        for kw in (dict(handover=0, bank_account="1210"), dict(handover=60_000, bank_account="1210"),
                   dict(partner_id=self.partner, bank_account="1210"), dict()):
            with self.assertRaises(treasury.TreasuryError):
                self.hand(**{"handover": 100, **kw})
        with self.assertRaises(treasury.TreasuryError):
            self.hand(bank_account="1110")
        self.assertEqual(self.con.execute("SELECT count(*) FROM cash_closings").fetchone()[0], 0)

    def test_replay_returns_the_same_closing_without_a_second_movement(self):
        first = self.hand(bank_account="1210", key="hand-key-0001")
        again = treasury.close_and_hand_over(
            self.con, self.admin, request_key="hand-key-0001", closing_date=TODAY, counted=50_000,
            expected_book=50_000, memo=None, handover=40_000, bank_account="1210")
        self.assertTrue(again.replayed)
        self.assertEqual(again.id, first.id)
        self.assertEqual(self.con.execute("SELECT count(*) FROM treasury_movements").fetchone()[0], 1)

    def test_cashier_cannot_hand_over(self):
        with self.assertRaises(treasury.TreasuryError):
            treasury.close_and_hand_over(self.con, self.cashier, request_key=self.key(), closing_date=TODAY,
                                         counted=50_000, expected_book=50_000, memo=None, handover=100,
                                         bank_account="1210")


if __name__ == "__main__":
    unittest.main()
