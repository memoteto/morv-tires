"""Full synthetic installment returns with EIR, receipts and inventory integrity."""
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import (installment_view, installments, interest_recognition, inventory,
                        ledger, purchasing, sale_returns, sales, users)
from morv.infra.db.bootstrap import open_main_db
from morv.infra.installments import ContractError
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnError, ReturnLineIn
from morv.infra.sales import SaleLineIn


class FullInstallmentReturnTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="morv_eir_return_"))
        self.con = open_main_db(self.folder / "synthetic.db")
        self.manager = users.create_user(
            self.con, None, "fullreturn_admin", "مدير اختبار", "Admin12345", "manager",
        )
        self.customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000099',0) RETURNING id"
        ).fetchone()[0]
        self.supplier = self.con.execute(
            "INSERT INTO suppliers (name,name_norm,mobile) "
            "VALUES ('مورد وهمي','مورد وهمي','01000000088') RETURNING id"
        ).fetchone()[0]
        self.service = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','EIR-RETURN-SVC','خدمة','خدمه',1200000,1200000,1200000) RETURNING id"
        ).fetchone()[0]
        self.tire = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('tire','EIR-RETURN-TIRE','إطار','اطار',600000,600000,600000) RETURNING id"
        ).fetchone()[0]

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def make_sale(self, *, tire=False, qty=1):
        if tire:
            purchasing.post_invoice(
                self.con, self.manager, request_key="eir-return-purchase-0001",
                supplier_id=self.supplier, supplier_invoice_no="EIR-TEST-01",
                invoice_date="2025-07-01", lines=[PurchaseLine(self.tire, qty, 300000, "2324")],
            )
        item = self.tire if tire else self.service
        amount = 600000 * qty if tire else 1200000
        sale = sales.post_sale(
            self.con, self.manager, request_key="eir-return-sale-0001",
            customer_id=self.customer, lines=[SaleLineIn(item, qty, amount // qty)],
            payments=[Payment("installment", amount)], sale_date="2025-07-10",
        )
        row_id = self.con.execute(
            "SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,),
        ).fetchone()[0]
        return sale, row_id

    def contract(self, sale, *, down=200000, rate_bp=1500, method="card"):
        return installments.post_contract(
            self.con, self.manager, request_key="eir-return-contract-0001",
            sale_id=sale.id, down=down, rate_bp=rate_bp, months=10,
            first_due="2025-08-13", national_id="29001011201234",
            down_method=method if down else None, contract_date="2025-07-13",
        )

    def collect(self, contract, *, amount=115000):
        return installments.collect_payment(
            self.con, self.manager, request_key="eir-return-collection-0001",
            contract_id=contract.id, amount=amount, method="cash",
            payment_date="2025-08-13",
        )

    def lines(self, row_id, qty=1, condition="sound"):
        return [ReturnLineIn(row_id, qty, condition)]

    def cancel(self, sale, lines, *, key="eir-return-posted-0001", day="2025-09-02",
               refund_method=None):
        return sale_returns.post_return(
            self.con, self.manager, request_key=key, sale_id=sale.id, lines=lines,
            reason="عيب في الصنف", return_date=day, refund_method=refund_method,
        )

    def state(self):
        tables = (
            "sale_returns", "sale_return_lines", "installment_return_adjustments",
            "installment_return_refunds", "installment_return_allocations",
            "journal_entries", "journal_lines", "stock_movements", "cost_allocations",
            "stock_lots", "cost_layers", "installment_payments", "interest_recognitions",
        )
        return (
            tuple((t, tuple(self.con.execute(f"SELECT * FROM {t} ORDER BY rowid"))) for t in tables),
            tuple(ledger.account_balance(self.con, code) for code in
                  ("1110", "1220", "1320", "1410", "1430", "2410", "4140", "4190", "5110")),
        )

    def test_full_cancel_refunds_only_receipts_and_cancels_unpaid_rows(self):
        sale, line = self.make_sale()
        contract = self.contract(sale)
        self.collect(contract)
        preview = sale_returns.preview_installment_refund(
            self.con, sale.id, self.lines(line), "2025-09-02",
        )
        self.assertEqual((preview.principal, preview.markup, preview.refunded,
                          preview.receivable_reduced, preview.recognized_reversal),
                         (1200000, 150000, 315000, 1035000, 0))
        self.assertEqual(dict(preview.refunds), {"cash": 115000, "card": 200000})
        result = self.cancel(sale, self.lines(line))
        self.assertEqual(result.total, 1200000)
        self.assertEqual(self.con.execute(
            "SELECT principal,markup,refunded,receivable_reduced "
            "FROM installment_return_adjustments"
        ).fetchone(), (1200000, 150000, 315000, 1035000))
        for account in ("1110", "1220", "1320", "2410", "4140"):
            self.assertEqual(ledger.account_balance(self.con, account), 0, account)
        self.assertEqual(ledger.account_balance(self.con, "4190"), 1200000)
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])
        self.assertEqual(installment_view.collection_view(self.con, contract.id).remaining_total, 0)
        item = installment_view.list_view(self.con, today="2025-09-20").rows[0]
        self.assertEqual((item.status, item.can_collect), ("مرتجع بالكامل", False))
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(ledger.incomplete_entries(self.con), [])
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(ContractError):
            self.collect(contract, amount=1)

    def test_cancel_after_eir_recognition_reverses_earned_and_deferred_interest(self):
        sale, line = self.make_sale()
        contract = self.contract(sale)
        interest_recognition.recognise_month(
            self.con, self.manager, contract_id=contract.id, period="2025-07",
            entry_date="2025-08-01",
        )
        self.collect(contract)
        interest_recognition.recognise_month(
            self.con, self.manager, contract_id=contract.id, period="2025-08",
            entry_date="2025-09-01",
        )
        recognized = self.con.execute(
            "SELECT sum(amount) FROM interest_recognitions WHERE contract_id=?",
            (contract.id,),
        ).fetchone()[0]
        self.assertGreater(recognized, 0)
        count_before = self.con.execute("SELECT count(*) FROM interest_recognitions").fetchone()[0]
        preview = sale_returns.preview_installment_refund(
            self.con, sale.id, self.lines(line), "2025-09-02",
        )
        self.assertEqual((preview.recognized_reversal, preview.deferred_reversal),
                         (recognized, 150000 - recognized))
        result = self.cancel(sale, self.lines(line))
        entry = self.con.execute(
            "SELECT journal_entry_id FROM sale_returns WHERE id=?", (result.id,),
        ).fetchone()[0]
        self.assertEqual(self.con.execute(
            "SELECT debit FROM journal_lines WHERE entry_id=? AND account_code='4140'",
            (entry,),
        ).fetchone()[0], recognized)
        self.assertEqual(self.con.execute(
            "SELECT debit FROM journal_lines WHERE entry_id=? AND account_code='2410'",
            (entry,),
        ).fetchone()[0], 150000 - recognized)
        self.assertEqual(self.con.execute("SELECT count(*) FROM interest_recognitions").fetchone()[0],
                         count_before)
        for account in ("1110", "1220", "1320", "2410", "4140"):
            self.assertEqual(ledger.account_balance(self.con, account), 0, account)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(ledger.incomplete_entries(self.con), [])
        with self.assertRaisesRegex(interest_recognition.RecognitionError, "مرتجع"):
            interest_recognition.recognise_month(
                self.con, self.manager, contract_id=contract.id, period="2025-09",
            )

    def test_no_upfront_cash_or_markup_does_not_invent_a_cash_refund(self):
        sale, line = self.make_sale()
        contract = self.contract(sale, down=0, rate_bp=0)
        preview = sale_returns.preview_installment_refund(
            self.con, sale.id, self.lines(line), "2025-07-15",
        )
        self.assertEqual((preview.refunded, preview.receivable_reduced), (0, 1200000))
        self.cancel(sale, self.lines(line), day="2025-07-15")
        self.assertEqual(ledger.account_balance(self.con, "1320"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1110"), 0)
        self.assertEqual(installments.remaining_rows(self.con, contract.id), [])

    def test_defective_tire_is_not_sellable_and_preserves_fifo_dot(self):
        sale, line = self.make_sale(tire=True, qty=2)
        self.contract(sale)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 0)
        self.cancel(sale, self.lines(line, qty=2, condition="defective"), day="2025-07-15")
        self.assertEqual(inventory.on_hand(self.con, self.tire), 0)
        self.assertEqual(ledger.account_balance(self.con, "1430"), 600000)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.account_balance(self.con, "1320"), 0)

    def test_sound_tire_goes_back_to_original_stock_layer(self):
        sale, line = self.make_sale(tire=True, qty=2)
        self.contract(sale)
        self.cancel(sale, self.lines(line, qty=2), day="2025-07-15")
        self.assertEqual(inventory.on_hand(self.con, self.tire), 2)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.account_balance(self.con, "1410"), 600000)

    def test_partial_is_refused_and_does_not_mutate_money_or_inventory(self):
        sale, line = self.make_sale(tire=True, qty=2)
        self.contract(sale)
        before = self.state()
        with self.assertRaisesRegex(ReturnError, "جزئي"):
            self.cancel(sale, self.lines(line, qty=1), day="2025-07-15")
        with self.assertRaisesRegex(ReturnError, "جزئي"):
            sale_returns.preview_installment_refund(
                self.con, sale.id, self.lines(line, qty=1), "2025-07-15",
            )
        self.assertEqual(self.state(), before)

    def test_wrong_refund_method_cannot_override_contract_settlement(self):
        sale, line = self.make_sale()
        self.contract(sale)
        before = self.state()
        with self.assertRaises(ReturnError):
            self.cancel(sale, self.lines(line), refund_method="cash", day="2025-07-15")
        self.assertEqual(self.state(), before)

    def test_no_contract_cannot_cancel_invoice_or_issue_refund(self):
        sale, line = self.make_sale()
        before = self.state()
        with self.assertRaisesRegex(ReturnError, "قبل اعتماد"):
            self.cancel(sale, self.lines(line), day="2025-07-15")
        self.assertEqual(self.state(), before)

    def test_backdated_cancel_before_recognized_entry_is_refused(self):
        sale, line = self.make_sale()
        contract = self.contract(sale)
        interest_recognition.recognise_month(
            self.con, self.manager, contract_id=contract.id, period="2025-07",
            entry_date="2025-08-10",
        )
        before = self.state()
        with self.assertRaisesRegex(ReturnError, "تاريخ"):
            self.cancel(sale, self.lines(line), day="2025-08-09")
        self.assertEqual(self.state(), before)

    def test_repeat_request_key_is_idempotent_and_new_key_cannot_refund_twice(self):
        sale, line = self.make_sale()
        contract = self.contract(sale)
        self.collect(contract)
        first = self.cancel(sale, self.lines(line))
        snapshot = self.state()
        replay = self.cancel(sale, self.lines(line))
        self.assertEqual((replay.id, replay.replayed), (first.id, True))
        self.assertEqual(self.state(), snapshot)
        with self.assertRaises(ReturnError):
            self.cancel(sale, self.lines(line), key="eir-return-posted-0002")
        self.assertEqual(self.state(), snapshot)


if __name__ == "__main__":
    unittest.main()
