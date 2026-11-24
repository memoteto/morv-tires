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
