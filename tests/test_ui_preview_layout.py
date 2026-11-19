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

            def fetch(self, con, query, filter_key, offset):
                return [(1, "الأول"), (2, "الثاني"), (3, "بلا أزرار")]

            def build_preview(self, row_id):
                self.heading(f"عنوان {row_id}", f"وصف {row_id}")
                for i in range(50 if row_id == 1 else 2):
                    self.kv(f"بيان {i}", f"قيمة {i}")
                self.kv("التكلفة", "1,500.00")
                self.kv("الملاحظات", "هذا شرح عربي طويل يمكن تقسيمه بين الأسطر عند ضيق المكان")
                self.kv("حد الآجل", "لا يسمح بالآجل")
                self.kv("تذكيرات واتساب", "لم يوافق")
                self.kv("فواتير الشراء", "1 فاتورة · 100,000.00 جنيه")
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


    def test_preview_uses_split_and_compact_modes(self):
        from morv.ui.tokens import LAYOUT
        self.page.resize(1280, 520)
        self.page.show_preview_id(1)
        self.app.processEvents()
        self.assertTrue(self.page.list_panel.isVisibleTo(self.page))
        self.assertGreaterEqual(self.page.list_panel.width(), LAYOUT["list_min"])
        self.assertGreaterEqual(self.page.preview.width(), LAYOUT["preview_min"])
        self.page.resize(880, 520)
        self.app.processEvents()
        self.assertFalse(self.page.list_panel.isVisibleTo(self.page))
        self.assertGreater(self.page.preview.width(), 700)
        self.page.hide_preview()
        self.app.processEvents()
        self.assertTrue(self.page.list_panel.isVisibleTo(self.page))
        self.assertFalse(self.page.preview.isVisibleTo(self.page))
        self.page.resize(1280, 520)
        self.page.show_preview_id(2)
        self.app.processEvents()
        self.assertTrue(self.page.list_panel.isVisibleTo(self.page))

    def test_wider_preview_keeps_list_usable_and_scroll_gutter(self):
        from morv.ui.tokens import LAYOUT
        self.page.resize(1280, 600)
        self.page.show_preview_id(1)
        self.app.processEvents()
        self.assertGreaterEqual(self.page.preview.width(), 500)
        self.assertLessEqual(self.page.preview.width(), LAYOUT["preview_max"])
        self.assertGreaterEqual(self.page.list_panel.width(), LAYOUT["list_min"])
        margins = self.page.preview_layout.contentsMargins()
        self.assertEqual(margins.left(), LAYOUT["preview_scroll_gutter"])
        self.assertEqual(margins.right(), LAYOUT["preview_scroll_end_gap"])
        self.assertGreater(margins.left(), margins.right())
        self.assertGreater(self.page.preview_scroll.verticalScrollBar().maximum(), 0)

    def test_values_wrap_only_when_they_are_text(self):
        from PySide6.QtWidgets import QLabel
        from morv.ui.tokens import LAYOUT
        self.page.show_preview_id(1)
        self.app.processEvents()
        labels = self.page.preview_body.findChildren(QLabel)
        numeric = next(w for w in labels if w.text().endswith("1,500.00"))
        prose = next(w for w in labels if w.text().endswith("عند ضيق المكان"))
        key = next(w for w in labels if w.text() == "التكلفة")
        self.assertFalse(numeric.wordWrap())
        self.assertTrue(prose.wordWrap())
        self.assertEqual(key.maximumWidth(), LAYOUT["field_label_max"])

    def test_field_labels_keep_their_column_under_value_pressure(self):
        from PySide6.QtWidgets import QLabel
        from morv.ui.tokens import LAYOUT
        self.page.resize(1120, 520)
        self.page.show_preview_id(2)
        self.app.processEvents()
        self.assertGreaterEqual(self.page.preview.width(), LAYOUT["preview_min"])
        labels = self.page.preview_body.findChildren(QLabel)
        for title in ("حد الآجل", "تذكيرات واتساب", "فواتير الشراء"):
            key = next(w for w in labels if w.text() == title)
            self.assertEqual(key.width(), LAYOUT["field_label_max"])
            self.assertEqual(key.minimumWidth(), LAYOUT["field_label_max"])
            self.assertEqual(key.maximumWidth(), LAYOUT["field_label_max"])
            self.assertLessEqual(key.fontMetrics().horizontalAdvance(title), key.width())

    def test_filters_reflow_without_losing_buttons(self):
        from PySide6.QtWidgets import QPushButton
        for i in range(5):
            self.page.filter_buttons[f"extra-{i}"] = QPushButton(f"اختيار طويل {i}")
        self.page.resize(700, 520)
        self.page._update_responsive_layout()
        self.app.processEvents()
        self.assertTrue(self.page._filter_layout_state[0])
        self.assertTrue(all(self.page.tools_layout.indexOf(b) >= 0
                            for b in self.page.filter_buttons.values()))


if __name__ == "__main__":
    unittest.main()
