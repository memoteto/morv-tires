"""Offscreen purchases screen: list, preview, permissions and the invoice dialog."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class PurchasesUiTest(unittest.TestCase):
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
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1", "إطار وهمي")
        self.battery = self.item("battery", "BT-1", "بطارية وهمية")
        self.service = self.item("service", "SV-1", "خدمة وهمية")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def item(self, kind, code, name):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,100,100,100)", (kind, code, name, name)).lastrowid

    def destroy_on_cleanup(self, widget):
        def _destroy():
            from PySide6.QtCore import QEvent
            widget.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(_destroy)
        return widget

    def dialog(self, session=None):
        from morv.ui.purchases_page import PurchaseDialog
        return self.destroy_on_cleanup(PurchaseDialog(session or self.session))

    def page(self, session=None):
        from morv.ui.purchases_page import PurchasesPage
        return self.destroy_on_cleanup(PurchasesPage(session or self.session))

    def pick(self, dlg, item_id):
        for i in range(dlg.item_combo.count()):
            if dlg.item_combo.itemData(i)[0] == item_id:
                dlg.item_combo.setCurrentIndex(i)
                return
        self.fail(f"الصنف {item_id} غير ظاهر في قائمة الاختيار")

    def fill_line(self, dlg, item_id, qty, cost, dot=""):
        self.pick(dlg, item_id)
        dlg.qty.setValue(qty)
        dlg.cost.setText(cost)
        dlg.dot.setText(dot)
        return dlg.add_line()

    def invoices(self):
        return self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0]

    # ---------- Dialog ----------
    def test_post_invoice_end_to_end(self):
        from morv.infra import inventory
        dlg = self.dialog()
        self.assertTrue(self.fill_line(dlg, self.tire, 4, "1,000.50", "2324"))
        self.assertTrue(self.fill_line(dlg, self.battery, 2, "500"))
        self.assertEqual(dlg.lines_model.rowCount(), 2)
        self.assertEqual(dlg.total_minor(), 4 * 100050 + 2 * 50000)
        self.assertIn("5,002.00", dlg.total_label.text())   # 4,002.00 + 1,000.00
        dlg.inv_no.setText("S-100")
        dlg.inv_date.setText("2026-11-05")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(dlg.posted.total, 500200)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_lots").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT dot FROM stock_lots WHERE item_id=?", (self.tire,)).fetchone()[0], "2324")
        self.assertEqual(inventory.on_hand(self.con, self.tire), 4)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_services_not_offered(self):
        dlg = self.dialog()
        ids = {dlg.item_combo.itemData(i)[0] for i in range(dlg.item_combo.count())}
        self.assertEqual(ids, {self.tire, self.battery})
        dlg.item_search.setText("BT-1")
        self.assertEqual(dlg.item_combo.count(), 1)
        self.assertEqual(dlg.item_combo.currentData()[0], self.battery)

    def test_line_validation_messages(self):
        dlg = self.dialog()
        cases = [(self.tire, "100", "", "DOT"), (self.tire, "100", "9999", "DOT"), (self.tire, "1,50", "2324", "الفاصلة"),
                 (self.tire, "0", "2324", "أكبر من صفر"), (self.tire, "", "2324", "المبلغ"), (self.battery, "abc", "", "مبلغ")]
        for item_id, cost, dot, expect in cases:
            with self.subTest(cost=cost, dot=dot):
                self.assertFalse(self.fill_line(dlg, item_id, 1, cost, dot))
                self.assertIn(expect, dlg.notice.text())
                self.assertEqual(dlg.lines_model.rowCount(), 0)
        dlg.item_search.setText("لا يوجد صنف بهذا الاسم")
        self.assertFalse(dlg.add_line())
        self.assertIn("اختر صنفًا", dlg.notice.text())

    def test_header_validation_before_posting(self):
        dlg = self.dialog()
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIn("سطرًا واحدًا", dlg.notice.text())
        self.fill_line(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("  ")
        dlg.post()
        self.assertIn("رقم فاتورة المورد", dlg.notice.text())
        dlg.inv_no.setText("S-1")
        dlg.inv_date.setText("2026-13-45")
        dlg.post()
        self.assertIn("لم تُرحَّل", dlg.notice.text())
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.invoices(), 0)

    def test_engine_rejection_is_shown_and_keeps_input(self):
        first = self.dialog()
        self.fill_line(first, self.battery, 1, "10")
        first.inv_no.setText("S-1")
        first.post()
        self.assertEqual(self.invoices(), 1)
        second = self.dialog()
        self.fill_line(second, self.battery, 3, "20")
        second.inv_no.setText("s-1")
        second.post()
        self.assertIsNone(second.posted)
        self.assertIn("نفس رقم فاتورة المورد", second.notice.text())
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(second.lines_model.rowCount(), 1)
        self.assertTrue(second.post_btn.isEnabled())
        second.inv_no.setText("S-2")
        second.post()
        self.assertIsNotNone(second.posted)
        self.assertEqual(self.invoices(), 2)

    def test_double_post_does_not_duplicate(self):
        dlg = self.dialog()
        self.fill_line(dlg, self.battery, 2, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        first_id = dlg.posted.id
        dlg.post()
        self.assertEqual(dlg.posted.id, first_id)
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_lots").fetchone()[0], 1)

    def test_remove_line_updates_total(self):
        dlg = self.dialog()
        self.fill_line(dlg, self.battery, 1, "10")
        self.fill_line(dlg, self.battery, 2, "20")
        dlg.remove_selected()
        self.assertIn("اختر سطرًا", dlg.notice.text())
        dlg.view.selectRow(0)
        dlg.view.setCurrentIndex(dlg.lines_model.index(0, 0))
        dlg.remove_selected()
        self.assertEqual(dlg.lines_model.rowCount(), 1)
        self.assertEqual(dlg.total_minor(), 2 * 2000)

    def test_no_active_suppliers_warns_and_blocks(self):
        self.con.execute("UPDATE suppliers SET active = 0")
        dlg = self.dialog()
        self.assertIn("لا يوجد موردون", dlg.notice.text())
        self.fill_line(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIn("اختر المورد", dlg.notice.text())
        self.assertEqual(self.invoices(), 0)

    def test_engine_permission_enforced_even_if_ui_bypassed(self):
        dlg = self.dialog(self.cashier_session)
        self.fill_line(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIn("صلاحية", dlg.notice.text())
        self.assertEqual(self.invoices(), 0)

    # ---------- Page ----------
    def test_page_lists_and_previews_invoice(self):
        dlg = self.dialog()
        self.fill_line(dlg, self.tire, 4, "1000", "2324")
        dlg.inv_no.setText("S-77")
        dlg.post()
        page = self.page()
        self.assertEqual(page.model.rowCount(), 1)
        self.assertTrue(page.add_btn.isEnabled())
        self.assertIn("1 فاتورة", page.count_label.text())
        self.assertFalse(page.preview.isVisibleTo(page))
        page.show_preview(page.model.rows[0])
        self.assertTrue(page.preview.isVisibleTo(page))
        page.open_dialog(page.model.rows[0][0])      # opening an existing row previews it and opens no edit form
        self.assertTrue(page.preview.isVisibleTo(page))
        page.search.setText("S-77")
        page.reload()
        self.assertEqual(page.model.rowCount(), 1)
        page.search.setText("غير موجود")
        page.reload()
        self.assertEqual(page.model.rowCount(), 0)

    def test_cashier_cannot_post_or_view(self):
        dlg = self.dialog()
        self.fill_line(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        page = self.page(self.cashier_session)
        self.assertFalse(page.add_btn.isEnabled())
        self.assertEqual(page.model.rowCount(), 0)
        self.assertIn("لا تملك صلاحية", page.count_label.text())
        page.open_dialog(None)       # opens no dialog; one would hang the test on exec

    def test_shell_uses_real_purchases_page(self):
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.shell import MainWindow
        w = self.destroy_on_cleanup(MainWindow(self.session, self.app))
        w.health_timer.stop()
        self.assertIsInstance(w.pages["purchases"], PurchasesPage)
        w.go_key("purchases")
        self.assertEqual(w.title.text(), "المشتريات")


if __name__ == "__main__":
    unittest.main()
