"""The reports header row must fit at 1366 even with the longest report and a custom range."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class ReportsHeaderWidthTest(unittest.TestCase):
    """UI-9 claimed the reports header row crowds at 1366; this measures it instead of guessing."""

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

    def test_reports_header_fits_at_1366_with_a_custom_range(self):
        from morv.ui.reports_page import CUSTOM, ReportsPage
        from morv.ui.widgets import dispose
        page = ReportsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        page.resize(1366 - 244 - 48, 700)       # page width at 1366 with the sidebar open
        page.show()
        widest = max(range(page.report_combo.count()),
                     key=lambda i: len(page.report_combo.itemText(i)))
        page.report_combo.setCurrentIndex(widest)
        page.set_filter(CUSTOM)                 # the custom range is what reveals the two date fields
        for _ in range(5):
            self.app.processEvents()
        host = page.report_combo.parentWidget()
        needed, room = host.sizeHint().width(), host.width()
        print(f"REPORTS_HEADER report={page.report_combo.currentText()!r} needed={needed} room={room} "
              f"dates_visible={page.from_edit.isVisible()}", flush=True)
        self.assertLessEqual(needed, room, f"the reports header wants {needed}px but has {room}px")
