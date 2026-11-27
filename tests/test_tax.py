"""P04-4a/4b: pure rate-resolution, inclusive/exclusive split and the purchase-line split, no DB."""
import unittest

from morv.core import money, tax


class ResolveRateTest(unittest.TestCase):
    def test_standard_falls_back_to_general_rate(self):
        self.assertEqual(tax.resolve_rate("standard", None, 1400), 1400)

    def test_standard_item_override_wins(self):
        self.assertEqual(tax.resolve_rate("standard", 500, 1400), 500)

    def test_exempt_is_zero_even_with_an_override_set(self):
        self.assertEqual(tax.resolve_rate("exempt", 500, 1400), 0)

    def test_zero_rated_is_zero(self):
        self.assertEqual(tax.resolve_rate("zero", None, 1400), 0)

    def test_rejects_unknown_treatment(self):
        with self.assertRaises(money.MoneyError):
            tax.resolve_rate("half", None, 1400)

    def test_rejects_non_int_item_rate(self):
        for bad in (14.0, "1400", True):
            with self.assertRaises(money.MoneyError, msg=repr(bad)):
                tax.resolve_rate("standard", bad, 1400)

    def test_rejects_non_int_general_rate(self):
        for bad in (14.0, "1400", None):
            with self.assertRaises(money.MoneyError, msg=repr(bad)):
                tax.resolve_rate("standard", None, bad)

    def test_rejects_negative_rates(self):
        with self.assertRaises(money.MoneyError):
            tax.resolve_rate("standard", -1, 1400)
        with self.assertRaises(money.MoneyError):
            tax.resolve_rate("standard", None, -1)


class TaxOnExclusiveTest(unittest.TestCase):
    def test_14pct_of_10000_is_1400(self):
        self.assertEqual(tax.tax_on_exclusive(10000, 1400), 1400)

    def test_zero_rate_is_zero(self):
        self.assertEqual(tax.tax_on_exclusive(10000, 0), 0)

    def test_rejects_negative_base(self):
        with self.assertRaises(money.MoneyError):
            tax.tax_on_exclusive(-1, 1400)

    def test_rejects_non_int_inputs(self):
        for bad_base in (10000.0, "10000"):
            with self.assertRaises(money.MoneyError, msg=repr(bad_base)):
                tax.tax_on_exclusive(bad_base, 1400)
        for bad_rate in (1400.0, "1400", None):
            with self.assertRaises(money.MoneyError, msg=repr(bad_rate)):
                tax.tax_on_exclusive(10000, bad_rate)


class SplitInclusiveTest(unittest.TestCase):
    def test_14pct_reconstructs_exactly(self):
        net, amount = tax.split_inclusive(11400, 1400)
        self.assertEqual((net, amount), (10000, 1400))
        self.assertEqual(net + amount, 11400)

    def test_zero_rate_is_all_net(self):
        self.assertEqual(tax.split_inclusive(10000, 0), (10000, 0))

    def test_rejects_negative_total(self):
        with self.assertRaises(money.MoneyError):
            tax.split_inclusive(-1, 1400)

    def test_rejects_non_int_inputs(self):
        for bad_total in (10000.0, "10000"):
            with self.assertRaises(money.MoneyError, msg=repr(bad_total)):
                tax.split_inclusive(bad_total, 1400)
        for bad_rate in (1400.0, "1400", None):
            with self.assertRaises(money.MoneyError, msg=repr(bad_rate)):
                tax.split_inclusive(10000, bad_rate)

    def test_always_reconstructs_the_total_exactly_across_a_range(self):
        for total in range(0, 500):
            for rate in (0, 500, 1400, 2000):
                net, amount = tax.split_inclusive(total, rate)
                self.assertEqual(net + amount, total)
                self.assertGreaterEqual(net, 0)
                self.assertGreaterEqual(amount, 0)


class DirectlyEnteredInclusivePriceTest(unittest.TestCase):
    """A shelf/sticker price the cashier types as the tax-inclusive total directly -- never
    built by adding tax_on_exclusive(net) to some net. These are hand-verified against the
    documented formula (total*rate/(10000+rate), half up), not against tax_on_exclusive, since
    there is no "original net" for a directly-typed total to be consistent with.
    """

    def test_a_typical_shelf_price(self):
        self.assertEqual(tax.split_inclusive(11500, 1400), (10088, 1412))

    def test_an_odd_three_digit_price(self):
        self.assertEqual(tax.split_inclusive(999, 1400), (876, 123))

    def test_a_small_price(self):
        self.assertEqual(tax.split_inclusive(50, 1400), (44, 6))

    def test_one_piaster_is_all_net_no_tax_roundable(self):
        self.assertEqual(tax.split_inclusive(1, 1400), (1, 0))

    def test_a_large_invoice_total(self):
        self.assertEqual(tax.split_inclusive(123456789, 1400), (108295429, 15161360))

    def test_sum_always_reconstructs_the_typed_total_exactly(self):
        for total in (11500, 999, 50, 1, 123456789):
            net, amount = tax.split_inclusive(total, 1400)
            self.assertEqual(net + amount, total)


class InclusiveExclusiveConsistencyTest(unittest.TestCase):
    """The owner's finding on PR #175: split_inclusive disagreed with tax_on_exclusive on the
    very same sale. net=10, tax=1 (14%) gives total=11; decoding 11 back used to yield (9, 2) --
    a different tax on the same invoice depending only on which price mode was used. Fixed by
    computing split_inclusive's tax with the same formula/rounding direction as tax_on_exclusive
    instead of flooring the net; this is the reproduction plus the permanent regression guard.
    """

    def test_owners_exact_example_round_trips(self):
        net, tax_amount = 10, tax.tax_on_exclusive(10, 1400)
        self.assertEqual(tax_amount, 1)
        total = net + tax_amount
        self.assertEqual(total, 11)
        self.assertEqual(tax.split_inclusive(total, 1400), (net, tax_amount))

    def test_every_rate_and_exclusive_net_round_trips_through_the_inclusive_total(self):
        for rate in (0, 500, 1400, 1437, 2000, 10000):
            for net in range(0, 400):
                amount = tax.tax_on_exclusive(net, rate)
                total = net + amount
                self.assertEqual(tax.split_inclusive(total, rate), (net, amount),
                                 msg=f"rate={rate} net={net} total={total}")


class PurchaseLineSplitTest(unittest.TestCase):
    """P04-4b: per-line tax base/tax/cost split for a purchase invoice (freight already in
    net_total per P01). Invariants proven by a 20,000-case randomized stress test before these
    were written: inclusive mode cost_amount+tax_amount==net_total; exclusive mode
    cost_amount==net_total and cost_amount+tax_amount==net_total+tax_amount.
    """

    def test_zero_rate_leaves_everything_in_cost_both_modes(self):
        for inclusive in (True, False):
            for taxable in (True, False):
                base, tax_amount, cost = tax.purchase_line_split(10000, 2000, taxable, 0, inclusive)
                self.assertEqual(tax_amount, 0)
                self.assertEqual(cost, 10000)
                self.assertEqual(base, 10000 if taxable else 8000)

    def test_exclusive_mode_tax_is_additive_cost_unchanged(self):
        base, tax_amount, cost = tax.purchase_line_split(10000, 0, True, 1400, False)
        self.assertEqual(base, 10000)
        self.assertEqual(tax_amount, 1400)
        self.assertEqual(cost, 10000)

    def test_inclusive_mode_tax_is_carved_out_of_net_total(self):
        base, tax_amount, cost = tax.purchase_line_split(11400, 0, True, 1400, True)
        self.assertEqual(tax_amount, 1400)
        self.assertEqual(cost + tax_amount, 11400)

    def test_freight_not_taxable_excluded_from_base_but_kept_in_cost_exclusive(self):
        # net_total=10700 includes a 700 freight_share; base for tax is only the 10000 goods part.
        base, tax_amount, cost = tax.purchase_line_split(10700, 700, False, 1400, False)
        self.assertEqual(base, 10000)
        self.assertEqual(tax_amount, 1400)
        self.assertEqual(cost, 10700)  # freight stays in cost, untaxed

    def test_freight_not_taxable_excluded_from_base_but_kept_in_cost_inclusive(self):
        base, tax_amount, cost = tax.purchase_line_split(11400, 700, False, 1400, True)
        net_goods, tax_amount_expected = tax.split_inclusive(11400 - 700, 1400)
        self.assertEqual(base, net_goods)
        self.assertEqual(tax_amount, tax_amount_expected)
        self.assertEqual(cost + tax_amount, 11400)

    def test_freight_taxable_is_included_in_base(self):
        base, tax_amount, cost = tax.purchase_line_split(10700, 700, True, 1400, False)
        self.assertEqual(base, 10700)
        self.assertEqual(tax_amount, tax.tax_on_exclusive(10700, 1400))

    def test_randomized_invariants_hold(self):
        import random
        rng = random.Random(4)
        for _ in range(2000):
            net_total = rng.randint(0, 5_000_000)
            freight_share = rng.randint(0, net_total)
            freight_taxable = rng.choice((True, False))
            rate_bp = rng.choice((0, 50, 137, 1400, 9999, 15000))
            for inclusive in (True, False):
                base, tax_amount, cost = tax.purchase_line_split(
                    net_total, freight_share, freight_taxable, rate_bp, inclusive)
                if inclusive:
                    self.assertEqual(cost + tax_amount, net_total)
                else:
                    self.assertEqual(cost, net_total)
                    self.assertEqual(cost + tax_amount, net_total + tax_amount)
                self.assertGreaterEqual(tax_amount, 0)
                self.assertGreaterEqual(cost, 0)

    def test_rejects_negative_and_bad_types(self):
        with self.assertRaises(money.MoneyError):
            tax.purchase_line_split(-1, 0, True, 1400, False)
        with self.assertRaises(money.MoneyError):
            tax.purchase_line_split(1000, -1, True, 1400, False)
        with self.assertRaises(money.MoneyError):
            tax.purchase_line_split(1000, 0, True, -1, False)
        with self.assertRaises(money.MoneyError):
            tax.purchase_line_split(1000.0, 0, True, 1400, False)

    def test_rejects_freight_share_larger_than_net_total(self):
        with self.assertRaises(money.MoneyError):
            tax.purchase_line_split(1000, 1001, True, 1400, False)


if __name__ == "__main__":
    unittest.main()
