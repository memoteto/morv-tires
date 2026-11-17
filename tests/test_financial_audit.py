"""Isolated financial-risk regression cases using synthetic SQLite data."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import installments as calc
from morv.core.sales_rules import Payment
from morv.infra import inventory, items, ledger, purchasing, sale_returns, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.items import ItemError
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnError, ReturnLineIn
from morv.infra.sales import SaleError, SaleLineIn
from tests.qt_app import get_app, require_qt

NID = "29001011201234"


class FinancialAuditFixture(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="morv_financial_audit_"))
        os.environ["MORV_DATA_DIR"] = str(self.temp)
        self.con = open_main_db(self.temp / "synthetic_only.db")
        self.admin = users.create_user(self.con, None, "auditadmin", "مدير وهمي", "Admin12345", "manager")
        self.customer = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, price_level, credit_limit) "
            "VALUES ('عميل وهمي', 'عميل وهمي', '01000000011', 'wholesale', 1000000)"
        ).lastrowid
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service', 'SV-AUDIT', 'خدمة وهمية', 'خدمه وهميه', 10000, 8000, 7000)"
        ).lastrowid
        self.tire = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire', 'TR-AUDIT', 'إطار وهمي', 'اطار وهمي', 10000, 9000, 8000)"
        ).lastrowid
        self.supplier = self.con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile) "
            "VALUES ('مورد وهمي', 'مورد وهمي', '01000000001')"
        ).lastrowid
        self.seq = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.temp, ignore_errors=True)

    def sell(self, item_id, amount, payments, day="2026-11-10"):
        self.seq += 1
        return sales.post_sale(
            self.con, self.admin, request_key=f"audit-sale-{self.seq:05d}",
            customer_id=self.customer, lines=[SaleLineIn(item_id, 1, amount)],
            payments=payments, sale_date=day,
        )

    def line_id(self, sale_id):
        return self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale_id,)).fetchone()[0]

    def return_sale(self, sale_id, refund_method=None):
        self.seq += 1
        return sale_returns.post_return(
            self.con, self.admin, request_key=f"audit-return-{self.seq:05d}",
            sale_id=sale_id, lines=[ReturnLineIn(self.line_id(sale_id), 1)],
            refund_method=refund_method, reason="اختبار مرتجع وهمي", return_date="2026-11-14",
        )


class FinancialAuditTests(FinancialAuditFixture):
    def test_installment_return_must_be_handled_as_domain_refusal(self):
        sale = self.sell(self.service, 100000, [Payment("installment", 100000)])
        with self.assertRaises(ReturnError):
            self.return_sale(sale.id)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 0)

    def test_full_mixed_refund_must_clear_customer_credit(self):
        sale = self.sell(
            self.service, 100000,
            [Payment("cash", 10000, received=10000), Payment("credit", 90000)],
        )
        self.return_sale(sale.id, refund_method="cash")
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(
            ledger.account_balance(self.con, "1310"), 0,
            "A fully returned sale must not leave this customer's original credit outstanding.",
        )

    def test_sold_service_kind_must_not_be_changed_into_stock(self):
        self.sell(self.service, 10000, [Payment("cash", 10000, received=10000)])
        item = items.get(self.con, self.service)
        item.kind = "battery"
        item.brand = "Demo"
        item.attrs = {
            "volt": "12V", "ah": "70", "cca": "640", "tech": "EFB",
            "box": "L3", "pol": "الموجب يمين", "dims": "278×175×190",
        }
        with self.assertRaises(ItemError):
            items.update(self.con, self.admin, item)

    def test_part_installment_payment_must_not_create_contractless_sale(self):
        with self.assertRaises(SaleError):
            self.sell(
                self.service, 100000,
                [Payment("installment", 90000), Payment("cash", 10000, received=10000)],
            )

    def test_contract_replay_must_preserve_displayed_monthly_amount(self):
        from morv.infra import installments

        sale = self.sell(self.service, 5, [Payment("installment", 5)])
        kwargs = dict(
            request_key="audit-contract-replay", sale_id=sale.id,
            down=0, rate_bp=4000, months=3, first_due="2026-12-13",
            national_id=NID, down_method=None, contract_date="2026-11-13",
        )
        original = installments.post_contract(self.con, self.admin, **kwargs)
        replayed = installments.post_contract(self.con, self.admin, **kwargs)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.monthly, original.monthly)

    def test_tiny_financed_amount_must_not_produce_zero_principal_rows(self):
        terms = calc.plan(5, 0, 0, 6, "2026-12-13")
        self.assertTrue(
            all(row.principal > 0 for row in terms.rows),
            "The SQLite installment_rows table refuses zero principal rows.",
        )

    def test_sale_before_stock_receipt_must_be_refused(self):
        purchasing.post_invoice(
            self.con, self.admin, request_key="audit-purchase-0001",
            supplier_id=self.supplier, supplier_invoice_no="AUDIT-1",
            invoice_date="2026-11-10", received_date="2026-11-10",
            lines=[PurchaseLine(self.tire, 1, 5000, "2324")],
        )
        with self.assertRaises(SaleError):
            self.sell(self.tire, 10000, [Payment("cash", 10000, received=10000)], day="2026-11-09")
        self.assertEqual(inventory.on_hand(self.con, self.tire), 1)


@require_qt()
class FinancialAuditUiTests(FinancialAuditFixture):
    def test_customer_price_tier_change_must_reprice_unedited_line(self):
        from PySide6.QtCore import QEvent
        from morv.ui.pos_page import PosPage
        from morv.ui.session import Session

        app = get_app()
        page = PosPage(Session(self.con, self.admin))
        try:
            page._add_row(self.service, "service", "خدمة وهمية")
            price_edit = page._rows[0]["price_edit"]
            self.assertEqual(price_edit.text(), "100.00")
            index = page.customer.findText("عميل وهمي")
            self.assertGreater(index, 0)
            page.customer.setCurrentIndex(index)
            self.assertEqual(page.tier, "wholesale")
            self.assertEqual(price_edit.text(), "80.00")
        finally:
            page.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
