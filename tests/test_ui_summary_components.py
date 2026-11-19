"""Check centralized summary widgets and preserved legacy preview behavior."""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class SharedSummaryComponentsTest(unittest.TestCase):
    def setUp(self):
        from morv.ui.listpage import ListPage

        class Example(ListPage):
            columns = [("السجل", lambda r: r[1])]

            def fetch(self, con, query, filter_key, offset):
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

    def test_central_metric_captions_and_values_align_right(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFrame, QLabel
        metrics = [frame for frame in self.page.preview_body.findChildren(QFrame)
                   if frame.objectName() == "summaryMetric"]
        self.assertEqual(len(metrics), 2)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter
        for frame in metrics:
            caption = next(label for label in frame.findChildren(QLabel)
                           if label.objectName() == "muted")
            amount = next(label for label in frame.findChildren(QLabel)
                          if label.objectName() == "summaryMetricValue")
            self.assertEqual(caption.alignment(), right)
            self.assertEqual(amount.alignment(), right)
            self.assertFalse(amount.wordWrap())

    def test_record_headers_and_values_share_center_alignment(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QFrame, QLabel
        from morv.ui.tokens import LAYOUT

        record = next(frame for frame in self.page.preview_body.findChildren(QFrame)
                      if frame.objectName() == "summaryRecord")
        grid = record.layout().itemAt(1).layout()
        self.assertIsNotNone(grid)
        for col in range(LAYOUT["summary_record_columns"]):
            self.assertEqual(grid.columnStretch(col), 1)
        captions = record.findChildren(QLabel, "summaryRecordLabel")
        self.assertEqual(len(captions), 3)
        for caption in captions:
            value = caption.parentWidget().findChild(QLabel, "summaryRecordValue")
            self.assertIsNotNone(value)
            self.assertIs(value.parentWidget(), caption.parentWidget())
            self.assertEqual(caption.alignment(), Qt.AlignmentFlag.AlignCenter)
            self.assertEqual(value.alignment(), Qt.AlignmentFlag.AlignCenter)

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
        from morv.ui.listpage import SummaryDetailRow
        detail = SummaryDetailRow("رقم فاتورة المورد", "AUTO-VERY-LONG-INVOICE-REFERENCE-1234567890")
        detail.resize(250, 90)
        detail.show()
        self.app.processEvents()
        self.assertTrue(detail._stacked)
        self.assertIn("1234567890", detail.value_label.text())
        detail.resize(470, 90)
        self.app.processEvents()
        self.assertLessEqual(detail.key_label.maximumWidth(), 160)
        detail.close()
        detail.deleteLater()

    def test_short_arabic_detail_labels_do_not_wrap(self):
        from morv.ui.listpage import SummaryDetailRow
        for title, value in (("حد الآجل", "لا يُسمح بالآجل"),
                             ("مدة الائتمان", "0 يوم"),
                             ("تذكيرات واتساب", "لم يوافق")):
            row = SummaryDetailRow(title, value)
            row.resize(356, 52)
            row.show()
            self.app.processEvents()
            self.assertFalse(row._stacked, title)
            self.assertFalse(row.key_label.wordWrap(), title)
            self.assertGreaterEqual(
                row.key_label.width(), row.key_label.fontMetrics().horizontalAdvance(title),
                title)
            row.close()
            row.deleteLater()

    def test_schedule_uses_cards_in_narrow_previews(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        from morv.ui.listpage import SummaryAdaptiveTable
        table = self.page.summary_table(
            ["القسط", "الاستحقاق", "المبلغ", "الأصل", "الفائدة"],
            [("1", "2026-12-17", "1,731.35", "1,549.00", "232.35") for _ in range(10)])
        self.app.processEvents()
        wrapper = table.parentWidget()
        self.assertIsInstance(wrapper, SummaryAdaptiveTable)
        self.assertIs(wrapper.stack.currentWidget(), wrapper.cards_scroll)
        values = [label.text() for label in wrapper.cards_scroll.findChildren(QLabel)]
        self.assertIn("2026-12-17", values)
        self.assertIn("1,731.35", values)
        self.assertEqual(table.model().rowCount(), 10)
        self.assertEqual(wrapper.cards_scroll.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_table_only_keeps_fixed_header_and_alignments_in_narrow_preview(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableView
        table = self.page.summary_table(
            ["القسط", "الاستحقاق", "المبلغ", "الأصل", "الفائدة"],
            [("1", "2026-12-17", "1,731.35", "1,549.00", "232.35")],
            column_alignments=(Qt.AlignmentFlag.AlignCenter,) * 5,
            keep_table=True)
        self.app.processEvents()
        self.assertIs(table.parentWidget(), self.page.preview_body)
        self.assertIsInstance(table, QTableView)
        self.assertEqual(table.model().columnCount(), 5)
        self.assertTrue(table.horizontalHeader().isVisibleTo(self.page))
        self.assertEqual(table.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        for column in range(5):
            self.assertEqual(table.model().data(table.model().index(0, column),
                                                Qt.ItemDataRole.TextAlignmentRole),
                             Qt.AlignmentFlag.AlignCenter)
            self.assertEqual(table.model().headerData(column, Qt.Orientation.Horizontal,
                                                       Qt.ItemDataRole.TextAlignmentRole),
                             Qt.AlignmentFlag.AlignCenter)

    def test_adaptive_summary_table_restores_table_when_wide(self):
        from PySide6.QtWidgets import QLabel, QTableView, QVBoxLayout, QWidget
        from morv.ui.listpage import SummaryAdaptiveTable
        table = QTableView()
        table.setFixedHeight(270)
        cards = QWidget()
        layout = QVBoxLayout(cards)
        layout.addWidget(QLabel("اختبار البطاقات"))
        wrapper = SummaryAdaptiveTable(table, cards, 2)
        wrapper.resize(430, 240)
        wrapper.show()
        self.app.processEvents()
        self.assertIs(wrapper.stack.currentWidget(), wrapper.cards_scroll)
        wrapper.resize(660, wrapper.height())
        self.app.processEvents()
        self.assertIs(wrapper.stack.currentWidget(), table)
        wrapper.close()
        wrapper.deleteLater()

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
