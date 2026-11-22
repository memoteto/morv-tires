"""A purchase return is reachable from the purchases screen, not only from under its supplier."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class PurchaseReturnsOnPurchasesScreenTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import suppliers, users
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
        from morv.infra.suppliers import Supplier
        self.supplier = suppliers.create(self.con, self.admin, Supplier(None, "مورد الشرقاوي", "01500000002"))
        self.app = get_app()

    def _page(self, session=None):
        from morv.ui.purchases_page import PurchasesListPage
        from morv.ui.widgets import dispose
        page = PurchasesListPage(session or self.session)
        self.addCleanup(dispose, page, self.app)
        return page

    def test_the_purchases_screen_offers_the_returns_list(self):
        page = self._page()
        self.assertIsNotNone(page.returns_btn, "a purchase return had no trace on the purchases screen")
        self.assertEqual("مرتجعات الشراء", page.returns_btn.text())

    def test_the_returns_list_without_a_supplier_covers_them_all(self):
        from morv.ui.purchase_return_dialogs import SupplierReturnsDialog
        from morv.ui.widgets import dispose
        dlg = SupplierReturnsDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        self.assertIn("مرتجعات الشراء", dlg.title_label.text())
        self.assertIn("لا توجد مرتجعات شراء بعد", dlg.note.text())

    def test_the_returns_list_of_one_supplier_still_names_him(self):
        from morv.ui.purchase_return_dialogs import SupplierReturnsDialog
        from morv.ui.widgets import dispose
        dlg = SupplierReturnsDialog(self.session, self.supplier.id, self.supplier.name)
        self.addCleanup(dispose, dlg, self.app)
        self.assertIn(self.supplier.name, dlg.title_label.text())
        self.assertIn("لهذا المورد", dlg.note.text())

    def test_a_cashier_is_offered_neither(self):
        from morv.infra import users
        from morv.ui.session import Session
        clerk = users.create_user(self.con, self.admin, "clerk", "موظف", "Clerk12345", "cashier")
        page = self._page(Session(self.con, clerk))
        self.assertIsNone(page.returns_btn)

    def test_the_invoice_view_carries_the_supplier_id_the_return_needs(self):
        from morv.infra import items, purchase_view, purchasing
        from morv.infra.items import Item
        from morv.infra.purchasing import PurchaseLine
        tire = items.create(self.con, self.admin, Item(
            id=None, kind="tire", code="TR-1", name="إطار اختبار", price_retail=645000,
            brand="Michelin", model="Primacy 4",
            attrs={"size": "205/55r16", "li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"}))
        posted = purchasing.post_invoice(
            self.con, self.admin, request_key="pr-key-0001", supplier_id=self.supplier.id,
            supplier_invoice_no="SUP-1", invoice_date="2026-01-05",
            lines=[PurchaseLine(tire.id, 2, 300000, dot="0124")])
        view = purchase_view.get_invoice(self.con, posted.id)
        self.assertEqual(self.supplier.id, view.supplier_id)

    def test_an_invoice_preview_offers_a_return_to_its_own_supplier(self):
        from PySide6.QtWidgets import QAbstractButton
        posted = self._an_invoice()
        page = self._page()
        page.show_preview((posted.id,))
        self.app.processEvents()
        shown = [b.text() for b in page.preview_footer.findChildren(QAbstractButton) if b.text().strip()]
        self.assertIn("مرتجع للمورد", shown)

    def test_a_cashier_gets_no_return_action_on_an_invoice(self):
        from PySide6.QtWidgets import QAbstractButton
        from morv.infra import users
        from morv.ui.session import Session
        posted = self._an_invoice()
        clerk = users.create_user(self.con, self.admin, "clerk2", "موظف٢", "Clerk12345", "cashier")
        users.set_permission(self.con, self.admin, clerk.id, "items.cost_view", True)   # may read, may not post
        page = self._page(Session(self.con, clerk))
        page.show_preview((posted.id,))
        self.app.processEvents()
        shown = [b.text() for b in page.preview_footer.findChildren(QAbstractButton) if b.text().strip()]
        self.assertNotIn("مرتجع للمورد", shown)

    def _an_invoice(self):
        import uuid

        from morv.infra import items, purchasing
        from morv.infra.items import Item
        from morv.infra.purchasing import PurchaseLine
        tire = items.create(self.con, self.admin, Item(
            id=None, kind="tire", code=f"TR-{uuid.uuid4().hex[:6]}", name="إطار اختبار", price_retail=645000,
            brand="Michelin", model="Primacy 4",
            attrs={"size": "205/55r16", "li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"}))
        return purchasing.post_invoice(
            self.con, self.admin, request_key=uuid.uuid4().hex, supplier_id=self.supplier.id,
            supplier_invoice_no="SUP-9", invoice_date="2026-01-05",
            lines=[PurchaseLine(tire.id, 2, 300000, dot="0124")])
