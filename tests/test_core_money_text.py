"""Money and Arabic text tests."""
import unittest

from morv.core.money import MoneyError, fmt, parse_money, percent_of, to_latin
from morv.core.text import ltr, norm_ar, scanner_fix


class Money(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_money("1,234.5"), 123450)
        self.assertEqual(parse_money("١٬٢٣٤٫٥٠"), 123450)
        self.assertEqual(parse_money("0.01"), 1)

    def test_parse_rejects(self):
        for bad in ("", "abc", "1.234", "1.005"):
            with self.assertRaises(MoneyError):
                parse_money(bad)
        with self.assertRaises(MoneyError):
            parse_money(12)

    def test_fmt(self):
        self.assertEqual(fmt(123450), "1,234.50")
        self.assertEqual(fmt(-5), "-0.05")
        self.assertEqual(fmt(0), "0.00")
        self.assertEqual(to_latin("٠١٢٣"), "0123")

    def test_percent(self):
        self.assertEqual(percent_of(1000000, "15"), 150000)
        self.assertEqual(percent_of(333, "50"), 167)
        self.assertEqual(percent_of(100, "12.5"), 13)


class Text(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(norm_ar("إطار  بريدجستون"), norm_ar("اطار بريدجستون"))
        self.assertEqual(norm_ar("بطارية"), "بطاريه")
        self.assertEqual(norm_ar("٢٠٥/٥٥"), "205/55")

    def test_scanner(self):
        self.assertEqual(scanner_fix("ف000123"), "T000123")
        self.assertEqual(scanner_fix("٦٢٢١٢٣"), "622123")
        self.assertEqual(scanner_fix("لا0001"), "B0001")

    def test_ltr(self):
        self.assertEqual(ltr("59 MB"), "⁦59 MB⁩")
        self.assertEqual(ltr(""), "")


if __name__ == "__main__":
    unittest.main()
