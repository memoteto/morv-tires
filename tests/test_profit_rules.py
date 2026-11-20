"""Profit rules: percent parsing, reserve, exact allocation without losing a piaster."""
import unittest

from morv.core import profit_rules as r


class ProfitRulesTest(unittest.TestCase):
    def test_parse_percent(self):
        self.assertEqual(r.parse_percent("50"), 5000)
        self.assertEqual(r.parse_percent("12.5%"), 1250)
        self.assertEqual(r.parse_percent("٣٣٫٣٣"), 3333)
        self.assertEqual(r.parse_percent("100"), 10000)
        for bad in ("", "abc", "-1", "100.01", "10.123", "101"):
            with self.assertRaises(r.ShareError):
                r.parse_percent(bad)

    def test_format_percent(self):
        self.assertEqual([r.fmt_percent(x) for x in (5000, 1250, 3333, 10000, 5)], ["50", "12.5", "33.33", "100", "0.05"])

    def test_reserve_only_on_a_positive_result(self):
        self.assertEqual(r.reserve_for(100_000, 500), 5_000)
        self.assertEqual(r.reserve_for(-100_000, 500), 0)
        self.assertEqual(r.reserve_for(100_001, 500), 5_000)
        self.assertEqual(r.reserve_for(100_000, 0), 0)

    def test_split_is_exact_and_deterministic(self):
        parts = r.split(100, [(1, 3334), (2, 3333), (3, 3333)])
        self.assertEqual(sum(a for _, a in parts), 100)
        self.assertEqual(parts, [(1, 34), (2, 33), (3, 33)])
        parts = r.split(1, [(7, 5000), (3, 5000)])
        self.assertEqual(sorted(parts), [(3, 1), (7, 0)])     # tie: the lower partner id gets the piaster
        self.assertEqual(r.split(500, [(1, 10000)]), [(1, 500)])

    def test_split_rejects_bad_input(self):
        with self.assertRaises(r.ShareError):
            r.split(100, [(1, 5000), (2, 4000)])
        with self.assertRaises(r.ShareError):
            r.split(-1, [(1, 10000)])

    def test_split_never_loses_money_for_many_amounts(self):
        shares = [(1, 4000), (2, 3500), (3, 2500)]
        for amount in range(0, 1000, 7):
            self.assertEqual(sum(a for _, a in r.split(amount, shares)), amount)


if __name__ == "__main__":
    unittest.main()
