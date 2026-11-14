"""اختبارات هيكل النافذة: الاتجاه، والتنقل، وحفظ الثيم وطي الشريط."""
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class ShellTest(unittest.TestCase):
    """النافذة الرئيسية على قاعدة وهمية مؤقتة."""

    def setUp(self):
        """قاعدة جديدة في مجلد مؤقت لكل اختبار."""
        from morv.infra.db.bootstrap import open_main_db
        self.tmp = Path(tempfile.mkdtemp())
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.app = get_app()

    def tearDown(self):
        """إغلاق القاعدة وحذف المجلد المؤقت."""
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def window(self):
        """نافذة جديدة على القاعدة الحالية."""
        from morv.ui.shell import MainWindow
        return MainWindow(self.con, self.app)

    def test_rtl_and_pages(self):
        """الاتجاه RTL للتطبيق كله، ولكل عنصر في الشريط صفحة."""
        from PySide6.QtCore import Qt
        from morv.ui.shell import NAV
        w = self.window()
        self.assertEqual(self.app.layoutDirection(), Qt.LayoutDirection.RightToLeft)
        self.assertEqual(w.stack.count(), len(NAV))
        self.assertEqual(w.nav.count(), len(NAV))
        self.assertIn("backup", [k for k, *_ in NAV])  # النسخ الاحتياطي شاشة ضرورية

    def test_navigation(self):
        """اختيار صفحة يغيّر المحتوى والعنوان."""
        w = self.window()
        w.go_key("backup")
        self.assertEqual(w.title.text(), "النسخ الاحتياطي")
        self.assertEqual(w.stack.currentIndex(), w.nav.currentRow())
        with self.assertRaises(KeyError):
            w.go_key("not_a_page")

    def test_theme_persists(self):
        """الثيم المختار يُحفظ ويعود عند فتح نافذة جديدة."""
        from morv.infra import settings
        w = self.window()
        self.assertFalse(w.dark)
        w.toggle_theme()
        self.assertEqual(settings.get(self.con, "ui.theme"), "dark")
        self.assertTrue(self.window().dark)

    def test_sidebar_collapse_persists(self):
        """طي الشريط يغيّر العرض ويخفي النصوص ويُحفظ."""
        from morv.ui.shell import SIDE_NARROW, SIDE_WIDE
        w = self.window()
        self.assertEqual(w.side.width(), SIDE_WIDE)
        w.toggle_sidebar()
        self.assertEqual(w.side.width(), SIDE_NARROW)
        self.assertEqual(w.nav.item(0).text(), "")
        self.assertTrue(self.window().collapsed)


if __name__ == "__main__":
    unittest.main()
