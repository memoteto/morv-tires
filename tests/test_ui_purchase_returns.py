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
        dlg.qty.setText("2")
        dlg.reason.setText("عيب تصنيع")
        dlg.save()  # the first action prepares a visible preview, not a blind posting
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
        dlg.lot.setCurrentIndex(next(i for i in range(dlg.lot.count()) if dlg.lot.itemData(i)[0] == line_id))
        dlg.qty.setText("2")
        dlg.reason.setText("عيب تصنيع")
        dlg.add_line()
        self.assertEqual(dlg.book_total, 184000)
        self.assertIn("1,840.00", dlg.list_label.text())
        dlg.save()
        self.assertIsNone(dlg.saved)  # a settlement method must be chosen explicitly
        dlg.settlement_mode.setCurrentIndex(dlg.settlement_mode.findData("manual"))
        dlg.settlement_edit.setText("1800")
        self.assertIn("1,800.00", dlg.settlement_label.text())
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
