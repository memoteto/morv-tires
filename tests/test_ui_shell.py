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
        """قاعدة جديدة في مجلد مؤقت لكل اختبار، ومدير داخل."""
        import os
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.user = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.user)
        self.app = get_app()

    def tearDown(self):
        """إغلاق القاعدة وحذف المجلد المؤقت."""
        import os
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def window(self):
        """نافذة جديدة على الجلسة الحالية."""
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        w = MainWindow(self.session, self.app)
        w.health_timer.stop()
        self.addCleanup(dispose, w, self.app)
        return w

    def test_live_window_exits_cleanly(self):
        """نافذة كاملة تُدمَّر صراحةً ثم تخرج العملية برمز 0 (كانت تنهار على Windows عند الخروج)."""
        import os
        import subprocess
        import sys
        script = (
            "import os, sys; sys.path.insert(0, os.getcwd())\n"
            "from tests.qt_app import get_app\n"
            "from pathlib import Path\n"
            "app = get_app()\n"
            "from morv.infra import users\n"
            "from morv.infra.db.bootstrap import open_main_db\n"
            "from morv.ui.session import Session\n"
            "from morv.ui.shell import MainWindow\n"
            "from morv.ui.widgets import dispose\n"
            "con = open_main_db(Path(sys.argv[1]) / 'm.db')\n"
            "u = users.create_user(con, None, 'admin', 'x', 'Admin12345', 'manager')\n"
            "w = MainWindow(Session(con, u), app); w.health_timer.stop()\n"
            "dispose(w, app); con.close()\n"
            "sys.stdout.flush(); os._exit(0)\n")
        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ, MORV_DATA_DIR=str(self.tmp / "sub"))
        r = subprocess.run([sys.executable, "-c", script, str(self.tmp)], cwd=root, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_rtl_and_pages(self):
        """الاتجاه RTL للتطبيق كله، ولكل عنصر في الشريط صفحة."""
        from PySide6.QtCore import Qt
        from morv.ui.shell import NAV
        w = self.window()
        self.assertEqual(self.app.layoutDirection(), Qt.LayoutDirection.RightToLeft)
        self.assertEqual(w.stack.count(), len(NAV))
        self.assertEqual(w.nav.count(), len(NAV))
        self.assertIn("settings", [k for k, *_ in NAV])  # النسخ الاحتياطي داخل الإعدادات
        from PySide6.QtWidgets import QStatusBar
        self.assertIsNone(w.findChild(QStatusBar))  # شريط الحالة كان يقطع الشريط الجانبي

    def test_navigation(self):
        """اختيار صفحة يغيّر المحتوى والعنوان."""
        w = self.window()
        w.go_key("settings")
        self.assertEqual(w.title.text(), "الإعدادات")
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
        from morv.infra import settings
        from morv.ui.shell import SIDE_NARROW, SIDE_WIDE
        settings.put(self.con, "ui.sidebar_collapsed", "0")   # شاشة CI ضيقة فيبدأ مطويًا افتراضيًا
        w = self.window()
        self.assertEqual(w.side.width(), SIDE_WIDE)
        w.toggle_sidebar()
        self.assertEqual(w.side.width(), SIDE_NARROW)
        self.assertEqual(w.nav.item(0).text(), "")
        self.assertTrue(self.window().collapsed)


class NavDataTest(unittest.TestCase):
    """بيانات الشريط الجانبي (لا تحتاج Qt)."""

    def test_nav_entries(self):
        """مفاتيح وأسماء وأيقونات فريدة، وكل أيقونة مرسومة فعلًا.

        خطأ سابق: أيقونة الإعدادات كانت تشبه أيقونة الشمس (زر الوضع الفاتح)،
        فلا يُسمح لعنصر في الشريط باستخدام أيقونات الشريط العلوي.
        """
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "morv" / "ui").joinpath
        nav = ast.literal_eval(src("shell.py").read_text(encoding="utf-8").split("NAV = ", 1)[1].split("\n]\n", 1)[0] + "\n]")
        paths = ast.literal_eval(src("icons.py").read_text(encoding="utf-8").split("PATHS = ", 1)[1].split("\n}\n", 1)[0] + "\n}")
        for column in range(3):
            values = [entry[column] for entry in nav]
            self.assertEqual(len(values), len(set(values)), f"تكرار في العمود {column}")
        for _key, _title, icon_name, _phase in nav:
            self.assertIn(icon_name, paths)
            self.assertNotIn(icon_name, {"sun", "moon", "collapse", "expand"})
        self.assertNotIn("backup", [e[0] for e in nav])


if __name__ == "__main__":
    unittest.main()
