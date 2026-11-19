"""Treasury view: balances and movements come only from posted entries on the four money accounts."""
import tempfile
import unittest
from pathlib import Path

from morv.infra import ledger, treasury_view, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction


class TreasuryViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")

    def post(self, day, source, memo, lines):
        with transaction(self.con):
            return ledger.post_entry(self.con, self.admin, day, source, 1, memo, lines)

    def test_empty_ledger_has_zero_balances_and_no_movements(self):
        self.assertEqual(treasury_view.balances(self.con), (("1110", 0), ("1210", 0), ("1220", 0), ("1230", 0)))
        self.assertEqual(treasury_view.movements(self.con), [])
        self.assertEqual(treasury_view.count(self.con), 0)

    def test_balances_follow_debits_and_credits_and_ignore_other_accounts(self):
        self.post("2026-11-01", "sale", "بيع", [("1110", 50000, 0), ("4110", 0, 50000)])
        self.post("2026-11-02", "sale", "بيع تحويل", [("1210", 30000, 0), ("4110", 0, 30000)])
        self.post("2026-11-03", "purchase_invoice", "شراء", [("1410", 20000, 0), ("1110", 0, 20000)])
        self.assertEqual(dict(treasury_view.balances(self.con)), {"1110": 30000, "1210": 30000, "1220": 0, "1230": 0})
        self.assertEqual(treasury_view.count(self.con), 3)

    def test_movements_are_newest_first_and_skip_entries_without_money_lines(self):
        self.post("2026-11-01", "sale", "أول", [("1110", 100, 0), ("4110", 0, 100)])
        self.post("2026-11-02", "stock_issue", "تكلفة", [("5110", 70, 0), ("1410", 0, 70)])
        self.post("2026-11-03", "sale", "ثان", [("1110", 200, 0), ("4110", 0, 200)])
        rows = treasury_view.movements(self.con)
        self.assertEqual([r[3] for r in rows], ["ثان", "أول"])
        self.assertEqual(rows[0][1:3], ("2026-11-03", "1110"))
        self.assertEqual((rows[0][5], rows[0][6]), (200, 0))

    def test_account_filter_and_transfer_between_accounts_shows_both_sides(self):
        self.post("2026-11-01", "sale", "بيع", [("1110", 500, 0), ("4110", 0, 500)])
        self.post("2026-11-02", "test_transfer", "إيداع", [("1210", 300, 0), ("1110", 0, 300)])
        self.assertEqual({r[2] for r in treasury_view.movements(self.con)}, {"1110", "1210"})
        self.assertEqual([r[2] for r in treasury_view.movements(self.con, "1210")], ["1210"])
        self.assertEqual(len(treasury_view.movements(self.con, "1110")), 2)
        self.assertEqual(treasury_view.movements(self.con, "1220"), [])

    def test_search_matches_memo_or_date_literally(self):
        self.post("2026-11-01", "sale", "فاتورة بيع 100%", [("1110", 100, 0), ("4110", 0, 100)])
        self.post("2026-12-05", "sale", "فاتورة أخرى", [("1110", 100, 0), ("4110", 0, 100)])
        self.assertEqual(len(treasury_view.movements(self.con, None, "أخرى")), 1)
        self.assertEqual(len(treasury_view.movements(self.con, None, "2026-11")), 1)
        self.assertEqual(len(treasury_view.movements(self.con, None, "100%")), 1)
        self.assertEqual(len(treasury_view.movements(self.con, None, "%")), 1)
        self.assertEqual(treasury_view.movements(self.con, None, "لا شيء"), [])

    def test_paging_walks_every_movement_once(self):
        for i in range(7):
            self.post(f"2026-11-0{i + 1}", "sale", f"بيع {i}", [("1110", 100 + i, 0), ("4110", 0, 100 + i)])
        first = treasury_view.movements(self.con, None, "", limit=3, offset=0)
        second = treasury_view.movements(self.con, None, "", limit=3, offset=3)
        third = treasury_view.movements(self.con, None, "", limit=3, offset=6)
        ids = [r[0] for r in first + second + third]
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(set(ids)), 7)
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_get_entry_returns_all_lines_with_account_names_and_user(self):
        self.post("2026-11-02", "test_transfer", "إيداع بنكي", [("1210", 300, 0), ("1110", 0, 300)])
        line_id = treasury_view.movements(self.con, "1210")[0][0]
        entry = treasury_view.get_entry(self.con, line_id)
        self.assertEqual((entry.entry_date, entry.source_type, entry.memo), ("2026-11-02", "test_transfer", "إيداع بنكي"))
        self.assertEqual(entry.user_name, "المدير")
        self.assertEqual([(c, d, k) for c, _n, d, k in entry.lines], [("1210", 300, 0), ("1110", 0, 300)])
        self.assertEqual(entry.lines[0][1], "بنك التحويلات")
        with self.assertRaises(LookupError):
            treasury_view.get_entry(self.con, 999999)

    def test_reader_connection_sees_the_same_numbers(self):
        from morv.infra.db.connection import db_path, open_reader
        self.post("2026-11-01", "sale", "بيع", [("1110", 900, 0), ("4110", 0, 900)])
        reader = open_reader(db_path(self.con))
        self.addCleanup(reader.close)
        self.assertEqual(treasury_view.balances(reader), treasury_view.balances(self.con))
        self.assertEqual(treasury_view.movements(reader), treasury_view.movements(self.con))
        self.assertIsNotNone(treasury_view.get_entry(reader, treasury_view.movements(reader)[0][0]))


if __name__ == "__main__":
    unittest.main()
