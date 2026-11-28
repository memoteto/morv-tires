"""P04-4D.3: full supplier return must reverse original deductible input VAT.

The original red regression assertions are retained unchanged.
"""

from morv.core.sales_rules import Payment
from morv.infra import inventory, ledger, purchase_returns, sales, settings, treasury
from morv.infra.purchase_returns import ReturnLine
from morv.infra.purchasing import PurchaseLine
from morv.infra.sales import SaleLineIn
from tests.test_purchase_input_vat import Base


class PurchaseReturnVatTest(Base):
    def buy_one(self, *, deductible):
        self.enable_tax()
        invoice = self.buy(self.item(), 1, 100000, rate=1400, deductible=deductible)
        line = self.con.execute(
            "SELECT id, tax_amount, deductible_tax_amount FROM purchase_lines WHERE invoice_id=?",
            (invoice.id,),
        ).fetchone()
        self.assertEqual(invoice.total, 114000)
        self.assertEqual(line[1:], (14000, 14000 if deductible else 0))
        self.assertEqual(self.balances(), (100000 if deductible else 114000,
                                          14000 if deductible else 0, -114000, 0))
        # Returns must use original purchase data, not current settings.
        settings.put(self.con, "tax.rate", "2000")
        settings.put(self.con, "tax.enabled", "0")
        return line[0]

    def balances(self):
        return tuple(ledger.account_balance(self.con, code)
                     for code in ("1410", "1520", "2110", "5160"))

    def send(self, line, **kwargs):
        result = purchase_returns.post_return(
            self.con, self.admin, request_key="vat-return-full-001",
            supplier_id=self.sup, lines=[ReturnLine(line, 1)],
            reason="عيب تصنيع", return_date="2026-11-28", **kwargs,
        )
        self.assertEqual(self.con.execute(
            "SELECT qty_on_hand FROM stock_lots WHERE purchase_line_id=?", (line,),
        ).fetchone(), (0,))
        self.assertEqual(self.con.execute(
            "SELECT qty_remaining, cost_remaining FROM cost_layers WHERE purchase_line_id=?", (line,),
        ).fetchone(), (0, 0))
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        return result

    def test_full_default_settlement_reverses_deductible_input_vat(self):
        line = self.buy_one(deductible=True)
        result = self.send(line)
        self.assertEqual(result.total, 100000)  # unchanged inventory book value
        self.assertEqual(
            (self.balances(), treasury.supplier_owed(self.con, self.sup)),
            ((0, 0, 0, 0), 0),
            "Full return must clear inventory, deductible VAT and supplier liability.",
        )

    def test_full_manual_settlement_does_not_treat_vat_as_gain(self):
        line = self.buy_one(deductible=True)
        result = self.send(line, settlement_amount=114000, settlement_reason="تسوية كاملة")
        self.assertEqual(result.total, 100000)
        self.assertEqual(
            (self.balances(), treasury.supplier_owed(self.con, self.sup)),
            ((0, 0, 0, 0), 0),
            "Original deductible VAT is not a settlement gain on 5160.",
        )

    def test_non_deductible_vat_stays_in_book_cost_on_return(self):
        line = self.buy_one(deductible=False)
        result = self.send(line)
        self.assertEqual(result.total, 114000)
        self.assertEqual(self.balances(), (0, 0, 0, 0))
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 0)


class PurchaseReturnVatPartialTest(Base):
    def buy_line(self, qty=3, cost=100001, *, inclusive=False, deductible=True, **kwargs):
        self.enable_tax()
        invoice = self.buy(self.item(), qty, cost, rate=1400, deductible=deductible,
                           prices_tax_inclusive=inclusive, **kwargs)
        return self.con.execute("SELECT id FROM purchase_lines WHERE invoice_id=?", (invoice.id,)).fetchone()[0]

    def send(self, line, qty=1, *, tax=None, key=None, **kwargs):
        self.n += 1
        return purchase_returns.post_return(
            self.con, self.admin, request_key=key or f"vat-part-{self.n:06d}", supplier_id=self.sup,
            lines=[ReturnLine(line, qty, tax)], reason="عيب تصنيع", return_date="2026-11-28", **kwargs)

    def reverse(self, result):
        self.n += 1
        return purchase_returns.reverse_return(self.con, self.admin, request_key=f"vat-rev-{self.n:06d}",
                                               return_id=result.id, reason="تصحيح مرتجع")

    def amounts(self, result):
        return self.con.execute(
            "SELECT total, settlement_amount, tax_total, deductible_tax_total FROM purchase_returns WHERE id=?",
            (result.id,)).fetchone()

    def reconciled(self):
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), -ledger.account_balance(self.con, "2110"))

    def test_inclusive_exclusive_full_and_reversal(self):
        for inclusive in (False, True):
            with self.subTest(inclusive=inclusive):
                line = self.buy_line(1, 114000 if inclusive else 100000, inclusive=inclusive)
                result = self.send(line)
                self.assertEqual(self.amounts(result), (100000, 114000, 14000, 14000))
                reverse = self.reverse(result)
                self.assertEqual(self.amounts(reverse), self.amounts(result))
                self.assertEqual(self.amounts(self.send(line)), self.amounts(result))
                self.reconciled()
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)

    def test_partial_out_of_order_reversal_and_rounding(self):
        line = self.buy_line(qty=3, cost=10002)
        original = self.con.execute("SELECT tax_amount FROM purchase_lines WHERE id=?", (line,)).fetchone()[0]
        first, second = self.send(line), self.send(line)
        self.reverse(first)
        third = self.send(line, 2)
        self.assertEqual(self.amounts(second)[2] + self.amounts(third)[2], original)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
        self.reconciled()
        self.reverse(second)
        self.assertEqual(self.amounts(self.send(line))[2], self.amounts(second)[2])
        self.reconciled()

    def test_taxable_freight_not_refunded_is_not_reallocated(self):
        line = self.buy_line(2, 100000, freight_amount=20000, freight_taxable=True)
        first = self.send(line, tax=14000, settlement_amount=114000,
                          credit_note_ref="CN-1", settlement_reason="الشحن غير مسترد")
        self.assertEqual(self.amounts(first), (110000, 114000, 14000, 14000))
        self.assertEqual(ledger.account_balance(self.con, "5160"), 10000)
        second = self.send(line)
        self.assertEqual(self.amounts(second), (110000, 125400, 15400, 15400))
        self.assertEqual(ledger.account_balance(self.con, "1520"), 1400)
        self.reverse(first)
        self.assertEqual(self.amounts(self.send(line, tax=14000, settlement_amount=114000,
                                              credit_note_ref="CN-2", settlement_reason="الشحن غير مسترد")),
                         self.amounts(first))
        self.reconciled()

    def test_exceptional_settlement_requires_explicit_tax_and_reference(self):
        line = self.buy_line(1, 100000)
        before = self.con.execute("SELECT count(*) FROM journal_entries").fetchone()
        for kwargs in (dict(settlement_amount=0, settlement_reason="لا تسوية"),
                       dict(tax=0, settlement_amount=0, settlement_reason="لا تسوية"),
                       dict(tax=14001, settlement_amount=114001, credit_note_ref="CN-1",
                            settlement_reason="مبلغ مختلف")):
            with self.subTest(kwargs=kwargs), self.assertRaises(purchase_returns.PurchaseReturnError):
                self.send(line, **kwargs)
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone(), before)
        result = self.send(line, tax=0, settlement_amount=0, credit_note_ref="CN-2",
                           settlement_reason="لا تسوية ولا رد ضريبة")
        self.assertEqual(self.amounts(result), (100000, 0, 0, 0))
        self.assertEqual(ledger.account_balance(self.con, "5160"), 100000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 14000)
        self.reverse(result)
        self.assertEqual(ledger.account_balance(self.con, "5160"), 0)
        self.reconciled()

    def test_quote_guard_and_replay(self):
        line = self.buy_line(2, 10002)
        quote = purchase_returns.preview_return(self.con, self.sup, [ReturnLine(line, 1)], "2026-11-28")
        with self.assertRaises(purchase_returns.PurchaseReturnError):
            self.send(line, expected_book_total=quote.book_total)
        result = self.send(line, key="vat-quote-001", expected_quote=quote)
        self.assertEqual(self.send(line, key="vat-quote-001", expected_quote=quote).id, result.id)
        # Remaining allocation is one penny larger; book-only comparison cannot detect this.
        fresh = purchase_returns.preview_return(self.con, self.sup, [ReturnLine(line, 1)], "2026-11-28")
        self.assertNotEqual(fresh, quote)
        with self.assertRaises(purchase_returns.PurchaseReturnError):
            self.send(line, expected_quote=quote)
        self.send(line, expected_quote=fresh)
        self.reconciled()

    def test_sale_before_return_preserves_vat_on_sold_units(self):
        line = self.buy_line(5, 100000)
        item = self.con.execute("SELECT item_id FROM purchase_lines WHERE id=?", (line,)).fetchone()[0]
        sales.post_sale(
            self.con, self.admin, request_key="vat-sale-before-return", customer_id=None,
            sale_date="2026-11-28", lines=[SaleLineIn(item, 2, 150000)],
            payments=[Payment("cash", 342000, received=342000)], prices_tax_inclusive=False,
        )
        self.assertEqual(ledger.account_balance(self.con, "1520"), 70000)
        self.assertEqual(ledger.account_balance(self.con, "5110"), 200000)
        self.assertEqual(ledger.account_balance(self.con, "2350"), -42000)
        with self.assertRaises(purchase_returns.PurchaseReturnError):
            self.send(line, 4)
        returned = self.send(line, 3)
        self.assertEqual(self.amounts(returned), (300000, 342000, 42000, 42000))
        self.assertEqual(ledger.account_balance(self.con, "1520"), 28000)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 0)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 228000)
        self.assertEqual(ledger.account_balance(self.con, "5110"), 200000)
        self.assertEqual(ledger.account_balance(self.con, "2350"), -42000)
        self.reconciled()
        self.reverse(returned)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 70000)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 300000)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 570000)
        self.assertEqual(inventory.on_hand(self.con, item), 3)
        self.reconciled()

    def test_mixed_rates_and_deductibility_on_one_document(self):
        self.enable_tax()
        a, b, c, d = (self.item() for _ in range(4))
        invoice = self.buy(a, 1, 100000, lines=[
            PurchaseLine(a, 1, 100000, "2324", input_tax_rate=1400, input_tax_deductible=True),
            PurchaseLine(b, 1, 200000, "2324", input_tax_rate=500, input_tax_deductible=True),
            PurchaseLine(c, 1, 300000, "2324", input_tax_rate=1400, input_tax_deductible=False),
            PurchaseLine(d, 1, 400000, "2324", input_tax_rate=0, input_tax_deductible=False),
        ])
        ids = [r[0] for r in self.con.execute(
            "SELECT id FROM purchase_lines WHERE invoice_id=? ORDER BY line_no", (invoice.id,))]
        self.assertEqual(invoice.total, 1066000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 24000)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 1042000)
        self.enable_tax(2000)
        lines = [ReturnLine(i, 1) for i in ids]
        quote = purchase_returns.preview_return(self.con, self.sup, lines, "2026-11-28")
        self.assertEqual((quote.book_total, quote.tax_total, quote.deductible_tax_total, quote.default_settlement),
                         (1042000, 66000, 24000, 1066000))
        result = purchase_returns.post_return(
            self.con, self.admin, request_key="vat-mixed-return", supplier_id=self.sup,
            lines=lines, reason="مرتجع متعدد الأصناف", return_date="2026-11-28", expected_quote=quote)
        self.assertEqual(self.amounts(result), (1042000, 1066000, 66000, 24000))
        self.assertEqual(self.con.execute(
            "SELECT tax_amount,deductible_tax_amount FROM purchase_return_lines WHERE return_id=? ORDER BY line_no",
            (result.id,)).fetchall(), [(14000, 14000), (10000, 10000), (42000, 0), (0, 0)])
        for code in ("1410", "1520", "2110", "5160"):
            self.assertEqual(ledger.account_balance(self.con, code), 0)
        self.reconciled()
        self.assertEqual(self.amounts(self.reverse(result)), self.amounts(result))
        self.assertEqual(ledger.account_balance(self.con, "1520"), 24000)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 1042000)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 1066000)
        self.reconciled()

    def test_untaxed_freight_not_refunded(self):
        line = self.buy_line(1, 100000, freight_amount=10000, freight_taxable=False)
        result = self.send(line, tax=14000, settlement_amount=114000,
                           credit_note_ref="CN-UNTAXED", settlement_reason="الشحن غير مسترد")
        self.assertEqual(self.amounts(result), (110000, 114000, 14000, 14000))
        self.assertEqual(ledger.account_balance(self.con, "5160"), 10000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 10000)
        self.reconciled()
        self.reverse(result)
        self.assertEqual(ledger.account_balance(self.con, "5160"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 14000)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 124000)
        self.reconciled()

    def test_confirmed_tax_with_settlement_gain_and_loss(self):
        line = self.buy_line(1, 100000)
        for settlement, difference in ((109000, 5000), (119000, -5000)):
            with self.subTest(settlement=settlement):
                with self.assertRaises(purchase_returns.PurchaseReturnError):
                    self.send(line, settlement_amount=settlement, settlement_reason="تسوية مختلفة")
                result = self.send(line, tax=14000, settlement_amount=settlement,
                                   credit_note_ref=f"CN-{settlement}", settlement_reason="تسوية مختلفة")
                self.assertEqual(self.amounts(result), (100000, settlement, 14000, 14000))
                self.assertEqual(ledger.account_balance(self.con, "5160"), difference)
                self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
                self.assertEqual(treasury.supplier_owed(self.con, self.sup), difference)
                self.reconciled()
                self.reverse(result)
                self.assertEqual(ledger.account_balance(self.con, "5160"), 0)
                self.assertEqual(ledger.account_balance(self.con, "1520"), 14000)
                self.assertEqual(treasury.supplier_owed(self.con, self.sup), 114000)
                self.reconciled()
