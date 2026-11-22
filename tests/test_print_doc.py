"""The printed document carries the shop's head, its own lines and totals, on every paper shape."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


def _shop(**kw):
    from morv.infra.shop import Shop
    base = dict(name="مورف للإطارات", phone="01000000000", address="المنصورة")
    base.update(kw)
    return Shop(**base)


def _doc():
    from morv.ui.print_doc import Column, Document
    return Document(
        title="فاتورة بيع", number="SI-000123", date="2026-11-22",
        parties=[("العميل", "أحمد محمود")],
        columns=[Column("الصنف", "60%"), Column("الكمية", "15%"), Column("الإجمالي", "25%", money=True)],
        rows=[("إطار ميشلان 205/55R16", "2", 1290000)],
        totals=[("الإجمالي النهائي", 1290000, True)])


@require_qt()
class PrintDocumentTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def html(self, shop=None, shape_key="a4") -> str:
        from morv.ui.print_doc import SHAPES, document_html
        return document_html(shop or _shop(), _doc(), SHAPES[shape_key])

    def test_the_head_carries_the_shop_and_the_document_its_own_lines(self):
        html = self.html()
        for text in ("مورف للإطارات", "01000000000", "المنصورة", "فاتورة بيع", "SI-000123",
                     "أحمد محمود", "إطار ميشلان", "الإجمالي النهائي"):
            self.assertIn(text, html, text)

    def test_tax_and_register_print_only_once_the_shop_has_them(self):
        self.assertNotIn("رقم ضريبي", self.html())
        self.assertNotIn("سجل تجاري", self.html())
        filled = self.html(_shop(tax_id="123-456-789", commercial_register="12345"))
        self.assertIn("رقم ضريبي", filled)
        self.assertIn("123-456-789", filled)
        self.assertIn("سجل تجاري", filled)

    def test_the_footer_keeps_its_room_even_while_the_shop_has_not_written_it(self):
        from morv.ui.print_doc import FOOTER_MIN_HEIGHT_MM
        empty = self.html()
        self.assertIn(f"min-height: {FOOTER_MIN_HEIGHT_MM}mm", empty)
        written = self.html(_shop(invoice_footer="الضمان سنة على عيوب الصناعة"))
        self.assertIn("الضمان سنة على عيوب الصناعة", written)

    def test_money_prints_as_pounds_and_stays_left_to_right(self):
        html = self.html()
        self.assertIn("12,900.00", html, "money must print formatted, not in piasters")
        self.assertNotIn("1290000", html)
        self.assertIn("dir='ltr'", html, "numbers inside an Arabic line must be marked left-to-right")

    def test_a_roll_stacks_each_line_instead_of_using_columns(self):
        wide, narrow = self.html(shape_key="a4"), self.html(shape_key="80mm")
        self.assertIn("<th", wide, "the wide shape keeps a real table head")
        self.assertIn("display: none", narrow, "a roll is too narrow for column heads")
        self.assertIn("الكمية 2", narrow, "the roll labels each value instead of aligning columns")

    def test_every_shape_writes_a_pdf_with_content(self):
        from morv.ui.print_doc import SHAPES, write_pdf
        for key in SHAPES:
            path = self.tmp / f"{key}.pdf"
            write_pdf(_shop(), _doc(), str(path), key)
            self.assertTrue(path.exists(), key)
            head = path.read_bytes()[:5]
            self.assertEqual(b"%PDF-", head, f"{key} did not write a PDF")
            self.assertGreater(path.stat().st_size, 1000, f"{key} wrote an empty-looking PDF")

    def test_the_two_rolls_are_narrower_than_the_sheets(self):
        from morv.ui.print_doc import SHAPES
        self.assertTrue(SHAPES["80mm"].narrow and SHAPES["58mm"].narrow)
        self.assertFalse(SHAPES["a4"].narrow or SHAPES["a5"].narrow)
        self.assertLess(SHAPES["58mm"].width_mm, SHAPES["80mm"].width_mm)
        self.assertLess(SHAPES["80mm"].width_mm, SHAPES["a5"].width_mm)


@require_qt()
class ShopPrintFieldsTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()

    def test_the_settings_tab_saves_the_three_fields_the_printer_needs(self):
        from morv.infra import shop
        from morv.ui.settings_page import ShopTab
        from morv.ui.widgets import dispose
        tab = ShopTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.name.setText("مورف للإطارات")
        tab.phone.setText("01000000000")
        tab.tax_id.setText("123-456-789")
        tab.register_no.setText("12345")
        tab.invoice_footer.setPlainText("الضمان سنة")
        tab.save()
        saved = shop.load(self.con)
        self.assertEqual("123-456-789", saved.tax_id)
        self.assertEqual("12345", saved.commercial_register)
        self.assertEqual("الضمان سنة", saved.invoice_footer)
