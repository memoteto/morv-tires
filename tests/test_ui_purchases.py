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

    def _draft_back_button(self, form):
        from PySide6.QtWidgets import QPushButton
        return next(
            b for b in form.findChildren(QPushButton)
            if b.text() == "رجوع للقائمة"
        )

    def _draft_db_state(self):
        tables = (
            "purchase_invoices", "stock_lots",
            "stock_movements", "cost_layers",
            "journal_entries", "journal_lines",
        )
        return {
            t: self.con.execute(
                f"SELECT * FROM {t} ORDER BY rowid"
            ).fetchall()
            for t in tables
        }

    def test_draft_back_declined(self):
        from unittest.mock import patch

        f = self.dialog()
        self.add_row(f, self.tire, 4, "1000", "2324")
        f.inv_no.setText("QA-100")
        f.notes.setText("ملاحظة تجريبية")

        before = self._draft_db_state()
        key = f._request_key

        with patch("morv.ui.modal.ask", return_value=False) as confirm:
            self._draft_back_button(f).click()

        self.assertIs(f.stack.currentWidget(), f)
        self.assertEqual(f._request_key, key)
        self.assertEqual(f._supplier_id, self.sup)
        self.assertEqual(f.inv_no.text(), "QA-100")
        self.assertEqual(f.notes.text(), "ملاحظة تجريبية")
        self.assertEqual(len(f._rows), 1)
        self.assertEqual(f._rows[0]["qty_spin"].value(), 4)
        self.assertEqual(f._rows[0]["cost_edit"].text(), "1000")
        self.assertEqual(f._rows[0]["dot_edit"].text(), "2324")
        self.assertEqual(self._draft_db_state(), before)
        confirm.assert_called_once()

    def test_draft_back_confirmed(self):
        from unittest.mock import patch

        f = self.dialog()
        self.add_row(f, self.battery, 2, "500")
        f.inv_no.setText("QA-200")
        stack = f.stack
        before = self._draft_db_state()

        with patch("morv.ui.modal.ask", return_value=True) as confirm:
            self._draft_back_button(f).click()
            self.assertIs(stack.currentWidget(), stack.list_page)
            stack.open_form()

        self.assertEqual(stack.form_page._rows, [])
        self.assertEqual(stack.form_page.inv_no.text(), "")
        self.assertIsNone(stack.form_page.posted)
        self.assertEqual(self._draft_db_state(), before)
        confirm.assert_called_once()

    def test_draft_empty_back_without_confirmation(self):
        from unittest.mock import patch

        stack = self.page()
        stack.open_form()
        f = stack.form_page

        with patch("morv.ui.modal.ask", return_value=False) as confirm:
            self._draft_back_button(f).click()

        self.assertIs(stack.currentWidget(), stack.list_page)
        confirm.assert_not_called()

    def test_draft_replacement_declined(self):
        from unittest.mock import patch

        f = self.dialog()
        self.add_row(f, self.battery, 3, "500")
        stack = f.stack
        before = self._draft_db_state()
        key = f._request_key

        with patch("morv.ui.modal.ask", return_value=False) as confirm:
            stack.open_form()

        self.assertIs(stack.form_page, f)
        self.assertIs(stack.currentWidget(), f)
        self.assertEqual(f._request_key, key)
        self.assertEqual(len(f._rows), 1)
        self.assertEqual(self._draft_db_state(), before)
        confirm.assert_called_once()

    def test_draft_notes_only_are_protected(self):
        from unittest.mock import patch

        stack = self.page()
        stack.open_form()
        f = stack.form_page
        f.notes.setText("ملاحظة تجريبية")

        with patch("morv.ui.modal.ask", return_value=False) as confirm:
            self._draft_back_button(f).click()

        self.assertIs(stack.currentWidget(), f)
        self.assertEqual(f.notes.text(), "ملاحظة تجريبية")
        confirm.assert_called_once()

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

    def test_new_item_dialog_saves_and_is_added_as_a_row(self):
        from morv.ui.inventory_page import ItemDialog
        dlg = self.dialog()
        new_dlg = self.destroy_on_cleanup(ItemDialog(self.session, None, parent=dlg))
        new_dlg.set_kind("service")        # service hides brand/model, so only name/code/price are needed
        new_dlg.widgets["name"].setText("خدمة سريعة وهمية")
        new_dlg.widgets["code"].setText("SVC-TEST-1")
        new_dlg.widgets["price_retail"].setText("100")
        new_dlg.save()
        self.assertIsNotNone(new_dlg.saved)
        self.assertEqual(new_dlg.saved.name, "خدمة سريعة وهمية")
        dlg._add_row(new_dlg.saved.id, new_dlg.saved.kind, new_dlg.saved.name)
        self.assertEqual(dlg._rows[-1]["item_id"], new_dlg.saved.id)
        self.assertEqual(dlg._rows[-1]["name"], "خدمة سريعة وهمية")

    def test_stepper_buttons_have_their_own_compact_style(self):
        # Regression: a plain QPushButton inherits 14px of horizontal padding
        # from the base QSS rule, which left no room for the glyph inside a
        # narrow fixed-width button and made it look blank.
        from PySide6.QtWidgets import QPushButton
        dlg = self.dialog()
        row = self.add_row(dlg, self.battery, 1, "10")
        row_w = row["widget"]
        # The amount/percent toggle buttons share the compact stepperBtn style too, so this
        # regression (the −/+ glyph buttons) filters to the quantity steppers by their glyph.
        steppers = [b for b in row_w.findChildren(QPushButton)
                    if b.objectName() == "stepperBtn" and b.text() in ("−", "+")]
        self.assertEqual(len(steppers), 2)

    def test_cost_note_hidden_until_there_is_something_to_say(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "10")
        dlg.inv_no.setText("S-1")
        dlg.post()
        second = self.dialog()
        row2 = self.add_row(second, self.battery, 1, "")
        self.assertFalse(row2["cost_note"].isVisibleTo(second))
        row2["cost_edit"].setText("11")
        self.assertTrue(row2["cost_note"].isVisibleTo(second))
        self.assertTrue(row2["cost_note"].text())

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

    def _configure_supplier_vat(self, status, inclusive=False):
        from morv.infra import settings, suppliers
        supplier = suppliers.get(self.con, self.sup)
        supplier.vat_status = status
        supplier.vat_prices_inclusive = inclusive
        suppliers.update(self.con, self.admin, supplier)
        settings.put(self.con, "tax.enabled", "1")

    def test_supplier_without_vat_calculates_and_posts_zero_tax(self):
        from morv.infra import ledger
        self._configure_supplier_vat("unregistered")
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "5000")
        self.assertEqual(dlg._vat_mode, "none")
        self.assertIn("0.00", dlg.tax.text())
        self.assertIn("5,000.00", dlg._total_lbl.text())
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(dlg.posted.total, 500000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 500000)

    def _confirm_vat(self, dlg, eligible=True):
        from unittest.mock import patch

        from PySide6.QtWidgets import QDialog
        from morv.ui.purchase_vat import PurchaseVatDialog

        def confirm(modal):
            for _, deduction in modal.editors:
                deduction.setCurrentIndex(deduction.findData(eligible))
            modal.save()
            self.assertIsNotNone(modal.result_values)
            return QDialog.DialogCode.Accepted

        with patch.object(PurchaseVatDialog, "exec", confirm):
            dlg._edit_vat_details()

    def test_exclusive_supplier_tax_preview_and_posting_match(self):
        from PySide6.QtWidgets import QLabel
        from morv.infra import inventory, ledger, purchase_view, suppliers
        self._configure_supplier_vat("registered")
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "5000")
        self.assertIsInstance(dlg.tax, QLabel)
        self.assertIn("700.00", dlg.tax.text())
        self.assertIn("5,700.00", dlg._total_lbl.text())
        dlg.post()
        self.assertIsNone(dlg.posted)
        self.assertIn("قابلية خصم", dlg.notice.text())
        self._confirm_vat(dlg)
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(dlg.posted.total, 570000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 70000)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 500000)
        self.assertEqual(ledger.account_balance(self.con, "2110"), -570000)
        self.assertEqual(inventory.reconcile(self.con), [])
        supplier = suppliers.get(self.con, self.sup)
        supplier.vat_status = "unregistered"
        suppliers.update(self.con, self.admin, supplier)
        historical = purchase_view.get_invoice(self.con, dlg.posted.id)
        self.assertEqual((historical.total, historical.tax_total, historical.prices_tax_inclusive),
                         (570000, 70000, False))

    def test_inclusive_supplier_tax_extracted_not_added(self):
        from morv.infra import ledger
        self._configure_supplier_vat("registered", True)
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "5000")
        self.assertIn("614.04", dlg.tax.text())
        self.assertIn("5,000.00", dlg._total_lbl.text())
        self._confirm_vat(dlg)
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(dlg.posted.total, 500000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 61404)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 438596)

    def test_unknown_supplier_never_assumed_to_have_zero_tax(self):
        self._configure_supplier_vat("unknown")
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 1, "5000")
        self.assertEqual(dlg.tax.text(), "—")
        self.assertEqual(dlg._total_lbl.text(), "—")
        dlg.post()
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.invoices(), 0)
        self.assertIn("حدد حالة ضريبة المورد", dlg.notice.text())

    def test_supplier_switch_resets_tax_exceptions(self):
        from morv.infra import suppliers
        self._configure_supplier_vat("registered")
        dlg = self.dialog()
        row = self.add_row(dlg, self.battery, 1, "5000")
        self._confirm_vat(dlg, False)
        self.assertEqual(row["vat_override"], (1400, False))
        other = suppliers.create(self.con, self.admin, suppliers.Supplier(
            None, "مورد آخر", "01000000002", vat_status="unregistered"))
        dlg._pick_supplier(other.id, other.name, other.mobile)
        self.assertNotIn("vat_override", row)
        self.assertIn("0.00", dlg.tax.text())
        self.assertIn("5,000.00", dlg._total_lbl.text())

    def test_supplier_folded_details_restore_compact_height(self):
        """A supplier opened expanded must shrink to the same size as a fresh folded form."""
        from PySide6.QtTest import QTest
        from morv.infra import suppliers
        from morv.ui import theme
        from morv.ui.suppliers_page import SupplierDialog

        old = (self.app.palette(), self.app.styleSheet(), self.app.font(), self.app.layoutDirection())
        self.addCleanup(self.app.setPalette, old[0])
        self.addCleanup(self.app.setStyleSheet, old[1])
        self.addCleanup(self.app.setFont, old[2])
        self.addCleanup(self.app.setLayoutDirection, old[3])
        theme.apply(self.app, False)
        supplier = suppliers.get(self.con, self.sup)
        supplier.vat_status = "registered"

        def settle():
            self.app.processEvents()
            QTest.qWait(100)
            self.app.processEvents()

        def measure(dlg):
            return dict(height=dlg.height(), width=dlg.width(),
                        minimum_scroll=dlg.scroll.minimumHeight(),
                        body_hint=dlg.scroll.widget().sizeHint().height(),
                        viewport=dlg.scroll.viewport().height(),
                        gaps=tuple(b.geometry().top() - a.geometry().bottom() - 1
                                   for a, b in ((dlg.name, dlg.mobile), (dlg.mobile, dlg.address),
                                                (dlg.address, dlg.notes))),
                        footer_visible=dlg.footer.isVisibleTo(dlg))

        compact = self.destroy_on_cleanup(SupplierDialog(self.session, supplier))
        compact.more.setChecked(False)
        compact.show()
        settle()
        baseline = measure(compact)
        compact.hide()

        dlg = self.destroy_on_cleanup(SupplierDialog(self.session, supplier))
        self.assertTrue(dlg.more.isChecked())
        dlg.show()
        settle()
        expanded = measure(dlg)
        self.assertGreater(expanded["body_hint"], baseline["body_hint"], (baseline, expanded))
        self.assertGreater(expanded["height"], baseline["height"], (baseline, expanded))
        for _ in range(2):
            dlg.more.setChecked(False)
            settle()
            folded = measure(dlg)
            self.assertTrue(dlg.more_box.isHidden())
            self.assertTrue(folded["footer_visible"])
            self.assertEqual(folded["width"], baseline["width"])
            self.assertLessEqual(abs(folded["height"] - baseline["height"]), 4,
                                 f"SUPPLIER_FOLD_HEIGHT baseline={baseline} expanded={expanded} folded={folded}")
            self.assertTrue(all(abs(x - y) <= 4 for x, y in zip(folded["gaps"], baseline["gaps"], strict=True)),
                            f"SUPPLIER_FOLD_GAPS baseline={baseline} folded={folded}")
            dlg.more.setChecked(True)
            settle()
            reopened = measure(dlg)
            self.assertLessEqual(abs(reopened["height"] - expanded["height"]), 4,
                                 f"SUPPLIER_REOPEN_HEIGHT expanded={expanded} reopened={reopened}")
            self.assertTrue(reopened["footer_visible"])

    def test_supplier_vat_rows_match_existing_form(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFormLayout
        from morv.infra import suppliers
        from morv.ui import theme
        from morv.ui.suppliers_page import SupplierDialog
        from tests.test_ui_invoice_summary_polish import contrast

        old = (self.app.palette(), self.app.styleSheet(), self.app.font(), self.app.layoutDirection())
        self.addCleanup(self.app.setPalette, old[0])
        self.addCleanup(self.app.setStyleSheet, old[1])
        self.addCleanup(self.app.setFont, old[2])
        self.addCleanup(self.app.setLayoutDirection, old[3])
        theme.apply(self.app, False)
        dlg = self.destroy_on_cleanup(SupplierDialog(self.session, suppliers.get(self.con, self.sup)))
        dlg.more.setChecked(True)
        dlg.show()
        self.app.processEvents()
        layout = dlg.more_box.layout()
        self.assertIsInstance(layout, QFormLayout)
        vat = dlg.vat_fields
        old_label = layout.labelForField(dlg.tax)
        status_label = layout.labelForField(vat.status)
        price_label = layout.labelForField(vat.prices)
        self.assertEqual(status_label.text(), "القيمة المضافة")
        self.assertEqual(price_label.text(), "أساس السعر")
        self.assertTrue(vat.prices.isHidden())
        self.assertTrue(price_label.isHidden())
        vat.status.setCurrentIndex(vat.status.findData("registered"))
        self.app.processEvents()
        layout.activate()
        self.assertFalse(vat.prices.isHidden())
        self.assertFalse(price_label.isHidden())
        self.assertEqual(dlg.layoutDirection(), Qt.LayoutDirection.RightToLeft)
        for label, editor in ((status_label, vat.status), (price_label, vat.prices)):
            self.assertEqual(label.objectName(), old_label.objectName())
            self.assertEqual(label.font(), old_label.font())
            self.assertEqual(editor.geometry().x(), dlg.tax.geometry().x())
            self.assertEqual(editor.width(), dlg.tax.width())
            self.assertGreater(label.geometry().left(), editor.geometry().right())
            self.assertEqual(label.palette().color(label.foregroundRole()),
                             old_label.palette().color(old_label.foregroundRole()))
            image = label.grab().toImage()
            bg = image.pixelColor(0, 0)
            self.assertGreaterEqual(contrast(label.palette().color(label.foregroundRole()), bg), 4.5)
        self.assertEqual(vat.prices.itemText(0), "غير شامل الضريبة")
        self.assertEqual(vat.prices.itemText(1), "شامل الضريبة")
        vat.status.setCurrentIndex(vat.status.findData("unregistered"))
        self.assertTrue(vat.prices.isHidden())
        self.assertTrue(price_label.isHidden())

    def test_full_and_quick_supplier_vat_defaults_are_saved(self):
        from morv.infra import suppliers
        from morv.ui.purchases_page import _NewSupplierDialog
        from morv.ui.suppliers_page import SupplierDialog

        full = self.destroy_on_cleanup(SupplierDialog(self.session, suppliers.get(self.con, self.sup)))
        full.vat_fields.status.setCurrentIndex(full.vat_fields.status.findData("registered"))
        full.vat_fields.prices.setCurrentIndex(full.vat_fields.prices.findData(True))
        full.save()
        self.assertIsNotNone(full.saved)
        self.assertEqual(suppliers.get(self.con, self.sup).vat_mode, "inclusive")
        quick = self.destroy_on_cleanup(_NewSupplierDialog("مورد سريع", "01000000002", self.session))
        quick.vat_fields.status.setCurrentIndex(quick.vat_fields.status.findData("unregistered"))
        quick.save()
        self.assertIsNotNone(quick.created)
        self.assertEqual(quick.created.vat_mode, "none")

    def test_discount_and_shipping_enabled_tax_and_payment_still_disabled(self):
        # P01/P02 enable discount and shipping; tax waits for P04 and the payment
        # fields are a separate future feature, so both stay disabled.
        dlg = self.dialog()
        for enabled in (dlg.disc_edit, dlg.ship, dlg.freight_basis):
            self.assertTrue(enabled.isEnabled())
        for disabled in (dlg.tax, dlg.paid, dlg.pay_method):
            self.assertFalse(disabled.isEnabled())

    def test_still_disabled_fields_never_affect_the_shown_or_posted_total(self):
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 2, "100")
        # Force values into the fields that remain disabled (as a stray future edit
        # might) to prove the summary and the posted total ignore them completely.
        dlg.tax.setText("20")
        dlg.paid.setText("999")
        dlg._refresh_summary()
        self.assertIn("200.00", dlg._total_lbl.text())
        self.assertIn("200.00", dlg._rem_lbl.text())
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(dlg.posted.total, 20000)
        stored_total = self.con.execute(
            "SELECT total FROM purchase_invoices WHERE id=?", (dlg.posted.id,)).fetchone()[0]
        self.assertEqual(stored_total, 20000)
        inv_debit = self.con.execute(
            "SELECT sum(debit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id "
            "WHERE je.source_type='purchase_invoice' AND je.source_id=? AND jl.account_code='1410'",
            (dlg.posted.id,)).fetchone()[0]
        sup_credit = self.con.execute(
            "SELECT sum(credit) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id "
            "WHERE je.source_type='purchase_invoice' AND je.source_id=? AND jl.account_code='2110'",
            (dlg.posted.id,)).fetchone()[0]
        self.assertEqual(inv_debit, 20000)
        self.assertEqual(sup_credit, 20000)

    def test_discount_and_freight_flow_through_the_form_to_the_engine(self):
        # End-to-end UI wiring: a discount and shipping entered in the form reach
        # purchasing.post_invoice and land in net_total, not just the engine tests.
        dlg = self.dialog()
        self.add_row(dlg, self.battery, 2, "100", "")
        dlg.disc_edit.setText("50")          # 5000 piasters off the 20000 gross
        dlg.ship.setText("30")               # 3000 piasters freight, default qty basis
        dlg.inv_no.setText("S-1")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(dlg.posted.total, 18000)      # 20000 - 5000 + 3000
        net = self.con.execute(
            "SELECT net_total FROM purchase_lines WHERE invoice_id=?", (dlg.posted.id,)).fetchone()[0]
        self.assertEqual(net, 18000)
        freight, discount = self.con.execute(
            "SELECT freight_amount, invoice_discount FROM purchase_invoices WHERE id=?",
            (dlg.posted.id,)).fetchone()
        self.assertEqual((freight, discount), (3000, 5000))

    def test_per_line_allocation_preview_matches_posting_in_all_freight_modes(self):
        from PySide6.QtWidgets import QLabel

        from morv.core.money import fmt
        from morv.infra import inventory

        dlg = self.dialog()
        self.add_row(dlg, self.battery, 2, "100", "")
        self.add_row(dlg, self.tire, 1, "200", "2324")
        a, b = dlg._rows
        a["disc_edit"].setText("50")
        dlg.disc_edit.setText("35")
        dlg.ship.setText("30")
        # After line discount: 150 and 200; invoice discount: 15 and 20.
        for basis, shares, totals in (
            ("qty", (2000, 1000), (15500, 19000)),
            ("value", (1286, 1714), (14786, 19714)),
            ("manual", (1000, 2000), (14500, 20000)),
        ):
            dlg.freight_basis.setCurrentIndex(dlg.freight_basis.findData(basis))
            if basis == "manual":
                a["freight_edit"].setText("10")
                b["freight_edit"].setText("20")
            for row, share, total in zip((a, b), shares, totals, strict=True):
                self.assertIn(fmt(share), row["freight_value"].text())
                self.assertIn(fmt(total), row["total_lbl"].text())
                tooltip = row["total_lbl"].toolTip()
                self.assertIn("حصة خصم الفاتورة", tooltip)
                self.assertIn("حصة الشحن", tooltip)
                self.assertIn(fmt(share), tooltip)
                self.assertIn("متوسط تكلفة الوحدة", tooltip)
                self.assertFalse(any("متوسط تكلفة الوحدة" in label.text()
                                     for label in row["widget"].findChildren(QLabel)))
            self.assertIn("345.00", dlg._total_lbl.text())
        self.assertFalse(any("الضريبة غير مفعّلة بعد" in label.text()
                             for label in dlg.findChildren(QLabel)))
        b["freight_edit"].setText("19")
        self.assertEqual(dlg._total_lbl.text(), "—")
        self.assertEqual(a["total_lbl"].toolTip(), "")
        self.assertEqual(a["total_lbl"].text(), "—")
        b["freight_edit"].setText("20")
        self.assertIn("345.00", dlg._total_lbl.text())
        dlg.inv_no.setText("PREVIEW-1")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(dlg.posted.total, 34500)
        rows = self.con.execute(
            "SELECT line_discount, invoice_share, freight_share, net_total FROM purchase_lines "
            "WHERE invoice_id=? ORDER BY line_no", (dlg.posted.id,)).fetchall()
        self.assertEqual(rows, [(5000, 1500, 1000, 14500), (0, 2000, 2000, 20000)])
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_shell_uses_real_purchases_page(self):
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.shell import MainWindow
        w = self.destroy_on_cleanup(MainWindow(self.session, self.app))
        w.health_timer.stop()
        # Page is built on first navigation (lazy).
        w.go_key("purchases")
        self.assertIsInstance(w.pages["purchases"], PurchasesPage)
        self.assertEqual(w.title.text(), "المشتريات")


if __name__ == "__main__":
    unittest.main()
