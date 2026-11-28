"""Offscreen return preview gates full installment rescission with recognized EIR."""
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from morv.core.sales_rules import Payment
from morv.infra import interest_recognition, ledger, sale_view, sales, users, installments
from morv.infra.db.bootstrap import open_main_db
from morv.infra.sales import SaleLineIn
from tests.qt_app import get_app, require_qt


@require_qt()
class InstallmentReturnEirUiTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()
        self.folder = Path(tempfile.mkdtemp(prefix="morv_eir_return_ui_"))
        self.con = open_main_db(self.folder / "ui.db")
        self.manager = users.create_user(self.con, None, "eir_return_ui", "مدير اختبار", "Admin12345", "manager")
        customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل اختبار','عميل اختبار','01000000057',0) RETURNING id"
        ).fetchone()[0]
        item = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','EIR-UI-SVC','خدمة وهمية','خدمه وهميه',600000,600000,600000) "
            "RETURNING id"
        ).fetchone()[0]
        self.sale = sales.post_sale(
            self.con, self.manager, request_key="eir-return-ui-sale",
            customer_id=customer, lines=[SaleLineIn(item, 2, 600000)],
            payments=[Payment("installment", 1200000)], sale_date="2025-07-10",
        )
        contract = installments.post_contract(
            self.con, self.manager, request_key="eir-return-ui-contract",
            sale_id=self.sale.id, down=200000, rate_bp=1500, months=10,
            first_due="2025-08-13", national_id="29001011201234",
            down_method="cash", contract_date="2025-07-13",
        )
        interest_recognition.recognise_month(
            self.con, self.manager, contract_id=contract.id, period="2025-07",
            entry_date="2025-08-01",
        )
        self.line_id = self.con.execute(
            "SELECT id FROM sale_lines WHERE sale_id=?", (self.sale.id,),
        ).fetchone()[0]
        from morv.ui.sale_return_dialog import SaleReturnDialog
        self.dialog = SaleReturnDialog(
            SimpleNamespace(con=self.con, user=self.manager),
            sale_view.get_sale(self.con, self.sale.id),
            sale_view.returnable_lines(self.con, self.sale.id),
            sale_view.refund_methods(self.con, self.sale.id),
        )
        self.dialog.return_date.setText("2025-09-02")

    def tearDown(self):
        from PySide6.QtCore import QEvent
        self.dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.con.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_partial_return_disables_post_and_explains_refusal(self):
        self.assertEqual(self.dialog.refund.currentData(), "installment")
        self.assertFalse(self.dialog.post_btn.isEnabled())
        self.assertTrue(self.dialog.model.setData(self.dialog.model.index(0, self.dialog.model.QTY), 1))
        self.assertFalse(self.dialog.post_btn.isEnabled())
        self.assertIn("جزئي", self.dialog.refund_breakdown.text())
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 0)

    def test_full_return_previews_paid_debt_and_recognized_interest_before_posting(self):
        self.dialog.reason.setCurrentText("عيب في الصنف")
        self.assertTrue(self.dialog.model.setData(self.dialog.model.index(0, self.dialog.model.QTY), 2))
        self.assertTrue(self.dialog.post_btn.isEnabled())
        self.assertIn("سيُرد للعميل", self.dialog.refund_breakdown.text())
        self.assertIn("تُلغى المديونية", self.dialog.refund_breakdown.text())
        self.assertIn("الفوائد المعترف بها سابقًا", self.dialog.refund_breakdown.text())
        self.dialog.post()
        self.assertIsNotNone(self.dialog.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 1)
        self.assertEqual(ledger.account_balance(self.con, "1320"), 0)
        self.assertEqual(ledger.account_balance(self.con, "2410"), 0)
        self.assertEqual(ledger.account_balance(self.con, "4140"), 0)


if __name__ == "__main__":
    unittest.main()
