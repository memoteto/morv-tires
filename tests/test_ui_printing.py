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

    def test_vat_totals_and_per_line_treatments_on_all_paper_shapes(self):
        from morv.ui import print_doc
        from tools.print_samples import _vat_sale

        for inclusive in (True, False):
            doc = _vat_sale(inclusive)
            with self.subTest(inclusive=inclusive):
                self.assertIn(("أساس السعر", "شامل الضريبة" if inclusive else "غير شامل الضريبة"), doc.info)
                self.assertEqual(doc.totals, [
                    ("الإجمالي قبل الخصم", "2,000.00"),
                    ("الخصم", "200.00"),
                    ("الإجمالي قبل الضريبة", "1,689.47" if inclusive else "1,800.00"),
                    ("ضريبة القيمة المضافة", "110.53" if inclusive else "126.00"),
                    ("الإجمالي شامل القيمة المضافة", "1,800.00 ج.م" if inclusive else "1,926.00 ج.م"),
                ])
                self.assertIn("ضريبة 14%", doc.lines[0].detail)
                self.assertEqual(doc.lines[0].discount, 10000)
                self.assertEqual(doc.lines[0].tax_amount, 11053 if inclusive else 12600)
                self.assertEqual(doc.lines[1].detail, "معفى")
                self.assertEqual(doc.lines[1].tax_amount, 0)
                for shape in print_doc.SHAPES.values():
                    with self.subTest(shape=shape.key):
                        rendered = print_doc.render(_shop(), doc, shape)
                        self.assertIn("ضريبة القيمة المضافة", rendered.toPlainText())
                        self.assertIn("الإجمالي قبل الضريبة", rendered.toPlainText())
                        self.assertIn("الإجمالي شامل القيمة المضافة", rendered.toPlainText())
                        self.assertIn("الضريبة", rendered.toPlainText())
                        image = print_doc.preview_image(_shop(), doc, shape)
                        self.assertFalse(image.isNull())

    def test_optional_invoice_identity_and_notes_are_in_document(self):
        from morv.ui.printing import sale_document
        sale = _sale(customer_mobile="01000000001", customer_address="المنصورة",
                     customer_tax_id="123456789", cashier_name="مدير",
                     recorded_at="2026-11-28 08:00:00", odometer=12000,
                     notes="تجربة <ملاحظة>")
        doc = sale_document(sale, _shop())
        for pair in (("الموبايل", "01000000001"), ("عنوان العميل", "المنصورة"),
                     ("رقم العميل الضريبي", "123456789"), ("الكاشير", "مدير"),
                     ("وقت التسجيل", "2026-11-28 08:00:00"), ("قراءة العداد", "12000"),
                     ("نقدي", "37,500.00")):
            self.assertIn(pair, doc.info)
        self.assertEqual(doc.remarks, sale.notes)

    def test_print_preview_fits_single_sheet_and_can_zoom(self):
        from morv.ui.printing import PrintPreview, sale_document
        dlg = PrintPreview(None, _shop(), sale_document(_sale(), _shop()))
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self.addCleanup(dlg.close)
        for _ in range(5):
            self.app.processEvents()
        self.assertIsNone(dlg.scroll)
        self.assertEqual(dlg.preview_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(dlg.preview_area.horizontalScrollBar().maximum(), 0)
        self.assertTrue(dlg.print_btn.isVisible())
        self.assertLessEqual(dlg.height(), dlg.screen().availableGeometry().height())
        self.assertLessEqual(dlg.width(), dlg.screen().availableGeometry().width())
        self.assertGreater(dlg.page.pixmap().height(), 0)
        dlg.zoom_btn.click()
        for _ in range(5):
            self.app.processEvents()
        self.assertTrue(dlg.zoom_btn.isChecked())
        dlg.zoom_btn.click()
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(dlg.preview_area.verticalScrollBar().maximum(), 0)

    def test_multi_page_preview_can_reach_last_sheet(self):
        from morv.ui.printing import PrintPreview, sale_document
        sale = _sale(lines=_sale().lines * 80)
        dlg = PrintPreview(None, _shop(), sale_document(sale, _shop()))
        self.addCleanup(dlg.deleteLater)
        dlg.show()
        self.addCleanup(dlg.close)
        self.app.processEvents()
        self.assertGreater(dlg.page_count, 1)
        self.assertTrue(dlg.next_btn.isEnabled())
        dlg.next_btn.click()
        self.assertEqual(dlg.page_index, 1)
        dlg._show_page(dlg.page_count - 1)
        self.assertFalse(dlg.next_btn.isEnabled())
        self.assertTrue(dlg.previous_btn.isEnabled())

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
