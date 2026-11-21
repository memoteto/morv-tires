"""Report catalog: trial balance, account statement, income, suspended table and CSV export."""
import csv
import tempfile
import unittest
from pathlib import Path

from morv.infra import ledger, reports, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.n = 0
        self.post("2026-10-05", "1110", "4110", 10_000)
        self.post("2026-10-20", "5110", "1410", 3_000)
        self.post("2026-11-02", "1110", "4110", 5_000)

    def post(self, day, debit, credit, amount):
        self.n += 1
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, day, "test", 900 + self.n, f"قيد {self.n}",
                              [(debit, amount, 0), (credit, 0, amount)])

    def test_trial_balance_is_balanced_and_respects_the_end_date(self):
        t = reports.trial_balance(self.con, "2026-10-31")
        total = t.rows[-1][1]
        self.assertEqual(total[2], total[3])
        self.assertEqual(total[2], 10_000 + 3_000)
        self.assertIn("متوازن", t.subtitle)
        later = reports.trial_balance(self.con, "2026-11-30").rows[-1][1]
        self.assertEqual(later[2], 18_000)

    def test_account_statement_has_opening_running_balance_and_totals(self):
        t = reports.account_statement(self.con, "1110", "2026-11-01", "2026-11-30")
        self.assertEqual(t.rows[0][1][5], 10_000)                      # opening
        self.assertEqual([r[1][5] for r in t.rows[1:-1]], [15_000])    # running balance after the November line
        self.assertEqual(t.rows[-1][1][3:], (5_000, 0, 15_000))

    def test_credit_account_balance_is_shown_on_its_credit_side(self):
        t = reports.account_statement(self.con, "4110", "2026-10-01", "2026-11-30")
        self.assertEqual(t.rows[-1][1][5], 15_000)

    def test_bad_inputs_raise_report_errors(self):
        for call in (lambda: reports.account_statement(self.con, "", "2026-10-01", "2026-10-31"),
                     lambda: reports.account_statement(self.con, "1110", "2026-11-01", "2026-10-01"),
                     lambda: reports.income(self.con, "x", "2026-10-01"),
                     lambda: reports.build(self.con, "nope", "2026-10-01", "2026-10-31")):
            with self.assertRaises(reports.ReportError):
                call()

    def test_income_and_suspended_tables_build(self):
        inc = reports.build(self.con, "income", "2026-10-01", "2026-11-30")
        self.assertEqual(inc.rows[-1][0], "grand")
        self.assertEqual(inc.rows[-1][1][2], 15_000 - 3_000)
        self.assertEqual(reports.build(self.con, "suspended", "", "").key, "suspended")

    def test_every_table_row_matches_its_columns(self):
        for key in (k for k in reports.KEYS if k not in ("partner", "item_card")):   # these need a chosen partner or item
            t = reports.build(self.con, key, "2026-10-01", "2026-11-30", "1110")
            for _kind, cells in t.rows:
                self.assertEqual(len(cells), len(t.headers), key)

    def test_previous_range_is_the_same_span_one_month_or_year_back(self):
        self.assertEqual(reports.previous_range("2026-11-01", "2026-11-20"), ("2026-10-01", "2026-10-20"))
        self.assertEqual(reports.previous_range("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28"))
        self.assertEqual(reports.previous_range("2026-01-01", "2026-11-20"), ("2025-01-01", "2025-11-20"))
        self.assertEqual(reports.previous_range("2024-01-01", "2024-02-29"), ("2023-01-01", "2023-02-28"))
        self.assertEqual(reports.previous_range("2026-11-05", "2026-11-14"), ("2026-10-26", "2026-11-04"))
        with self.assertRaises(reports.ReportError):
            reports.previous_range("0001-01-01", "2026-11-20")

    def test_income_comparison_aligns_accounts_and_computes_change(self):
        self.post("2026-12-05", "1110", "4110", 20_000)
        self.post("2026-12-06", "5110", "1410", 8_000)
        self.post("2026-12-07", "5110", "1410", 1_000)
        t = reports.income(self.con, "2026-12-01", "2026-12-31", compare=True)
        self.assertIn("2026-11-01 إلى 2026-11-30", t.subtitle)
        rows = {cells[0]: cells for kind, cells in t.rows if kind == "line"}
        self.assertEqual(rows["4110"][2:5], (20_000, 5_000, 15_000))          # November had 5,000
        self.assertEqual(rows["4110"][5], "300.0%")
        self.assertEqual(rows["5110"][2:5], (9_000, 0, 9_000))                 # cost only in December
        self.assertEqual(rows["5110"][5], "")
        net = t.rows[-1][1]
        self.assertEqual((net[2], net[3], net[4]), (20_000 - 9_000, 5_000, 20_000 - 9_000 - 5_000))

    def test_account_only_in_the_previous_period_still_appears(self):
        t = reports.income(self.con, "2026-12-01", "2026-12-31", compare=True)
        rows = {cells[0]: cells for kind, cells in t.rows if kind == "line"}
        self.assertEqual(rows["4110"][2:5], (0, 5_000, -5_000))
        self.assertEqual(len(t.headers), len(t.rows[0][1]))

    def test_csv_export_is_excel_friendly(self):
        path = str(Path(self.tmp.name) / "r.csv")
        reports.to_csv(reports.trial_balance(self.con, "2026-11-30"), path)
        raw = Path(path).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
        self.assertEqual(rows[2], ["الحساب", "اسم الحساب", "مدين", "دائن"])
        self.assertIn("180.00", rows[-1])


if __name__ == "__main__":
    unittest.main()
