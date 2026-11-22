"""Tabs carry an icon beside their title, and yes/no columns carry theirs as an icon plus a tooltip."""
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

    def test_every_settings_tab_has_an_icon(self):
        from morv.ui.settings_page import SettingsPage
        from morv.ui.widgets import dispose
        page = SettingsPage(self.session)
        self.addCleanup(dispose, page, self.app)
        bare = [page.tabs.tabText(i) for i in range(page.tabs.count()) if page.tabs.tabIcon(i).isNull()]
        self.assertEqual([], bare, "these settings tabs still have no icon")

    def test_every_inventory_tab_has_an_icon(self):
        from morv.ui.inventory_page import InventoryPage
        from morv.ui.widgets import dispose
        page = InventoryPage(self.session)
        self.addCleanup(dispose, page, self.app)
        bare = [page.tabs.tabText(i) for i in range(page.tabs.count()) if page.tabs.tabIcon(i).isNull()]
        self.assertEqual([], bare, "these inventory tabs still have no icon")

    def test_yes_no_columns_show_an_icon_and_keep_their_title_as_a_tooltip(self):
        from morv.ui.inventory_page import ItemFieldsTab
        from morv.ui.widgets import dispose
        tab = ItemFieldsTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        for col in ItemFieldsTab.COL_ICONS:
            head = tab.table.horizontalHeaderItem(col)
            self.assertFalse(head.icon().isNull(), f"column {col} lost its icon")
            self.assertEqual("", head.text(), f"column {col} still spends width on a title")
            self.assertEqual(ItemFieldsTab.COLS[col], head.toolTip(), f"column {col} lost its tooltip")

    def test_the_settings_tabs_still_fit_on_one_row_at_1366(self):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1366, 768)
        win.show()
        win.go_key("settings")
        self.app.processEvents()
        tabs = win.stack.currentWidget().tabs
        for _ in range(5):
            self.app.processEvents()
        bar = tabs.tabBar()
        # tabSizeHint is what each tab wants; tabRect is what it got after Qt starts scrolling the bar
        needed = sum(bar.tabSizeHint(i).width() for i in range(bar.count()))
        room = tabs.width()
        print(f"TAB_BAR_FIT tabs={bar.count()} needed={needed} room={room} "
              f"widths={[bar.tabSizeHint(i).width() for i in range(bar.count())]}", flush=True)
        self.assertLessEqual(needed, room,
                             f"the tabs want {needed}px but the bar has {room}px, so some hide behind arrows")

    def test_the_icon_columns_are_narrower_than_the_titles_they_replaced(self):
        from PySide6.QtWidgets import QHeaderView
        from morv.ui.inventory_page import ItemFieldsTab
        from morv.ui.widgets import dispose
        tab = ItemFieldsTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.resize(900, 500)
        tab.show()
        self.app.processEvents()
        header = tab.table.horizontalHeader()
        for col, title in ((c, ItemFieldsTab.COLS[c]) for c in ItemFieldsTab.COL_ICONS):
            self.assertEqual(QHeaderView.ResizeMode.ResizeToContents, header.sectionResizeMode(col),
                             f"column {col} ({title}) would keep its old fixed width")
