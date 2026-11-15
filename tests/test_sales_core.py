"""اختبارات حسابات فاتورة البيع النقية (المرحلة 4أ): الأسعار والإيراد والتحصيل وسطور القيد بالقروش."""
import unittest

from morv.core import sales as calc


class SalesCoreTest(unittest.TestCase):
    """حسابات بلا قاعدة بيانات."""

    def test_line_total_integers_only(self):
        """الإجمالي = الكمية × السعر، وغير الصحيح أو غير الموجب أو bool مرفوض."""
        self.assertEqual(calc.line_total(4, 645000), 2580000)
        for qty, price in ((0, 100), (-1, 100), (1, 0), (1, 10.5), (1.0, 100), (True, 100), (1, True)):
            with self.assertRaises(calc.SaleCalcError, msg=f"{qty!r},{price!r}"):
                calc.line_total(qty, price)

    def test_price_column_by_level(self):
        """فئة العميل تحدد عمود السعر، وبلا عميل أو فئة مجهولة يُستعمل القطاعي."""
        self.assertEqual(calc.price_column("wholesale"), "price_wholesale")
        self.assertEqual(calc.price_column("super"), "price_super")
        self.assertEqual(calc.price_column(None), "price_retail")
        self.assertEqual(calc.price_column("x"), "price_retail")

    def test_revenue_by_account(self):
        """الإيراد يتجمّع على 4110 و4120 و4130 حسب النوع، ونوع مجهول مرفوض."""
        got = calc.revenue_by_account([("tire", 100), ("battery", 50), ("tire", 25), ("service", 10)])
        self.assertEqual(got, {"4110": 125, "4120": 50, "4130": 10})
        with self.assertRaises(calc.SaleCalcError):
            calc.revenue_by_account([("food", 1)])

    def test_split_payments(self):
        """المحصَّل ≤ الإجمالي والباقي آجل؛ وسيلة مجهولة أو مبلغ غير موجب أو زائد مرفوض."""
        self.assertEqual(calc.split_payments(1000, {"cash": 300, "card": 200}), (500, 500))
        self.assertEqual(calc.split_payments(1000, {}), (0, 1000))
        self.assertEqual(calc.split_payments(1000, {"transfer": 1000}), (1000, 0))
        for bad in ({"cash": 1001}, {"cash": 600, "card": 600}, {"cash": 0}, {"cash": -5}, {"cash": 1.5}, {"visa": 5}):
            with self.assertRaises(calc.SaleCalcError, msg=str(bad)):
                calc.split_payments(1000, bad)

    def test_entry_lines_balanced_and_revenue_once(self):
        """القيد متوازن، والإيراد دائن مرة واحدة بمجموعه، ولا سطر بصفر."""
        pays = {"cash": 300, "card": 295}
        rev = {"4110": 645, "4120": 300, "4130": 50}
        lines = calc.entry_lines(pays, 400, rev)
        self.assertEqual(sum(d for _, d, _ in lines), sum(c for _, _, c in lines))
        self.assertEqual(sum(c for _, _, c in lines), 995)
        self.assertEqual(sorted(a for a, d, _ in lines if d), ["1110", "1220", "1310"])
        self.assertTrue(all((d > 0) != (c > 0) for _, d, c in lines))
        self.assertNotIn("1310", [a for a, _, _ in calc.entry_lines({"cash": 995}, 0, rev)])

    def test_below_cost(self):
        """تحت التكلفة فقط إن كان الإجمالي أقل من التكلفة؛ الخدمة (تكلفة صفر) لا تُنبَّه."""
        self.assertTrue(calc.is_below_cost(99, 100))
        self.assertFalse(calc.is_below_cost(100, 100))
        self.assertFalse(calc.is_below_cost(1, 0))

    def test_credit_warning(self):
        """حد صفر = لا آجل (تنبيه)، وتجاوز الحد تنبيه، وداخل الحد أو بلا آجل لا تنبيه."""
        self.assertIsNone(calc.credit_warning(0, 0, 0))
        self.assertIn("غير مصرّح", calc.credit_warning(0, 0, 10))
        self.assertIn("يتجاوز", calc.credit_warning(100, 60, 50))
        self.assertIsNone(calc.credit_warning(100, 50, 50))


if __name__ == "__main__":
    unittest.main()
