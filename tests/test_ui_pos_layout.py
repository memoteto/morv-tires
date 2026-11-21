"""POS layout: the approval buttons stay pinned outside every scroll area, on short windows too."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


class PosFixture(unittest.TestCase):
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
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, admin)
        self.app = get_app()

    def page(self, width=1366, height=600):
        from morv.ui.pos_page import PosPage
        from morv.ui.widgets import dispose
        p = PosPage(self.session)
        self.addCleanup(dispose, p, self.app)
        p.resize(width, height)
        p.show()
        self.app.processEvents()
        return p

    def inside_page(self, p, button):
        from PySide6.QtCore import QPoint
        top_left = button.mapTo(p, QPoint(0, 0))
        return (top_left.x() >= 0 and top_left.y() >= 0
                and top_left.x() + button.width() <= p.width() and top_left.y() + button.height() <= p.height())



@require_qt()
class PosPinnedActionsTest(PosFixture):
    def test_post_button_is_outside_every_scroll_area(self):
        from PySide6.QtWidgets import QScrollArea
        p = self.page()
        parent = p.post_btn.parentWidget()
        while parent is not None:
            self.assertNotIsInstance(parent, QScrollArea)
            parent = parent.parentWidget()

    def test_post_button_is_visible_on_a_short_window_even_with_the_split_open(self):
        p = self.page(1366, 560)
        p.split_btn.setChecked(True)
        self.app.processEvents()
        self.assertTrue(p.post_btn.isVisible())
        self.assertTrue(self.inside_page(p, p.post_btn))

    def test_post_button_keeps_its_slot_and_shortcut_label(self):
        p = self.page()
        self.assertEqual(p.post_btn.text(), "اعتماد وتحصيل")
        self.assertEqual(p.post_btn.objectName(), "primaryLarge")

    def test_summary_and_payment_cards_are_never_clipped(self):
        for width in (1280, 1366, 1920):
            with self.subTest(width=width):
                p = self.page(width, 700)
                self.assertGreaterEqual(p.side_scroll.viewport().width(), p.side_content.minimumSizeHint().width())
                self.assertFalse(p.side_scroll.horizontalScrollBar().isVisible())

@require_qt()
class PosCompactInvoiceTest(PosFixture):
    def test_optional_details_start_folded_and_open_on_demand(self):
        p = self.page()
        self.assertFalse(p.extra_box.isVisibleTo(p))
        p.extra_toggle.setChecked(True)
        self.assertTrue(p.extra_box.isVisibleTo(p))
        self.assertTrue(p.odometer.isVisibleTo(p) and p.plate.isVisibleTo(p) and p.note.isVisibleTo(p))

    def test_hidden_details_are_still_saved_with_the_invoice(self):
        p = self.page()
        self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',50000,50000,50000)")
        item = self.con.execute("SELECT id FROM items").fetchone()[0]
        p.odometer.setText("84500")
        p.plate.setText("ABC 123")
        p.note.setText("ملاحظة وهمية")
        self.assertFalse(p.extra_box.isVisibleTo(p))
        p._add_row(item, "service", "خدمة")
        p.pay1_received.setText("500.00")       # cash needs a received amount covering the total
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        row = self.con.execute("SELECT odometer, vehicle_plate, notes FROM sales ORDER BY id DESC").fetchone()
        self.assertEqual(row, (84500, "ABC 123", "ملاحظة وهمية"))

    def test_summary_shows_hidden_values_and_reset_folds_again(self):
        p = self.page()
        p.odometer.setText("84500")
        self.assertIn("84500", p.extra_summary.text())
        p.extra_toggle.setChecked(True)
        p._reset_after_post()
        self.assertFalse(p.extra_box.isVisibleTo(p))
        self.assertEqual(p.extra_summary.text(), "")

    def test_invoice_number_and_date_are_header_chips(self):
        p = self.page()
        self.assertTrue(p.number_edit.text().startswith("SI-"))
        self.assertRegex(p.sale_date.text(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
