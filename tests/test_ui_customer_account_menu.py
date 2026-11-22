"""The customer preview groups its four account actions behind one button instead of stacking them."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt

ENTRIES = ["سند قبض", "عربون", "سجل السندات", "كشف الحساب"]


@require_qt()
class CustomerAccountMenuTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import customers, users
        from morv.infra.customers import Customer
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
        customers.create(self.con, self.admin, Customer(None, "أمين محمد", "01000000020"))
        self.app = get_app()

    def _preview(self, session=None):
        from morv.ui.customers_page import CustomersPage
        from morv.ui.widgets import dispose
        page = CustomersPage(session or self.session)
        self.addCleanup(dispose, page, self.app)
        page.show_preview(page.model.rows[0])
        self.app.processEvents()
        return page

    def test_the_four_account_actions_sit_in_one_menu(self):
        page = self._preview()
        menu = page.account_btn.menu()
        self.assertIsNotNone(menu, "the account button opens no menu")
        self.assertEqual([a.text() for a in menu.actions()], ENTRIES)

    def test_the_preview_shows_three_buttons_not_six(self):
        from PySide6.QtWidgets import QAbstractButton
        page = self._preview()
        shown = [b.text() for b in page.preview_footer.findChildren(QAbstractButton) if b.text().strip()]
        self.assertEqual(sorted(shown), sorted(["تعديل", "إيقاف", "الحساب"]), f"found {shown}")

    def test_the_two_posting_entries_follow_the_receipts_permission(self):
        from morv.infra import users
        from morv.ui.session import Session
        clerk = users.create_user(self.con, self.admin, "clerk", "موظف", "Clerk12345", "cashier")
        page = self._preview(Session(self.con, clerk))
        by_text = {a.text(): a for a in page.account_btn.menu().actions()}
        self.assertFalse(by_text["سند قبض"].isEnabled())
        self.assertFalse(by_text["عربون"].isEnabled())
        self.assertTrue(by_text["كشف الحساب"].isEnabled(), "reading the statement needs no posting permission")
