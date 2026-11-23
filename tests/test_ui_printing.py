"""Printing a saved invoice: the page carries the sale, and a printer failure never hides itself."""
import unittest

from tests.qt_app import get_app, require_qt


def _shop():
    from morv.infra.shop import Shop
    return Shop(name="عالم البطاريات", phone="01000000000", paper="a4")


def _sale(**kw):
    from morv.infra.sale_view import SaleLineView, SaleView
    base = dict(id=1, number="S-2026-000154", customer_name="أحمد محمود", sale_date="2026-11-23",
                subtotal=3750000, line_discount=0, invoice_discount=0, total=3750000,
                odometer=None, vehicle_plate="ق س ط 4826",
                payments=(("cash", 3750000, 3750000, None),),
                lines=(SaleLineView(1, "إطار ميشلان", 2, 645000, 0, 0, 1290000),
                       SaleLineView(2, "بطارية ACDelco", 1, 380000, 0, 0, 380000)),
                returned=0, returns=())
    base.update(kw)
    return SaleView(**base)


@require_qt()
class SaleDocumentTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_the_page_carries_the_customer_the_vehicle_and_how_it_was_paid(self):
        from morv.ui.printing import sale_document
        doc = sale_document(_sale(), _shop())
        self.assertEqual("فاتورة بيع", doc.title)
        self.assertEqual("S-2026-000154", doc.number)
        self.assertIn(("العميل", "أحمد محمود"), doc.info)
        self.assertIn(("المركبة", "ق س ط 4826"), doc.info)
        self.assertIn(("الدفع", "نقدي"), doc.info)
        self.assertEqual(2, len(doc.lines))
        self.assertEqual("إطار ميشلان", doc.lines[0].name)
        self.assertEqual(1290000, doc.lines[0].total)

    def test_a_discount_is_shown_or_the_customer_cannot_follow_the_arithmetic(self):
        from morv.ui.printing import sale_document
        plain = sale_document(_sale(), _shop())
        self.assertEqual(["الإجمالي", "الصافي"], [label for label, _v in plain.totals])
        cut = sale_document(_sale(invoice_discount=250000, total=3500000), _shop())
        self.assertEqual(["الإجمالي", "الخصم", "الصافي"], [label for label, _v in cut.totals])
        self.assertIn("2,500.00", dict(cut.totals)["الخصم"])

    def test_what_came_back_is_taken_off_the_net(self):
        from morv.ui.printing import sale_document
        doc = sale_document(_sale(returned=380000), _shop())
        self.assertIn(("المرتجع", "3,800.00"), doc.totals)
        self.assertEqual("33,700.00 ج.م", dict(doc.totals)["الصافي"])

    def test_the_paper_follows_the_shop_setting(self):
        from morv.ui.printing import shape_for
        self.assertEqual("a4", shape_for(_shop()).key)
        self.assertEqual("r80", shape_for(_shop_with("r80")).key)
        self.assertEqual("a4", shape_for(_shop_with("nonsense")).key, "an unknown paper falls back")

    def test_a_device_with_no_printer_says_so_instead_of_failing_quietly(self):
        from unittest import mock
        from PySide6.QtPrintSupport import QPrinterInfo
        from morv.ui import printing
        with mock.patch.object(QPrinterInfo, "defaultPrinter", return_value=QPrinterInfo()):
            with self.assertRaises(printing.PrintError) as caught:
                printing.send(_shop(), printing.sale_document(_sale(), _shop()))
        self.assertIn("لا توجد طابعة", str(caught.exception))


def _shop_with(paper: str):
    from morv.infra.shop import Shop
    return Shop(name="عالم البطاريات", phone="01000000000", paper=paper)


if __name__ == "__main__":
    unittest.main()
