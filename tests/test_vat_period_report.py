"""P04-4E.2: VAT period reporting against posted source documents and ledger."""
import unittest
from datetime import date
from unittest.mock import patch

from morv.core.sales_rules import Payment
from morv.infra import ledger, purchase_returns, purchasing, reports, sale_returns, settings
from morv.infra.db.connection import transaction
from morv.infra.purchase_returns import ReturnLine
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_sale_output_vat import _Fixture


class VatPeriodReportTest(_Fixture, unittest.TestCase):
    version = 40

    def values(self, start, end, *, warning=False):
        table = reports.build(self.con, "vat", start, end)
        self.assertEqual(table.key, "vat")
        self.assertEqual(table.kinds, (reports.TEXT, reports.MONEY))
        if warning:
            self.assertIn("تحذير", table.subtitle)
        else:
            self.assertIn("تطابق الدفتر", table.subtitle)
        return dict(c for _, c in table.rows)

    def scenario(self, inclusive):
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", "1400")
        item = self.item("tire", "PERIOD-VAT", 171000)
        other = self.item("tire", "PERIOD-ND", 114000)
        price = 114000 if inclusive else 100000
        invoice = purchasing.post_invoice(
            self.con, self.admin, request_key="period-buy", supplier_id=self.supplier,
            supplier_invoice_no="PERIOD-VAT", invoice_date="2026-11-02",
            lines=[PurchaseLine(item, 5, price, "2324", input_tax_rate=1400, input_tax_deductible=True),
                   PurchaseLine(other, 1, price, "2324", input_tax_rate=1400, input_tax_deductible=False)],
            prices_tax_inclusive=inclusive)
        pl = self.con.execute("SELECT id FROM purchase_lines WHERE invoice_id=? AND item_id=?",
                              (invoice.id, item)).fetchone()[0]
        sale = self.post("period-sale", [SaleLineIn(item, 2, 171000 if inclusive else 150000)],
                         [Payment("credit", 342000)], customer_id=self.customer,
                         prices_tax_inclusive=inclusive)
        sl = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        settings.put(self.con, "tax.enabled", "0")
        settings.put(self.con, "tax.rate", "2000")
        sale_returns.post_return(self.con, self.admin, request_key="period-sale-return", sale_id=sale.id,
                                 lines=[ReturnLineIn(sl, 1)], reason="اختبار مرتجع", return_date="2026-11-11")
        ret = purchase_returns.post_return(self.con, self.admin, request_key="period-purchase-return",
                                           supplier_id=self.supplier, lines=[ReturnLine(pl, 3)],
                                           reason="اختبار مرتجع", return_date="2026-11-12")
        with patch("morv.infra.purchase_returns.date") as clock:
            clock.today.return_value = date(2026, 11, 20)
            purchase_returns.reverse_return(self.con, self.admin, request_key="period-reversal",
                                            return_id=ret.id, reason="اختبار عكس")

    def assert_periods(self, inclusive):
        self.scenario(inclusive)
        before = self.snapshot(("journal_entries", "journal_lines", "sales", "purchase_returns"))
        a = self.values("2026-11-02", "2026-11-10")
        self.assertEqual(a["ضريبة المبيعات"], 42000)
        self.assertEqual(a["ضريبة المشتريات القابلة للخصم"], 70000)
        self.assertEqual(a["صافي ضريبة الفترة قبل التسويات"], -28000)
        b = self.values("2026-11-11", "2026-11-19")
        self.assertEqual((b["رصيد 1520 ضريبة القيمة المضافة - مدخلات قابلة للخصم أول الفترة (مدين)"], b["رصيد 2350 ضريبة القيمة المضافة - مخرجات مستحقة أول الفترة (دائن)"]),
                         (70000, 42000))
        self.assertEqual((b["ضريبة مرتجعات البيع"], b["ضريبة مرتجعات الشراء القابلة للخصم"],
                          b["ضريبة عكس مرتجعات الشراء"]), (-21000, -42000, 0))
        self.assertEqual(b["صافي ضريبة الفترة قبل التسويات"], 21000)
        self.assertEqual((b["رصيد 1520 ضريبة القيمة المضافة - مدخلات قابلة للخصم آخر الفترة (مدين)"], b["رصيد 2350 ضريبة القيمة المضافة - مخرجات مستحقة آخر الفترة (دائن)"]),
                         (28000, 21000))
        c = self.values("2026-11-20", "2026-11-30")
        self.assertEqual(c["ضريبة عكس مرتجعات الشراء"], 42000)
        self.assertEqual(c["صافي ضريبة الفترة قبل التسويات"], -42000)
        self.assertEqual((c["رصيد 1520 ضريبة القيمة المضافة - مدخلات قابلة للخصم آخر الفترة (مدين)"], c["رصيد 2350 ضريبة القيمة المضافة - مخرجات مستحقة آخر الفترة (دائن)"]),
                         (self.balance("1520"), -self.balance("2350")))
        d = self.values("2026-11-01", "2026-11-30")
        self.assertEqual(d["صافي ضريبة الفترة قبل التسويات"], -49000)
        self.assertEqual(d["صافي الرصيد الختامي (دائن / مدين بالسالب)"], -49000)
        self.assertEqual(before, self.snapshot(("journal_entries", "journal_lines", "sales", "purchase_returns")))

    def test_inclusive_periods(self):
        self.assert_periods(True)

    def test_exclusive_periods(self):
        self.assert_periods(False)

    def test_empty_period_and_carried_balances(self):
        d = self.values("2026-11-01", "2026-11-01")
        self.assertEqual(d["صافي ضريبة الفترة قبل التسويات"], 0)
        self.scenario(True)
        d = self.values("2026-11-21", "2026-11-30")
        self.assertEqual(d["صافي ضريبة الفترة قبل التسويات"], 0)
        self.assertEqual(d["صافي الرصيد الختامي (دائن / مدين بالسالب)"], -49000)

    def test_unclassified_posted_movement_visible_not_treated_as_settlement(self):
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, "2026-11-05", "treasury_movement", 987654,
                              "اختبار حركة غير مصنفة", [("1520", 1000, 0), ("1110", 0, 1000)])
        d = self.values("2026-11-01", "2026-11-30", warning=True)
        self.assertEqual(d["صافي ضريبة الفترة قبل التسويات"], 0)
        self.assertEqual(d["حركات أخرى في 1520 ضريبة القيمة المضافة - مدخلات قابلة للخصم"], 1000)
        self.assertEqual(d["رصيد 1520 ضريبة القيمة المضافة - مدخلات قابلة للخصم آخر الفترة (مدين)"], 1000)
        self.assertEqual(d["صافي الرصيد الختامي (دائن / مدين بالسالب)"], -1000)

    def test_document_mismatch_warns_and_ledger_is_authoritative(self):
        self.scenario(True)
        # Simulate historical corruption in this disposable fixture only, not production writes.
        self.con.execute("DROP TRIGGER sales_no_update")
        self.con.execute("UPDATE sales SET tax_total=tax_total-1")
        d = self.values("2026-11-01", "2026-11-30", warning=True)
        self.assertEqual(d["ضريبة المبيعات"], 42000)
        self.assertEqual(d["رصيد 2350 ضريبة القيمة المضافة - مخرجات مستحقة آخر الفترة (دائن)"], 21000)

    def test_reversed_date_range_refused(self):
        with self.assertRaises(reports.ReportError):
            reports.build(self.con, "vat", "2026-11-30", "2026-11-01")
