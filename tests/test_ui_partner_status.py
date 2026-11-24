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
