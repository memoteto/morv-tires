"""Central controls: combo arrow from the theme, dialogs use the screen height, list tables stay quiet."""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class CentralControlsTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_theme_draws_one_combo_arrow_from_an_existing_file(self):
        import re
        from pathlib import Path
        from morv.ui import theme
        for dark in (False, True):
            css = theme.qss(dict(theme.colors(dark), fs=14))
            paths = re.findall(r"QComboBox::down-arrow \{ image:url\(([^)]+)\)", css)
            self.assertEqual(len(paths), 1)
            self.assertTrue(Path(paths[0]).exists())
            self.assertIn("QComboBox#statementFilter::down-arrow { image:none", css)   # keeps its own indicator

    def test_tall_dialog_uses_the_screen_height_not_two_thirds(self):
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QLineEdit
        from morv.ui.modal import Modal
        from morv.ui.widgets import dispose
        dlg = Modal("نافذة طويلة")
        self.addCleanup(dispose, dlg, self.app)
        for _ in range(40):
            dlg.body.addWidget(QLineEdit())
        dlg.show()
        self.app.processEvents()
        screen_h = QGuiApplication.primaryScreen().availableGeometry().height()
        self.assertGreater(dlg.height(), screen_h * 2 // 3 + 10)
        self.assertLessEqual(dlg.height(), dlg.maximumHeight())

    def test_short_dialog_keeps_its_natural_height(self):
        from PySide6.QtWidgets import QLineEdit
        from morv.ui.modal import Modal
        from morv.ui.widgets import dispose
        dlg = Modal("نافذة قصيرة")
        self.addCleanup(dispose, dlg, self.app)
        dlg.body.addWidget(QLineEdit())
        dlg.show()
        self.app.processEvents()
        self.assertLess(dlg.height(), 300)

    def test_list_tables_have_no_grid_and_leading_titles(self):
        from PySide6.QtCore import Qt
        from morv.ui.listpage import ListPage
        from morv.ui.widgets import dispose

        class Sample(ListPage):
            columns = [("الاسم", lambda r: r[1])]

            def fetch(self, con, query, filter_key, offset):
                return []

            def build_preview(self, row_id):
                pass

            def open_dialog(self, row_id):
                pass

        page = Sample(None)
        self.addCleanup(dispose, page, self.app)
        self.assertFalse(page.view.showGrid())
        self.assertTrue(page.view.horizontalHeader().defaultAlignment() & Qt.AlignmentFlag.AlignLeading)
