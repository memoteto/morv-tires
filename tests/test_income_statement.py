"""Income statement: sections, sign rules, period filtering, shortages in expenses, overages in suspense."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from morv.infra import income_statement as inc
from morv.infra import ledger, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction


class IncomeStatementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.n = 0

    def post(self, day, debit, credit, amount):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "test", self.n, "قيد وهمي",
                              [(debit, amount, 0), (credit, 0, amount)])

    def test_sections_and_net_result(self):
        self.post("2026-11-05", "1110", "4110", 100_000)       # tire sale
        self.post("2026-11-05", "5110", "1410", 60_000)        # its cost
        self.post("2026-11-06", "4190", "1110", 10_000)        # sales return reduces revenue
        self.post("2026-11-07", "5210", "1110", 5_000)         # rent
        st = inc.build(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual(st.revenue_total, 90_000)
        self.assertEqual(st.cost_total, 60_000)
        self.assertEqual(st.gross_profit, 30_000)
        self.assertEqual(st.expense_total, 5_000)
        self.assertEqual(st.net_result, 25_000)
        self.assertEqual({line.code: line.amount for line in st.revenue}, {"4110": 100_000, "4190": -10_000})

    def test_only_the_period_counts(self):
        self.post("2026-10-31", "1110", "4110", 7_000)
        self.post("2026-11-01", "1110", "4110", 1_000)
        self.post("2026-11-30", "1110", "4110", 2_000)
        self.post("2026-12-01", "1110", "4110", 4_000)
        self.assertEqual(inc.build(self.con, "2026-11-01", "2026-11-30").net_result, 3_000)

    def test_open_entries_are_ignored(self):
        with transaction(self.con):
            e = self.con.execute("INSERT INTO journal_entries (entry_date, source_type, source_id, line_count, total, "
                                 "status) VALUES ('2026-11-02','test',999,2,500,'open')").lastrowid
            self.con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) "
                             "VALUES (?,1,'1110',500,0)", (e,))
            self.con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) "
                             "VALUES (?,2,'4110',0,500)", (e,))
        self.assertEqual(inc.build(self.con, "2026-11-01", "2026-11-30").net_result, 0)

    def test_till_and_safe_shortages_reduce_the_result_and_overages_stay_out(self):
        self.post("2026-11-03", "5291", "1110", 1_000)
        self.post("2026-11-03", "5292", "1130", 500)
        self.post("2026-11-04", "1110", "2320", 700)
        self.post("2026-11-04", "1130", "2330", 300)
        st = inc.build(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual(st.net_result, -1_500)
        self.assertEqual({line.code: line.amount for line in st.suspense}, {"2320": 700, "2330": 300})

    def test_balance_sheet_accounts_never_enter_the_statement(self):
        self.post("2026-11-03", "1210", "1110", 9_000)
        st = inc.build(self.con, "2026-11-01", "2026-11-30")
        self.assertEqual((st.revenue, st.cost_of_sales, st.expenses, st.net_result), ((), (), (), 0))

    def test_table_rows_end_with_the_net_result(self):
        self.post("2026-11-05", "1110", "4110", 100_000)
        rows = inc.table_rows(inc.build(self.con, "2026-11-01", "2026-11-30"))
        self.assertEqual(rows[-1][1:], ("grand", "", "صافي النتيجة", 100_000))
        self.assertEqual([r[0] for r in rows], list(range(1, len(rows) + 1)))

    def test_period_ranges(self):
        today = date(2026, 3, 15)
        self.assertEqual(inc.period_range("month", today), ("2026-03-01", "2026-03-15"))
        self.assertEqual(inc.period_range("last_month", today), ("2026-02-01", "2026-02-28"))
        self.assertEqual(inc.period_range("year", today), ("2026-01-01", "2026-03-15"))
        self.assertEqual(inc.period_range("last_year", today), ("2025-01-01", "2025-12-31"))
        self.assertEqual(inc.period_range("all", today)[1], "2026-03-15")
        with self.assertRaises(ValueError):
            inc.period_range("x")


if __name__ == "__main__":
    unittest.main()
