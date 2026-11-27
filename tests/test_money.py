"""Money parsing, formatting, percentage and largest-remainder allocation: pure, no database."""
from unittest import TestCase

from morv.core import money


class AllocateTest(TestCase):
    def test_exact_division_has_no_remainder_to_place(self):
        self.assertEqual(money.allocate([25, 25, 25, 25], 100), [25, 25, 25, 25])

    def test_shares_sum_to_the_amount_for_an_uneven_split(self):
        shares = money.allocate([100, 100, 100], 100)
        self.assertEqual(sum(shares), 100)
        self.assertEqual(sorted(shares), [33, 33, 34])

    def test_largest_fractional_remainder_wins_the_extra_piaster(self):
        # weights 5,3,3 of 10: floor shares 4,2,2, remainder 2 to the largest
        # fractions (6/11, 8/11, 8/11); ties on 8/11 go to the earlier line.
        shares = money.allocate([5, 3, 3], 10)
        self.assertEqual(shares, [4, 3, 3])

    def test_ties_break_by_line_order_so_the_result_is_deterministic(self):
        self.assertEqual(money.allocate([1, 1], 1), [1, 0])

    def test_zero_amount_yields_all_zero_shares(self):
        self.assertEqual(money.allocate([3, 1], 0), [0, 0])

    def test_zero_total_with_zero_amount_is_allowed(self):
        self.assertEqual(money.allocate([0, 0], 0), [0, 0])

    def test_zero_total_with_a_positive_amount_is_refused(self):
        with self.assertRaises(money.MoneyError):
            money.allocate([0, 0], 3)

    def test_amount_is_not_bounded_by_the_total_unlike_a_discount(self):
        # a freight charge, for example, has no reason to be <= the quantities or values
        # it is weighted by; only a caller-enforced rule (e.g. a discount) bounds it.
        self.assertEqual(money.allocate([1, 1], 101), [51, 50])

    def test_negative_amount_is_refused(self):
        with self.assertRaises(money.MoneyError):
            money.allocate([1, 1], -1)

    def test_many_lines_still_sum_exactly(self):
        bases = [n * 1000 for n in range(1, 38)]   # an odd, non-round split across 37 lines
        shares = money.allocate(bases, 10_000)
        self.assertEqual(sum(shares), 10_000)
        self.assertEqual(len(shares), len(bases))
