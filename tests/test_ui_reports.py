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
        self.assertEqual(page.model.rows[-1][4], 100000)

    def test_suspended_balances_dialog_opens_read_only(self):
        from morv.ui.suspended_dialogs import SuspendedBalancesDialog
        page = self.page()
        self.assertTrue(hasattr(page, "suspended_btn"))
        dlg = SuspendedBalancesDialog(self.session, None)
        self.addCleanup(dlg.deleteLater)
        self.assertEqual((dlg.report.shortages, dlg.report.overages), ((), ()))

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
