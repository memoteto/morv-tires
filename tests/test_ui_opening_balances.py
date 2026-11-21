"""Opening balances screen: approval is blocked until the preview balances; tab follows the live document."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class OpeningBalancesUiTest(unittest.TestCase):
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

    def dialog(self):
        from morv.ui.opening_dialogs import OpeningBalancesDialog
        from morv.ui.widgets import dispose
        dlg = OpeningBalancesDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        return dlg

    def add(self, dlg, kind, amount="", retained=False):
        dlg.kind.setCurrentIndex(dlg.kind.findData(kind))
        dlg.amount.setText(amount)
        dlg.add_line()

    def test_unbalanced_preview_keeps_approval_disabled(self):
        dlg = self.dialog()
        dlg.kind.setCurrentIndex(dlg.kind.findData("account"))
        dlg.amount.setText("5000")
        dlg.add_line()
        dlg.review()
        self.assertFalse(dlg.post_btn.isEnabled())
        self.assertIn("غير متوازن", dlg.summary.text())

    def test_balanced_lines_enable_approval_and_post(self):
        dlg = self.dialog()
        self.add(dlg, "account", "5000")
        self.add(dlg, "retained", "5000")
        dlg.review()
        self.assertTrue(dlg.post_btn.isEnabled())
        dlg.save()
        self.assertIsNotNone(dlg.saved)

    def test_editing_lines_after_review_disables_approval_again(self):
        dlg = self.dialog()
        self.add(dlg, "account", "5000")
        self.add(dlg, "retained", "5000")
        dlg.review()
        dlg.remove_last()
        self.assertFalse(dlg.post_btn.isEnabled())

    def test_tab_shows_live_document_and_enables_reversal(self):
        from morv.ui.opening_dialogs import OpeningTab
        from morv.ui.widgets import dispose
        tab = OpeningTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        self.assertTrue(tab.enter_btn.isEnabled())
        self.assertFalse(tab.reverse_btn.isEnabled())
        dlg = self.dialog()
        self.add(dlg, "account", "5000")
        self.add(dlg, "retained", "5000")
        dlg.review()
        dlg.save()
        tab.refresh()
        self.assertFalse(tab.enter_btn.isEnabled())
        self.assertTrue(tab.reverse_btn.isEnabled())
