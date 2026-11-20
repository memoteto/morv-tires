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

    def test_cashier_sees_nothing(self):
        self.assertEqual(self.page(self.cashier_session).model.rows, [])
