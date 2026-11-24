"""Treasury UI: new-movement dialog, reverse action on the preview, and the partners settings tab."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class TreasuryMovementsUiTest(unittest.TestCase):
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
        cashier = users.create_user(self.con, admin, "karim", "كريم", "Karim12345", "cashier")
        self.session = Session(self.con, admin)
        self.cashier_session = Session(self.con, cashier)
        self.app = get_app()
        from unittest import mock
        patcher = mock.patch("morv.ui.treasury_dialogs.ask", side_effect=AssertionError("unexpected confirmation"))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Seed cash so no insufficient-balance warning opens a blocking confirmation dialog in tests
        from morv.infra import ledger
        from morv.infra.db.connection import transaction
        with transaction(self.con):
            ledger.post_entry(self.con, admin, "2026-11-01", "sale", 1, "فاتورة بيع 1",
                              [("1110", 100000, 0), ("4110", 0, 100000)])

    def page(self, session=None):
        from morv.ui.treasury_page import TreasuryPage
        from morv.ui.widgets import dispose
        page = TreasuryPage(session or self.session)
        self.addCleanup(dispose, page, self.app)
        return page

    def test_bank_reconciliation_dialog_shows_difference_and_saves(self):
        from morv.infra import bank_reconciliation, ledger
        from morv.infra.db.connection import transaction
        from morv.ui.reconciliation_dialogs import ReconciliationDialog
        from morv.ui.widgets import dispose
        with transaction(self.con):
            ledger.post_entry(self.con, self.session.user, "2026-11-02", "sale", 2, "فاتورة بيع 2",
                              [("1210", 30000, 0), ("4110", 0, 30000)])
        dlg = ReconciliationDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.statement.setText("290")
        self.assertIn("أقل", dlg.diff_label.text())
        dlg.save()
        self.assertIsNone(dlg.saved)
        dlg.memo.setText("رسوم بنكية")
        dlg.save()
        self.assertEqual(dlg.saved.difference, -1000)
        self.assertEqual(len(bank_reconciliation.history(self.con, "1210")), 1)
        self.assertIsNone(self.page(self.cashier_session).bank_btn)

    def test_bank_fee_checkbox_books_the_shortfall_and_saves_a_matching_record(self):
        from morv.infra import ledger
        from morv.infra.db.connection import transaction
        from morv.ui.reconciliation_dialogs import ReconciliationDialog
        from morv.ui.widgets import dispose
        with transaction(self.con):
            ledger.post_entry(self.con, self.session.user, "2026-11-02", "sale", 3, "فاتورة بيع 3",
                              [("1210", 30000, 0), ("4110", 0, 30000)])
        dlg = ReconciliationDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        self.assertTrue(dlg.fee_box.isHidden())
        dlg.statement.setText("290")
        self.assertFalse(dlg.fee_box.isHidden())
        dlg.fee_box.setChecked(True)
        dlg.save()
        self.assertEqual(dlg.saved.difference, 0)
        self.assertEqual(ledger.account_balance(self.con, "5260"), 1000)

    def test_cashier_cannot_add_movements(self):
        self.assertFalse(self.page(self.cashier_session).can_edit())
        self.assertTrue(self.page().can_edit())

    def test_expense_dialog_posts_and_the_page_lists_it(self):
        from morv.ui.treasury_dialogs import MovementDialog
        from morv.ui.widgets import dispose
        dlg = MovementDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.kind.setCurrentIndex(dlg.kind.findData("expense"))
        dlg.other.setCurrentIndex(0)
        dlg.amount.setText("50")
        dlg.memo.setText("كهرباء")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        page = self.page()
        self.assertEqual(len(page.model.rows), 2)

    def test_partner_kind_without_partners_is_refused_with_a_hint(self):
        from morv.ui.treasury_dialogs import MovementDialog
        from morv.ui.widgets import dispose
        dlg = MovementDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.kind.setCurrentIndex(dlg.kind.findData("partner_withdrawal"))
        dlg.amount.setText("10")
        dlg.memo.setText("سحب")
        dlg.save()
        self.assertIsNone(dlg.saved)

    def test_reverse_dialog_reverses_and_preview_hides_the_action_afterwards(self):
        from morv.infra import treasury
        from morv.ui.treasury_dialogs import ReverseDialog
        from morv.ui.widgets import dispose
        treasury.add_partner(self.con, self.session.user, "أحمد")
        pid = treasury.list_partners(self.con)[0][0]
        posted = treasury.post_movement(self.con, self.session.user, request_key="k-ui-reverse-1",
                                        kind="partner_capital", amount=10000, memo="رأس مال",
                                        money_account="1110", partner_id=pid)
        dlg = ReverseDialog(self.session, posted.id)
        self.addCleanup(dispose, dlg, self.app)
        dlg.memo.setText("خطأ في الإدخال")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        page = self.page()
        self.assertEqual(len(page.model.rows), 3)

    def test_partners_tab_adds_and_stops_a_partner(self):
        from morv.ui.partners_tab import NewPartnerDialog, PartnersTab
        from morv.ui.widgets import dispose
        tab = PartnersTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        dlg = NewPartnerDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.name.setText("محمد")
        dlg.save()
        tab.reload(dlg.created.id)
        self.assertEqual(tab.model.rowCount(), 1)
        tab.toggle_active()
        self.assertEqual(tab.model.index(0, 3).data(), "موقوف")
        self.assertEqual(tab.active_btn.text(), "تفعيل الشريك")
        tab.toggle_active()
        self.assertEqual(tab.model.index(0, 3).data(), "نشط")
        self.assertEqual(tab.active_btn.text(), "إيقاف الشريك")

    def test_settings_shows_partners_tab_only_to_treasury_managers(self):
        from morv.ui.settings_page import SettingsPage
        from morv.ui.widgets import dispose
        page = SettingsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        titles = [page.title_for(key) for key in page.destinations]
        self.assertIn("الشركاء", titles)
        other = SettingsPage(self.cashier_session)
        self.addCleanup(dispose, other, self.app)
        self.assertNotIn("الشركاء", [other.title_for(key) for key in other.destinations])

    def test_close_dialog_closes_the_till_and_the_page_shows_the_note(self):
        from morv.core.text import ltr
        from morv.ui.treasury_dialogs import CloseDialog
        from morv.ui.widgets import dispose
        dlg = CloseDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.counted.setText("1000")
        self.assertIn("مطابق", dlg.diff_label.text())
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        page = self.page()
        self.assertIn(ltr(dlg.date.text()), page.close_note.text())
        again = CloseDialog(self.session)
        self.addCleanup(dispose, again, self.app)
        self.assertTrue(again.cancel_btn.isVisibleTo(again))

    def test_difference_needs_a_reason_and_cashier_has_no_close_button(self):
        from unittest import mock

        from morv.ui.treasury_dialogs import CloseDialog
        from morv.ui.widgets import dispose
        dlg = CloseDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.counted.setText("900")
        with mock.patch("morv.ui.treasury_dialogs.ask", return_value=True):
            dlg.save()
        self.assertIsNone(dlg.saved)
        self.assertIsNone(self.page(self.cashier_session).close_btn)
        self.assertIsNotNone(self.page().close_btn)

    def test_close_dialog_hands_over_the_surplus_to_a_bank(self):
        from morv.infra import ledger
        from morv.ui.treasury_dialogs import CloseDialog
        from morv.ui.widgets import dispose
        dlg = CloseDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.counted.setText("1000")
        dlg.hand_check.setChecked(True)
        dlg.hand_to.setCurrentIndex(dlg.hand_to.findData("bank:1210"))
        dlg.keep.setText("100")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 10_000)
        self.assertEqual(ledger.account_balance(self.con, "1210"), 90_000)

    def test_close_dialog_partner_handover_asks_before_overdrawing(self):
        from unittest import mock

        from morv.infra import ledger, treasury
        from morv.ui.treasury_dialogs import CloseDialog
        from morv.ui.widgets import dispose
        partner = treasury.add_partner(self.con, self.session.user, "أحمد")
        dlg = CloseDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.counted.setText("1000")
        dlg.hand_check.setChecked(True)
        dlg.hand_to.setCurrentIndex(dlg.hand_to.findData(f"partner:{partner.id}"))
        with mock.patch("morv.ui.treasury_dialogs.ask", return_value=False):
            dlg.save()
        self.assertIsNone(dlg.saved)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 100_000)
        with mock.patch("morv.ui.treasury_dialogs.ask", return_value=True):
            dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 0)

    def test_safe_count_dialog_counts_and_cashier_has_no_button(self):
        from morv.infra import ledger
        from morv.infra.db.connection import transaction
        from morv.ui.treasury_dialogs import SafeCountDialog
        from morv.ui.widgets import dispose
        with transaction(self.con):
            ledger.post_entry(self.con, self.session.user, "2026-11-02", "treasury_movement", 77, "تسليم",
                              [("1130", 5000, 0), ("1110", 0, 5000)])
        dlg = SafeCountDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.counted.setText("50")
        self.assertIn("مطابق", dlg.diff_label.text())
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertIsNone(self.page(self.cashier_session).safe_btn)
        page = self.page()
        self.assertIn("آخر جرد للخزنة", page.close_note.text())
