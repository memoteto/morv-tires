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

    def test_always_reconstructs_exactly_across_a_range(self):
        for total in range(0, 500):
            for rate in (0, 500, 1400, 2000):
                net, amount = tax.split_inclusive(total, rate)
                self.assertEqual(net + amount, total)
                self.assertGreaterEqual(net, 0)
                self.assertGreaterEqual(amount, 0)


class TaxOnExclusiveTest(unittest.TestCase):
    def test_14pct_of_10000_is_1400(self):
        self.assertEqual(tax.tax_on_exclusive(10000, 1400), 1400)

    def test_zero_rate_is_zero(self):
        self.assertEqual(tax.tax_on_exclusive(10000, 0), 0)

    def test_rejects_negative_base(self):
        with self.assertRaises(money.MoneyError):
            tax.tax_on_exclusive(-1, 1400)


if __name__ == "__main__":
    unittest.main()
