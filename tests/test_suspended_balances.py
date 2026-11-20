"""Suspended balances report: live shortages and overages, partner debts, and ledger drift."""
import tempfile
import unittest
from pathlib import Path

from morv.infra import cash_closing, ledger, safe_count, suspended_balances, treasury, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction


class SuspendedBalancesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.partner = treasury.add_partner(self.con, self.admin, "أحمد")
        self.n = 0

    def key(self):
        self.n += 1
        return f"susp-key-{self.n:04d}"

    def post(self, day, debit, credit, amount):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "test", 900 + self.n, "قيد وهمي",
                              [(debit, amount, 0), (credit, 0, amount)])

    def close(self, day, counted, memo="تحقيق"):
        book = cash_closing.book_snapshot(self.con)[1]
        return cash_closing.close_day(self.con, self.admin, request_key=self.key(), closing_date=day,
                                      counted=counted, expected_book=book, memo=memo)

    def count_safe(self, day, counted, memo="جرد"):
        book = cash_closing.book_snapshot(self.con, "1130")[1]
        return safe_count.count_safe(self.con, self.admin, request_key=self.key(), count_date=day,
                                     counted=counted, expected_book=book, memo=memo)

    def test_empty_report(self):
        r = suspended_balances.build(self.con)
        self.assertEqual((r.shortages, r.overages, r.partner_debts), ((), (), ()))
        self.assertEqual((r.shortage_drift, r.overage_drift), (0, 0))

    def test_shortages_and_overages_of_till_and_safe_with_totals(self):
        self.post("2026-11-01", "1110", "4110", 10_000)
        self.close("2026-11-01", 9_000)                       # till shortage 1,000
        self.post("2026-11-02", "1130", "4110", 5_000)
        self.count_safe("2026-11-02", 5_500)                  # safe overage 500
        r = suspended_balances.build(self.con)
        self.assertEqual([(d.source, d.amount) for d in r.shortages], [("إقفال الصندوق", 1_000)])
        self.assertEqual([(d.source, d.amount) for d in r.overages], [("جرد الخزنة", 500)])
        self.assertEqual((r.shortage_total, r.overage_total), (1_000, 500))
        self.assertEqual((r.shortage_drift, r.overage_drift), (0, 0))

    def test_cancelled_closing_leaves_the_report(self):
        self.post("2026-11-01", "1110", "4110", 10_000)
        closed = self.close("2026-11-01", 9_000)
        cash_closing.cancel_closing(self.con, self.admin, request_key=self.key(), closing_id=closed.id, memo="خطأ عد")
        r = suspended_balances.build(self.con)
        self.assertEqual((r.shortages, r.shortage_total, r.shortage_drift), ((), 0, 0))

    def test_partner_with_a_debit_current_account_is_listed(self):
        self.post("2026-11-01", "1110", "4110", 10_000)
        self.post("2026-11-01", self.partner.account, "1110", 3_000)
        r = suspended_balances.build(self.con)
        self.assertEqual([(d.name, d.amount) for d in r.partner_debts], [("أحمد", 3_000)])

    def test_drift_flags_a_difference_the_ledger_does_not_show(self):
        self.post("2026-11-01", "1110", "4110", 10_000)
        self.close("2026-11-01", 9_000)
        self.post("2026-11-02", "5291", "1110", 200)          # extra shortage posted outside any document
        self.assertEqual(suspended_balances.build(self.con).shortage_drift, -200)

    def test_the_report_never_writes(self):
        self.post("2026-11-01", "1110", "4110", 10_000)
        self.close("2026-11-01", 9_000)
        before = self.con.execute("SELECT (SELECT count(*) FROM journal_entries), (SELECT count(*) FROM audit_log)").fetchone()
        suspended_balances.build(self.con)
        self.assertEqual(self.con.execute(
            "SELECT (SELECT count(*) FROM journal_entries), (SELECT count(*) FROM audit_log)").fetchone(), before)


if __name__ == "__main__":
    unittest.main()
