"""No widget may be shown before it has a parent, since a parentless show flashes a native window."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.qt_app import get_app, require_qt


def _parentless_show_watcher():
    import shiboken6
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QWidget

    class ParentlessShowWatcher(QObject):
        """A widget shown while parentless that later gains a parent was never meant to be a window."""

        def __init__(self):
            super().__init__()
            self.seen = []

        def eventFilter(self, obj, event):
            if (event.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                    and shiboken6.isValid(obj) and obj.parentWidget() is None):
                text = obj.text()[:40] if hasattr(obj, "text") else ""
                self.seen.append((obj, f"{type(obj).__name__}({text})"))   # Read now: Qt may delete it later
            return False

        def offenders(self) -> list[str]:
            found = set()
            for widget, label in self.seen:
                if not shiboken6.isValid(widget):
                    continue                 # Qt destroyed it; touching the wrapper crashes on Windows
                if widget.parentWidget() is None:
                    continue                 # A window on purpose: a modal, a menu, or the main window
                found.add(label)
            return sorted(found)

    return ParentlessShowWatcher()


@require_qt()
class TransientWindowTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(patch.dict(os.environ, {"MORV_DATA_DIR": str(self.tmp)}))
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.user = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.user)
        self.app = get_app()

    def test_a_notice_waits_for_a_parent_before_it_turns_visible(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from morv.ui.widgets import Notice
        n = Notice()
        n.show_message("تنبيه للمستخدم")
        self.assertFalse(n.isVisible(), "a parentless notice becomes a top-level window and flashes on screen")
        host = QWidget()
        self.addCleanup(host.deleteLater)
        QVBoxLayout(host).addWidget(n)
        host.show()
        self.app.processEvents()
        self.assertTrue(n.isVisible(), "the notice must still appear once it sits in a layout")
        self.assertEqual(n.text(), "تنبيه للمستخدم")

    def test_an_emptied_notice_is_hidden_again(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from morv.ui.widgets import Notice
        host = QWidget()
        self.addCleanup(host.deleteLater)
        n = Notice()
        QVBoxLayout(host).addWidget(n)
        host.show()
        n.show_message("خطأ")
        self.app.processEvents()
        self.assertTrue(n.isVisible())
        n.show_message("")
        self.assertFalse(n.isVisible(), "clearing the text must hide the notice as it always did")

    def test_navigating_every_section_shows_nothing_before_it_is_parented(self):
        from morv.ui.shell import NAV, MainWindow
        from morv.ui.widgets import dispose
        self.assertTrue(self.session.can("installments.manage"),
                        "the manager must hold this, or the installments buttons prove nothing")
        watch = _parentless_show_watcher()
        window = None
        self.app.installEventFilter(watch)
        try:
            window = MainWindow(self.session, self.app)
            window.health_timer.stop()
            window.show()
            for key, *_ in NAV:
                window.go_key(key)
                self.app.processEvents()
            found = watch.offenders()
        finally:
            # Off before teardown: the filter would inspect widgets Qt is in the middle of destroying
            self.app.removeEventFilter(watch)
            if window is not None:
                dispose(window, self.app)
        self.assertEqual(found, [], "these were shown before anything owned them")
