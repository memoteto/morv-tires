"""P04-4D.3: full supplier return must reverse original deductible input VAT.

The original red regression assertions are retained unchanged.
"""

from morv.infra import inventory, ledger, purchase_returns, settings, treasury
from morv.infra.purchase_returns import ReturnLine
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
