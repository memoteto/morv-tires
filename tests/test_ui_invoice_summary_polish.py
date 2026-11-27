"""Visual acceptance guards for the compact, readable invoice summary."""
from tests import test_ui_fixed_invoice_summary as fixtures
from tests.qt_app import require_qt


def contrast(foreground, background):
    """WCAG relative luminance contrast, compositing translucent foreground."""
    def linear(value):
        return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4

    bg = (background.redF(), background.greenF(), background.blueF())
    fg = (foreground.redF(), foreground.greenF(), foreground.blueF())
    alpha = foreground.alphaF()
    fg = tuple(a * alpha + b * (1 - alpha) for a, b in zip(fg, bg, strict=True))

    def luminance(rgb):
        return sum(linear(c) * w for c, w in zip(rgb, (.2126, .7152, .0722), strict=True))

    light, dark = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (light + .05) / (dark + .05)


@require_qt()
class InvoiceSummaryPolishTest(fixtures.PosFixture):
    settle = fixtures.FixedInvoiceSummaryTest.settle
    invoice = fixtures.FixedInvoiceSummaryTest.invoice
    visible_inside_page = fixtures.FixedInvoiceSummaryTest.visible_inside_page

    def assert_adjacent(self, purchase):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QFrame
        win, page = self.invoice(purchase=purchase)
        win.resize(1920, 1080)
        self.settle()
        area = page.invoice_layout
        self.assertEqual(area.columns, 2, "SETUP: wide invoice must have two columns")
        card = area.summary.findChild(QFrame, "card")
        self.assertIsNotNone(card)
        self.assertEqual(area.summary_area.verticalScrollBar().maximum(), 0,
                         "SETUP: normal summary should fit on large screen")
        gap = area.footer.mapTo(page, QPoint()).y() - (
            card.mapTo(page, QPoint()).y() + card.height())
        self.assertTrue(0 <= gap <= 24, f"POS4_SUMMARY_GAP: {gap}px")
        self.assertTrue(self.visible_inside_page(page, page.total_box))
        self.assertTrue(self.visible_inside_page(page, page.post_btn))

    def test_wide_sale_summary_has_no_large_gap(self):
        self.assert_adjacent(False)

    def test_wide_purchase_summary_has_no_large_gap(self):
        self.assert_adjacent(True)

    def assert_compact_row(self, purchase):
        from PySide6.QtCore import QPoint, QRect
        _, page = self.invoice(purchase=purchase)
        self.assertEqual(page.invoice_layout.columns, 1, "SETUP: narrow invoice")
        controls = [page.total_box, page.post_btn]
        if not purchase:
            controls.append(page.post_print_btn)
        rects = [QRect(w.mapTo(page, QPoint()), w.size()) for w in controls]
        for i, rect in enumerate(rects):
            self.assertTrue(self.visible_inside_page(page, controls[i]))
            for other in rects[i + 1:]:
                self.assertFalse(rect.intersects(other))
        self.assertLessEqual(max(r.top() for r in rects), min(r.bottom() for r in rects),
                             f"POS4_COMPACT_ROW: {rects}")
        self.assertIsNone(page.posted)

    def test_narrow_sale_total_and_buttons_share_a_row(self):
        self.assert_compact_row(False)

    def test_narrow_purchase_total_and_button_share_a_row(self):
        self.assert_compact_row(True)

    def test_approval_buttons_are_compact(self):
        win, page = self.invoice()
        win.resize(1366, 768)
        self.settle()
        for button in (page.post_btn, page.post_print_btn):
            self.assertLessEqual(button.height(), 36,
                                 f"POS4_BUTTON_HEIGHT: {button.text()} {button.height()}px")
            self.assertGreaterEqual(button.height(), button.fontMetrics().height())
            self.assertTrue(self.visible_inside_page(page, button))

    def test_dark_total_text_contrast(self):
        from PySide6.QtWidgets import QLabel
        win, page = self.invoice(purchase=True)
        win.resize(1366, 768)
        if not win.dark:
            win.toggle_theme()
        self.settle()
        box = page.total_box
        img = box.grab().toImage()
        bg = img.pixelColor(img.width() // 2, 5)
        for label in box.findChildren(QLabel):
            fg = label.palette().color(label.foregroundRole())
            ratio = contrast(fg, bg)
            self.assertGreaterEqual(ratio, 4.5,
                                    f"POS4_TOTAL_CONTRAST: {label.text()} {ratio:.2f}")

    def test_dark_disabled_purchase_placeholder_contrast(self):
        from PySide6.QtGui import QPalette
        win, page = self.invoice(purchase=True)
        win.resize(1366, 768)
        if not win.dark:
            win.toggle_theme()
        self.settle()
        field = page.tax      # discount/shipping are now enabled (P01/P02); tax stays disabled until P04
        self.assertFalse(field.isEnabled())
        self.assertEqual(field.placeholderText(), "0.00")
        self.assertEqual(field.text(), "")
        self.assertTrue(self.visible_inside_page(page, field))
        img = field.grab().toImage()
        bg = img.pixelColor(img.width() // 2, img.height() // 2)
        fg = field.palette().color(QPalette.ColorGroup.Disabled,
                                   QPalette.ColorRole.PlaceholderText)
        ratio = contrast(fg, bg)
        self.assertGreaterEqual(ratio, 4.5, f"POS4_DISABLED_CONTRAST: {ratio:.2f}")
