"""Button hierarchy: each main page shows at most one primary action at a time."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt

PRIMARY_NAMES = ("primary", "primaryLarge")


@require_qt()
class OnePrimaryPerPageTest(unittest.TestCase):
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
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, admin)
        self.app = get_app()

    def test_each_page_shows_at_most_one_primary_button(self):
        from PySide6.QtWidgets import QPushButton
        from morv.ui.shell import NAV, MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1366, 768)
        win.show()
        problems = []
        for key, _title, _icon, _phase in NAV:
            win.go_key(key)
            self.app.processEvents()
            page = win.pages[key]
            shown = [b.text() for b in page.findChildren(QPushButton)
                     if b.objectName() in PRIMARY_NAMES and b.isVisibleTo(page)]
            if len(shown) > 1:
                problems.append(f"{key}: {shown}")
        self.assertEqual(problems, [], "pages showing more than one primary button")

    def test_treasury_checks_are_secondary_and_reports_lock_is_not_primary(self):
        from morv.ui.reports_page import ReportsPage
        from morv.ui.treasury_page import TreasuryPage
        from morv.ui.widgets import dispose
        treasury = TreasuryPage(self.session)
        reports = ReportsPage(self.session)
        for page in (treasury, reports):
            self.addCleanup(dispose, page, self.app)
        for btn in (treasury.close_btn, treasury.safe_btn, treasury.bank_btn,
                    treasury.distribute_btn, treasury.lock_btn):
            self.assertNotIn(btn.objectName(), PRIMARY_NAMES, btn.text())
        self.assertEqual(treasury.add_btn.objectName(), "primary")
        self.assertIsNone(reports.add_btn, "the reports screen creates nothing, so it needs no button")
