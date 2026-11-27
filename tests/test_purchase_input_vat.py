"""P04-4b: input VAT on purchase invoices (1520, inclusive/exclusive, freight-taxability)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.infra import inventory, ledger, purchasing, settings, treasury, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction
from morv.infra.purchasing import PurchaseLine


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "m.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد','مورد','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, treatment="standard", rate=None):
        iid = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super, "
            "tax_treatment, tax_rate) VALUES ('tire', ?, 'x', 'x', 100, 100, 100, ?, ?) RETURNING id",
            (f"IT-{self.n}", treatment, rate)).fetchone()[0]
        self.n += 1
        return iid

    def enable_tax(self, rate_bp=1400):
        settings.put(self.con, "tax.enabled", "1")
        settings.put(self.con, "tax.rate", str(rate_bp))

    def buy(self, item_id, qty, cost, *, rate=None, deductible=None, **kw):
        self.n += 1
        args = dict(request_key=f"key-{self.n:06d}", supplier_id=self.sup, supplier_invoice_no=f"S-{self.n}",
                    invoice_date="2026-11-27", lines=[PurchaseLine(
                        item_id, qty, cost, "2324", input_tax_rate=rate, input_tax_deductible=deductible)])
        args.update(kw)
        return purchasing.post_invoice(self.con, self.admin, **args)

    def line_row(self, invoice_id):
        return self.con.execute(
            "SELECT net_total, tax_base, tax_rate, tax_amount FROM purchase_lines WHERE invoice_id=?",
            (invoice_id,)).fetchone()

    def cost_layer(self, invoice_id):
        return self.con.execute(
            "SELECT cost_total FROM cost_layers WHERE purchase_line_id IN "
            "(SELECT id FROM purchase_lines WHERE invoice_id=?)", (invoice_id,)).fetchone()[0]


class TaxDisabledIsUnchangedTest(Base):
    """Default (tax.enabled='0') must be byte-identical to pre-4b behaviour."""

    def test_no_1520_line_and_cost_equals_net_total(self):
        item_id = self.item()
        posted = self.buy(item_id, 5, 20000)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
        net_total, tax_base, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual((tax_rate, tax_amount), (0, 0))
        self.assertEqual(tax_base, net_total)
        self.assertEqual(self.cost_layer(posted.id), net_total)
        self.assertEqual(posted.total, net_total)
        self.assertEqual(ledger.account_balance(self.con, "2110"), -net_total)


class ExclusivePricesTest(Base):
    def setUp(self):
        super().setUp()
        self.enable_tax(1400)

    def test_standard_item_debits_1520_and_credits_supplier_net_plus_tax(self):
        item_id = self.item("standard")
        posted = self.buy(item_id, 1, 10000, rate=1400, deductible=True,
                          prices_tax_inclusive=False, freight_taxable=False)
        net_total, tax_base, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual(net_total, 10000)
        self.assertEqual(tax_rate, 1400)
        self.assertEqual(tax_amount, 1400)                 # 14% of 10000
        self.assertEqual(posted.total, 11400)               # supplier owed net + tax
        self.assertEqual(ledger.account_balance(self.con, "1520"), 1400)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 10000)   # cost excludes tax
        self.assertEqual(ledger.account_balance(self.con, "2110"), -11400)
        self.assertEqual(self.cost_layer(posted.id), 10000)  # inventory cost unaffected by tax

    def test_supplier_invoice_without_vat_stays_fully_in_cost(self):
        item_id = self.item("standard")
        posted = self.buy(item_id, 1, 10000, rate=0, deductible=False)
        net_total, tax_base, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual((tax_rate, tax_amount), (0, 0))
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
        self.assertEqual(posted.total, 10000)
        self.assertEqual(self.cost_layer(posted.id), 10000)

    def test_supplier_rate_is_independent_of_output_treatment_and_rate(self):
        item_id = self.item("exempt", 2000)
        posted = self.buy(item_id, 1, 10000, rate=500, deductible=True)
        _, _, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual(tax_rate, 500)
        self.assertEqual(tax_amount, 500)

    def test_freight_not_taxable_excluded_from_base_but_kept_in_cost(self):
        item_id = self.item("standard")
        posted = self.buy(item_id, 1, 10000, rate=1400, deductible=True,
                          freight_amount=700, freight_basis="qty", freight_taxable=False)
        net_total, tax_base, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual(net_total, 10700)       # freight folded into net_total (P01)
        self.assertEqual(tax_base, 10000)        # but excluded from the tax base
        self.assertEqual(tax_amount, 1400)
        self.assertEqual(self.cost_layer(posted.id), 10700)    # freight still fully in cost, untaxed
        self.assertEqual(posted.total, 10700 + 1400)

    def test_freight_taxable_is_included_in_base(self):
        item_id = self.item("standard")
        posted = self.buy(item_id, 1, 10000, rate=1400, deductible=True,
                          freight_amount=700, freight_basis="qty", freight_taxable=True)
        net_total, tax_base, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual(tax_base, 10700)
        self.assertEqual(tax_amount, 1498)   # 14% of 10700


class InclusivePricesTest(Base):
    def setUp(self):
        super().setUp()
        self.enable_tax(1400)

    def test_tax_is_carved_out_of_net_total_cost_excludes_it(self):
        item_id = self.item("standard")
        posted = self.buy(item_id, 1, 11400, rate=1400, deductible=True,
                          prices_tax_inclusive=True, freight_taxable=False)
        net_total, tax_base, tax_rate, tax_amount = self.line_row(posted.id)
        self.assertEqual(net_total, 11400)
        self.assertEqual(tax_amount, 1400)
        self.assertEqual(posted.total, 11400)                  # supplier owed stays net_total (tax already in it)
        self.assertEqual(self.cost_layer(posted.id), 11400 - 1400)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 1400)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 11400 - 1400)


class MultiLineRoundingTest(Base):
    """Debit always equals credit and tax_total is the exact sum of the line tax_amounts."""

    def test_two_lines_odd_rate_balances_exactly(self):
        self.enable_tax(1437)
        a, b = self.item(), self.item()
        self.n += 1
        posted = purchasing.post_invoice(
            self.con, self.admin, request_key="multi-key-01", supplier_id=self.sup, supplier_invoice_no="S-multi",
            invoice_date="2026-11-27", lines=[
                PurchaseLine(a, 3, 777, "2324", input_tax_rate=1437, input_tax_deductible=True),
                PurchaseLine(b, 5, 1333, "2324", input_tax_rate=1437, input_tax_deductible=True)])
        rows = self.con.execute("SELECT tax_amount FROM purchase_lines WHERE invoice_id=?", (posted.id,)).fetchall()
        tax_total = self.con.execute("SELECT tax_total FROM purchase_invoices WHERE id=?", (posted.id,)).fetchone()[0]
        self.assertEqual(sum(r[0] for r in rows), tax_total)
        self.assertEqual(ledger.account_balance(self.con, "1520"), tax_total)
        self.assertEqual(ledger.account_balance(self.con, "2110"), -posted.total)
        self.assertEqual(ledger.account_balance(self.con, "1410"), posted.total - tax_total)


class InputTaxEligibilityTest(Base):
    """Supplier VAT and its deductibility are distinct from the item's output tax treatment."""

    def test_non_deductible_vat_capitalized_in_both_price_modes(self):
        self.enable_tax()
        item_id = self.item("zero")
        for inclusive, price in ((False, 10000), (True, 11400)):
            posted = self.buy(item_id, 1, price, rate=1400, deductible=False,
                              prices_tax_inclusive=inclusive)
            self.assertEqual(posted.total, 11400)
            self.assertEqual(self.cost_layer(posted.id), 11400)
            self.assertEqual(self.line_row(posted.id)[2:], (1400, 1400))
            self.assertEqual(self.con.execute(
                "SELECT deductible_tax_amount FROM purchase_lines WHERE invoice_id=?", (posted.id,)
            ).fetchone()[0], 0)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 22800)
        self.assertEqual(ledger.account_balance(self.con, "2110"), -22800)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 22800)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_mixed_deductibility_on_same_item(self):
        self.enable_tax()
        item_id = self.item()
        posted = self.buy(item_id, 1, 10000, lines=[
            PurchaseLine(item_id, 1, 10000, "2324", input_tax_rate=1400, input_tax_deductible=True),
            PurchaseLine(item_id, 1, 10000, "2325", input_tax_rate=1400, input_tax_deductible=False)])
        rows = self.con.execute(
            "SELECT tax_amount, deductible_tax_amount FROM purchase_lines WHERE invoice_id=? ORDER BY line_no",
            (posted.id,)).fetchall()
        self.assertEqual(rows, [(1400, 1400), (1400, 0)])
        self.assertEqual(self.con.execute(
            "SELECT tax_total FROM purchase_invoices WHERE id=?", (posted.id,)).fetchone()[0], 2800)
        self.assertEqual(posted.total, 22800)
        self.assertEqual(ledger.account_balance(self.con, "1520"), 1400)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 21400)
        self.assertEqual(ledger.account_balance(self.con, "2110"), -22800)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_explicit_rate_and_eligibility_required_when_enabled(self):
        self.enable_tax()
        item_id = self.item()
        for rate, deductible in ((None, True), (1400, None), (True, True), (-1, True), (1400, 1)):
            with self.subTest(rate=rate, deductible=deductible):
                with self.assertRaises(purchasing.PurchaseError):
                    self.buy(item_id, 1, 10000, rate=rate, deductible=deductible)
        self.assertEqual(self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0], 0)
        self.assertEqual(ledger.account_balance(self.con, "1410"), 0)

    def test_disabled_tax_rejects_explicit_vat_data(self):
        with self.assertRaises(purchasing.PurchaseError):
            self.buy(self.item(), 1, 10000, rate=1400, deductible=True)

    def test_tax_choice_is_part_of_request_identity(self):
        self.enable_tax()
        item_id = self.item()
        args = dict(request_key="vat-replay-01", supplier_id=self.sup, supplier_invoice_no="S-vat",
                    invoice_date="2026-11-27")
        line = PurchaseLine(item_id, 1, 10000, "2324", input_tax_rate=1400, input_tax_deductible=True)
        posted = purchasing.post_invoice(self.con, self.admin, lines=[line], **args)
        self.assertTrue(purchasing.post_invoice(self.con, self.admin, lines=[line], **args).replayed)
        self.assertEqual(posted.total, 11400)
        changed = PurchaseLine(item_id, 1, 10000, "2324", input_tax_rate=1400, input_tax_deductible=False)
        with self.assertRaises(purchasing.PurchaseError):
            purchasing.post_invoice(self.con, self.admin, lines=[changed], **args)

    def test_rejects_cost_below_one_piaster_per_unit_after_deduction(self):
        self.enable_tax()
        with self.assertRaises(purchasing.PurchaseError):
            self.buy(self.item(), 2, 1, rate=10000, deductible=True, prices_tax_inclusive=True)
        self.assertEqual(self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0], 0)


class ReplayBackwardCompatibilityTest(Base):
    """A request_key stored under the pre-4b (8-arg) payload signature must still replay, not refuse.

    purchase_invoices rows are immutable (no UPDATE), so the pre-4b row is seeded directly by raw
    SQL carrying the OLD hash, exactly mirroring what post_invoice inserted before this change --
    not by posting then rewriting, which the immutability trigger would refuse anyway.
    """

    def test_pre_tax_hash_still_matches_on_replay(self):
        item_id = self.item()
        clean = [PurchaseLine(item_id, 2, 5000, "2324")]
        old_hash = purchasing._pre_tax_payload_hash(
            self.sup, "s-replay", "2026-11-27", "2026-11-27", clean, 0, "qty", 0)
        with transaction(self.con):
            entry_id = ledger.post_entry(self.con, self.admin, "2026-11-27", "purchase_invoice", 1,
                                         "فاتورة شراء PI-000001", [("1410", 10000, 0), ("2110", 0, 10000)])
            inv_id = self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, "
                "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, user_id) "
                "VALUES ('PI-000001', 'replay-key-01', ?, ?, 's-replay', '2026-11-27', '2026-11-27', "
                "10000, ?, ?) RETURNING id", (old_hash, self.sup, entry_id, self.admin.id)).fetchone()[0]
            self.con.execute(
                "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total, "
                "net_total) VALUES (?, 1, ?, '2324', 2, 5000, 10000, 10000)", (inv_id, item_id))
        replayed = purchasing.post_invoice(
            self.con, self.admin, request_key="replay-key-01", supplier_id=self.sup,
            supplier_invoice_no="s-replay", invoice_date="2026-11-27", lines=clean)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.id, inv_id)

    def test_mismatched_content_under_the_same_key_is_still_refused(self):
        item_id = self.item()
        clean = [PurchaseLine(item_id, 2, 5000, "2324")]
        old_hash = purchasing._pre_tax_payload_hash(
            self.sup, "s-replay-2", "2026-11-27", "2026-11-27", clean, 0, "qty", 0)
        with transaction(self.con):
            entry_id = ledger.post_entry(self.con, self.admin, "2026-11-27", "purchase_invoice", 1,
                                         "فاتورة شراء PI-000002", [("1410", 10000, 0), ("2110", 0, 10000)])
            self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, "
                "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, user_id) "
                "VALUES ('PI-000002', 'replay-key-02', ?, ?, 's-replay-2', '2026-11-27', '2026-11-27', "
                "10000, ?, ?)", (old_hash, self.sup, entry_id, self.admin.id))
        with self.assertRaises(purchasing.PurchaseError):
            purchasing.post_invoice(
                self.con, self.admin, request_key="replay-key-02", supplier_id=self.sup,
                supplier_invoice_no="s-replay-2", invoice_date="2026-11-27",
                lines=[PurchaseLine(item_id, 3, 5000, "2324")])   # different qty -> different content


if __name__ == "__main__":
    unittest.main()
