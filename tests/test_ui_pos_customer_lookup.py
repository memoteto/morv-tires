"""POS customer lookup: customers beyond the preloaded first 300 are found by name or mobile."""
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
class PosCustomerLookupBeyond300Test(PosFixture):
    def setUp(self):
        super().setUp()
        for i in range(320):
            self.con.execute("INSERT INTO customers (name, name_norm, mobile, credit_limit) VALUES (?,?,?,0)",
                             (f"عميل {i:03d}", f"عميل {i:03d}", f"010{i:08d}"))
        self.con.execute("INSERT INTO customers (name, name_norm, mobile, credit_limit) "
                         "VALUES ('ياسر الأخير','ياسر الاخير','01099999999',0)")

    def test_preload_stops_at_300_but_a_typed_mobile_still_finds_the_customer(self):
        p = self.page()
        self.assertEqual(p.customer.count(), 301)              # cash customer plus the first 300
        self.assertEqual(p.customer.findText("ياسر الأخير"), -1)
        p.mobile.setText("01099999999")
        p._on_mobile_typed()
        self.assertEqual(p.customer.currentText(), "ياسر الأخير")
        self.assertEqual(p.notice.text(), "")

    def test_a_typed_name_beyond_the_preload_is_found(self):
        p = self.page()
        p.customer.lineEdit().setText("ياسر الأخير")
        p._on_customer_typed()
        self.assertEqual(p.customer.currentText(), "ياسر الأخير")
        self.assertEqual(p.customer.currentData()[2], "01099999999")

    def test_typing_loads_matches_so_the_completer_can_offer_them(self):
        p = self.page()
        p.customer.lineEdit().setText("ياسر")
        p._load_customer_matches("ياسر")
        self.assertGreaterEqual(p.customer.findText("ياسر الأخير"), 0)

    def test_an_unknown_mobile_still_warns_and_does_not_duplicate_items(self):
        p = self.page()
        before = p.customer.count()
        p.mobile.setText("01011112222")
        p._on_mobile_typed()
        self.assertIn("لا يوجد عميل مسجّل", p.notice.text())
        self.assertEqual(p.customer.count(), before)
