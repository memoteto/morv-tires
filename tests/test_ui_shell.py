"""Shell tests: direction, navigation, theme persistence, sidebar collapse."""
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class ShellTest(unittest.TestCase):
    def setUp(self):
        import os
        import sys
        import traceback

        errors = []
        original_hook = sys.excepthook

        def capture(kind, value, tb):
            errors.append("".join(traceback.format_exception(kind, value, tb)))
            original_hook(kind, value, tb)

        self.enterContext(patch.object(sys, "excepthook", capture))
        self.addCleanup(self.assertEqual, errors, [])
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(
            patch.dict(os.environ, {"MORV_DATA_DIR": str(self.tmp)}))
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.user = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.user)
        self.app = get_app()

    def window(self):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        w = MainWindow(self.session, self.app)
        w.health_timer.stop()
        self.addCleanup(dispose, w, self.app)
        return w

    def test_sidebar_shows_the_logo_in_proportion_and_hides_it_when_collapsed(self):
        from morv.ui.shell import LOGO_HEIGHT
        w = self.window()
        w.show()
        shown = w.brand.pixmap()
        self.assertFalse(shown.isNull())
        self.assertEqual(round(shown.height() / shown.devicePixelRatio()), LOGO_HEIGHT)
        self.assertAlmostEqual(shown.width() / shown.height(), 939 / 487, delta=0.01)
        self.assertEqual(w.brand.text(), "", "the logo already says Morv Tires")
        w.collapsed = False
        w._apply_collapse()
        self.assertTrue(w.brand.isVisible())
        w.toggle_sidebar()
        self.assertFalse(w.brand.isVisible())

    def test_every_entry_fits_with_the_logo_at_1366x768_and_125_percent(self):
        from morv.infra import settings
        settings.put(self.con, "ui.sidebar_collapsed", "0")
        w = self.window()
        w.resize(1093, 614 - 40)
        w.show()
        self.app.processEvents()
        self.assertEqual(w.nav.verticalScrollBar().maximum(), 0, "an entry would need scrolling")

    def test_hidden_dashboard_does_not_reload_until_shown_again(self):
        from morv.ui import dashboard_page
        from morv.ui.widgets import dispose

        page = dashboard_page.DashboardPage(self.session)
        self.addCleanup(dispose, page, self.app)

        with patch.object(dashboard_page, "run_async",
                          wraps=dashboard_page.run_async) as fetch:
            page.show()
            page.hide()
            self.app.processEvents()
            fetch.assert_not_called()

            page.show()
            self.app.processEvents()
            fetch.assert_called_once()
            page.hide()

    def test_live_window_exits_cleanly(self):
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
        from PySide6.QtCore import Qt
        from morv.ui.shell import NAV
        w = self.window()
        self.assertEqual(self.app.layoutDirection(), Qt.LayoutDirection.RightToLeft)
        self.assertEqual(w.stack.count(), len(NAV))
        self.assertEqual(w.nav.count(), len(NAV))
        self.assertIn("settings", [k for k, *_ in NAV])  # backup lives inside settings
        from PySide6.QtWidgets import QStatusBar
        self.assertIsNone(w.findChild(QStatusBar))  # the status bar used to cut the sidebar off

    def test_navigation(self):
        w = self.window()
        w.go_key("settings")
        self.assertEqual(w.title.text(), "الإعدادات")
        self.assertEqual(w.stack.currentIndex(), w.nav.currentRow())
        with self.assertRaises(KeyError):
            w.go_key("not_a_page")

    def test_theme_persists(self):
        from morv.infra import settings
        w = self.window()
        self.assertFalse(w.dark)
        w.toggle_theme()
        self.assertEqual(settings.get(self.con, "ui.theme"), "dark")
        self.assertTrue(self.window().dark)

    def test_sidebar_collapse_persists(self):
        from morv.infra import settings
        from morv.ui.shell import SIDE_NARROW, SIDE_WIDE
        settings.put(self.con, "ui.sidebar_collapsed", "0")   # the CI screen is narrow, so it starts collapsed
        w = self.window()
        self.assertEqual(w.side.width(), SIDE_WIDE)
        w.toggle_sidebar()
        self.assertEqual(w.side.width(), SIDE_NARROW)
        self.assertEqual(w.nav.item(0).text(), "")
        self.assertTrue(self.window().collapsed)


class NavDataTest(unittest.TestCase):
    def test_nav_entries(self):
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
