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
        self.con, self.admin = con, admin
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

    def test_reference_geometry_matrix(self):
        from PySide6.QtCore import QPoint, QRect
        from morv.infra import items
        from morv.ui import theme

        cases = 0
        with patch.object(items, "set_field", wraps=items.set_field) as save:
            for font in theme.FONT_SIZES:
                self.win.set_font(theme.DEFAULT_FONT, font)
                for dark in (False, True):
                    if self.win.dark != dark:
                        self.win.toggle_theme()
                    for collapsed in (False, True):
                        if self.win.collapsed != collapsed:
                            self.win.toggle_sidebar()
                        for width, height in ((1920, 1080), (1093, 614), (1366, 768)):
                            self.win.setMinimumSize(min(1024, width - 40), min(640, height - 80))
                            self.win.resize(width, height - 40)
                            for group in ("common", *items.KINDS):
                                self.tab.kind.setCurrentIndex(self.tab.kind.findData(group))
                                self.settle()
                                cases += 1
                                with self.subTest(font=font, dark=dark, collapsed=collapsed,
                                                  size=(width, height), group=group):
                                    self.assertEqual((self.win.width(), self.win.height()), (width, height - 40))
                                    self.assertEqual(self.tab.kind.currentData(), group)
                                    self.assertFalse(self.table.horizontalScrollBar().isVisible())
                                    for col in (0, 1):
                                        self.assert_column_fits(col)
                                    header = self.table.horizontalHeader()
                                    for col in range(self.table.columnCount()):
                                        self.assertGreaterEqual(self.table.columnWidth(col), header.sectionSizeHint(col))
                                        self.assertGreaterEqual(self.table.columnWidth(col),
                                                                self.table.sizeHintForColumn(col))
                                    for row in range(self.table.rowCount()):
                                        self.assertGreaterEqual(self.table.rowHeight(row), 36)
                                        self.assertGreaterEqual(self.table.rowHeight(row), self.table.sizeHintForRow(row))
                                    button = self.tab.add_btn
                                    rect = QRect(button.mapTo(self.tab, QPoint()), button.size())
                                    self.assertTrue(button.isVisible())
                                    self.assertTrue(self.tab.rect().contains(rect))
                                    self.assertGreaterEqual(button.width(), button.minimumSizeHint().width())
                                    self.assertGreaterEqual(button.height(), button.minimumSizeHint().height())
                                    self.assertLess(rect.bottom(), self.table.geometry().top())
                                    self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().maximum())
                                    self.assertEqual(QRect(button.mapTo(self.tab, QPoint()), button.size()), rect)
            save.assert_not_called()
        self.assertEqual(cases, 288)

    def test_field_rules_and_custom_types(self):
        from PySide6.QtCore import Qt
        from morv.infra import items
        from morv.ui.inventory_page import CustomFieldDialog
        from morv.ui.widgets import dispose

        checked = Qt.CheckState.Checked
        unchecked = Qt.CheckState.Unchecked
        for group, ftype in zip(("common", *items.KINDS), ("text", "num", "sel", "bool"), strict=True):
            self.tab.kind.setCurrentIndex(self.tab.kind.findData(group))

            def accept_custom(dialog, kind=ftype):
                dialog.type.setCurrentIndex(dialog.type.findData(kind))
                self.assertEqual(dialog.options_field.isHidden(), kind != "sel")
                dialog.name.setText("Test " + kind)
                dialog.opts.setText("one | two")
                dialog.go()
                return dialog.result()

            with patch.object(CustomFieldDialog, "exec", accept_custom):
                self.tab.add_custom()
            fields = [f for f in items.fields(self.con, group) if f.kind == group]
            custom = next(f for f in fields if f.label == "Test " + ftype)
            self.assertEqual(custom.ftype, ftype)
            self.assertEqual(custom.options, ["one", "two"] if ftype == "sel" else [])
            for index, field_data in enumerate(fields):
                if field_data.builtin:
                    self.assertFalse(self.table.item(index, 0).icon().isNull())
                    for col in (2, 3):
                        self.assertFalse(self.table.item(index, col).flags() & Qt.ItemFlag.ItemIsEnabled)
                    for col in (4, 5):
                        self.assertTrue(self.table.item(index, col).flags() & Qt.ItemFlag.ItemIsEnabled)
            row = next(i for i, f in enumerate(fields) if f.key == custom.key)
            self.table.setCurrentCell(row, 0)
            for col, state in ((2, unchecked), (3, checked), (4, checked), (5, checked), (2, unchecked)):
                self.table.item(row, col).setCheckState(state)
                saved = next(f for f in items.fields(self.con, group) if f.key == custom.key)
                if col == 3:
                    self.assertTrue(saved.show and saved.required)
                if col == 4:
                    self.assertTrue(saved.print)
                if col == 5:
                    self.assertTrue(saved.filter)
                if col == 2:
                    self.assertFalse(saved.show or saved.required)
                self.assertEqual(self.table.currentRow(), row)
            self.assertTrue(saved.print and saved.filter)
        dlg = CustomFieldDialog(self.tab)
        self.addCleanup(dispose, dlg, self.app)
        dlg.go()
        self.assertEqual(dlg.label, "")
        dlg.name.setText("Choices")
        dlg.type.setCurrentIndex(dlg.type.findData("sel"))
        dlg.go()
        self.assertEqual(dlg.label, "")
        self.assertIn("مطلوبة", dlg.notice.text())

    def test_read_only_permission_and_revocation(self):
        from PySide6.QtCore import Qt
        from morv.infra import items, users
        from morv.ui.inventory_page import ItemFieldsTab
        from morv.ui.session import Session
        from morv.ui.widgets import dispose

        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", True)
        users.set_permission(self.con, self.admin, cashier.id, "items.manage", False)
        tab = ItemFieldsTab(Session(self.con, cashier))
        self.addCleanup(dispose, tab, self.app)
        tab.kind.setCurrentIndex(tab.kind.findData("tire"))
        row = next(i for i in range(tab.table.rowCount())
                   if tab.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == "season")
        original = next(f for f in items.fields(self.con, "tire") if f.key == "season")
        self.assertFalse(tab.add_btn.isEnabled())
        for col in range(2, 6):
            self.assertFalse(tab.table.item(row, col).flags() & Qt.ItemFlag.ItemIsEnabled)
        with patch("morv.ui.inventory_page.CustomFieldDialog") as dialog:
            tab.add_custom()
            dialog.assert_not_called()
        users.set_permission(self.con, self.admin, cashier.id, "items.manage", True)
        tab.reload()
        self.assertTrue(tab.add_btn.isEnabled())
        self.assertTrue(tab.table.item(row, 2).flags() & Qt.ItemFlag.ItemIsEnabled)
        users.set_permission(self.con, self.admin, cashier.id, "items.manage", False)
        tab.table.item(row, 2).setCheckState(Qt.CheckState.Unchecked if original.show else Qt.CheckState.Checked)
        self.assertEqual(next(f for f in items.fields(self.con, "tire") if f.key == "season"), original)
        self.assertFalse(tab.add_btn.isEnabled())
        self.assertFalse(tab.table.item(row, 2).flags() & Qt.ItemFlag.ItemIsEnabled)
