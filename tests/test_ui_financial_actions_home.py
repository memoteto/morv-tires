"""Locking a period and distributing profit are decisions, so they live in the treasury, not in reports."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class FinancialActionsHomeTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()

    def _buttons(self, page) -> list:
        from PySide6.QtWidgets import QAbstractButton
        return [b.text() for b in page.findChildren(QAbstractButton) if b.text().strip()]

    def test_reports_offers_no_financial_decision(self):
        from morv.ui.reports_page import ReportsPage
        from morv.ui.widgets import dispose
        page = ReportsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        texts = self._buttons(page)
        self.assertNotIn("قفل الفترة", texts)
        self.assertNotIn("توزيع الأرباح", texts)
        self.assertIsNone(page.add_btn, "a reading screen should offer no create button")

    def test_a_double_click_on_a_report_line_cannot_open_a_financial_dialog(self):
        from morv.ui.reports_page import ReportsPage
        from morv.ui.widgets import dispose
        page = ReportsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        # can_edit() guards both the double click and Ctrl+N in ListPage
        self.assertFalse(page.can_edit(), "a manager could open the period lock by double-clicking a report line")

    def test_the_treasury_offers_both_decisions_to_a_manager(self):
        from morv.ui.treasury_page import TreasuryPage
        from morv.ui.widgets import dispose
        page = TreasuryPage(self.session)
        self.addCleanup(dispose, page, self.app)
        texts = self._buttons(page)
        self.assertIn("قفل الفترة", texts)
        self.assertIn("توزيع الأرباح", texts)

    def test_each_action_follows_its_own_permission(self):
        from morv.infra import users
        from morv.ui.session import Session
        from morv.ui.treasury_page import TreasuryPage
        from morv.ui.widgets import dispose
        clerk = users.create_user(self.con, self.admin, "clerk", "موظف", "Clerk12345", "cashier")
        users.set_permission(self.con, self.admin, clerk.id, "treasury.view", True)
        page = TreasuryPage(Session(self.con, clerk))
        self.addCleanup(dispose, page, self.app)
        texts = self._buttons(page)
        self.assertNotIn("قفل الفترة", texts)
        self.assertNotIn("توزيع الأرباح", texts)
