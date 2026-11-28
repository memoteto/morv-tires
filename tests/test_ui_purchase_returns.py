"""Supplier return dialogs: post a return and list it with a reversal offered."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class PurchaseReturnDialogsUiTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import purchasing, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, admin)
        self.app = get_app()
        self.sup = self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) "
                                    "VALUES ('مورد وهمي','مورد وهمي','01000000001') RETURNING id").fetchone()[0]
        item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-1','إطار','اطار',100,100,100) RETURNING id").fetchone()[0]
        purchasing.post_invoice(self.con, admin, request_key="ui-buy-0001", supplier_id=self.sup,
                                supplier_invoice_no="S-1", invoice_date="2026-11-01",
                                lines=[PurchaseLine(item, 4, 100000, "2324")])

    def test_dialog_posts_and_the_list_offers_a_reversal(self):
        from morv.ui.purchase_return_dialogs import PurchaseReturnDialog, SupplierReturnsDialog
        from morv.ui.widgets import dispose
        dlg = PurchaseReturnDialog(self.session, self.sup, "مورد وهمي")
        self.addCleanup(dispose, dlg, self.app)
        dlg.reason.setText("عيب تصنيع")
        self.assertTrue(dlg.model.setData(dlg.model.index(0, dlg.model.QTY), 2))
        dlg.save()  # an explicit settlement choice is still required
        self.assertIsNone(dlg.saved)
        self.assertEqual(dlg.book_total, 200000)
        dlg.settlement_mode.setCurrentIndex(dlg.settlement_mode.findData("book"))
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(dlg.saved.total, 200000)
        docs = SupplierReturnsDialog(self.session, self.sup, "مورد وهمي")
        self.addCleanup(dispose, docs, self.app)
        self.assertEqual(docs.docs.count(), 1)
        self.assertTrue(docs.reverse_btn.isEnabled())

    def test_adjusted_cost_preview_and_explicit_supplier_settlement(self):
        from morv.infra import inventory, ledger, purchasing
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.purchase_return_dialogs import PurchaseReturnDialog
        from morv.ui.widgets import dispose

        item = self.con.execute("SELECT id FROM items WHERE code='TR-1'").fetchone()[0]
        inv = purchasing.post_invoice(
            self.con, self.session.user, request_key="ui-buy-0002", supplier_id=self.sup,
            supplier_invoice_no="S-2", invoice_date="2026-11-01",
            lines=[PurchaseLine(item, 4, 100000, "2324", 40000)], freight_amount=8000)
        line_id = self.con.execute("SELECT id FROM purchase_lines WHERE invoice_id=?", (inv.id,)).fetchone()[0]
        dlg = PurchaseReturnDialog(self.session, self.sup, "مورد وهمي")
        self.addCleanup(dispose, dlg, self.app)
        row = next(i for i, r in enumerate(dlg.model.rows) if r.purchase_line_id == line_id)
        dlg.reason.setText("عيب تصنيع")
        self.assertTrue(dlg.model.setData(dlg.model.index(row, dlg.model.QTY), 2))
        self.assertEqual(dlg.book_total, 184000)
        self.assertIn("1,840.00", dlg.book_label.text())
        dlg.save()
        self.assertIsNone(dlg.saved)  # a settlement method must be chosen explicitly
        dlg.settlement_mode.setCurrentIndex(dlg.settlement_mode.findData("manual"))
        dlg.settlement_edit.setText("1800")
        self.assertIn("1,800.00", dlg.total_label.text())
        dlg.save()
        self.assertIsNone(dlg.saved)  # differing settlement requires a reason
        dlg.settlement_reason.setText("شحن غير مسترد")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(dlg.saved.total, 184000)
        self.assertEqual(self.con.execute("SELECT settlement_amount FROM purchase_returns WHERE id=?",
                                          (dlg.saved.id,)).fetchone()[0], 180000)
        self.assertEqual(ledger.account_balance(self.con, "5160"), 4000)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_a_supplier_without_stock_gets_a_notice_and_nothing_posts(self):
        from morv.ui.purchase_return_dialogs import PurchaseReturnDialog
        from morv.ui.widgets import dispose
        other = self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) "
                                 "VALUES ('مورد ثانٍ','مورد ثان','01000000002') RETURNING id").fetchone()[0]
        dlg = PurchaseReturnDialog(self.session, other, "مورد ثانٍ")
        self.addCleanup(dispose, dlg, self.app)
        dlg.reason.setText("عيب تصنيع")
        dlg.save()
        self.assertIsNone(dlg.saved)


    def test_taxable_rows_use_original_quote_and_common_layout(self):
        from morv.infra import ledger, purchasing, settings
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.document_table import DocumentTableDialog
        from morv.ui.purchase_return_dialogs import PurchaseReturnDialog
        from morv.ui.widgets import dispose

        item = self.con.execute("SELECT id FROM items WHERE code='TR-1'").fetchone()[0]
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", "1400")
        inv = purchasing.post_invoice(
            self.con, self.session.user, request_key="ui-vat-buy-01", supplier_id=self.sup,
            supplier_invoice_no="VAT-1", invoice_date="2026-11-01",
            lines=[PurchaseLine(item, 2, 100000, "2324", input_tax_rate=1400, input_tax_deductible=True)])
        settings.put(self.con, "tax.enabled", "0")
        line_id = self.con.execute("SELECT id FROM purchase_lines WHERE invoice_id=?", (inv.id,)).fetchone()[0]
        dlg = PurchaseReturnDialog(self.session, self.sup, "مورد وهمي")
        self.addCleanup(dispose, dlg, self.app)
        self.assertIsInstance(dlg, DocumentTableDialog)
        self.assertIsNone(dlg.scroll)
        self.assertFalse(dlg.view.isColumnHidden(dlg.model.TAX))
        row = next(i for i, r in enumerate(dlg.model.rows) if r.purchase_line_id == line_id)
        index = dlg.model.index(row, dlg.model.QTY)
        self.assertFalse(dlg.model.setData(index, 3))
        self.assertTrue(dlg.model.setData(index, 1))
        self.assertTrue(dlg.model.setData(index, 0))
        self.assertIsNone(dlg.quote)
        self.assertTrue(dlg.model.setData(index, 2))
        dlg.reason.setText("عيب تصنيع")
        dlg.settlement_mode.setCurrentIndex(dlg.settlement_mode.findData("book"))
        self.assertEqual((dlg.quote.book_total, dlg.quote.tax_total, dlg.quote.default_settlement),
                         (200000, 28000, 228000))
        self.assertIn("280.00", dlg.tax_label.text())
        self.assertIn("2,280.00", dlg.total_label.text())
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)

    def test_manual_tax_requires_explicit_amount_reference_and_reason(self):
        from morv.infra import ledger, purchasing, settings
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.purchase_return_dialogs import PurchaseReturnDialog
        from morv.ui.widgets import dispose

        item = self.con.execute("SELECT id FROM items WHERE code='TR-1'").fetchone()[0]
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", "1400")
        inv = purchasing.post_invoice(
            self.con, self.session.user, request_key="ui-vat-buy-02", supplier_id=self.sup,
            supplier_invoice_no="VAT-2", invoice_date="2026-11-01",
            lines=[PurchaseLine(item, 1, 100000, "2324", input_tax_rate=1400, input_tax_deductible=True)])
        line_id = self.con.execute("SELECT id FROM purchase_lines WHERE invoice_id=?", (inv.id,)).fetchone()[0]
        dlg = PurchaseReturnDialog(self.session, self.sup, "مورد وهمي")
        self.addCleanup(dispose, dlg, self.app)
        row = next(i for i, r in enumerate(dlg.model.rows) if r.purchase_line_id == line_id)
        dlg.model.setData(dlg.model.index(row, dlg.model.QTY), 1)
        dlg.reason.setText("عيب تصنيع")
        dlg.settlement_mode.setCurrentIndex(dlg.settlement_mode.findData("manual"))
        dlg.settlement_edit.setText("1090")
        dlg.settlement_reason.setText("تسوية مختلفة")
        dlg.credit_note.setText("CN-1")
        self.assertFalse(dlg.post_btn.isEnabled())
        tax_index = dlg.model.index(row, dlg.model.TAX)
        dlg.model.setData(tax_index, "150")
        self.assertIsNone(dlg.quote)
        self.assertFalse(dlg.post_btn.isEnabled())
        dlg.model.setData(tax_index, "140")
        self.assertTrue(dlg.post_btn.isEnabled())
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(ledger.account_balance(self.con, "5160"), 5000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
