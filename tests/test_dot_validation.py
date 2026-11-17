"""DOT manufacturing-week regression tests without optional test dependencies."""
from datetime import date
from unittest import TestCase
from unittest.mock import patch

from morv.core import dot


class DotValidationTest(TestCase):
    def test_valid_dot_and_arabic_digits(self):
        values = (("4526", "4526"), ("4626", "4626"), ("4523", "4523"),
                  ("5323", "5323"), ("2020", "2020"), ("٢٣٢٤", "2324"))
        with patch("morv.core.dot.date", wraps=date) as mock_date:
            mock_date.today.return_value = date(2026, 11, 17)
            for value, expected in values:
                with self.subTest(value=value):
                    self.assertEqual(dot.parse_dot(value), expected)

    def test_invalid_and_future_dot_rejected(self):
        values = ("5426", "0026", "2045", "4527", "5325", "5320",
                  "4726", "202024", "ab26")
        with patch("morv.core.dot.date", wraps=date) as mock_date:
            mock_date.today.return_value = date(2026, 11, 17)
            for value in values:
                with self.subTest(value=value):
                    with self.assertRaises(dot.DotError):
                        dot.parse_dot(value)

    def test_current_week_accepted_only_after_start(self):
        with patch("morv.core.dot.date", wraps=date) as mock_date:
            mock_date.today.return_value = date(2026, 11, 14)
            with self.assertRaises(dot.DotError):
                dot.parse_dot("4626")
            mock_date.today.return_value = date(2026, 11, 15)
            self.assertEqual(dot.parse_dot("4626"), "4626")

    def test_week_53_starts_at_end_of_2023(self):
        with patch("morv.core.dot.date", wraps=date) as mock_date:
            mock_date.today.return_value = date(2023, 12, 30)
            with self.assertRaises(dot.DotError):
                dot.parse_dot("5323")
            mock_date.today.return_value = date(2023, 12, 31)
            self.assertEqual(dot.parse_dot("5323"), "5323")

    def test_original_mfg_key_order_unchanged(self):
        with patch("morv.core.dot.date", wraps=date) as mock_date:
            mock_date.today.return_value = date(2026, 11, 17)
            self.assertLess(dot.mfg_key("5223"), dot.mfg_key("0124"))
            self.assertEqual(dot.mfg_key("4526"), 2645)
