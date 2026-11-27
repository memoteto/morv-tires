"""Audit display wraps long details and paginates without changing stored rows."""
import unittest

from tests.qt_app import require_qt
from tests import test_ui_settings_hub


@require_qt()
class AuditLogTest(unittest.TestCase):
    settle = test_ui_settings_hub.SettingsHubTest.settle

    def setUp(self):
        from PySide6.QtCore import Qt
        test_ui_settings_hub.SettingsHubTest.setUp(self)
        self.addCleanup(self.app.setLayoutDirection, self.app.layoutDirection())
        self.app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    def test_pagination_and_refresh_preserve_original_rows(self):
        from morv.infra import users
        from morv.infra.db.connection import transaction
        from morv.ui.settings_page import AuditTab
        from morv.ui.widgets import dispose

        with transaction(self.con):
            for i in range(425):
                users.audit(self.con, self.admin, "sale.post", "sale", i, f"Example {i}")
        before = users.audit_rows(self.con, 1000)
        tab = AuditTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        self.assertEqual(tab.model.rows, before[:200])
        tab.more_btn.click()
        self.assertEqual(tab.model.rows, before[:400])
        tab.more_btn.click()
        self.assertEqual(tab.model.rows, before)
        tab.more_btn.click()
        self.assertFalse(tab.more_btn.isEnabled())
        self.assertEqual(len({r[0] for r in tab.model.rows}), len(before))
        self.assertEqual(users.audit_rows(self.con, 1000), before)
        users.audit(self.con, self.admin, "opening.post", detail="New event")
        tab.refresh_btn.click()
        self.assertTrue(tab.more_btn.isEnabled())
        self.assertEqual(tab.model.rows, users.audit_rows(self.con, 200))

    def test_wrapping_translation_and_fixed_actions(self):
        from PySide6.QtCore import QPoint, QRect, Qt
        from morv.infra import users
        from morv.ui.settings_page import AuditTab
        from morv.ui.widgets import dispose

        detail = "تفاصيل عملية تجريبية " * 40
        users.audit(self.con, self.admin, "unknown.example", detail="Unknown action")
        users.audit(self.con, self.admin, "sale.post", detail=detail)
        before = users.audit_rows(self.con)
        tab = AuditTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.show()
        for width in (801, 1074, 1628):
            tab.resize(width, 482)
            self.settle()
            self.assertEqual(tab.view.horizontalScrollBar().maximum(), 0)
            observed = {
                "requested_width": width, "tab_size": tab.size().toTuple(),
                "viewport_size": tab.view.viewport().size().toTuple(),
                "column_widths": [tab.view.columnWidth(c) for c in range(tab.model.columnCount())],
                "row_height": tab.view.rowHeight(0), "row_hint": tab.view.sizeHintForRow(0),
                "minimum_row": tab.view.verticalHeader().minimumSectionSize(),
                "word_wrap": tab.view.wordWrap(), "font": tab.view.font().toString(),
                "reflow_pending": tab.columns.timer.isActive(),
                "first_row_detail_matches": tab.model.index(0, 3).data() == detail,
            }
            self.assertGreater(tab.view.rowHeight(0), 36, f"AUDIT_ROW_LAYOUT {observed}")
            self.assertGreaterEqual(tab.view.rowHeight(0), tab.view.sizeHintForRow(0),
                                    f"AUDIT_ROW_LAYOUT {observed}")
            self.assertEqual(tab.model.index(0, 3).data(), detail)
            self.assertEqual(tab.model.index(0, 3).data(Qt.ItemDataRole.ToolTipRole), detail)
            self.assertEqual(tab.model.index(0, 2).data(), "ترحيل بيع")
            self.assertEqual(tab.model.index(0, 2).data(Qt.ItemDataRole.ToolTipRole), "sale.post")
            self.assertEqual(tab.model.index(1, 2).data(), "unknown.example")
            self.assertEqual(tab.model.rows, before)
            for button in (tab.refresh_btn, tab.more_btn):
                rect = QRect(button.mapTo(tab, QPoint()), button.size())
                self.assertTrue(tab.rect().contains(rect))
                self.assertGreater(rect.top(), tab.view.mapTo(tab, QPoint()).y() + tab.view.height())
        self.assertEqual(users.audit_rows(self.con), before)
