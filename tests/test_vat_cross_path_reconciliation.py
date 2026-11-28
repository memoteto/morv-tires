"""P04-4D.3.3: reconcile posted purchase/sale/return paths before tax reporting.

This is not R18 or final VAT acceptance: no tax settlement, financing VAT or report
is tested here. Both equivalent price modes run against independent fresh databases.
"""
import unittest

from morv.core.sales_rules import Payment
from morv.infra import customer_receipts, inventory, ledger, purchase_returns, purchasing
from morv.infra import sale_returns, settings, treasury
from morv.infra.purchasing import PurchaseLine
from morv.infra.purchase_returns import ReturnLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_sale_output_vat import _Fixture


class VatCrossPathReconciliationTest(_Fixture, unittest.TestCase):
    version = 40

    def scalar(self, sql, args=()):
        return self.con.execute(sql, args).fetchone()[0]

    def assert_vat_matches(self, expected_input, expected_output):
        # Independent persisted document totals, not the tax calculation helpers.
        purchased = self.scalar("SELECT coalesce(sum(deductible_tax_amount),0) FROM purchase_lines")
        returned_input = self.scalar(
            "SELECT coalesce(sum(CASE WHEN kind='return' THEN deductible_tax_total "
            "ELSE -deductible_tax_total END),0) FROM purchase_returns")
        sold = self.scalar("SELECT coalesce(sum(tax_total),0) FROM sales")
        returned_output = self.scalar("SELECT coalesce(sum(tax_total),0) FROM sale_returns")
        self.assertEqual((purchased - returned_input, sold - returned_output),
                         (expected_input, expected_output))
        self.assertEqual((self.balance("1520"), -self.balance("2350")),
                         (expected_input, expected_output))
        self.assertEqual(self.scalar("SELECT coalesce(sum(tax_amount),0) FROM sale_lines"), sold)
        self.assertEqual(self.scalar("SELECT coalesce(sum(tax_amount),0) FROM sale_return_lines"),
                         returned_output)
        self.assertEqual(self.scalar(
            "SELECT coalesce(sum(CASE WHEN r.kind='return' THEN l.deductible_tax_amount "
            "ELSE -l.deductible_tax_amount END),0) FROM purchase_return_lines l "
            "JOIN purchase_returns r ON r.id=l.return_id"), returned_input)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def scenario(self, inclusive):
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", "1400")
        item = self.item("tire", "CROSS-T", 171000)
        opening_inventory = self.balance("1410")
        opening_supplier = treasury.supplier_owed(self.con, self.supplier)
        invoice = purchasing.post_invoice(
            self.con, self.admin, request_key="cross-buy-001", supplier_id=self.supplier,
            supplier_invoice_no="CROSS-BUY", invoice_date="2026-11-02",
            lines=[PurchaseLine(item, 5, 114000 if inclusive else 100000, "2324",
                                input_tax_rate=1400, input_tax_deductible=True)],
            prices_tax_inclusive=inclusive)
        pl = self.scalar("SELECT id FROM purchase_lines WHERE invoice_id=?", (invoice.id,))
        self.assertEqual(invoice.total, 570000)
        self.assert_vat_matches(70000, 0)
        sale = self.post("cross-sale-001", [SaleLineIn(item, 2, 171000 if inclusive else 150000)],
                         [Payment("credit", 342000)], customer_id=self.customer,
                         prices_tax_inclusive=inclusive)
        sl = self.scalar("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,))
        self.assertEqual(sale.total, 342000)
        self.assertEqual((self.balance("4110"), self.balance("5110")), (-300000, 200000))
        self.assertEqual(customer_receipts.customer_balance(self.con, self.customer), 342000)
        self.assert_vat_matches(70000, 42000)
        settings.put(self.con, "tax.rate", "2000")
        settings.put(self.con, "tax.enabled", "0")
        returned = sale_returns.post_return(
            self.con, self.admin, request_key="cross-sale-return-001", sale_id=sale.id,
            lines=[ReturnLineIn(sl, 1)], reason="اختبار مرتجع", return_date="2026-11-11")
        self.assertEqual(returned.total, 171000)
        self.assertEqual((self.balance("4190"), self.balance("5110"), self.balance("1310")),
                         (150000, 100000, 171000))
        self.assertEqual(customer_receipts.customer_balance(self.con, self.customer), 171000)
        self.assert_vat_matches(70000, 21000)

        def send(qty, key):
            return purchase_returns.post_return(
                self.con, self.admin, request_key=key, supplier_id=self.supplier,
                lines=[ReturnLine(pl, qty)], reason="اختبار مرتجع", return_date="2026-11-12")

        first = send(3, "cross-preturn-001")
        self.assertEqual(first.total, 300000)
        self.assertEqual(inventory.on_hand(self.con, item), 1)
        self.assertEqual(treasury.supplier_owed(self.con, self.supplier) - opening_supplier, 228000)
        self.assert_vat_matches(28000, 21000)
        purchase_returns.reverse_return(self.con, self.admin, request_key="cross-reverse-001",
                                        return_id=first.id, reason="اختبار عكس")
        self.assertEqual(inventory.on_hand(self.con, item), 4)
        self.assertEqual(treasury.supplier_owed(self.con, self.supplier) - opening_supplier, 570000)
        self.assert_vat_matches(70000, 21000)
        second = send(4, "cross-preturn-002")
        self.assertEqual(second.total, 400000)
        self.assertTrue(send(4, "cross-preturn-002").replayed)
        self.assert_vat_matches(14000, 21000)
        self.assertEqual(self.balance("1410"), opening_inventory)
        self.assertEqual(inventory.on_hand(self.con, item), 0)
        self.assertEqual(self.con.execute(
            "SELECT qty_on_hand FROM stock_lots WHERE purchase_line_id=?", (pl,)).fetchone(), (0,))
        self.assertEqual(self.con.execute(
            "SELECT qty_remaining,cost_remaining FROM cost_layers WHERE purchase_line_id=?", (pl,)).fetchone(),
            (0, 0))
        self.assertEqual(treasury.supplier_owed(self.con, self.supplier) - opening_supplier, 114000)
        self.assertEqual(-self.balance("2110"), treasury.supplier_owed(self.con, self.supplier))
        self.assertEqual(customer_receipts.customer_balance(self.con, self.customer), 171000)
        self.assertEqual(self.balance("5160"), 0)
        self.assertEqual(-self.balance("4110") - self.balance("4190") - self.balance("5110"), 50000)

    def test_inclusive_cross_path(self):
        self.scenario(True)

    def test_exclusive_cross_path(self):
        self.scenario(False)
