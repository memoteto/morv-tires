"""Partner status action must describe the operation it actually performs."""
from tests.test_ui_phase1 import _Base


class PartnerStatusTest(_Base):
    def test_stopped_partner_action_describes_activation(self):
        from morv.infra import treasury
        from morv.ui.partners_tab import PartnersTab
        from morv.ui.widgets import dispose

        self.make_admin()
        partner = treasury.add_partner(self.con, self.admin, "Test partner")
        treasury.set_partner_active(self.con, self.admin, partner.id, False)
        tab = PartnersTab(self.session())
        self.addCleanup(dispose, tab, self.app)
        tab.reload(partner.id)
        self.assertEqual(tab.active_btn.text(), "تفعيل الشريك")

    def test_empty_and_selected_partner_details(self):
        from morv.infra import treasury
        from morv.ui.partners_tab import PartnersTab
        from morv.ui.widgets import dispose

        self.make_admin()
        tab = PartnersTab(self.session())
        try:
            self.assertFalse(tab.active_btn.isEnabled())
            self.assertEqual(tab.model.rowCount(), 0)
            self.assertIsNotNone(tab.shares_btn)
            a = treasury.add_partner(self.con, self.admin, "Test partner A")
            b = treasury.add_partner(self.con, self.admin, "Test partner B")
            tab.reload(a.id)
            self.assertEqual(tab.name_label.text(), a.name)
            tab.table.setCurrentIndex(tab.model.index(1, 0))
            self.assertEqual(tab._current()[0], b.id)
            self.assertEqual(tab.name_label.text(), b.name)
            self.assertTrue(tab.active_btn.isEnabled())
        finally:
            dispose(tab, self.app)

    def test_effective_share_and_fixed_identity_action(self):
        from datetime import date, timedelta
        from morv.core.text import ltr
        from morv.infra import profit, treasury
        from morv.ui.partners_tab import PartnersTab
        from morv.ui.widgets import dispose

        self.make_admin()
        a = treasury.add_partner(self.con, self.admin, "Test partner A")
        b = treasury.add_partner(self.con, self.admin, "Test partner B")
        tab = PartnersTab(self.session())
        try:
            self.assertEqual(tab.share_row.value_label.text(), "غير معتمدة")
            self.assertTrue(tab.identity_card.isAncestorOf(tab.active_btn))
            self.assertFalse(tab.detail_scroll.isAncestorOf(tab.active_btn))
            today = date.today()
            profit.set_shares(self.con, self.admin, request_key="s5-current-test",
                              effective_from=today.isoformat(),
                              shares=[(a.id, 6000), (b.id, 4000)], memo="Test current")
            profit.set_shares(self.con, self.admin, request_key="s5-future-test",
                              effective_from=(today + timedelta(days=1)).isoformat(),
                              shares=[(a.id, 5000), (b.id, 5000)], memo="Test future")
            tab.reload(a.id)
            self.assertEqual(tab.share_row.value_label.text(), ltr("60%"))
            tab.table.setCurrentIndex(tab.model.index(1, 0))
            self.assertEqual(tab.share_row.value_label.text(), ltr("40%"))
        finally:
            dispose(tab, self.app)
