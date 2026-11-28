"""No widget may be shown before it has a parent, since a parentless show flashes a native window."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.qt_app import get_app, require_qt


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

    def test_every_section_can_be_opened_in_one_window(self):
        from morv.ui.shell import NAV, MainWindow
        from morv.ui.widgets import dispose
        window = MainWindow(self.session, self.app)
        window.health_timer.stop()
        self.addCleanup(dispose, window, self.app)
        window.show()
        for key, *_ in NAV:
            window.go_key(key)
            self.app.processEvents()
        self.assertEqual(window.stack.count(), len(NAV))

    def test_the_installments_header_buttons_are_not_shown_before_the_page_owns_them(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from morv.ui.installments_page import InstallmentsPage
        self.assertTrue(self.session.can("installments.manage"),
                        "the manager must hold this, or these buttons prove nothing")
        page = InstallmentsPage(self.session)
        self.addCleanup(page.deleteLater)
        buttons = page.extra_actions()
        self.assertTrue(buttons, "the page is expected to offer header actions")
        for b in buttons:
            with self.subTest(button=b.text()):
                self.assertIsNone(b.parentWidget(), "extra_actions hands back unparented buttons")
                self.assertFalse(b.isVisible(), "a parentless button turns into a native window and flashes")
        host = QWidget()
        self.addCleanup(host.deleteLater)
        lay = QVBoxLayout(host)
        for b in buttons:
            lay.addWidget(b)
        host.show()
        self.app.processEvents()
        for b in buttons:
            with self.subTest(button=b.text()):
                self.assertTrue(b.isVisible(), "a permitted button must still appear once it is in a layout")
