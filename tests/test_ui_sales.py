"""Offscreen POS screen: it builds, it posts through the engine, it refuses empty."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class SalesUiTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        from morv.core.text import norm_ar
        self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة وهمية',?,100,100,100)", (norm_ar("خدمة وهمية"),))

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def destroy_on_cleanup(self, widget):
        def _destroy():
            from PySide6.QtCore import QEvent
            widget.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(_destroy)
        return widget

    def test_page_builds_and_counts_zero_sales(self):
        from morv.ui.sales_page import SalesPage
        page = self.destroy_on_cleanup(SalesPage(self.session))
        self.assertEqual(page.total(), "0 فاتورة بيع")

    def test_dialog_posts_service_sale_through_engine(self):
        from morv.ui.pos_page import PosPage
        dlg = self.destroy_on_cleanup(PosPage(self.session))
        dlg.item_search.setText("خدمة")
        self.assertEqual(dlg.item_combo.count(), 1)
        dlg.price.setText("1.00")
        self.assertTrue(dlg.add_line())
        dlg.pay1_received.setText("2.00")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT total FROM sales").fetchone()[0], 100)

    def test_dialog_refuses_empty_invoice(self):
        from morv.ui.pos_page import PosPage
        dlg = self.destroy_on_cleanup(PosPage(self.session))
        dlg.post()
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 0)


@require_qt()
class SaleReturnUiTest(SalesUiTest):
    def setUp(self):
        super().setUp()
        from morv.core.sales_rules import Payment
        from morv.infra import sales
        from morv.infra.sales import SaleLineIn
        item = self.con.execute("SELECT id FROM items").fetchone()[0]
        self.sale = sales.post_sale(self.con, self.admin, request_key="ui-return-key-1", customer_id=None,
                                    lines=[SaleLineIn(item, 2, 100)],
                                    payments=[Payment("cash", 200, received=200)], sale_date="2026-11-10")

    def dialog(self):
        from morv.infra import sale_view
        from morv.ui.sale_return_dialog import SaleReturnDialog
        con = self.session.con
        return self.destroy_on_cleanup(SaleReturnDialog(
            self.session, sale_view.get_sale(con, self.sale.id), sale_view.returnable_lines(con, self.sale.id),
            sale_view.refund_methods(con, self.sale.id)))

    def test_dialog_posts_a_return_and_shows_its_value(self):
        dlg = self.dialog()
        self.assertEqual(dlg.line_combo.count(), 1)
        dlg.qty.setValue(1)
        self.assertTrue(dlg.add_line())
        self.assertEqual(dlg.model.rows[0][4], 100)
        dlg.reason.setCurrentText("عيب في الصنف")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 1)

    def test_dialog_refuses_empty_and_short_reason(self):
        dlg = self.dialog()
        dlg.post()
        self.assertIsNone(dlg.posted)
        dlg.qty.setValue(1)
        dlg.add_line()
        dlg.reason.setCurrentText("لا")
        dlg.post()
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 0)

    def test_available_shrinks_after_a_line_is_added(self):
        dlg = self.dialog()
        dlg.qty.setValue(2)
        dlg.add_line()
        self.assertEqual(dlg.line_combo.count(), 0)
        self.assertFalse(dlg.add_btn.isEnabled())

    def test_page_shows_return_button_only_with_permission(self):
        from morv.infra import users
        from morv.ui.sales_page import SalesPage
        from morv.ui.session import Session
        page = self.destroy_on_cleanup(SalesPage(self.session))
        self.assertTrue(page.can_return())
        cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        other = self.destroy_on_cleanup(SalesPage(Session(self.con, cashier)))
        self.assertFalse(other.can_return())
