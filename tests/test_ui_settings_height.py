"""No settings tab may need more height than the window gives it, or its rows overlap."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt

# A window on a 1366x768 screen is shorter than the screen: the title bar and the task bar take this
CHROME_PX = 40


@require_qt()
class SettingsTabHeightTest(unittest.TestCase):
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

    def test_no_settings_tab_is_squeezed_on_the_smallest_screen(self):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.show()
        win.resize(1366, 768 - CHROME_PX)
        win.go_key("settings")
        self.app.processEvents()
        page = win.pages["settings"]
        squeezed = []
        for key, tab in page.destinations.items():
            self.assertTrue(page.open_page(key))
            self.app.processEvents()
            layout = tab.layout()
            if layout is None:
                continue
            needs = layout.minimumSize().height()
            if needs > tab.height():
                squeezed.append(f"{page.title_for(key)}: needs {needs}px, has {tab.height()}px")
        self.assertEqual([], squeezed, "a squeezed tab draws its rows on top of each other")
