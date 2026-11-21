"""Reports screen: income statement rows by period, permission gate."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class ReportsPageTest(unittest.TestCase):
    def setUp(self):
        from datetime import date

        from morv.infra import ledger, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.db.connection import transaction
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        cashier = users.create_user(self.con, admin, "karim", "كريم", "Karim12345", "cashier")
        self.session, self.cashier_session = Session(self.con, admin), Session(self.con, cashier)
        self.app = get_app()
        with transaction(self.con):
            ledger.post_entry(self.con, admin, date.today().isoformat(), "sale", 1, "بيع",
                              [("1110", 100000, 0), ("4110", 0, 100000)])

    def page(self, session=None):
        from morv.ui.reports_page import ReportsPage
        from morv.ui.widgets import dispose
        page = ReportsPage(session or self.session)
        self.addCleanup(dispose, page, self.app)
        return page

    def test_manager_sees_the_statement_ending_with_the_net_result(self):
        page = self.page()
        self.assertEqual(page.model.rows[-1][1], "grand")
        self.assertEqual(page.model.rows[-1][2][2], 100000)

    def test_partner_report_lists_partners_and_account_report_lists_accounts(self):
        page = self.page()
        page.report_combo.setCurrentIndex(page.report_combo.findData("ledger"))
        self.assertGreater(page.account_combo.count(), 10)
        page.report_combo.setCurrentIndex(page.report_combo.findData("partner"))
        self.assertEqual(page.account_combo.count(), 0)          # no partners in this fixture
        self.assertIn("شريكًا", page.count_label.text())
        page.report_combo.setCurrentIndex(page.report_combo.findData("ledger"))
        self.assertGreater(page.account_combo.count(), 10)

    def test_report_picker_switches_tables_and_exports(self):
        import os
        from morv.infra.reports import KEYS as reports_keys
        self.con.execute(          # the item card needs one stocked item to open on
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-1','إطار','اطار',100,100,100)")
        page = self.page()
        self.assertEqual([page.report_combo.itemData(i) for i in range(page.report_combo.count())],
                         list(reports_keys))
        for key in ("trial", "ledger", "sales_profit", "aging", "suspended", "daily_sales", "top_items",
                    "slow_moving", "journal", "suppliers", "income", "credit_aging", "stock_valuation",
                    "expenses", "collections", "purchases", "returns", "item_card", "supplier_aging"):
            page.report_combo.setCurrentIndex(page.report_combo.findData(key))
            self.assertEqual(page.table.key, key)
            self.assertEqual(page.model.columnCount(), len(page.table.headers))
        self.assertTrue(page.account_combo.isHidden())
        page.report_combo.setCurrentIndex(page.report_combo.findData("ledger"))
        self.assertFalse(page.account_combo.isHidden())
        for ext, fn in (("csv", page.export_csv_to), ("pdf", page.export_pdf_to)):
            path = os.path.join(self.tmp, f"r.{ext}")
            fn(path)
            self.assertGreater(os.path.getsize(path), 100)
        with open(os.path.join(self.tmp, "r.pdf"), "rb") as f:
            self.assertEqual(f.read(4), b"%PDF")

    def test_pdf_header_is_a_visible_paragraph_with_ltr_dates(self):
        from morv.ui.report_export import table_html
        page = self.page()
        html = table_html(page.table)
        self.assertNotIn("<h2>", html)
        self.assertIn("font-size:16pt", html)
        self.assertIn("<span dir='ltr'>", html.split("</p>")[1])      # the subtitle line

    def test_slow_moving_offers_day_thresholds_and_journal_exports_everything(self):
        import os
        page = self.page()
        page.report_combo.setCurrentIndex(page.report_combo.findData("slow_moving"))
        self.assertEqual([page.account_combo.itemData(i) for i in range(page.account_combo.count())],
                         ["30", "60", "90", "180", "365"])
        self.assertEqual(page.account_combo.currentData(), "90")
        page.report_combo.setCurrentIndex(page.report_combo.findData("journal"))
        path = os.path.join(self.tmp, "journal.csv")
        page.export_csv_to(path)
        self.assertGreater(os.path.getsize(path), 50)

    def test_compare_checkbox_adds_the_previous_period_columns_for_income_only(self):
        page = self.page()
        self.assertFalse(page.compare_box.isHidden())
        page.compare_box.setChecked(True)
        self.assertEqual(page.model.columnCount(), 6)
        page.report_combo.setCurrentIndex(page.report_combo.findData("trial"))
        self.assertTrue(page.compare_box.isHidden())
        self.assertEqual(page.model.columnCount(), 4)

    def test_custom_period_shows_dates_and_reports_bad_input_inline(self):
        page = self.page()
        page.set_filter("custom")
        self.assertFalse(page.from_edit.isHidden())
        page.from_edit.setText("2026-02-01")
        page.to_edit.setText("2026-01-01")
        page.reload()
        self.assertIn("بعد", page.count_label.text())
        page.to_edit.setText("2026-12-31")
        page.reload()
        self.assertIsNotNone(page.table)

    def test_period_filter_is_a_dropdown_that_switches_the_statement(self):
        page = self.page()
        self.assertIsNotNone(page.filter_combo)
        self.assertEqual(page.filter_buttons, {})
        page.filter_combo.setCurrentIndex(page.filter_combo.findData("last_year"))
        self.assertEqual(page.filter_key, "last_year")
        page.set_filter("month")
        self.assertEqual(page.filter_combo.currentData(), "month")

    def test_cashier_sees_nothing(self):
        self.assertEqual(self.page(self.cashier_session).model.rows, [])

    def test_lock_dialog_locks_and_reports_page_shows_it(self):
        from morv.infra import ledger
        from morv.ui.period_dialogs import PeriodLockDialog
        from morv.ui.widgets import dispose
        page = self.page()
        self.assertTrue(page.can_edit())
        self.assertFalse(self.page(self.cashier_session).can_edit())
        dlg = PeriodLockDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(ledger.locked_through(self.con), dlg.through.text())
        self.assertIn("مقفلة حتى", self.page().total())


@require_qt()
class ProfitUiTest(unittest.TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from morv.infra import cash_closing, ledger, period_lock, treasury, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.db.connection import transaction
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        cashier = users.create_user(self.con, admin, "karim", "كريم", "Karim12345", "cashier")
        self.session, self.cashier_session = Session(self.con, admin), Session(self.con, cashier)
        self.app = get_app()
        self.a = treasury.add_partner(self.con, admin, "أحمد").id
        self.b = treasury.add_partner(self.con, admin, "بهاء").id
        old = (date.today() - timedelta(days=8)).isoformat()
        through = (date.today() - timedelta(days=6)).isoformat()
        with transaction(self.con):
            ledger.post_entry(self.con, admin, old, "sale", 1, "بيع", [("1110", 100000, 0), ("4110", 0, 100000)])
        cash_closing.close_day(self.con, admin, request_key="ui-close-0001", closing_date=old, counted=100000,
                               expected_book=100000)
        period_lock.lock_period(self.con, admin, request_key="ui-lock-0001", through_date=through)
        self.old = old

    def test_share_dialog_distribution_dialog_and_permissions(self):
        from morv.infra import profit, treasury
        from morv.ui.partners_tab import PartnersTab
        from morv.ui.profit_dialogs import DistributionDialog, ShareSetDialog
        from morv.ui.reports_page import ReportsPage
        from morv.ui.widgets import dispose
        sd = ShareSetDialog(self.session)
        self.addCleanup(dispose, sd, self.app)
        sd.edits[self.a].setText("60")
        sd.edits[self.b].setText("40")
        sd.date.setText(self.old)
        sd.memo.setText("محضر")
        sd.save()
        self.assertIsNotNone(sd.saved)
        dd = DistributionDialog(self.session)
        self.addCleanup(dispose, dd, self.app)
        self.assertIsNotNone(dd._preview)
        dd.memo.setText("توزيع")
        from unittest import mock
        with mock.patch("morv.ui.profit_dialogs.ask", return_value=True):
            dd.save()
        self.assertIsNotNone(dd.saved)
        self.assertEqual(treasury.partner_balance(self.con, self.a), 60000)
        self.assertEqual(profit.latest_distribution(self.con)[3], 100000)
        page = ReportsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        self.assertIsNotNone(page.distribute_btn)
        other = ReportsPage(self.cashier_session)
        self.addCleanup(dispose, other, self.app)
        self.assertIsNone(other.distribute_btn)
        tab = PartnersTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.reserve_edit.setText("5")
        tab.save_reserve()
        self.assertEqual(profit.reserve_bp(self.con), 500)
