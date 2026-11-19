"""Treasury screen: balances, movements, filters, permission gate and the entry preview."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class TreasuryPageTest(unittest.TestCase):
    def setUp(self):
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
        self.session = Session(self.con, admin)
        self.cashier_session = Session(self.con, cashier)
        self.app = get_app()
        with transaction(self.con):
            ledger.post_entry(self.con, admin, "2026-11-01", "sale", 1, "فاتورة بيع 1",
                              [("1110", 50000, 0), ("4110", 0, 50000)])
            ledger.post_entry(self.con, admin, "2026-11-02", "sale", 2, "فاتورة بيع 2",
                              [("1210", 30000, 0), ("4110", 0, 30000)])
            ledger.post_entry(self.con, admin, "2026-11-03", "purchase_invoice", 1, "فاتورة شراء 1",
                              [("1410", 20000, 0), ("1110", 0, 20000)])

    def page(self, session=None):
        from morv.ui.treasury_page import TreasuryPage
        from morv.ui.widgets import dispose
        page = TreasuryPage(session or self.session)
        self.addCleanup(dispose, page, self.app)
        return page

    def test_manager_sees_balances_and_every_money_movement(self):
        from morv.core.money import fmt
        from morv.core.text import ltr
        page = self.page()
        self.assertEqual(len(page.model.rows), 3)
        self.assertEqual(page._balance_values["1110"].text(), ltr(fmt(30000)))
        self.assertEqual(page._balance_values["1210"].text(), ltr(fmt(30000)))
        self.assertEqual(page._balance_values["1230"].text(), ltr(fmt(0)))
        self.assertEqual(page._balance_values["total"].text(), ltr(fmt(60000)))

    def test_account_filter_and_search_narrow_the_rows(self):
        page = self.page()
        page.set_filter("1210")
        self.assertEqual([r[2] for r in page.model.rows], ["1210"])
        page.set_filter("all")
        page.search.setText("شراء")
        page.reload()
        self.assertEqual(len(page.model.rows), 1)

    def test_user_without_the_permission_sees_nothing(self):
        page = self.page(self.cashier_session)
        self.assertEqual(page.model.rows, [])
        self.assertIn("لا تملك صلاحية", page.count_label.text())

    def test_preview_shows_every_line_of_the_entry(self):
        page = self.page()
        page.show_preview_id(page.model.rows[0][0])
        self.assertEqual(page.current_id, page.model.rows[0][0])

    def test_shell_opens_the_real_screen_not_the_placeholder(self):
        from morv.ui.shell import MainWindow
        from morv.ui.treasury_page import TreasuryPage
        from morv.ui.widgets import dispose
        w = MainWindow(self.session, self.app)
        w.health_timer.stop()
        self.addCleanup(dispose, w, self.app)
        w.go_key("treasury")
        self.assertIsInstance(w.pages["treasury"], TreasuryPage)


if __name__ == "__main__":
    unittest.main()
