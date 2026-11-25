"""The printed document follows design/Shop.dc.html: column order, gated lines, sheet and roll."""
import os
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.qt_app import get_app, require_qt


def _shop(**kw):
    from morv.infra.shop import Shop
    base = dict(name="مورف للإطارات", activity="إطارات وبطاريات", phone="01000000000",
                phone2="01222222222", whatsapp="01000000000", address="المنصورة",
                hours="9 ص - 10 م", thanks="شكرًا لتعاملكم معنا", policy="الاستبدال خلال 14 يومًا")
    base.update(kw)
    return Shop(**base)


def _doc():
    from morv.ui.print_doc import Document, Line
    return Document(
        title="فاتورة بيع", number="S-2026-000154", date="2026-11-22",
        info=[("العميل", "أحمد محمود"), ("الموبايل", "01000000021"),
              ("المركبة", "تويوتا كورولا"), ("الدفع", "نقدي")],
        lines=[Line("إطار ميشلان Primacy 4", "205/55R16 · 91V", "2", 645000, 1290000)],
        totals=[("الإجمالي", "12,900.00"), ("الصافي", "12,900.00 ج.م")])


@require_qt()
class PrintedSheetTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(
            patch.dict(os.environ, {"MORV_DATA_DIR": str(self.tmp)}))

    def html(self, shop=None, shape_key="a4") -> str:
        from morv.ui.print_doc import SHAPES, document_html
        return document_html(shop or _shop(), _doc(), SHAPES[shape_key])

    def reads(self, shop=None, shape_key="a4") -> str:
        """What a reader sees: the markup that keeps numbers left-to-right is not part of the words."""
        bare = re.sub(r"<[^>]+>", "", self.html(shop, shape_key))
        return re.sub(r"[⁦⁩‎‏\xa0]", " ", bare)

    def test_the_sheet_carries_the_shop_the_document_and_its_lines(self):
        reads = self.reads()
        for text in ("مورف للإطارات", "إطارات وبطاريات", "فاتورة بيع", "S-2026-000154",
                     "العميل", "أحمد محمود", "المركبة", "الدفع", "إطار ميشلان Primacy 4",
                     "205/55R16", "الصافي", "شكرًا لتعاملكم معنا", "المنصورة"):
            self.assertIn(text, reads, text)

    def test_the_item_columns_keep_the_approved_order_from_the_serial_on_the_right(self):
        from morv.ui.print_doc import _COLS
        self.assertEqual(("الإجمالي", "السعر", "الكمية", "الصنف", "م"), tuple(t for t, _w in _COLS),
                         "columns are emitted left to right, so the serial comes last")
        # Inside the items table only: the serial header and the shop initial are the same letter
        table = self.html()[self.html().index("border='1'"):]
        places = [table.index(f">{title}<") for title, _w in _COLS]
        self.assertEqual(sorted(places), places, "the emitted order must match the approved order")

    def test_the_sheet_holds_no_column_the_design_does_not_have(self):
        reads = self.reads()
        for invented in ("الخصم", "فئة السعر", "المدفوع نقدًا", "الباقي للعميل"):
            self.assertNotIn(invented, reads, f"{invented} is not in the approved design")

    def test_the_colon_stays_beside_its_label_and_never_drifts_to_the_edge(self):
        html = self.html()
        self.assertIn("العميل:&#8207;", html, "a colon is a neutral: without a mark it jumps to the line's edge")
        for label in ("الموبايل", "المركبة", "الدفع"):
            self.assertIn(f"{label}:&#8207;", html, label)
        self.assertIn(">أحمد محمود</td>", html, "the value owns its cell, so no run spans the colon")

    def test_the_customer_pair_on_the_right_is_the_one_emitted_last(self):
        html = self.html()
        self.assertLess(html.index("الموبايل"), html.index("العميل"),
                        "an LTR wrapper puts the last cell on the right, where العميل belongs")

    def test_every_rule_is_a_painted_band_because_a_css_border_never_prints(self):
        from morv.ui.print_doc import BLUE, DASH, INK
        html = self.html()
        for color in (BLUE, INK, DASH):
            self.assertIn(f"bgcolor='{color}'", html, f"no band in {color}")
        for ignored in ("border-bottom", "border-top"):
            self.assertNotIn(ignored, html, "Qt's print engine draws no CSS border on a block")

    def test_the_net_total_is_the_only_blue_bold_row(self):
        from morv.ui.print_doc import BLUE
        html = self.html()
        net = html.rindex("الصافي")
        self.assertIn(BLUE, html[html.rindex("<tr", 0, net):net])
        first = html.index("الإجمالي", html.index("</table>", html.index("الصنف")))
        self.assertNotIn(BLUE, html[html.rindex("<tr", 0, first):first])

    def test_tax_ids_print_only_once_the_shop_says_it_is_registered(self):
        plain = self.reads(_shop(tax_id="123-456-789", commercial_register="12345"))
        self.assertNotIn("123-456-789", plain, "an unregistered shop prints no tax id")
        self.assertNotIn("س.ت", plain)
        registered = self.reads(_shop(tax_registered=True, tax_id="123-456-789",
                                      commercial_register="12345"))
        self.assertIn("123-456-789", registered)
        self.assertIn("رقم التسجيل الضريبي", registered)
        self.assertIn("س.ت", registered)

    def test_each_display_switch_takes_its_own_block_off_the_sheet(self):
        self.assertNotIn("01000000000", self.reads(_shop(show_whatsapp=False, phone="01222222222",
                                                         phone2="")))
        self.assertNotIn("9 ص - 10 م", self.reads(_shop(show_hours=False)))
        self.assertNotIn("الاستبدال خلال 14 يومًا", self.reads(_shop(show_policy=False)))
        self.assertNotIn("شكرًا", self.reads(_shop(thanks="")))

    def test_the_logo_square_carries_the_first_letter_and_the_switch_removes_it(self):
        from morv.ui.print_doc import BLUE
        square = f"bgcolor='{BLUE}'><tr><td align='center'"
        self.assertIn(square, self.html(), "no logo file, so the design's blue square stands in")
        self.assertIn(">م</td>", self.html().split(square)[1][:120], "the square carries the initial")
        self.assertNotIn(square, self.html(_shop(show_logo=False)))

    def test_uploaded_logo_is_rendered_and_can_be_hidden(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QColor, QImage, QTextDocument
        from morv.infra.shop import LOGO_FILE
        from morv.ui.print_doc import BLUE, LOGO_URL, SHAPES, render

        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("#123456"))
        self.assertTrue(image.save(str(self.tmp / LOGO_FILE)))

        square = f"bgcolor='{BLUE}'><tr><td align='center'"
        html = self.html()
        self.assertIn(f"src='{LOGO_URL}'", html)
        self.assertNotIn(square, html)

        text = render(_shop(), _doc(), SHAPES["a4"])
        held = text.resource(
            QTextDocument.ResourceType.ImageResource, QUrl(LOGO_URL))
        self.assertIsNotNone(held)
        self.assertFalse(held.isNull())
        self.assertEqual(held.pixelColor(0, 0), QColor("#123456"))

        hidden = self.html(_shop(show_logo=False))
        self.assertNotIn(f"src='{LOGO_URL}'", hidden)
        self.assertNotIn(square, hidden)

    def test_every_picture_is_a_resource_the_renderer_holds(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QTextDocument
        from morv.ui.print_doc import SHAPES, render
        sources = set(re.findall(r"<img src='([^']+)'", self.html()))
        self.assertTrue(sources, "the footer draws its icons")
        self.assertEqual([], [s for s in sources if s.startswith("file:")],
                         "a file path draws a broken-image box on paper")
        text = render(_shop(), _doc(), SHAPES["a4"])
        for src in sources:
            held = text.resource(QTextDocument.ResourceType.ImageResource, QUrl(src))
            self.assertTrue(held is not None and not held.isNull(), f"{src} is not registered")

    @staticmethod
    def _inked_rows(image) -> list[int]:
        return [y for y in range(image.height())
                if any(image.pixel(x, y) & 0xFFFFFF != 0xFFFFFF for x in range(0, image.width(), 3))]

    def test_the_contact_bar_is_pinned_to_the_foot_of_the_sheet(self):
        from morv.ui.print_doc import SHAPES, body_html, footer_html, preview_image
        shape = SHAPES["a4"]
        self.assertNotIn("morv:icon/pin", body_html(_shop(), _doc(), shape),
                         "the bar must not flow with the body, or it lands mid-page")
        self.assertIn("morv:icon/pin", footer_html(_shop(), shape))
        image = preview_image(_shop(), _doc(), shape)
        inked = self._inked_rows(image)
        self.assertGreater(max(inked), image.height() - 60, "the bar must reach the foot of the page")
        middle = [y for y in inked if image.height() // 3 < y < image.height() - 60]
        self.assertEqual([], middle, "a one-page document leaves the middle of the sheet empty")

    def test_a_roll_keeps_its_contact_lines_in_the_body(self):
        from morv.ui.print_doc import SHAPES, body_html, footer_html
        self.assertEqual("", footer_html(_shop(), SHAPES["r80"]), "a roll has no page foot to pin to")
        self.assertIn("morv:icon/pin", body_html(_shop(), _doc(), SHAPES["r80"]))

    def test_a_space_where_arabic_meets_latin_is_made_unbreakable(self):
        from morv.ui.print_doc import SHAPES, Document, Line, document_html
        doc = Document(title="فاتورة بيع", number="S-1", date="2026-11-23",
                       lines=[Line("بطارية ACDelco 70 أمبير", "", "1", 100, 100)],
                       totals=[("ض.ق.م 14%", "5,250.00"), ("الصافي", "42,750.00 ج.م")])
        # Tags and the order-pinning marks sit between the words, so read what the eye reads
        html = re.sub(r"<[^>]+>|[\u202a\u202c]", "", document_html(_shop(), doc, SHAPES["a4"]))
        for joined in ("ض.ق.م\u00a014%", "42,750.00\u00a0ج.م", "بطارية\u00a0ACDelco", "70\u00a0أمبير"):
            self.assertIn(joined, html, f"a plain space here is dropped when printing: {joined!r}")
        self.assertIn("ACDelco 70", html, "between two Latin runs the space is in no danger")

    def test_it_leaves_ordinary_spaces_and_the_markup_alone(self):
        from morv.ui.print_doc import SHAPES, document_html
        html = document_html(_shop(), _doc(), SHAPES["a4"])
        self.assertIn("أحمد محمود", html, "a space between two Arabic words needs no protection")
        self.assertNotIn("\u00a0", html.split(">")[0], "attributes must not be touched")
        self.assertIn("cellspacing='0' cellpadding='0'", html)

    def test_vertical_space_is_a_row_because_a_block_margin_is_ignored(self):
        html = self.html()
        self.assertNotIn("margin-top", html, "Qt's print engine draws no margin on a block")
        self.assertIn("العميل:&#8207;&nbsp;", html,
                      "a neutral colon needs a strong right-to-left neighbour, then its own space")

    def test_a_date_or_a_code_keeps_its_groups_in_order(self):
        from morv.ui.print_doc import SHAPES, Document, Line, document_html
        doc = Document(title="فاتورة بيع", number="S-1", date="2026-11-23",
                       lines=[Line("بطارية", "ضمان حتى 2027-11-22", "1", 100, 100),
                              Line("إطار", "205/55R16 · 91V", "2", 100, 200)],
                       totals=[("الصافي", "42,750.00 ج.م")])
        html = document_html(_shop(), doc, SHAPES["a4"])
        for value in ("2027-11-22", "205/55R16", "42,750.00"):
            self.assertIn(f"\u202a{value}\u202c", html,
                          f"{value} reverses group by group inside an Arabic line")
        self.assertNotIn("\u202aأمبير", html, "Arabic words are left alone")
        self.assertNotIn("<span dir='ltr'>2027", html, "Qt's layout does not read dir on an inline span")

    def test_money_prints_as_pounds_and_stays_left_to_right(self):
        self.assertIn("12,900.00", self.reads())
        self.assertNotIn("1290000", self.reads())
        self.assertIn("dir='ltr'", self.html(), "numbers in an Arabic line must be marked left-to-right")

    def test_a_document_without_lines_prints_its_words_instead_of_an_empty_table(self):
        from morv.ui.print_doc import SHAPES, Document, document_html
        receipt = Document(title="سند قبض", number="RC-000045", date="2026-11-22",
                           info=[("العميل", "شركة النيل")], totals=[("المبلغ المستلم", "15,000.00")],
                           note="سدادًا من حساب العميل")
        html = document_html(_shop(), receipt, SHAPES["a4"])
        self.assertIn("سدادًا من حساب العميل", html)
        self.assertNotIn("الصنف", html)


@require_qt()
class PrintedRollTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def html(self, key="r80", shop=None) -> str:
        from morv.ui.print_doc import SHAPES, document_html
        return document_html(shop or _shop(), _doc(), SHAPES[key])

    def test_a_roll_stacks_each_line_and_uses_no_column_table(self):
        html = self.html().replace("\u00a0", " ")
        self.assertNotIn("border='1'", html, "a roll is too narrow for a bordered column table")
        self.assertNotIn(">م<", html, "a roll carries no serial column")
        from morv.ui.print_doc import DASH
        self.assertIn(f"bgcolor='{DASH}'", html, "the design separates a roll's blocks with rules")
        for text in ("مورف للإطارات", "فاتورة بيع", "إطار ميشلان Primacy 4", "الصافي"):
            self.assertIn(text, html, text)

    def test_the_two_rolls_are_narrower_than_the_sheets_and_set_smaller_type(self):
        from morv.ui.print_doc import SHAPES
        self.assertTrue(SHAPES["r80"].roll and SHAPES["r58"].roll)
        self.assertFalse(SHAPES["a4"].roll or SHAPES["a5"].roll)
        self.assertLess(SHAPES["r58"].width_mm, SHAPES["r80"].width_mm)
        self.assertLess(SHAPES["r80"].width_mm, SHAPES["a5"].width_mm)
        self.assertLess(SHAPES["r58"].scale, SHAPES["r80"].scale)
        self.assertLess(SHAPES["a5"].scale, SHAPES["a4"].scale)


@require_qt()
class PrintedPaperTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(
            patch.dict(os.environ, {"MORV_DATA_DIR": str(self.tmp)}))

    def test_every_shape_writes_a_pdf_with_content(self):
        from morv.ui.print_doc import SHAPES, write_pdf
        for key in SHAPES:
            path = self.tmp / f"{key}.pdf"
            write_pdf(_shop(), _doc(), str(path), key)
            self.assertEqual(b"%PDF-", path.read_bytes()[:5], f"{key} did not write a PDF")
            self.assertGreater(path.stat().st_size, 1000, f"{key} wrote an empty-looking PDF")

    def test_every_shape_keeps_its_own_paper_size(self):
        from PySide6.QtGui import QPageSize
        from morv.ui.print_doc import SHAPES, page_layout
        for key, shape in SHAPES.items():
            size = page_layout(shape).pageSize().size(QPageSize.Unit.Millimeter)
            self.assertAlmostEqual(shape.width_mm, size.width(), places=0, msg=key)
            self.assertAlmostEqual(shape.height_mm, size.height(), places=0, msg=key)

    def test_the_page_names_a_font_that_exists_or_it_prints_empty_boxes(self):
        from PySide6.QtGui import QFontDatabase
        from morv.ui.print_doc import SHAPES, render
        for key in SHAPES:
            family = render(_shop(), _doc(), SHAPES[key]).defaultFont().family()
            self.assertTrue(family, f"{key} left the family to chance")
            self.assertIn(family, QFontDatabase.families(),
                          f"{key} asks for {family}, which this machine does not have")


@require_qt()
class ShopPrintFieldsTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(
            patch.dict(os.environ, {"MORV_DATA_DIR": str(self.tmp)}))
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()

    def test_an_unregistered_shop_cannot_keep_tax_ids_on_file(self):
        from morv.infra import shop
        saved = shop.save(self.con, self.admin, shop.Shop(
            name="مورف", phone="01000000000", tax_id="123", commercial_register="9"))
        self.assertEqual("", saved.tax_id)
        self.assertEqual("", saved.commercial_register)

    def test_a_second_number_is_refused_unless_it_is_a_real_mobile(self):
        from morv.infra import shop
        with self.assertRaises(shop.ShopError):
            shop.save(self.con, self.admin, shop.Shop(name="مورف", phone="01000000000", phone2="12"))

    def test_every_printed_field_survives_a_save_and_a_load(self):
        from morv.infra import shop
        shop.save(self.con, self.admin, shop.Shop(
            name="مورف للإطارات", activity="إطارات", phone="01000000000", phone2="01222222222",
            whatsapp="01000000000", address="المنصورة", hours="9-10", website="morv.example",
            tax_registered=True, tax_id="123-456-789", commercial_register="12345",
            thanks="شكرًا", policy="الاستبدال خلال 14 يومًا", show_hours=False, paper="r80"))
        saved = shop.load(self.con)
        self.assertEqual("إطارات", saved.activity)
        self.assertEqual("01222222222", saved.phone2)
        self.assertEqual("123-456-789", saved.tax_id)
        self.assertEqual("الاستبدال خلال 14 يومًا", saved.policy)
        self.assertEqual("r80", saved.paper)
        self.assertTrue(saved.tax_registered)
        self.assertFalse(saved.show_hours)
        self.assertTrue(saved.show_policy, "a switch left alone keeps its default")
