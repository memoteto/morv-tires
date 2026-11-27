"""P04-4a: pure rate-resolution and base/tax split, no DB, no Qt."""
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


if __name__ == "__main__":
    unittest.main()
