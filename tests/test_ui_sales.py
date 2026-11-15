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
