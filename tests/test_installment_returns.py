"""Synthetic-only regression tests for installment returns, ledger, FIFO and migration 0013."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from morv.core.sales_rules import Payment
from morv.infra import (installment_view, installments, inventory, ledger, purchasing,
                        sale_returns, sale_view, sales, users)
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.installments import ContractError
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnError, ReturnLineIn
from morv.infra.sales import SaleError, SaleLineIn
from tests.qt_app import get_app, require_qt

NID = "29001011201234"


class _Synthetic(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="morv_refund_synthetic_"))
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "synthetic.db")
        self.admin = users.create_user(self.con, None, "refundadmin", "مدير وهمي", "Admin12345", "manager")
        self.customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0)").lastrowid
        self.service = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','SV-RETURN','خدمة وهمية','خدمه وهميه',1200000,1200000,1200000)").lastrowid
        self.tire = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('tire','TR-RETURN','إطار وهمي','اطار وهمي',600000,600000,600000)").lastrowid
        self.supplier = self.con.execute(
            "INSERT INTO suppliers (name,name_norm,mobile) "
            "VALUES ('مورد وهمي','مورد وهمي','01000000001')").lastrowid
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def sale(self, item=None, qty=1, unit_price=1200000):
        self.n += 1
        price = qty * unit_price
        return sales.post_sale(
            self.con, self.admin, request_key=f"return-test-sale-{self.n:04d}",
            customer_id=self.customer, lines=[SaleLineIn(item or self.service, qty, unit_price)],
            payments=[Payment("installment", price)], sale_date="2026-11-10")

    def contract(self, sale, *, down=200000, down_method="cash", rate_bp=1500):
        self.n += 1
        return installments.post_contract(
            self.con, self.admin, request_key=f"return-test-contract-{self.n:04d}",
            sale_id=sale.id, down=down, rate_bp=rate_bp, months=10,
            first_due="2026-12-13", national_id=NID, down_method=down_method if down else None,
            contract_date="2026-11-13")

    def pay(self, contract, amount, method="cash", payment_date="2026-11-14"):
        self.n += 1
        return installments.collect_payment(
            self.con, self.admin, request_key=f"return-test-payment-{self.n:04d}",
            contract_id=contract.id, amount=amount, method=method, payment_date=payment_date)

    def ret(self, sale, qty=1, *, key=None, return_date="2026-11-15"):
        self.n += 1
        line_id = self.con.execute(
            "SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        return sale_returns.post_return(
            self.con, self.admin, request_key=key or f"return-test-return-{self.n:04d}",
            sale_id=sale.id, lines=[ReturnLineIn(line_id, qty)],
            reason="مرتجع وهمي للاختبار", return_date=return_date)

    def bal(self, account):
        return ledger.account_balance(self.con, account)


class InstallmentReturnTest(_Synthetic):
    def test_full_return_refunds_down_and_collected_installments_by_original_method(self):
        sale = self.sale()
        contract = self.contract(sale, down_method="card")
        self.pay(contract, 115000, "cash")
        before = self.con.execute("SELECT count(*) FROM installment_rows").fetchone()[0]
        posted = self.ret(sale)
        self.assertEqual(posted.total, 1200000)
        adjustment = self.con.execute(
            "SELECT principal,markup,refunded,receivable_reduced FROM installment_return_adjustments"
        ).fetchone()
        self.assertEqual(tuple(adjustment), (1200000, 150000, 315000, 1035000))
        refunds = self.con.execute(
            "SELECT method,amount FROM installment_return_refunds ORDER BY method").fetchall()
        self.assertEqual(refunds, [("card", 200000), ("cash", 115000)])
        self.assertEqual(self.bal("1320"), 0)
        self.assertEqual(self.bal("2410"), 0)
        self.assertEqual(self.bal("1220"), 0)
        self.assertEqual(self.bal("1110"), 0)
        self.assertEqual(self.bal("4190"), 1200000)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])
        self.assertEqual(self.con.execute("SELECT count(*) FROM installment_rows").fetchone()[0], before)
        v = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual((v.rows[0].status, v.rows[0].can_collect), ("مرتجع بالكامل", False))
        self.assertEqual(v.kpis.active_amount, 0)
        self.assertEqual(installment_view.collection_view(self.con, contract.id).remaining_total, 0)
        with self.assertRaises(ContractError):
            self.pay(contract, 1)

    def test_fully_paid_contract_returns_every_piaster(self):
        sale = self.sale()
        contract = self.contract(sale)
        self.pay(contract, 1150000)
        self.ret(sale)
        self.assertEqual(self.con.execute("SELECT refunded FROM installment_return_adjustments").fetchone()[0],
                         1350000)
        self.assertEqual(self.bal("1110"), 0)
        self.assertEqual(self.bal("1320"), 0)
        self.assertEqual(self.bal("2410"), 0)
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])

    def test_unpaid_installments_are_canceled_without_fictitious_cash_refund(self):
        sale = self.sale()
        contract = self.contract(sale, down=0, rate_bp=0)
        self.ret(sale)
        self.assertEqual(self.con.execute("SELECT refunded,receivable_reduced "
                                          "FROM installment_return_adjustments").fetchone(), (0, 1200000))
        self.assertEqual(self.bal("1110"), 0)
        self.assertEqual(self.bal("1320"), 0)
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])

    def test_partial_returns_prorate_refunds_and_stock_and_close_after_second_return(self):
        purchasing.post_invoice(
            self.con, self.admin, request_key="return-test-purchase-1", supplier_id=self.supplier,
            supplier_invoice_no="AUDIT-P1", invoice_date="2026-11-01",
            lines=[PurchaseLine(self.tire, 2, 300000, "2324")])
        sale = self.sale(self.tire, qty=2, unit_price=600000)
        contract = self.contract(sale)
        self.pay(contract, 115000, "card")
        self.assertEqual(inventory.on_hand(self.con, self.tire), 0)
        first = self.ret(sale, qty=1, key="return-first-part")
        self.assertEqual(first.total, 600000)
        row = self.con.execute(
            "SELECT markup, refunded,receivable_reduced FROM installment_return_adjustments "
            "WHERE return_id=?", (first.id,)).fetchone()
        self.assertEqual(tuple(row), (75000, 157500, 517500))
        self.assertEqual(self.bal("1320"), 517500)
        self.assertEqual(self.bal("2410"), -75000)
        self.assertEqual(sum(r[4] for r in installments.remaining_rows(self.con, contract.id)), 517500)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 1)
        self.assertEqual(inventory.reconcile(self.con), [])

        # An intervening collection applies only to still-outstanding obligations.
        self.pay(contract, 100000, "cash")
        self.assertEqual(sum(r[4] for r in installments.remaining_rows(self.con, contract.id)), 417500)
        second = self.ret(sale, qty=1, key="return-final-part")
        self.assertEqual(second.total, 600000)
        self.assertEqual(self.con.execute("SELECT sum(refunded) FROM installment_return_adjustments").fetchone()[0],
                         415000)  # original 315000 plus intervening 100000
        self.assertEqual(self.bal("1320"), 0)
        self.assertEqual(self.bal("2410"), 0)
        self.assertEqual(self.bal("1110"), 0)
        self.assertEqual(self.bal("1220"), 0)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 2)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])
        self.assertEqual(installment_view.list_view(self.con).rows[0].status, "مرتجع بالكامل")

    def test_replay_is_idempotent_including_settlement_and_refund(self):
        sale = self.sale()
        contract = self.contract(sale)
        first = self.ret(sale, key="return-replay-key")
        again = self.ret(sale, key="return-replay-key")
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.con.execute("SELECT count(*) FROM installment_return_adjustments").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 1)
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])

    def test_pending_contract_must_fail_cleanly(self):
        sale = self.sale()
        with self.assertRaisesRegex(ReturnError, "اعتماد عقد"):
            self.ret(sale)
        for name in ("sale_returns", "installment_return_adjustments", "journal_entries"):
            self.assertEqual(self.con.execute(f"SELECT count(*) FROM {name} WHERE 1=1").fetchone()[0],
                             1 if name == "journal_entries" else 0)
        self.assertEqual(self.bal("1320"), 1200000)

    def test_return_before_latest_collection_fails_without_mutation(self):
        sale = self.sale()
        contract = self.contract(sale)
        self.pay(contract, 10000, payment_date="2026-11-15")
        before = self.bal("1320")
        with self.assertRaisesRegex(ReturnError, "تاريخ"):
            self.ret(sale, return_date="2026-11-14")
        self.assertEqual(self.bal("1320"), before)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 0)

    def test_mixed_installment_sale_is_refused_before_posting(self):
        with self.assertRaisesRegex(SaleError, "التقسيط"):
            sales.post_sale(
                self.con, self.admin, request_key="split-installment-invalid", customer_id=self.customer,
                lines=[SaleLineIn(self.service, 1, 1200000)],
                payments=[Payment("installment", 1100000),
                          Payment("cash", 100000, received=100000)], sale_date="2026-11-10")
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 0)

    def test_preview_matches_posted_refund(self):
        sale = self.sale()
        contract = self.contract(sale)
        self.pay(contract, 115000, "card")
        line_id = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        preview = sale_returns.preview_installment_refund(
            self.con, sale.id, [ReturnLineIn(line_id, 1)], "2026-11-15")
        self.assertEqual(preview.refunded, 315000)
        self.assertEqual(preview.receivable_reduced, 1035000)
        self.assertEqual(sale_view.refund_methods(self.con, sale.id), ["installment"])
        self.ret(sale)
        self.assertEqual(self.con.execute("SELECT refunded FROM installment_return_adjustments").fetchone()[0],
                         preview.refunded)


class MigrationOfExistingData(unittest.TestCase):
    def test_existing_posted_return_and_stock_links_survive_upgrade(self):
        folder = Path(tempfile.mkdtemp(prefix="morv_old_return_upgrade_"))
        con = None
        try:
            con = open_db(folder / "old_synthetic.db", create=True)
            migrate(con, load_migrations()[:-1])
            self.assertEqual(current_version(con), 12)
            manager = users.create_user(con, None, "oldadmin", "مدير وهمي", "Admin12345", "manager")
            sid = con.execute(
                "INSERT INTO suppliers (name,name_norm,mobile) "
                "VALUES ('مورد وهمي','مورد وهمي','01000000001')").lastrowid
            iid = con.execute(
                "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
                "VALUES ('tire','TR-OLD','إطار وهمي','اطار وهمي',10000,10000,10000)").lastrowid
            purchasing.post_invoice(con, manager, request_key="old-purchase-01", supplier_id=sid,
                                    supplier_invoice_no="old1", invoice_date="2026-11-01",
                                    lines=[PurchaseLine(iid, 2, 5000, "2324")])
            sale = sales.post_sale(con, manager, request_key="old-sale-001", customer_id=None,
                                   lines=[SaleLineIn(iid, 1, 10000)],
                                   payments=[Payment("cash", 10000, received=10000)],
                                   sale_date="2026-11-10")
            line_id = con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
            old_return = sale_returns.post_return(
                con, manager, request_key="old-return-01", sale_id=sale.id,
                lines=[ReturnLineIn(line_id, 1)], reason="مرتجع وهمي",
                return_date="2026-11-12")
            before = con.execute("SELECT id,total,refund_method FROM sale_returns").fetchall()
            self.assertEqual(len(before), 1)
            self.assertEqual(migrate(con), [13])
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute("SELECT id,total,refund_method FROM sale_returns").fetchall(), before)
            self.assertEqual(con.execute("SELECT return_id FROM sale_return_lines").fetchone()[0], old_return.id)
            self.assertEqual(inventory.reconcile(con), [])
            self.assertEqual(current_version(con), 13)
        finally:
            if con is not None:
                con.close()
            shutil.rmtree(folder, ignore_errors=True)


@require_qt()
class InstallmentReturnUiTest(_Synthetic):
    def test_return_dialog_shows_preview_of_actual_refund_and_debt(self):
        from PySide6.QtCore import QEvent
        from morv.ui.sale_return_dialog import SaleReturnDialog

        app = get_app()
        sale = self.sale()
        contract = self.contract(sale)
        self.pay(contract, 115000, "cash")
        dialog = SaleReturnDialog(
            SimpleNamespace(con=self.con, user=self.admin), sale_view.get_sale(self.con, sale.id),
            sale_view.returnable_lines(self.con, sale.id),
            sale_view.refund_methods(self.con, sale.id))
        try:
            self.assertEqual(dialog.refund.currentData(), "installment")
            line_id = next(iter(dialog.returnable))
            dialog.lines = [ReturnLineIn(line_id, 1)]
            dialog._refresh_lines()
            self.assertIn("سيُرد للعميل", dialog.refund_breakdown.text())
            self.assertIn("تُلغى مديونية", dialog.refund_breakdown.text())
        finally:
            dialog.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
