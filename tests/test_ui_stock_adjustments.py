"""Stock adjustment dialogs: review before approval, cost rules shown, history with reversal."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class StockAdjustmentDialogsUiTest(unittest.TestCase):
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
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        sup = self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) "
                               "VALUES ('مورد وهمي','مورد وهمي','01000000001') RETURNING id").fetchone()[0]
        self.tire = self.item("TR-1")
        self.fresh = self.item("TR-2")
        purchasing.post_invoice(self.con, self.admin, request_key="ui-buy-0001", supplier_id=sup,
                                supplier_invoice_no="S-1", invoice_date="2026-11-01",
                                lines=[PurchaseLine(self.tire, 4, 100000, "2324")])

    def item(self, code):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire',?,?,?,100,100,100) RETURNING id", (code, code, code.lower())).fetchone()[0]

    def dialog(self, item_id):
        from morv.ui.stock_adjustment_dialogs import StockAdjustmentDialog
        from morv.ui.widgets import dispose
        dlg = StockAdjustmentDialog(self.session, item_id)
        self.addCleanup(dispose, dlg, self.app)
        return dlg

    def test_posting_needs_a_review_first(self):
        dlg = self.dialog(self.tire)
        dlg.reason.setText("جرد شهري")
        dlg.add_line()
        self.assertFalse(dlg.post_btn.isEnabled())
        dlg.save()
        self.assertIsNone(dlg.saved)
        dlg.review()
        self.assertTrue(dlg.post_btn.isEnabled())
        self.assertIn("5270", dlg.summary.text())
        dlg.save()
        self.assertEqual(dlg.saved.loss_total, 100000)

    def test_a_gain_without_a_cost_reference_asks_for_a_manual_cost(self):
        dlg = self.dialog(self.fresh)
        dlg.direction.setCurrentIndex(dlg.direction.findData("gain"))
        dlg.reason.setText("زيادة وُجدت")
        dlg.review()
        self.assertFalse(dlg.post_btn.isEnabled())
        self.assertIn("تكلفة", dlg.notice.text())
        dlg2 = self.dialog(self.fresh)
        dlg2.direction.setCurrentIndex(dlg2.direction.findData("gain"))
        dlg2.cost.setText("500.00")
        dlg2.reason.setText("زيادة وُجدت")
        dlg2.review()
        self.assertTrue(dlg2.post_btn.isEnabled())
        self.assertIn("4220", dlg2.summary.text())
        dlg2.save()
        self.assertEqual(dlg2.saved.gain_total, 50000)

    def test_history_lists_the_adjustment_and_reverses_it(self):
        from morv.infra import stock_adjustments
        from morv.infra.stock_adjustments import AdjustLine
        from morv.ui.stock_adjustment_dialogs import AdjustmentsHistoryDialog, ReverseAdjustmentDialog
        from morv.ui.widgets import dispose
        r = stock_adjustments.post_adjustment(self.con, self.admin, request_key="adj-ui-0001", adjust_date=None,
                                              reason="جرد", lines=[AdjustLine(self.tire, "loss", 1)])
        hist = AdjustmentsHistoryDialog(self.session)
        self.addCleanup(dispose, hist, self.app)
        self.assertEqual(hist.docs.count(), 1)
        self.assertTrue(hist.reverse_btn.isEnabled())
        rev = ReverseAdjustmentDialog(self.session, r.id)
        self.addCleanup(dispose, rev, self.app)
        rev.reason.setText("خطأ في العد")
        rev.save()
        self.assertIsNotNone(rev.saved)
        hist._fill()
        self.assertEqual(hist.docs.count(), 2)
        self.assertFalse(hist.reverse_btn.isEnabled())
