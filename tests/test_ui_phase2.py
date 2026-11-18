"""Offscreen phase 2 screens: customers, suppliers, stock and permissions."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class _Base(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.session = Session(self.con, self.admin)
        self.cashier_session = Session(self.con, self.cashier)
        self.app = get_app()

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


class CustomersPageTest(_Base):
    def test_preview_opens_on_click_only(self):
        from morv.infra import customers
        from morv.infra.customers import Customer
        from morv.ui.customers_page import CustomersPage
        customers.create(self.con, self.admin, Customer(None, "أحمد محمود", "01012345678"))
        page = CustomersPage(self.session)
        self.assertFalse(page.preview.isVisibleTo(page))
        self.assertEqual(page.model.rowCount(), 1)
        page.show_preview(page.model.rows[0])
        self.assertTrue(page.preview.isVisibleTo(page))
        page.hide_preview()
        self.assertFalse(page.preview.isVisibleTo(page))

    def test_search_filters_rows(self):
        from morv.infra import customers
        from morv.infra.customers import Customer
        from morv.ui.customers_page import CustomersPage
        customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        customers.create(self.con, self.admin, Customer(None, "محمد", "01111111111"))
        page = CustomersPage(self.session)
        page.search.setText("احمد")
        page.reload()
        self.assertEqual([r[1] for r in page.model.rows], ["أحمد"])
        page.search.setText("0111")
        page.reload()
        self.assertEqual([r[1] for r in page.model.rows], ["محمد"])

    def test_dialog_rejects_duplicate_and_saves(self):
        from morv.infra import customers
        from morv.infra.customers import Customer, Vehicle
        from morv.ui.customers_page import CustomerDialog
        customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        dlg = CustomerDialog(self.session, None)
        dlg.name.setText("سامي")
        dlg.mobile.setText("01012345678")
        dlg.save()
        self.assertIn("أحمد", dlg.notice.text())
        dlg.mobile.setText("01555555555")
        dlg.vehicles.append(Vehicle("كيا", "سيراتو", 2018, "ب هـ ج 7310"))
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(dlg.saved.vehicles[0].plate, "ب هـ ج 7310")
        self.assertEqual(len(customers.search(self.con, "7310")), 1)

    def test_cashier_can_add_but_not_change_level(self):
        from morv.ui.customers_page import CustomerDialog, CustomersPage
        page = CustomersPage(self.cashier_session)
        self.assertTrue(page.add_btn.isEnabled())
        dlg = CustomerDialog(self.cashier_session, None)
        self.assertFalse(dlg.level.isEnabled())


class SuppliersPageTest(_Base):
    def test_cashier_read_only(self):
        from morv.ui.suppliers_page import SuppliersPage
        cashier_page, admin_page = SuppliersPage(self.cashier_session), SuppliersPage(self.session)
        self.assertFalse(cashier_page.add_btn.isEnabled())
        self.assertTrue(admin_page.add_btn.isEnabled())

    def test_dialog_saves(self):
        from morv.ui.suppliers_page import SupplierDialog
        dlg = SupplierDialog(self.session, None)
        dlg.name.setText("شركة الإطارات")
        dlg.mobile.setText("01222222222")
        dlg.save()
        self.assertEqual(dlg.saved.name, "شركة الإطارات")


class InventoryPageTest(_Base):
    def test_tabs_and_item_dialog_from_fields(self):
        from morv.infra import items
        from morv.ui.inventory_page import InventoryPage, ItemDialog
        admin_page = InventoryPage(self.session)            # kept in a variable, or Qt takes it and its tabs away
        cashier_page = InventoryPage(self.cashier_session)
        self.assertEqual(admin_page.tabs.count(), 3)        # card fields moved into settings
        self.assertEqual(cashier_page.tabs.count(), 3)
        dlg = self.destroy_on_cleanup(ItemDialog(self.session, None))
        self.assertIn("size", dlg.widgets)          # the tyre field
        dlg.set_kind("battery")
        self.assertIn("ah", dlg.widgets)
        self.assertNotIn("size", dlg.widgets)
        dlg.set_kind("tire")
        dlg.widgets["brand"].setText("Michelin")
        dlg.widgets["model"].setText("Primacy 4")
        dlg.widgets["size"].setText("205/55r16")
        dlg.widgets["li"].setText("91")
        dlg.widgets["si"].setCurrentText("V")
        # name and code are suggested as placeholder text and accepted when left empty
        self.assertEqual(dlg.widgets["name"].placeholderText(), "إطار Michelin Primacy 4 205/55R16 91V")
        self.assertEqual(dlg.widgets["code"].placeholderText(), "TR-2055516-MI")
        keys = list(dlg.widgets)
        self.assertLess(keys.index("size"), keys.index("name"))   # kind fields come before the shared ones
        dlg.widgets["price_retail"].setText("6450")
        dlg.save()
        self.assertIn("الفئة", dlg.notice.text())     # a required field is missing
        dlg.widgets["cat"].setCurrentText("ملاكي")
        dlg.widgets["cons"].setCurrentText("Radial")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(dlg.saved.price_retail, 645000)
        self.assertEqual(dlg.saved.code, "TR-2055516-MI")
        self.assertEqual(items.count(self.con), 1)
        # a battery in the same dialog
        dlg2 = self.destroy_on_cleanup(ItemDialog(self.session, None))
        dlg2.set_kind("battery")
        dlg2.widgets["brand"].setText("Varta")
        dlg2.widgets["ah"].setText("70")
        dlg2.widgets["volt"].setCurrentText("12V")
        dlg2.widgets["cca"].setText("640")
        dlg2.widgets["tech"].setCurrentText("EFB")
        dlg2.widgets["box"].setText("L3")
        dlg2.widgets["pol"].setCurrentText("الموجب يمين")
        dlg2.widgets["dims"].setText("278×175×190")
        dlg2.widgets["price_retail"].setText("4200")
        dlg2.save()
        self.assertEqual(dlg2.saved.name, "بطارية Varta 70 أمبير")
        # a service
        dlg3 = self.destroy_on_cleanup(ItemDialog(self.session, None))
        dlg3.set_kind("service")
        dlg3.widgets["name"].setText("تركيب إطار")
        dlg3.widgets["price_retail"].setText("50")
        dlg3.save()
        self.assertEqual(dlg3.saved.code, "SV-001")

    def test_scan_enter_opens_preview(self):
        from morv.infra import items
        from morv.infra.items import Item
        from morv.ui.inventory_page import ItemsPage
        items.create(self.con, self.admin, Item(None, "service", "SV-001", "تركيب", barcode="12345678", price_retail=5000))
        page = ItemsPage(self.session)
        page.search.setText("12345678")
        page.scan_enter()
        self.assertTrue(page.preview.isVisibleTo(page))

    def test_fields_tab_toggle(self):
        from morv.infra import items
        from morv.ui.inventory_page import ItemFieldsTab
        from PySide6.QtCore import Qt
        tab = ItemFieldsTab(self.session)
        tab.kind.setCurrentIndex(1)   # the tyre
        row = next(i for i in range(tab.table.rowCount()) if tab.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == "season")
        tab.table.item(row, 2).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(next(f for f in items.fields(self.con, "tire") if f.key == "season").show)
        row = next(i for i in range(tab.table.rowCount()) if tab.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == "size")
        self.assertFalse(tab.table.item(row, 2).flags() & Qt.ItemFlag.ItemIsEnabled)


class ModalTest(_Base):
    def test_fits_screen_and_footer_visible(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from morv.ui.inventory_page import ItemDialog
        from morv.ui.customers_page import CustomerDialog
        avail = QGuiApplication.primaryScreen().availableGeometry().height()
        for dlg in (ItemDialog(self.session, None), CustomerDialog(self.session, None)):
            dlg.show()
            self.app.processEvents()
            self.assertLessEqual(dlg.height(), avail - 60)
            self.assertTrue(dlg.footer.isVisibleTo(dlg))
            self.assertTrue(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint)   # without an OS title bar
            dlg.close()
            dlg.deleteLater()
        self.app.processEvents()


class ShellPagesTest(_Base):
    def test_real_pages_mounted(self):
        from morv.ui.customers_page import CustomersPage
        from morv.ui.inventory_page import InventoryPage
        from morv.ui.shell import MainWindow
        from morv.ui.suppliers_page import SuppliersPage
        w = MainWindow(self.session, self.app)
        w.health_timer.stop()
        from morv.ui.widgets import dispose
        self.addCleanup(dispose, w, self.app)
        self.assertIsInstance(w.pages["customers"], CustomersPage)
        self.assertIsInstance(w.pages["suppliers"], SuppliersPage)
        self.assertIsInstance(w.pages["inventory"], InventoryPage)


if __name__ == "__main__":
    unittest.main()


@require_qt()
class CustomerStatementUiTest(unittest.TestCase):
    def setUp(self):
        from morv.core.sales_rules import Payment
        from morv.infra import sales, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.sales import SaleLineIn
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',50000,50000,50000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',5000000) RETURNING id").fetchone()[0]
        sales.post_sale(self.con, self.admin, request_key="ui-st-key-0001", customer_id=self.cust,
                        lines=[SaleLineIn(self.service, 1, 100000)], payments=[Payment("credit", 100000)],
                        sale_date="2026-02-01")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dialog(self):
        from morv.infra import customer_statement
        from morv.ui.customer_statement_dialog import CustomerStatementDialog
        dlg = CustomerStatementDialog(
            "عميل وهمي",
            lambda f, t: customer_statement.statement(self.con, self.cust, f, t))
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_dialog_shows_the_movement_and_the_balance(self):
        dlg = self.dialog()
        self.assertEqual(len(dlg.model.rows), 1)
        self.assertEqual(dlg.statement.closing, 100000)

    def test_period_before_the_sale_shows_nothing(self):
        dlg = self.dialog()
        dlg.date_from.setText("2026-05-01")
        dlg.date_to.setText("2026-06-01")
        dlg.reload()
        self.assertEqual(len(dlg.model.rows), 0)
        self.assertEqual(dlg.statement.opening, 100000)
        self.assertTrue(dlg.empty_label.isVisibleTo(dlg))

    def test_bad_date_is_refused_without_breaking_the_view(self):
        dlg = self.dialog()
        dlg.date_from.setText("2026-13-99")
        dlg.reload()
        self.assertEqual(len(dlg.model.rows), 1)

    def test_statement_layout_keeps_dates_and_opening_before_table(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QScrollArea
        dlg = self.dialog()
        dlg.show()
        self.app.processEvents()
        self.assertTrue(dlg.table_mode)
        self.assertIsNone(dlg.scroll)
        self.assertEqual(dlg.findChildren(QScrollArea), [])
        self.assertEqual(dlg.date_from.mapTo(dlg, QPoint()).y(),
                         dlg.date_to.mapTo(dlg, QPoint()).y())
        self.assertLess(dlg.opening_label.mapTo(dlg, QPoint()).y(),
                        dlg.view.mapTo(dlg, QPoint()).y())
        self.assertTrue(dlg.opening_label.isVisibleTo(dlg))
        self.assertTrue(dlg.footer.isVisibleTo(dlg))
        self.assertEqual(dlg.statement.closing, 100000)
        dlg.close()

    def test_statement_table_scrolls_without_moving_controls(self):
        from PySide6.QtCore import QPoint
        dlg = self.dialog()
        dlg.show()
        self.app.processEvents()
        dlg.model.reset_rows([
            ("2026-02-01", f"SI-{i:06d}", "فاتورة بيع آجلة", 100000, 0, 100000)
            for i in range(120)
        ])
        self.app.processEvents()
        bar = dlg.view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertGreater(dlg.view.height(), 120)
        fixed = (dlg.date_from.mapTo(dlg, QPoint()),
                 dlg.opening_label.mapTo(dlg, QPoint()),
                 dlg.footer.mapTo(dlg, QPoint()))
        bar.setValue(bar.maximum())
        self.app.processEvents()
        self.assertEqual(fixed, (dlg.date_from.mapTo(dlg, QPoint()),
                                 dlg.opening_label.mapTo(dlg, QPoint()),
                                 dlg.footer.mapTo(dlg, QPoint())))
        self.assertTrue(dlg.footer.isVisibleTo(dlg))
        self.assertTrue(dlg.opening_label.isVisibleTo(dlg))
        dlg.close()
