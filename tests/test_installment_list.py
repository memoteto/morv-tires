"""Tests for installment_view.list_view: per-contract urgency category, KPIs, search and filter."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installment_view, installments, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class ListViewTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "il.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.n = 0
        self.contract = self._make_contract("عميل أول", "01000000011")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make_contract(self, name, mobile):
        self.n += 1
        cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) VALUES (?, ?, ?, 0) RETURNING id",
            (name, name, mobile)).fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", customer_id=cust,
                               lines=[SaleLineIn(self.service, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-11-10")
        return installments.post_contract(
            self.con, self.admin, request_key=f"ic-key-{self.n:04d}", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-12-13", national_id=NID, down_method="cash", contract_date="2026-11-13")

    def _row(self, no=1):
        return self.con.execute("SELECT id, no, amount FROM installment_rows WHERE contract_id=? AND no=?",
                                (self.contract.id, no)).fetchone()

    def test_before_first_due_date_is_active_with_no_kpi_alarms(self):
        view = installment_view.list_view(self.con, today="2026-11-13")
        self.assertEqual(len(view.rows), 1)
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("نشط", ""))
        self.assertEqual((view.kpis.late_count, view.kpis.today_count, view.kpis.week_count), (0, 0, 0))
        self.assertEqual(view.kpis.active_count, 1)

    def test_row_due_today_counts_as_today_category(self):
        view = installment_view.list_view(self.con, today="2026-12-13")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("مستحق اليوم", "warn"))
        self.assertEqual(view.kpis.today_count, 1)
        self.assertEqual(view.kpis.today_amount, self._row()[2])

    def test_past_due_date_counts_as_late(self):
        view = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("متأخر", "danger"))
        self.assertEqual(view.kpis.late_count, 1)
        self.assertEqual(view.kpis.late_amount, self._row()[2])

    def test_due_within_week_is_a_separate_kpi_without_forcing_status(self):
        # Row 1 is due 2026-12-13; three days before that is within the 7-day window but not "today"/late.
        view = installment_view.list_view(self.con, today="2026-12-10")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("نشط", ""))
        self.assertEqual(view.kpis.week_count, 1)
        self.assertEqual(view.kpis.week_amount, self._row()[2])

    def test_fully_paid_contract_is_done_and_excluded_from_active(self):
        total = self.con.execute("SELECT sum(amount) FROM installment_rows WHERE contract_id=?",
                                 (self.contract.id,)).fetchone()[0]
        installments.collect_payment(self.con, self.admin, request_key="ip-key-1", contract_id=self.contract.id,
                                     amount=total, method="cash", payment_date="2026-11-14")
        view = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("مكتمل", "ok"))
        self.assertEqual(view.kpis.active_count, 0)
        self.assertEqual(view.rows[0].can_collect, False)
        self.assertIn("10 من 10", view.rows[0].progress_text)

    def test_paying_one_row_advances_progress_text(self):
        row = self._row()
        installments.collect_payment(self.con, self.admin, request_key="ip-key-1", contract_id=self.contract.id,
                                     amount=row[2], method="cash", payment_date="2026-11-14")
        view = installment_view.list_view(self.con, today="2026-11-20")
        self.assertIn("1 من 10", view.rows[0].progress_text)

    def test_filter_key_restricts_rows_but_kpis_cover_every_matching_contract(self):
        other = self._make_contract("عميل ثان", "01000000022")
        view_all_today_late = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual(view_all_today_late.kpis.late_count, 2)
        late_only = installment_view.list_view(self.con, filter_key="late", today="2026-12-20")
        self.assertEqual({r.contract_id for r in late_only.rows}, {self.contract.id, other.id})
        done_only = installment_view.list_view(self.con, filter_key="done", today="2026-12-20")
        self.assertEqual(done_only.rows, ())
        self.assertEqual(done_only.kpis.late_count, 2)   # kpis are not narrowed by filter_key

    def test_search_query_narrows_both_rows_and_kpis(self):
        self._make_contract("عميل ثان", "01000000022")
        view = installment_view.list_view(self.con, query="عميل أول", today="2026-12-20")
        self.assertEqual(len(view.rows), 1)
        self.assertEqual(view.rows[0].customer_name, "عميل أول")
        self.assertEqual(view.kpis.late_count, 1)   # the other contract's lateness isn't counted once searched out
