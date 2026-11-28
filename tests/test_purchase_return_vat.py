"""P04-4D.3: full supplier return must reverse original deductible input VAT.

Initial regression tests intentionally describe the approved accounting outcome,
not the current implementation. No production code changes in this commit.
"""

from morv.infra import ledger, purchase_returns, settings, treasury
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
