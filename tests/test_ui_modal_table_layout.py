"""Layout regressions for the opt-in fixed-body table modal."""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class ModalTableLayoutTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()
        self.dialogs = []

    def tearDown(self):
        from PySide6.QtCore import QEvent
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_default_form_mode_preserves_outer_scrolling(self):
        from PySide6.QtWidgets import QLabel, QScrollArea
        from morv.ui.modal import Modal

        dialog = Modal("نموذج", width=460)
        self.dialogs.append(dialog)
        for i in range(65):
            dialog.body.addWidget(QLabel(f"حقل {i}"))
        dialog.add_footer("حفظ", dialog.accept)
        dialog.show()
        self.app.processEvents()
        self.assertFalse(dialog.table_mode)
        self.assertIsInstance(dialog.scroll, QScrollArea)
        self.assertIs(dialog.scroll.widget().layout(), dialog.body)
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(dialog.footer.isVisibleTo(dialog))

    def test_table_mode_keeps_header_body_controls_and_footer_fixed(self):
        from PySide6.QtGui import QStandardItemModel
        from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QTableView
        from morv.ui.modal import Modal

        dialog = Modal("كشف جدولي", width=700, table_mode=True, height=620)
        self.dialogs.append(dialog)
        heading = QLabel("تصفية النتائج")
        dialog.body.addWidget(heading)
        model = QStandardItemModel(120, 2, dialog)
        table = QTableView()
        table.setModel(model)
        dialog.body.addWidget(table, 1)
        dialog.add_footer_widget(QPushButton("إغلاق"))
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.table_mode)
        self.assertIsNone(dialog.scroll)
        self.assertEqual(dialog.findChildren(QScrollArea), [])
        self.assertIs(dialog.body.parentWidget().parentWidget(),
                      dialog.findChild(QFrame, "modalFrame"))
        self.assertTrue(dialog.footer.isVisibleTo(dialog))
        self.assertTrue(heading.isVisibleTo(dialog))
        self.assertGreater(table.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(table.geometry().top(), heading.geometry().bottom())
        self.assertLess(dialog.footer.geometry().top(), dialog.height())

        footer_before = dialog.footer.mapTo(dialog, dialog.footer.rect().topLeft())
        table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertEqual(dialog.footer.mapTo(dialog, dialog.footer.rect().topLeft()), footer_before)
        self.assertTrue(heading.isVisibleTo(dialog))

    def test_table_mode_height_never_exceeds_available_screen(self):
        from PySide6.QtGui import QGuiApplication
        from morv.ui.modal import Modal

        dialog = Modal("جدول", table_mode=True, height=5000)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        available = QGuiApplication.primaryScreen().availableGeometry().height() - 60
        self.assertLessEqual(dialog.height(), available)
        self.assertEqual(dialog.height(), min(5000, available))


if __name__ == "__main__":
    unittest.main()
