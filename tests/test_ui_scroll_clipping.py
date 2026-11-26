"""No scroll area in the main pages or key dialogs may clip its content sideways."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class ScrollAreaClippingTest(unittest.TestCase):
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

    def clipped(self, root) -> list:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QScrollArea
        found = []
        for area in root.findChildren(QScrollArea):
            content = area.widget()
            if content is None or not area.isVisibleTo(root):
                continue
            needs = content.minimumSizeHint().width()
            has = area.viewport().width()
            off = area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if (off and needs > has) or area.horizontalScrollBar().isVisible():
                found.append(f"{area.objectName() or type(content).__name__}: content needs {needs}px, viewport {has}px"
                             f" [{self.widest(content)}]")
        return found

    @staticmethod
    def widest(content) -> str:
        """Names the widgets that force the width, so a failure points at its cause."""
        from PySide6.QtWidgets import QWidget
        rows = []
        for w in content.findChildren(QWidget):
            mw = w.minimumSizeHint().width()
            if mw >= 240 and w.isVisibleTo(content):
                label = getattr(w, "text", lambda: "")() if hasattr(w, "text") else ""
                rows.append((mw, f"{type(w).__name__}#{w.objectName()} '{str(label)[:20]}' {mw}px"))
        return "; ".join(t for _, t in sorted(rows, reverse=True)[:8])

    def test_main_pages_do_not_clip_content_at_common_sizes(self):
        from PySide6.QtTest import QTest
        from morv.infra import settings
        from morv.ui.shell import NAV, MainWindow
        from morv.ui.widgets import dispose
        problems = []
        # The sidebar's state changes the page width by 172px, so both states are checked
        for collapsed in ("1", "0"):
            settings.put(self.con, "ui.sidebar_collapsed", collapsed)
            win = MainWindow(self.session, self.app)
            win.health_timer.stop()
            self.addCleanup(dispose, win, self.app)
            win.show()
            for width, height in ((1366, 768), (1920, 1080)):
                win.resize(width, height)
                self.app.processEvents()
                for key, _title, _icon, _phase in NAV:
                    win.go_key(key)
                    self.app.processEvents()
                    # Let deferred layout and scrollbar events settle before measuring.
                    QTest.qWait(0)
                    self.app.processEvents()
                    side = "collapsed" if collapsed == "1" else "open"
                    problems += [f"{key} @{width}x{height} sidebar {side}: {c}" for c in self.clipped(win)]
                    if key == "settings":
                        page = win.pages[key]
                        for route in ("shop", "printing"):
                            self.assertTrue(page.open_page(route))
                            for _ in range(8):
                                self.app.processEvents()
                            problems += [f"settings/{route} @{width}x{height} sidebar {side}: {c}"
                                         for c in self.clipped(win)]
                        page.show_hub()
            win.close()
        self.assertEqual(problems, [], "scroll areas clip their content sideways")

    def test_key_dialogs_do_not_clip_content(self):
        from morv.ui.widgets import dispose
        from tools.ui_audit import _dialogs
        item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-1','TR-1','tr-1',100,100,100) RETURNING id").fetchone()[0]
        problems = []
        for name, make in _dialogs(self.session, item):
            dlg = make()
            self.addCleanup(dispose, dlg, self.app)
            dlg.show()
            self.app.processEvents()
            problems += [f"{name}: {c}" for c in self.clipped(dlg)]
        self.assertEqual(problems, [], "dialog scroll areas clip their content sideways")
