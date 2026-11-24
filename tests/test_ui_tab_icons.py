"""Navigation icons and adaptive check-column headers retain their accessible titles."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class TabAndHeaderIconsTest(unittest.TestCase):
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

    def test_every_inventory_tab_has_an_icon(self):
        from morv.ui.inventory_page import InventoryPage
        from morv.ui.widgets import dispose
        page = InventoryPage(self.session)
        self.addCleanup(dispose, page, self.app)
        bare = [page.tabs.tabText(i) for i in range(page.tabs.count()) if page.tabs.tabIcon(i).isNull()]
        self.assertEqual([], bare, "these inventory tabs still have no icon")

    def test_a_lazily_built_inventory_tab_keeps_its_icon(self):
        from morv.ui.inventory_page import InventoryPage
        from morv.ui.widgets import dispose
        page = InventoryPage(self.session)
        self.addCleanup(dispose, page, self.app)
        # tabs 1 and 2 start as empty placeholders and are rebuilt on first selection
        for index in (1, 2):
            page.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertFalse(page.tabs.tabIcon(index).isNull(),
                             f"the tab {page.tabs.tabText(index)} lost its icon when it was built")

    def test_yes_no_columns_keep_titles_and_tooltips_when_wide(self):
        from PySide6.QtCore import Qt
        from morv.ui.inventory_page import ItemFieldsTab
        from morv.ui.widgets import dispose
        tab = ItemFieldsTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.resize(1100, 500)
        tab.show()
        for _ in range(12):
            self.app.processEvents()
        for col, (label, _) in ItemFieldsTab.CONTROLS.items():
            head = tab.table.horizontalHeaderItem(col)
            self.assertEqual(label, head.text())
            self.assertEqual(ItemFieldsTab.COLS[col], head.toolTip())
            self.assertEqual(ItemFieldsTab.COLS[col], head.data(Qt.ItemDataRole.AccessibleTextRole))

    def test_tabbed_pages_fit_on_one_row_at_1366_with_the_sidebar_open(self):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1366, 768)
        win.show()
        if win.collapsed:                 # the open sidebar is the narrow case, so measure that one
            win.toggle_sidebar()
        tight = []
        for key in ("inventory",):
            win.go_key(key)
            for _ in range(5):
                self.app.processEvents()
            tabs = win.stack.currentWidget().tabs
            bar = tabs.tabBar()
            # tabSizeHint is the width a tab wants; tabRect only says where it landed after Qt scrolls the bar
            needed = sum(bar.tabSizeHint(i).width() for i in range(bar.count()))
            print(f"TAB_BAR_FIT {key} tabs={bar.count()} needed={needed} room={tabs.width()}", flush=True)
            if needed > tabs.width():
                tight.append(f"{key}: tabs want {needed}px, bar has {tabs.width()}px")
        self.assertEqual([], tight, "tabs would hide behind scroll arrows")

    def test_compact_icons_and_wrapped_long_field_at_narrow_width(self):
        from PySide6.QtCore import Qt
        from morv.infra import items
        from morv.ui.inventory_page import ItemFieldsTab
        from morv.ui.widgets import dispose, muted_icon
        long_name = " ".join(["اسم حقل مخصص طويل"] * 20)
        key = items.add_custom_field(self.con, self.session.user, "common", long_name, "text")
        tab = ItemFieldsTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.resize(620, 500)
        tab.show()
        for _ in range(12):
            self.app.processEvents()
        table = tab.table
        for col, (_, icon_name) in ItemFieldsTab.CONTROLS.items():
            head = table.horizontalHeaderItem(col)
            self.assertEqual("", head.text())
            self.assertFalse(head.icon().isNull())
            self.assertEqual(head.icon().pixmap(16, 16).toImage(),
                             muted_icon(icon_name).pixmap(16, 16).toImage())
            self.assertEqual(ItemFieldsTab.COLS[col], head.toolTip())
            self.assertEqual(ItemFieldsTab.COLS[col], head.data(Qt.ItemDataRole.AccessibleTextRole))
            self.assertGreaterEqual(table.columnWidth(col), table.sizeHintForColumn(col))
            self.assertGreaterEqual(table.columnWidth(col), table.horizontalHeader().sectionSizeHint(col))
        row = next(r for r in range(table.rowCount())
                   if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == key)
        self.assertEqual(table.item(row, 0).text(), long_name)
        self.assertEqual(table.item(row, 0).toolTip(), long_name)
        self.assertFalse(table.horizontalScrollBar().isVisible())
        self.assertGreater(table.rowHeight(row), 36)
        self.assertGreaterEqual(table.rowHeight(row), table.sizeHintForRow(row))
