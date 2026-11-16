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
        stack = self.page(session)
        stack.open_form()
        f = stack.form_page
        # Pre-select the default supplier for tests that don't test supplier selection
        if f._supplier_id is None and self.sup:
            f._pick_supplier(self.sup, "مورد وهمي", "01000000001")
        return f

    def page(self, session=None):
        from morv.ui.purchases_page import PurchasesPage
        return self.destroy_on_cleanup(PurchasesPage(session or self.session))

    def add_row(self, dlg, item_id, qty=1, cost="", dot=""):
        # Bypass the live-search popup (headless test): add the row directly,
        # then fill in qty/cost/DOT the way the inline row fields would.
        row = self.con.execute("SELECT kind, name FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            self.fail(f"الصنف {item_id} غير موجود في قاعدة البيانات")
        dlg._add_row(item_id, row[0], row[1])
        line = dlg._rows[-1]
        line["qty_spin"].setValue(qty)
        line["cost_edit"].setText(cost)
        line["dot_edit"].setText(dot)
        return line

    def invoices(self):
        return self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0]

    # ---------- Dialog ----------
    def test_post_invoice_end_to_end(self):
        from morv.infra import inventory
        dlg = self.dialog()
        self.add_row(dlg, self.tire, 4, "1,000.50", "2324")
        self.add_row(dlg, self.battery, 2, "500")
        self.assertEqual(len(dlg._rows), 2)
        self.assertEqual(dlg._sub_total(), 4 * 100050 + 2 * 50000)
        self.assertIn("5,002.00", dlg._sub_lbl.text())   # 4,002.00 + 1,000.00
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
        # Services must not appear in item search results
        from morv.infra import items as items_infra
        rows_all = items_infra.search(self.session.con, "", kind="tire", limit=50)
        rows_all += items_infra.search(self.session.con, "", kind="battery", limit=50)
        ids = {r[0] for r in rows_all}
        self.assertIn(self.tire, ids)
        self.assertIn(self.battery, ids)
        self.assertNotIn(self.service, ids)
        # Battery-specific search
        rows_bt = items_infra.search(self.session.con, "BT-1", kind="battery", limit=50)
        self.assertEqual(len(rows_bt), 1)
        self.assertEqual(rows_bt[0][0], self.battery)

    def test_line_validation_messages(self):
        dlg = self.dialog()
        cases = [(self.tire, "100", "", "DOT"), (self.tire, "100", "9999", "DOT"), (self.tire, "1,50", "2324", "الفاصلة"),
                 (self.tire, "0", "2324", "أكبر من صفر"), (self.tire, "", "2324", "المبلغ"), (self.battery, "abc", "", "مبلغ")]
        for item_id, cost, dot, expect in cases:
            with self.subTest(cost=cost, dot=dot):
                row = self.add_row(dlg, item_id, 1, cost, dot)
                lines, err = dlg._validate_lines()
                self.assertIsNone(lines)
                self.assertIn(expect, err)
                dlg._remove_row(row)
        # No rows at all -> should fail
        self.assertEqual(len(dlg._rows), 0)
        lines, err = dlg._validate_lines()
        self.assertIsNone(lines)
        self.assertIn("سطرًا واحدًا", err)

    def test_header_validation_before_posting(self):
        dlg = self.dialog()
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIn("سطرًا واحدًا", dlg.notice.text())
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_date.setText("2026-13-45")
        dlg.post()
        self.assertIn("لم تُرحَّل", dlg.notice.text())
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.invoices(), 0)

    def test_supplier_invoice_no_is_optional(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("   ")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.invoices(), 1)
        stored = self.con.execute("SELECT supplier_invoice_no FROM purchase_invoices WHERE id=?",
                                  (dlg.posted.id,)).fetchone()[0]
        self.assertTrue(stored.startswith("AUTO-"))

    def test_engine_rejection_is_shown_and_keeps_input(self):
        first = self.dialog()
        self.add_row(first, self.battery, 1, "10")
        first.inv_no.setText("S-1")
        first.post()
        self.assertEqual(self.invoices(), 1)
        second = self.dialog()
        self.add_row(second, self.battery, 3, "20")
        second.inv_no.setText("s-1")
        second.post()
        self.assertIsNone(second.posted)
        self.assertIn("نفس رقم فاتورة المورد", second.notice.text())
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(len(second._rows), 1)
        self.assertTrue(second.post_btn.isEnabled())
        second.inv_no.setText("S-2")
        second.post()
        self.assertIsNotNone(second.posted)
        self.assertEqual(self.invoices(), 2)

    def test_double_post_does_not_duplicate(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 2, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        first_id = dlg.posted.id
        dlg.post()
        self.assertEqual(dlg.posted.id, first_id)
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_lots").fetchone()[0], 1)

    def test_remove_line_updates_total(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "10")
        row2 = self.add_row(dlg, self.battery, 2, "20")
        self.assertEqual(len(dlg._rows), 2)
        dlg._remove_row(row2)
        self.assertEqual(len(dlg._rows), 1)
        self.assertEqual(dlg._sub_total(), 1 * 1000)

    def test_no_active_suppliers_warns_and_blocks(self):
        self.con.execute("UPDATE suppliers SET active = 0")
        stack = self.page()
        stack.open_form()
        dlg = stack.form_page
        # No supplier selected (all disabled), verify warning
        self.assertIn("لا يوجد موردون", dlg.notice.text())
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIn("اختر المورد", dlg.notice.text())
        self.assertEqual(self.invoices(), 0)

    def test_engine_permission_enforced_even_if_ui_bypassed(self):
        dlg = self.dialog(self.cashier_session)
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIn("صلاحية", dlg.notice.text())
        self.assertEqual(self.invoices(), 0)

    # ---------- Full-page navigation (no modal) ----------
    def test_add_opens_full_page_and_post_returns_to_list(self):
        stack = self.page()
        self.assertIs(stack.currentWidget(), stack.list_page)
        stack.list_page.open_dialog(None)
        self.assertIs(stack.currentWidget(), stack.form_page)
        f = stack.form_page
        f._pick_supplier(self.sup, "مورد وهمي", "01000000001")
        self.add_row(f, self.battery, 1, "10")
        f.inv_no.setText("S-1")
        f.post()
        self.assertIsNotNone(stack.form_page.posted)
        self.assertIs(stack.currentWidget(), stack.list_page)
        self.assertEqual(stack.list_page.model.rowCount(), 1)

    # ---------- Page ----------
    def test_page_lists_and_previews_invoice(self):
        dlg = self.dialog()
        self.add_row(dlg, self.tire, 4, "1000", "2324")
        dlg.inv_no.setText("S-77")
        dlg.post()
        stack = self.page()
        page = stack.list_page
        self.assertEqual(page.model.rowCount(), 1)
        self.assertTrue(page.add_btn.isEnabled())
        self.assertIn("1 فاتورة", page.count_label.text())
        self.assertFalse(page.preview.isVisibleTo(page))
        page.show_preview(page.model.rows[0])
        self.assertTrue(page.preview.isVisibleTo(page))
        page.open_dialog(page.model.rows[0][0])      # opening an existing row previews it and opens no edit form
        self.assertTrue(page.preview.isVisibleTo(page))
        self.assertIs(stack.currentWidget(), page)   # previewing an existing invoice never leaves the list
        page.search.setText("S-77")
        page.reload()
        self.assertEqual(page.model.rowCount(), 1)
        page.search.setText("غير موجود")
        page.reload()
        self.assertEqual(page.model.rowCount(), 0)

    def test_cashier_cannot_post_or_view(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        stack = self.page(self.cashier_session)
        page = stack.list_page
        self.assertFalse(page.add_btn.isEnabled())
        self.assertEqual(page.model.rowCount(), 0)
        self.assertIn("لا تملك صلاحية", page.count_label.text())
        page.open_dialog(None)       # opens no form; one would hang the test if it tried exec

    def test_new_supplier_dialog_saves_and_is_picked(self):
        from morv.ui.purchases_page import _NewSupplierDialog
        dlg = self.dialog()
        new_dlg = self.destroy_on_cleanup(
            _NewSupplierDialog(name_hint="مورد ثاني", mobile_hint="", session=self.session, parent=dlg))
        new_dlg.mobile_edit.setText("01099998888")
        new_dlg._save()
        self.assertIsNotNone(new_dlg.created)
        self.assertEqual(new_dlg.created.name, "مورد ثاني")
        dlg._pick_supplier(new_dlg.created.id, new_dlg.created.name, new_dlg.created.mobile)
        self.assertEqual(dlg._supplier_id, new_dlg.created.id)

    def test_cost_note_compares_against_last_purchase(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        second = self.dialog()
        row = self.add_row(second, self.battery, 1, "11")   # 10% higher than last cost
        self.assertIn("أعلى من آخر شراء", row["cost_note"].text())
        row["cost_edit"].setText("9")   # 10% lower than last cost
        self.assertIn("أقل من آخر شراء", row["cost_note"].text())

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
