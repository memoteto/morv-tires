"""Diagnostic: locate the unpainted table-header strip seen in RTL previews.

Reports Qt widget geometry and actual rendered pixels, not just stylesheet text.
This is deliberately diagnostic; a visual fix requires evidence from its output.
"""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class HeaderGapDiagnosticTest(unittest.TestCase):
    def test_rtl_unoccupied_header_pixels_and_owner(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QHeaderView, QTableView
        from morv.ui.theme import palette, qss
        from morv.ui.tokens import DARK

        app = get_app()
        old_style = app.styleSheet()
        old_palette = app.palette()
        old_direction = app.layoutDirection()
        table = None
        try:
            t = {**DARK, "fs": 14}
            app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            app.setPalette(palette(t))
            app.setStyleSheet(qss(t))
            model = QStandardItemModel(12, 5)
            model.setHorizontalHeaderLabels(["القسط", "الاستحقاق", "المبلغ", "الأصل", "الفائدة"])
            for row in range(12):
                for col, val in enumerate(
                    (str(row + 1), "2026-12-17", "1,781.35", "1,549.00", "232.35")
                ):
                    model.setItem(row, col, QStandardItem(val))

            table = QTableView()
            table.setObjectName("summaryTable")
            table.setModel(model)
            table.verticalHeader().hide()
            header = table.horizontalHeader()
            header.setMinimumSectionSize(72)
            header.setMaximumSectionSize(250)
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            table.resize(760, 260)
            table.show()
            app.processEvents()
            app.processEvents()

            viewport = header.viewport()
            width = viewport.width()
            intervals = [
                (header.sectionViewportPosition(col),
                 header.sectionViewportPosition(col) + header.sectionSize(col))
                for col in range(model.columnCount())
            ]
            uncovered = [
                x for x in range(width)
                if not any(lo <= x < hi for lo, hi in intervals)
            ]
            self.assertTrue(uncovered, "Diagnostic requires unoccupied header space")
            # Select the middle of an uncovered run, away from adjacent section borders.
            runs = []
            start = last = uncovered[0]
            for x in uncovered[1:]:
                if x > last + 1:
                    runs.append((start, last))
                    start = x
                last = x
            runs.append((start, last))
            lo, hi = max(runs, key=lambda v: v[1] - v[0])
            x = (lo + hi) // 2
            y = max(2, min(6, viewport.height() // 2))
            image = viewport.grab().toImage()
            color = image.pixelColor(x, y)
            area = header.mapTo(table, viewport.mapTo(header, viewport.rect().topLeft()))
            child = table.childAt(area.x() + x, area.y() + y)
            child_type = type(child).__name__ if child else "none"
            corner = [
                (type(w).__name__, w.isVisible(), w.geometry().getRect())
                for w in table.findChildren(__import__(
                    "PySide6.QtWidgets", fromlist=["QAbstractButton"]
                ).QAbstractButton)
            ]
            print(
                "HEADER_GAP_DIAGNOSTIC "
                f"platform={app.platformName()} view={table.geometry().getRect()} "
                f"header={header.geometry().getRect()} viewport={viewport.geometry().getRect()} "
                f"sections={intervals} gap={(lo, hi)} "
                f"sample={(x, y)} pixel={color.name()} "
                f"child={child_type} corner_buttons={corner} "
                f"vscroll={table.verticalScrollBar().geometry().getRect()} "
                f"vscroll_visible={table.verticalScrollBar().isVisible()} "
                f"palette_base={t['surface']} expected_header={t['surface2']}",
                flush=True,
            )
            self.assertGreaterEqual(hi - lo + 1, 15)
            self.assertEqual(
                color.name().lower(), t["surface2"].lower(),
                "Unoccupied header must use the same background as header sections",
            )
        finally:
            if table is not None:
                table.close()
                table.deleteLater()
            app.setStyleSheet(old_style)
            app.setPalette(old_palette)
            app.setLayoutDirection(old_direction)
            app.processEvents()
