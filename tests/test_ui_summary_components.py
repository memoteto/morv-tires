"""Check centralized summary widgets and preserved legacy preview behavior."""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class SharedSummaryComponentsTest(unittest.TestCase):
    def setUp(self):
        from morv.ui.listpage import ListPage

        class Example(ListPage):
            columns = [("السجل", lambda r: r[1])]

            def fetch(self, query, filter_key, offset):
                return [(1, "الملخص")]

            def build_preview(self, row_id):
                self.heading("صنف تجريبي", "الكود T-100")
                self.summary_metrics([("الرصيد", "4"), ("القيمة الدفترية", "6,900.00")])
                self.summary_section("الأسعار", "cash")
                self.summary_details([("قطاعي", "2,500.00"), ("جملة", "2,350.00")])
                self.summary_section("تفاصيل الدفعات")
                self.summary_table(["DOT", "المتبقي", "التكلفة"],
                                   [("2324", "3", "1,500.00"), ("2024", "1", "1,800.00")])
                self.summary_note("الأسعار التاريخية منفصلة عن تقييم FIFO.")
                self.summary_records(
                    ["الصنف", "الكمية", "سعر الوحدة", "الإجمالي"],
                    [("1. إطار 205/55R16 طويل الاسم لقياس توزيع البنود", "2", "2,500.00", "5,000.00")])

            def open_dialog(self, row_id):
                pass

        self.app = get_app()
        self.page = Example(None)
        self.page.resize(1350, 750)
        self.page.show()
        self.page.show_preview_id(1)
        self.app.processEvents()

    def tearDown(self):
        from PySide6.QtCore import QEvent
        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_shared_sections_cards_table_and_note(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFrame, QLabel, QTableView
        p = self.page
        frames = p.preview_body.findChildren(QFrame)
        self.assertEqual(sum(f.objectName() == "summaryMetric" for f in frames), 2)
        self.assertEqual(sum(f.objectName() == "summarySection" for f in frames), 2)
        self.assertEqual(sum(f.objectName() == "summaryDetails" for f in frames), 1)
        self.assertEqual(sum(f.objectName() == "summaryNote" for f in frames), 1)
        table = p.preview_body.findChild(QTableView, "summaryTable")
        self.assertIsNotNone(table)
        self.assertEqual(table.model().rowCount(), 2)
        self.assertEqual(table.model().headerData(
            0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole), "DOT")
        self.assertEqual(table.model().data(table.model().index(1, 2)), "1,800.00")
        self.assertTrue(table.horizontalHeader().isVisibleTo(p))
        labels = p.preview_body.findChildren(QLabel)
        self.assertTrue(any(x.text() == "القيمة الدفترية" for x in labels))
        self.assertTrue(any(x.text().endswith("2,500.00") for x in labels))

    def test_record_cards_preserve_long_names_and_values(self):
        from PySide6.QtWidgets import QFrame, QLabel
        records = [f for f in self.page.preview_body.findChildren(QFrame)
                   if f.objectName() == "summaryRecord"]
        self.assertEqual(len(records), 1)
        labels = records[0].findChildren(QLabel)
        self.assertTrue(any("إطار 205/55R16" in label.text() and label.wordWrap()
                            for label in labels))
        self.assertEqual({label.text() for label in labels if label.objectName() == "summaryRecordValue"},
                         {"2", "2,500.00", "5,000.00"})

    def test_responsive_details_stack_long_values_without_data_loss(self):
        from PySide6.QtWidgets import QBoxLayout
        from morv.ui.listpage import SummaryDetailRow
        detail = SummaryDetailRow("رقم فاتورة المورد", "AUTO-VERY-LONG-INVOICE-REFERENCE-1234567890")
        detail.resize(250, 90)
        detail.show()
        self.app.processEvents()
        self.assertEqual(detail.row.direction(), QBoxLayout.Direction.TopToBottom)
        self.assertIn("1234567890", detail.value_label.text())
        detail.resize(470, 90)
        self.app.processEvents()
        self.assertLessEqual(detail.key_label.maximumWidth(), 160)
        detail.close()
        detail.deleteLater()

    def test_wide_schedule_table_uses_horizontal_scrolling(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QHeaderView
        table = self.page.summary_table(
            ["القسط", "الاستحقاق", "المبلغ", "الأصل", "الفائدة"],
            [("1", "2026-12-17", "1,731.35", "1,549.00", "232.35") for _ in range(10)])
        self.app.processEvents()
        self.assertEqual(table.model().columnCount(), 5)
        self.assertEqual(table.model().rowCount(), 10)
        self.assertEqual(table.horizontalHeader().sectionResizeMode(0),
                         QHeaderView.ResizeMode.ResizeToContents)
        self.assertEqual(table.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def test_summary_widgets_refresh_as_one_group(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QFrame
        self.page.show_preview_id(1)
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(len([f for f in self.page.preview_body.findChildren(QFrame)
                              if f.objectName() == "summaryMetric"]), 2)
        self.assertTrue(self.page.preview_footer.isHidden())
        self.assertIs(self.page.preview_title.parentWidget(), self.page.preview)

    def test_preview_width_uses_shared_tokens(self):
        from morv.ui.tokens import LAYOUT
        self.assertGreaterEqual(self.page.preview.width(), LAYOUT["preview_min"])
        self.assertLessEqual(self.page.preview.width(), LAYOUT["preview_max"])
        self.assertGreaterEqual(self.page.list_panel.width(), LAYOUT["list_min"])


if __name__ == "__main__":
    unittest.main()
