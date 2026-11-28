"""P04-4D.1: immutable output VAT reversal, exact partials and legacy return safety."""
import sqlite3
import unittest

from morv.core import return_rules
from morv.core.sales_rules import Payment
from morv.infra import installments, inventory, ledger, sale_returns, sales, settings
from morv.infra.db.migrator import current_version, migrate
from morv.infra.sale_returns import ReturnError, ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.test_sale_output_vat import _Fixture


class SaleReturnVatTest(_Fixture, unittest.TestCase):
    version = 38

    def enable(self):
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", "1400")

    def lines(self, sale):
        return [row[0] for row in self.con.execute(
            "SELECT id FROM sale_lines WHERE sale_id=? ORDER BY line_no", (sale.id,))]

    def ret(self, sale, key, lines, **kw):
        return sale_returns.post_return(self.con, self.admin, request_key=key, sale_id=sale.id,
                                        lines=lines, reason="اختبار المرتجع", return_date="2026-11-11", **kw)

    def test_vat_return(self):
        for inclusive, price in ((True, 114000), (False, 100000)):
            with self.subTest(inclusive=inclusive):
                self.enable()
                sale = self.post(f"vat-source-{inclusive}", [SaleLineIn(self.tire, 1, price)],
                                 [Payment("cash", 114000, received=114000)],
                                 prices_tax_inclusive=inclusive)
                line = self.lines(sale)[0]
                settings.put(self.con, "tax.rate", "1000")
                settings.put(self.con, "tax.enabled", "0")
                accounts = ("1110", "4190", "2350", "1410", "5110")
                before = {c: self.balance(c) for c in accounts}
                original = self.snapshot(("sales", "sale_lines", "sale_payments"))
                result = self.ret(sale, f"vat-return-{inclusive}", [ReturnLineIn(line, 1)])
                self.assertEqual(result.total, 114000)
                self.assertEqual({c: self.balance(c) - before[c] for c in accounts},
                                 {"1110": -114000, "4190": 100000, "2350": 14000,
                                  "1410": 80000, "5110": -80000})
                self.assertEqual(self.con.execute(
                    "SELECT tax_total FROM sale_returns WHERE id=?", (result.id,)).fetchone(), (14000,))
                self.assertEqual(self.con.execute(
                    "SELECT tax_amount FROM sale_return_lines WHERE return_id=?", (result.id,)).fetchone(), (14000,))
                self.assertEqual(self.snapshot(original), original)
                saved = self.snapshot(("sale_returns", "sale_return_lines", "journal_entries", "journal_lines",
                                       "stock_lots", "cost_layers", "stock_movements", "cost_allocations"))
                again = self.ret(sale, f"vat-return-{inclusive}", [ReturnLineIn(line, 1)])
                self.assertEqual((again.id, again.replayed), (result.id, True))
                with self.assertRaises(ReturnError):
                    self.ret(sale, f"vat-over-{inclusive}", [ReturnLineIn(line, 1)])
                self.assertEqual(self.snapshot(saved), saved)
                self.assertEqual(inventory.reconcile(self.con), [])
                self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_partial_rounding_preserves_original_gross_and_tax(self):
        self.enable()
        lines = [SaleLineIn(self.service, 3, 10000)]
        quote = sales.compute_sale_totals(self.con, lines, 20000, prices_tax_inclusive=True)
        self.assertEqual((quote.total, quote.tax_total), (10000, 1228))
        sale = self.post("vat-part-src", lines, [Payment("cash", 10000, received=10000)],
                         invoice_discount=20000, prices_tax_inclusive=True)
        for n in range(3):
            self.ret(sale, f"vat-part-{n}", [ReturnLineIn(self.lines(sale)[0], 1)])
        self.assertEqual(self.con.execute(
            "SELECT total,tax_total FROM sale_returns ORDER BY id").fetchall(),
            [(3333, 409), (3333, 409), (3334, 410)])
        self.assertEqual((self.balance("1110"), self.balance("4190"), self.balance("2350")),
                         (0, 8772, 0))
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_mixed_treatments_discounts_and_credit_settlement(self):
        self.enable()
        exempt = self.item("service", "RET-E", 10000)
        zero = self.item("service", "RET-Z", 10000)
        self.con.execute("UPDATE items SET tax_treatment='exempt' WHERE id=?", (exempt,))
        self.con.execute("UPDATE items SET tax_treatment='zero' WHERE id=?", (zero,))
        lines = [SaleLineIn(self.service, 1, 11000, line_discount=1000),
                 SaleLineIn(exempt, 1, 10000), SaleLineIn(zero, 1, 10000)]
        sale = self.post("vat-mixed-return", lines, [Payment("credit", 28260)],
                         customer_id=self.customer, invoice_discount=3000, prices_tax_inclusive=False)
        self.ret(sale, "vat-mixed-ret", [ReturnLineIn(line, 1) for line in self.lines(sale)])
        self.assertEqual(self.con.execute(
            "SELECT value,tax_amount FROM sale_return_lines ORDER BY line_no").fetchall(),
            [(10260, 1260), (9000, 0), (9000, 0)])
        self.assertEqual((self.balance("1310"), self.balance("4190"), self.balance("2350")),
                         (0, 27000, 0))
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_mixed_cash_credit_and_defective_stock(self):
        self.enable()
        sale = self.post("vat-defect-src", [SaleLineIn(self.tire, 1, 114000)],
                         [Payment("cash", 14000, received=14000), Payment("credit", 100000)],
                         customer_id=self.customer, prices_tax_inclusive=True)
        before = inventory.on_hand(self.con, self.tire)
        result = self.ret(sale, "vat-defect-ret", [ReturnLineIn(self.lines(sale)[0], 1, "defective")],
                          refund_method="cash")
        self.assertEqual(self.con.execute("SELECT credit_part FROM sale_returns WHERE id=?",
                                          (result.id,)).fetchone(), (100000,))
        self.assertEqual(inventory.on_hand(self.con, self.tire), before)
        self.assertEqual((self.balance("1110"), self.balance("1310"), self.balance("2350"),
                          self.balance("1430"), self.balance("4190"), self.balance("5110")),
                         (0, 0, 0, 80000, 100000, 0))
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_installment_limits_and_full_zero_interest_return(self):
        self.enable()
        for rate in (0, 1000):
            with self.subTest(rate=rate):
                sale = self.post(f"vat-installment-{rate}", [SaleLineIn(self.service, 2, 50000)],
                                 [Payment("installment", 114000)], customer_id=self.customer,
                                 prices_tax_inclusive=False)
                contract = installments.post_contract(
                    self.con, self.admin, request_key=f"vat-contract-{rate}", sale_id=sale.id,
                    down=0, rate_bp=rate, months=2, first_due="2026-12-10",
                    national_id="29001011201234", contract_date="2026-11-10")
                line = self.lines(sale)[0]
                saved = self.snapshot(("sale_returns", "sale_return_lines", "journal_entries", "journal_lines"))
                with self.assertRaisesRegex(ReturnError, "الجزئي"):
                    self.ret(sale, f"vat-part-inst-{rate}", [ReturnLineIn(line, 1)])
                self.assertEqual(self.snapshot(saved), saved)
                if rate:
                    with self.assertRaisesRegex(ReturnError, "بفوائد"):
                        self.ret(sale, "vat-interest-ret", [ReturnLineIn(line, 2)])
                    self.assertEqual(self.snapshot(saved), saved)
                else:
                    result = self.ret(sale, "vat-zero-interest-ret", [ReturnLineIn(line, 2)])
                    self.assertEqual(result.total, 114000)
                    self.assertEqual(installments.remaining_rows(self.con, contract.id), [])
                    self.assertEqual((self.balance("1320"), self.balance("2350"), self.balance("4190")),
                                     (0, 0, 100000))
                self.assertEqual(ledger.unbalanced_entries(self.con), [])


class ReturnVatRoundingTest(unittest.TestCase):
    def test_tax_never_exceeds_partial_gross_even_for_tiny_values(self):
        for qty in range(1, 25):
            for gross in range(30):
                for tax in range(gross + 1):
                    amounts = [return_rules.tax_portion(gross, tax, n, 1, qty) for n in range(qty)]
                    self.assertEqual(sum(amounts), tax)
                    self.assertTrue(all(0 <= amount <= return_rules.portion(gross, n, 1, qty)
                                        for n, amount in enumerate(amounts)))


class SaleReturnVatUpgradeTest(_Fixture, unittest.TestCase):
    version = 37

    def test_additive_upgrade_preserves_legacy_returns_and_protection(self):
        sale = self.post("old-return-src", [SaleLineIn(self.tire, 2, 114000)],
                         [Payment("cash", 228000, received=228000)])
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        kw = dict(request_key="legacy-return", sale_id=sale.id, lines=[ReturnLineIn(line, 1)],
                  reason="اختبار", return_date="2026-11-11")
        old = sale_returns.post_return(self.con, self.admin, **kw)
        tables = ("sales", "sale_lines", "sale_payments", "sale_returns", "sale_return_lines",
                  "journal_entries", "journal_lines", "stock_lots", "cost_layers", "stock_movements",
                  "cost_allocations")
        columns = {table: [r[1] for r in self.con.execute(f"PRAGMA table_info({table})")] for table in tables}

        def original():
            return {table: tuple(self.con.execute(f"SELECT {','.join(names)} FROM {table} ORDER BY id"))
                    for table, names in columns.items()}

        before = original()
        self.assertEqual(current_version(self.con), 37)
        self.assertEqual(migrate(self.con, self.migrations[:38]), [38])
        self.assertEqual(original(), before)
        self.assertEqual(self.con.execute("SELECT tax_total FROM sale_returns").fetchall(), [(0,)])
        self.assertEqual(self.con.execute("SELECT tax_amount FROM sale_return_lines").fetchall(), [(0,)])
        for sql in ("UPDATE sale_returns SET tax_total=1", "DELETE FROM sale_returns",
                    "UPDATE sale_return_lines SET tax_amount=1", "DELETE FROM sale_return_lines"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql)
        replay = sale_returns.post_return(self.con, self.admin, **kw)
        self.assertEqual((replay.id, replay.replayed), (old.id, True))
        self.assertEqual(original(), before)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.con.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
