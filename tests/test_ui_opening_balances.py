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

    def test_fields_follow_all_seven_line_kinds(self):
        dlg = self.dialog()
        for kind in ("account", "customer", "supplier", "partner_capital",
                     "partner_current", "retained", "inventory"):
            with self.subTest(kind=kind):
                dlg.kind.setCurrentIndex(dlg.kind.findData(kind))
                self.assertEqual(dlg.amount.isVisibleTo(dlg), kind != "inventory")
                for widget in (dlg.qty, dlg.unit_cost, dlg.dot):
                    self.assertEqual(widget.isVisibleTo(dlg), kind == "inventory")
                self.assertEqual(dlg.target.isVisibleTo(dlg), kind != "retained")

    def test_draft_table_review_totals_and_invalidation(self):
        from morv.core.text import ltr
        from morv.core.money import fmt
        from morv.infra import ledger

        dlg = self.dialog()
        self.add(dlg, "account", "5000")
        self.assertEqual(dlg.line_model.rowCount(), 1)
        self.assertEqual(dlg.line_model.index(0, 3).data(), ltr(fmt(500000)))
        self.add(dlg, "retained", "5000")
        dlg.review()
        self.assertEqual(dlg.line_model.rowCount(), 2)
        self.assertEqual([label.text() for label in dlg.total_labels],
                         [ltr(fmt(x)) for x in (500000, 0, 500000, 0)])
        self.assertTrue(dlg.post_btn.isEnabled())
        self.assertEqual(ledger.account_balance(self.con, "1110"), 0)
        dlg.remove_last()
        self.assertEqual(dlg.line_model.rowCount(), 1)
        self.assertTrue(dlg.totals.isHidden())
        self.assertFalse(dlg.post_btn.isEnabled())

    def test_many_draft_lines_remain_available_for_review(self):
        dlg = self.dialog()
        dlg.kind.setCurrentIndex(dlg.kind.findData("customer"))
        for i in range(30):
            name = f"Test customer {i}"
            cid = self.con.execute(
                "INSERT INTO customers (name, name_norm, mobile) VALUES (?,?,?) RETURNING id",
                (name, name.lower(), f"0100000{i:04d}")).fetchone()[0]
            dlg._fill_targets()
            dlg.target.setCurrentIndex(dlg.target.findData(cid))
            dlg.amount.setText("100")
            dlg.add_line()
        self.add(dlg, "retained", "3000")
        dlg.review()
        self.assertEqual(dlg.line_model.rowCount(), 31)
        self.assertTrue(dlg.post_btn.isEnabled())
        self.assertEqual(dlg.lines[-1].amount, 300000)

    def test_reverse_dialog_requires_reason_and_restores_zero(self):
        from morv.infra import ledger, opening_balances
        from morv.ui.opening_dialogs import ReverseOpeningDialog
        from morv.ui.widgets import dispose

        dlg = self.dialog()
        self.add(dlg, "account", "5000")
        self.add(dlg, "retained", "5000")
        dlg.review()
        dlg.save()
        rev = ReverseOpeningDialog(self.session, dlg.saved.id)
        self.addCleanup(dispose, rev, self.app)
        rev.save()
        self.assertIsNone(rev.saved)
        self.assertIsNotNone(opening_balances.current(self.con))
        rev.reason.setText("Test correction")
        rev.save()
        self.assertIsNotNone(rev.saved)
        self.assertIsNone(opening_balances.current(self.con))
        self.assertEqual(ledger.account_balance(self.con, "1110"), 0)
        self.assertEqual(ledger.account_balance(self.con, "3500"), 0)
