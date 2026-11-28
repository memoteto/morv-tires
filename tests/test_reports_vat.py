"""P04-4E.1: reports must distinguish document cash value from posted revenue and cost."""
import unittest

from morv.core.sales_rules import Payment
from morv.infra import purchasing, reports, sale_returns, settings
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_sale_output_vat import _Fixture


class VatReportTest(_Fixture, unittest.TestCase):
    version = 40
    start, end = "2026-11-02", "2026-11-30"

    def enable(self):
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", "1400")

    def sale_and_return(self, inclusive):
        self.enable()
        sale = self.post("report-vat-sale", [SaleLineIn(self.tire, 2, 114000 if inclusive else 100000)],
                         [Payment("credit", 228000)], customer_id=self.customer,
                         prices_tax_inclusive=inclusive)
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        settings.put(self.con, "tax.enabled", "0")
        settings.put(self.con, "tax.rate", "2000")
        sale_returns.post_return(self.con, self.admin, request_key="report-vat-return", sale_id=sale.id,
                                 lines=[ReturnLineIn(line, 1)], reason="اختبار المرتجع", return_date="2026-11-11")
        self.assertEqual((self.balance("4110"), self.balance("4190"), self.balance("5110")),
                         (-200000, 100000, 80000))
        self.assertEqual(self.balance("2350"), -14000)

    def test_item_profit_excludes_vat_and_ties_to_posted_accounts(self):
        self.sale_and_return(True)
        table = reports.sales_profit(self.con, self.start, self.end)
        self.assertEqual(table.rows[-1][1][3:7], (200000, 100000, 80000, 20000))
        self.assertEqual(table.rows[-1][1][6],
                         -self.balance("4110") - self.balance("4190") - self.balance("5110"))

    def test_top_items_excludes_vat_from_net_sales(self):
        self.sale_and_return(False)
        table = reports.top_items(self.con, self.start, self.end)
        self.assertEqual(table.rows[-1][1][4], 100000)
        self.assertEqual(table.rows[-1][1][4], -self.balance("4110") - self.balance("4190"))

    def test_returns_report_ties_to_4190_without_output_vat(self):
        self.sale_and_return(True)
        table = reports.returns(self.con, self.start, self.end)
        self.assertEqual([cells[-1] for kind, cells in table.rows if kind == "total"], [100000, 100000])
        self.assertIn("يطابق الدفتر", table.subtitle)

    def purchase(self, inclusive):
        self.enable()
        other = self.item("tire", "VAT-ND", 171000)
        price = 114000 if inclusive else 100000
        before = self.balance("2110"), self.balance("1410"), self.balance("1520")
        invoice = purchasing.post_invoice(
            self.con, self.admin, request_key="report-vat-buy", supplier_id=self.supplier,
            supplier_invoice_no="REPORT-VAT", invoice_date=self.start,
            lines=[PurchaseLine(self.tire, 2, price, "2324", input_tax_rate=1400, input_tax_deductible=True),
                   PurchaseLine(other, 1, price, "2324", input_tax_rate=1400, input_tax_deductible=False)],
            prices_tax_inclusive=inclusive)
        self.assertEqual(invoice.total, 342000)
        self.assertEqual(tuple(self.balance(c) - v for c, v in zip(("2110", "1410", "1520"), before, strict=True)),
                         (-342000, 314000, 28000))
        settings.put(self.con, "tax.enabled", "0")
        settings.put(self.con, "tax.rate", "2000")
        table = reports.purchases(self.con, self.start, self.end)
        rows = {cells[1]: cells for kind, cells in table.rows if kind == "line"}
        self.assertEqual(rows["VAT-T"][2:], ("2", 228000, 100000))
        self.assertEqual(rows["VAT-ND"][2:], ("1", 114000, 114000))
        self.assertEqual(table.rows[-1][1][3], 342000)
        self.assertIn("يطابق الدفتر", table.subtitle)

    def test_purchases_inclusive_gross_payable_and_fifo_unit_cost(self):
        self.purchase(True)

    def test_purchases_exclusive_gross_payable_and_fifo_unit_cost(self):
        self.purchase(False)

    def test_daily_sales_keeps_gross_to_match_payment_methods(self):
        self.sale_and_return(True)
        table = reports.daily_sales(self.con, self.start, self.end)
        self.assertEqual(table.rows[-1][1][2:], (228000, 114000, 114000, 0, 0, 228000))
        self.assertEqual(self.balance("1310"), 114000)
        self.assertEqual(self.balance("2350"), -14000)
