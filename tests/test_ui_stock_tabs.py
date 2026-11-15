"""Lots and stocktake tabs: display, filtering, permissions and previews."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class StockTabsTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import inventory, purchasing, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.session = Session(self.con, self.admin)
        self.cashier_session = Session(self.con, self.cashier)
        self.app = get_app()
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1", "اطار وهمي", min_qty=10)
        self.tire2 = self.item("tire", "TR-2", "اطار بلا رصيد")
        self.battery = self.item("battery", "BT-1", "بطارية وهمية")
        purchasing.post_invoice(self.con, self.admin, request_key="key-000001", supplier_id=self.sup,
                                supplier_invoice_no="S-1", invoice_date="2026-11-01",
                                lines=[PurchaseLine(self.tire, 4, 100000, "2124"), PurchaseLine(self.tire, 2, 90000, "2024"),
                                       PurchaseLine(self.battery, 3, 50000)])
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=1, source_line_id=1)

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def item(self, kind, code, name, min_qty=0):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super, min_qty) "
            "VALUES (?,?,?,?,100,100,100,?)", (kind, code, name, name, min_qty)).lastrowid

    def destroy_on_cleanup(self, widget):
        def _destroy():
            from PySide6.QtCore import QEvent
            widget.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(_destroy)
        return widget

    def lots_page(self, session=None):
        from morv.ui.stock_pages import LotsPage
        return self.destroy_on_cleanup(LotsPage(session or self.session))

    def stock_page(self, session=None):
        from morv.ui.stock_pages import StockPage
        return self.destroy_on_cleanup(StockPage(session or self.session))

    def test_lots_oldest_dot_first_with_remaining(self):
        page = self.lots_page()
        rows = page.model.rows
        tire_rows = [r for r in rows if r[3] == "TR-1"]
        self.assertEqual([(r[4], r[7]) for r in tire_rows], [("2024", 1), ("2124", 4)])
        self.assertEqual(len(rows), 3)
        self.assertTrue(page.add_btn.isHidden())
        self.assertIn("8", page.count_label.text())     # 1 + 4 + 3

    def test_lots_filter_all_includes_exhausted_and_search_by_dot(self):
        from morv.infra import inventory
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=2, source_line_id=1)
        page = self.lots_page()
        self.assertEqual(page.model.rowCount(), 2)      # the 2024 lot is empty
        page.set_filter("all")
        self.assertEqual(page.model.rowCount(), 3)
        page.search.setText("2124")
        page.reload()
        self.assertEqual([r[4] for r in page.model.rows], ["2124"])

    def test_lot_preview_shows_movements(self):
        from PySide6.QtWidgets import QLabel
        page = self.lots_page()
        row = next(r for r in page.model.rows if r[4] == "2024")
        page.show_preview(row)
        texts = [lb.text() for lb in page.preview_body.findChildren(QLabel)]
        self.assertTrue(any("استلام" in t for t in texts))
        self.assertTrue(any("صرف" in t for t in texts))

    def test_cashier_sees_quantities_but_not_costs(self):
        lots = self.lots_page(self.cashier_session)
        self.assertEqual(lots.model.rowCount(), 3)
        self.assertTrue(all(r[8] is None for r in lots.model.rows))
        self.assertEqual({lots.columns[6][1](r) for r in lots.model.rows}, {"—"})
        stock = self.stock_page(self.cashier_session)
        self.assertTrue(all(r[6] is None for r in stock.model.rows))
        self.assertNotIn("القيمة", stock.count_label.text())
        self.assertFalse(stock.add_btn.isEnabled())
        stock.open_dialog(None)      # without permission there is no dialog, which would hang the test

    def test_stock_summary_quantities_and_value(self):
        from morv.infra import inventory
        page = self.stock_page()
        by_code = {r[2]: r for r in page.model.rows}
        self.assertEqual(by_code["TR-1"][4], 5)
        self.assertEqual(by_code["TR-1"][5], 2)
        self.assertEqual(by_code["TR-1"][6], inventory.book_value(self.con, self.tire))
        self.assertEqual(by_code["BT-1"][4], 3)
        self.assertEqual(by_code["TR-2"][4], 0)
        self.assertIn("جنيه", page.count_label.text())

    def test_stock_filters(self):
        page = self.stock_page()
        page.set_filter("stock")
        self.assertEqual({r[2] for r in page.model.rows}, {"TR-1", "BT-1"})
        page.set_filter("low")
        self.assertEqual([r[2] for r in page.model.rows], ["TR-1"])

    def test_reconcile_button_reports_ok_and_problems(self):
        from morv.ui import stock_pages
        shown = []
        original = stock_pages.info
        stock_pages.info = lambda _p, title, text, error=False: shown.append((title, text, error))
        self.addCleanup(setattr, stock_pages, "info", original)
        page = self.stock_page()
        page.open_dialog(None)
        self.assertEqual(shown[-1][0], "المطابقة")
        self.assertFalse(shown[-1][2])
        self.assertIn("متطابق", shown[-1][1])
        self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - 1 WHERE item_id = ?", (self.battery,))
        page.open_dialog(None)
        self.assertTrue(shown[-1][2])
        self.assertIn("فروق", shown[-1][1])

    def test_inventory_tabs_wired_and_refresh_on_open(self):
        from morv.infra import inventory
        from morv.ui.inventory_page import InventoryPage
        from morv.ui.stock_pages import LotsPage, StockPage
        page = self.destroy_on_cleanup(InventoryPage(self.session))
        self.assertIsInstance(page.tabs.widget(1), LotsPage)
        self.assertIsInstance(page.tabs.widget(2), StockPage)
        inventory.issue(self.con, self.admin, self.battery, 3, source_type="t", source_id=3, source_line_id=1)
        page.tabs.setCurrentIndex(1)
        self.assertEqual(len(page.lots_tab.model.rows), 2)      # the battery lot is empty and leaves the in-stock filter

    def test_item_and_supplier_previews_show_real_numbers(self):
        from PySide6.QtWidgets import QLabel
        from morv.ui.inventory_page import ItemsPage
        from morv.ui.suppliers_page import SuppliersPage
        items_page = self.destroy_on_cleanup(ItemsPage(self.session))
        items_page.show_preview_id(self.tire)
        texts = " ".join(lb.text() for lb in items_page.preview_body.findChildren(QLabel))
        self.assertIn("الرصيد", texts)
        self.assertIn("القيمة الدفترية", texts)
        sup_page = self.destroy_on_cleanup(SuppliersPage(self.session))
        sup_page.show_preview_id(self.sup)
        texts = " ".join(lb.text() for lb in sup_page.preview_body.findChildren(QLabel))
        self.assertIn("فواتير الشراء", texts)
        self.assertIn("1", texts)


if __name__ == "__main__":
    unittest.main()
