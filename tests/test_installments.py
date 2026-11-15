"""Instalment maths tests, matching the approved contract screen and the owner's settled rules."""
import unittest

from morv.core.installments import InstalmentError, add_months, markup_of, plan


class PlanTest(unittest.TestCase):
    def test_matches_the_approved_screen_example(self):
        """12000 cash, 2000 down, 15%, 10 months: the numbers the design board shows."""
        p = plan(1200000, 200000, 1500, 10, "2026-12-13")
        self.assertEqual(p.financed, 1000000)
        self.assertEqual(p.markup, 150000)
        self.assertEqual(p.monthly, 115000)
        self.assertEqual(p.instalments_total, 1150000)
        self.assertEqual(p.contract_total, 1350000)
        self.assertEqual(len(p.rows), 10)
        self.assertEqual(p.rows[0].due_date, "2026-12-13")
        self.assertEqual(p.rows[-1].due_date, "2027-09-13")

    def test_markup_is_on_the_financed_amount_only(self):
        """The owner's rule: interest on the instalments, never on the down payment."""
        self.assertEqual(plan(1000000, 400000, 1000, 6, "2026-01-01").markup, 60000)

    def test_instalments_add_up_to_the_contract_exactly(self):
        for months in range(2, 25):
            p = plan(999999, 133333, 1234, months, "2026-03-31")
            self.assertEqual(sum(r.amount for r in p.rows), p.instalments_total, months)
            self.assertEqual(sum(r.principal for r in p.rows), p.financed, months)
            self.assertEqual(sum(r.markup for r in p.rows), p.markup, months)

    def test_the_piaster_difference_lands_in_the_last_instalment(self):
        p = plan(100000, 0, 1000, 3, "2026-01-01")
        self.assertEqual([r.amount for r in p.rows[:-1]], [36666, 36666])
        self.assertEqual(p.rows[-1].amount, p.instalments_total - 36666 * 2)

    def test_balance_falls_to_zero(self):
        p = plan(777777, 77777, 1750, 7, "2026-05-15")
        self.assertEqual(p.rows[-1].balance_after, 0)
        self.assertTrue(all(a.balance_after > b.balance_after for a, b in zip(p.rows, p.rows[1:], strict=False)))

    def test_zero_rate_is_allowed(self):
        p = plan(600000, 100000, 0, 5, "2026-02-01")
        self.assertEqual(p.markup, 0)
        self.assertEqual(p.monthly, 100000)

    def test_refusals(self):
        for args, why in (
            ((1200000, 1300000, 1500, 10, "2026-12-13"), "المقدم أكبر من السعر"),
            ((1200000, 1200000, 1500, 10, "2026-12-13"), "المقدم يغطي السعر"),
            ((0, 0, 1500, 10, "2026-12-13"), "سعر صفر"),
            ((1200000, 0, 10001, 10, "2026-12-13"), "نسبة أكبر من 100"),
            ((1200000, 0, 1500, 1, "2026-12-13"), "شهر واحد"),
            ((1200000, 0, 1500, 25, "2026-12-13"), "أكثر من 24 شهرًا"),
            ((1200000, 0, 1500, 10, "2026-13-01"), "تاريخ غير صالح"),
        ):
            with self.assertRaises(InstalmentError, msg=why):
                plan(*args)


class DatesTest(unittest.TestCase):
    def test_plain_months(self):
        self.assertEqual(add_months("2026-01-15", 1), "2026-02-15")
        self.assertEqual(add_months("2026-01-15", 12), "2027-01-15")

    def test_a_day_past_the_month_end_clamps(self):
        """31 January plus one month is 28 February, not an impossible date."""
        self.assertEqual(add_months("2026-01-31", 1), "2026-02-28")
        self.assertEqual(add_months("2026-01-31", 3), "2026-04-30")
        self.assertEqual(add_months("2028-01-31", 1), "2028-02-29")

    def test_december_rolls_over(self):
        self.assertEqual(add_months("2026-12-31", 1), "2027-01-31")


class MarkupTest(unittest.TestCase):
    def test_half_up_in_piasters(self):
        self.assertEqual(markup_of(100, 50), 1)       # 0.5 piaster rounds up
        self.assertEqual(markup_of(100, 49), 0)


if __name__ == "__main__":
    unittest.main()
