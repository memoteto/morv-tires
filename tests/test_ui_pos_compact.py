"""POS compact layout: shortcuts from anywhere, folded details still saved, approval reached unscrolled."""
import unittest

from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class PosCompactTest(PosFixture):
    def window_on_pos(self):
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1366, 768)
        win.show()
        win.go_key("pos")
        self.app.processEvents()
        return win, win.pages["pos"]

    def test_shortcuts_fire_while_focus_is_outside_the_page(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QShortcut
        _win, pos = self.window_on_pos()
        shortcuts = {s.key().toString(): s for s in pos.findChildren(QShortcut)}
        for key in ("F2", "F4", "F8", "F9"):
            self.assertEqual(shortcuts[key].context(), Qt.ShortcutContext.WindowShortcut, key)

    def test_f2_from_the_sidebar_focuses_the_item_search(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        win, pos = self.window_on_pos()
        win.activateWindow()
        from PySide6.QtWidgets import QAbstractButton
        side = next(b for b in win.findChildren(QAbstractButton) if b.isVisible() and not pos.isAncestorOf(b))
        side.setFocus()
        self.app.processEvents()
        QTest.keyClick(side, Qt.Key.Key_F2)
        self.app.processEvents()
        self.assertTrue(pos.item_search.hasFocus())

    def test_approve_button_is_normal_size_and_visible_at_1366_without_scrolling(self):
        from PySide6.QtCore import QPoint
        _win, pos = self.window_on_pos()
        self.assertEqual(pos.post_btn.objectName(), "primary")
        self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-9','خدمة','خدمه',50000,50000,50000)")
        pos._add_row(self.con.execute("SELECT id FROM items").fetchone()[0], "service", "خدمة")
        self.app.processEvents()     # with a line the quick-amount row appears, the tallest normal state
        y = pos.post_btn.mapTo(pos, QPoint(0, 0)).y()
        self.assertLessEqual(y + pos.post_btn.height(), pos.height(), "approval needs scrolling at 1366x768")

    def test_unbuilt_actions_and_the_hint_bar_are_gone(self):
        from PySide6.QtWidgets import QPushButton
        _win, pos = self.window_on_pos()
        shown = [b.text() for b in pos.findChildren(QPushButton) if b.isVisibleTo(pos)]
        for label in ("آجل", "حفظ كمسودة", "اعتماد وطباعة", "تعليق الفاتورة", "معاينة الطباعة"):
            self.assertNotIn(label, shown)
        self.assertFalse(hasattr(pos, "_build_hints"))

    def test_folded_details_are_saved_and_summarised(self):
        p = self.page()
        self.assertFalse(p.extra_box.isVisibleTo(p))
        self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',50000,50000,50000)")
        item = self.con.execute("SELECT id FROM items").fetchone()[0]
        p.odometer.setText("84500")
        p.plate.setText("ABC 123")
        p.note.setText("ملاحظة وهمية")
        self.assertIn("84500", p.extra_summary.text())
        p._add_row(item, "service", "خدمة")
        p.pay1_received.setText("500.00")
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        row = self.con.execute("SELECT odometer, vehicle_plate, notes FROM sales ORDER BY id DESC").fetchone()
        self.assertEqual(row, (84500, "ABC 123", "ملاحظة وهمية"))
        self.assertFalse(p.extra_box.isVisibleTo(p))
        self.assertEqual(p.extra_summary.text(), "")

    def test_open_details_show_all_three_fields(self):
        p = self.page()
        p.extra_toggle.setChecked(True)
        for w in (p.odometer, p.plate, p.note):
            self.assertTrue(w.isVisibleTo(p))


if __name__ == "__main__":
    unittest.main()
