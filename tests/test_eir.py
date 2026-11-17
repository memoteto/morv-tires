"""Independent EIR reference examples and safety invariants; no database needed."""
import unittest
from datetime import date
from decimal import Decimal, localcontext

from morv.core.eir import EirError, calculate
from morv.core.installments import plan


class EirTest(unittest.TestCase):
    def test_exact_ten_percent_reference_with_annual_dates(self):
        # 5500 / 1.1 + 6050 / 1.1**2 = 10000, exactly.
        result = calculate(10000, "2026-01-01", [
            ("2027-01-01", 5500), ("2028-01-01", 6050),
        ])
        self.assertAlmostEqual(result.annual_rate, Decimal("0.1"), delta=Decimal("1e-30"))
        self.assertEqual([(r.interest, r.principal, r.closing_balance) for r in result.cashflows],
                         [(1000, 4500, 5500), (550, 5500, 0)])
        self.assertEqual(sum(m.interest for m in result.months), 1550)
        self.assertEqual(result.markup, 1550)

    def test_original_flat_instalments_are_unchanged(self):
        contract = plan(50000000, 200000, 1500, 10, "2026-12-13")
        payments = [(r.due_date, r.amount) for r in contract.rows]
        original = list(payments)
        result = calculate(contract.financed, "2026-11-13", payments)
        self.assertEqual(payments, original)
        self.assertEqual([(r.due_date, r.amount) for r in result.cashflows], payments)
        self.assertEqual(result.financed, contract.financed)
        self.assertEqual(result.markup, contract.markup)
        self.assertEqual(sum(r.interest for r in result.cashflows), contract.markup)
        self.assertEqual(sum(m.interest for m in result.months), contract.markup)
        self.assertEqual(result.cashflows[-1].closing_balance, 0)
        # Independent PV identity against decimal fractional-year discount factors.
        with localcontext() as ctx:
            ctx.prec = 45
            day0 = date.fromisoformat(result.contract_date)
            present = sum((Decimal(amount) / (1 + result.annual_rate) **
                          (Decimal((date.fromisoformat(due) - day0).days) / 365)
                          for due, amount in payments), Decimal(0))
            self.assertLess(abs(present - contract.financed), Decimal("0.00000001"))
        self.assertTrue(result.annual_rate > 0)

    def test_zero_markup_requires_no_interest(self):
        contract = plan(600000, 100000, 0, 5, "2026-12-13")
        result = calculate(contract.financed, "2026-11-13",
                           [(r.due_date, r.amount) for r in contract.rows])
        self.assertEqual(result.annual_rate, 0)
        self.assertTrue(all(r.interest == 0 for r in result.cashflows))
        self.assertTrue(all(m.interest == 0 for m in result.months))
        self.assertEqual(result.cashflows[-1].closing_balance, 0)

    def test_actual_days_include_leap_day_and_are_not_fixed_months(self):
        # March 2027 to March 2028 includes 29 February 2028: 366 actual days.
        result = calculate(10000, "2027-03-01", [
            ("2028-03-01", 5500), ("2029-03-01", 6050),
        ])
        self.assertEqual((date(2028, 3, 1) - date(2027, 3, 1)).days, 366)
        self.assertGreater(result.annual_rate, Decimal("0"))
        self.assertLess(result.annual_rate, Decimal("0.1"))
        self.assertEqual(sum(x.interest for x in result.months), 1550)
        self.assertEqual(result.cashflows[-1].closing_balance, 0)

    def test_monthly_accruals_and_rounding_reconcile_to_piaster(self):
        for months in (2, 3, 7, 12, 24):
            contract = plan(999999, 133333, 1234, months, "2026-03-31")
            result = calculate(contract.financed, "2026-02-15",
                               [(r.due_date, r.amount) for r in contract.rows])
            self.assertEqual(len(result.cashflows), months)
            self.assertEqual(sum(r.amount for r in result.cashflows), contract.instalments_total)
            self.assertEqual(sum(r.interest for r in result.cashflows), contract.markup)
            self.assertEqual(sum(r.principal for r in result.cashflows), contract.financed)
            self.assertEqual(sum(m.interest for m in result.months), contract.markup)
            self.assertEqual(result.cashflows[-1].closing_balance, 0)
            self.assertTrue(all(r.closing_balance >= 0 for r in result.cashflows))

    def test_zero_length_first_period_and_very_small_amounts(self):
        result = calculate(101, "2026-01-01", [
            ("2026-01-01", 30), ("2026-02-01", 40), ("2026-03-01", 41),
        ])
        self.assertEqual(result.cashflows[0].interest, 0)
        self.assertEqual(result.cashflows[-1].closing_balance, 0)
        self.assertEqual(sum(r.interest for r in result.cashflows), 10)
        self.assertEqual(sum(m.interest for m in result.months), 10)

    def test_far_future_month_end_is_safe(self):
        result = calculate(100, "9997-12-31", [
            ("9998-12-31", 55), ("9999-12-31", 66),
        ])
        self.assertEqual(result.cashflows[-1].closing_balance, 0)
        self.assertEqual(sum(x.interest for x in result.months), 21)

    def test_invalid_contract_flows_fail_closed(self):
        bad_cases = [
            (0, "2026-01-01", [("2026-02-01", 1), ("2026-03-01", 1)]),
            (True, "2026-01-01", [("2026-02-01", 1), ("2026-03-01", 1)]),
            (100, "2026-01-01", [("2026-02-01", 50)]),
            (100, "2026-01-01", [("2026-02-01", 50), ("2026-02-01", 60)]),
            (100, "2026-01-01", [("2025-12-01", 50), ("2026-03-01", 60)]),
            (100, "2026-01-01", [("2026-02-01", 30), ("2026-03-01", 40)]),
            (100, "2026-01-01", [("2026-02-01", 0), ("2026-03-01", 100)]),
            (100, "2026-01-01", [("2026-02-01", True), ("2026-03-01", 100)]),
            (100, "2026-01-01", [("2026-01-01", 100), ("2026-03-01", 1)]),
            (100, "2026-13-01", [("2026-02-01", 50), ("2026-03-01", 60)]),
            (100, "2026-01-01", [("20260201", 50), ("2026-03-01", 60)]),
        ]
        for args in bad_cases:
            with self.subTest(args=args), self.assertRaises(EirError):
                calculate(*args)


if __name__ == "__main__":
    unittest.main()
