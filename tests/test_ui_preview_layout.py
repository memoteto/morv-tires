"""Regression tests for the fixed list-preview header and action footer."""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class PreviewLayoutTest(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QPushButton
        from morv.ui.listpage import ListPage

        class SamplePage(ListPage):
            columns = [("السجل", lambda r: r[1])]

            def fetch(self, query, filter_key, offset):
                return [(1, "الأول"), (2, "الثاني"), (3, "بلا أزرار")]

            def build_preview(self, row_id):
                self.heading(f"عنوان {row_id}", f"وصف {row_id}")
                for i in range(50 if row_id == 1 else 2):
                    self.kv(f"بيان {i}", f"قيمة {i}")
                if row_id == 3:
                    return
                main = QPushButton(f"أساسي {row_id}")
                main.clicked.connect(lambda: self.action_hits.append(row_id))
                self.actions(main)
                if row_id == 1:
                    secondary = QPushButton("ثانوي")
                    secondary.setEnabled(False)
                    self.actions(secondary)

            def open_dialog(self, row_id):
                pass

        self.app = get_app()
        self.page = SamplePage(None)
        self.page.action_hits = []
        self.page.resize(1040, 520)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        from PySide6.QtCore import QEvent
        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_header_and_footer_are_outside_scroll(self):
        from PySide6.QtWidgets import QLabel
        self.page.show_preview_id(1)
        self.app.processEvents()
        self.assertEqual(self.page.preview_title.text(), "عنوان 1")
        self.assertEqual(self.page.preview_subtitle.text(), "وصف 1")
        self.assertIs(self.page.preview_title.parentWidget(), self.page.preview)
        self.assertIs(self.page.preview_footer.parentWidget(), self.page.preview)
        self.assertFalse(any(lbl.text() == "عنوان 1" for lbl in
                             self.page.preview_body.findChildren(QLabel)))
        self.assertEqual(self.page.preview_footer_layout.count(), 2)
        self.assertTrue(self.page.preview_footer.isVisibleTo(self.page))
        self.assertGreater(self.page.preview_scroll.verticalScrollBar().maximum(), 0)
        self.assertLess(self.page.preview_scroll.geometry().bottom(),
                        self.page.preview_footer.geometry().top())

    def test_changing_record_replaces_actions_and_resets_scroll(self):
        from PySide6.QtWidgets import QPushButton
        self.page.show_preview_id(1)
        self.app.processEvents()
        scrollbar = self.page.preview_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.assertGreater(scrollbar.value(), 0)

        self.page.show_preview_id(2)
        self.app.processEvents()
        self.assertEqual(scrollbar.value(), 0)
        self.assertEqual(self.page.preview_title.text(), "عنوان 2")
        self.assertEqual(self.page.preview_subtitle.text(), "وصف 2")
        self.assertEqual(self.page.preview_footer_layout.count(), 1)
        buttons = self.page.preview_footer_layout.itemAt(0).widget().findChildren(QPushButton)
        self.assertEqual([button.text() for button in buttons], ["أساسي 2"])
        buttons[0].click()
        self.assertEqual(self.page.action_hits, [2])

    def test_no_actions_hides_footer_and_close_works(self):
        self.page.show_preview_id(1)
        self.page.show_preview_id(3)
        self.app.processEvents()
        self.assertFalse(self.page.preview_footer.isVisibleTo(self.page))
        self.assertEqual(self.page.preview_footer_layout.count(), 0)
        self.page.preview_close.click()
        self.assertIsNone(self.page.current_id)
        self.assertFalse(self.page.preview.isVisibleTo(self.page))


if __name__ == "__main__":
    unittest.main()
