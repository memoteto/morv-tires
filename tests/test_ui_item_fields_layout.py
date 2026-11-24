"""Regression checks for measured item-field column clipping and horizontal scrolling."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.qt_app import get_app, require_qt


@require_qt()
class ItemFieldsLayoutTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui import theme
        from morv.ui.session import Session
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"MORV_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        con = open_main_db(Path(tmp.name) / "fields-layout.db")
        self.addCleanup(con.close)
        admin = users.create_user(con, None, "admin", "Test Admin", "Admin12345", "manager")
        self.app = get_app()
        theme.load_bundled_fonts()
        self.win = MainWindow(Session(con, admin), self.app)
        self.win.health_timer.stop()
        self.addCleanup(theme.apply, self.app)
        self.addCleanup(dispose, self.win, self.app)
        self.win.show()
        self.win.go_key("settings")
        page = self.win.pages["settings"]
        self.assertTrue(page.open_page("item_fields"))
        self.tab = page.fields_tab
        self.table = self.tab.table

    def settle(self):
        for _ in range(12):
            self.app.processEvents()

    def arrange(self, width, height, font, group):
        from morv.ui import theme

        self.win.setMinimumSize(min(1024, width - 40), min(640, height - 80))
        if self.win.dark:
            self.win.toggle_theme()
        if self.win.collapsed:
            self.win.toggle_sidebar()
        self.win.set_font(theme.DEFAULT_FONT, font)
        self.win.resize(width, height - 40)
        index = self.tab.kind.findData(group)
        self.assertGreaterEqual(index, 0)
        self.tab.kind.setCurrentIndex(index)
        self.settle()
        self.assertEqual((self.win.width(), self.win.height()), (width, height - 40))
        self.assertEqual(self.tab.kind.currentData(), group)
        self.assertTrue(self.table.isVisible())
        self.assertGreater(self.table.rowCount(), 0)
        self.assertEqual(self.table.columnCount(), 6)

    def assert_column_fits(self, col):
        widths = [self.table.columnWidth(c) for c in range(self.table.columnCount())]
        needed = [self.table.sizeHintForColumn(c) for c in range(self.table.columnCount())]
        header = self.table.horizontalHeader()
        overflow = [
            (r, self.table.sizeHintForIndex(self.table.model().index(r, col)).width())
            for r in range(self.table.rowCount())
            if self.table.sizeHintForIndex(self.table.model().index(r, col)).width() > widths[col]
        ]
        self.assertFalse(
            overflow,
            f"COLUMN_CLIP col={col} viewport={self.table.viewport().width()} "
            f"widths={widths} needed={needed} minimum_section={header.minimumSectionSize()} "
            f"rows_and_needed_width={overflow}",
        )

    def test_common_examples_fit_at_1366_font14(self):
        self.arrange(1366, 768, 14, "common")
        self.assert_column_fits(1)

    def test_tire_names_fit_at_1093_font18(self):
        self.arrange(1093, 614, 18, "tire")
        with self.subTest(check="field names"):
            self.assert_column_fits(0)
        with self.subTest(check="horizontal scroll"):
            self.assertFalse(
                self.table.horizontalScrollBar().isVisible(),
                f"HORIZONTAL_SCROLL viewport={self.table.viewport().width()} "
                f"columns={self.table.horizontalHeader().length()} "
                f"minimum_section={self.table.horizontalHeader().minimumSectionSize()}",
            )
