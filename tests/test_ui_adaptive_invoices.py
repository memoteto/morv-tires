"""Invoice reflow prevents clipping without losing drafts or hiding warnings incorrectly."""
from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class AdaptiveInvoiceTest(PosFixture):
    def settle(self):
        from PySide6.QtTest import QTest
        self.app.processEvents()
        QTest.qWait(150)
        self.app.processEvents()

    def window(self, page):
        from PySide6.QtCore import Qt
        from morv.infra import settings
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose
        old = self.app.layoutDirection()
        self.addCleanup(self.app.setLayoutDirection, old)
        self.app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        settings.put(self.con, "ui.sidebar_collapsed", "0")
        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1024, 640)
        win.show()
        win.go_key(page)
        self.settle()
        return win

    def area(self, page):
        return page.invoice_layout

    def fits(self, area, marker):
        visible = [scroll for scroll in area.areas if scroll.isVisibleTo(area)]
        self.assertEqual(len(visible), area.columns, marker)
        for scroll in visible:
            content = scroll.widget()
            width = scroll.viewport().width()
            self.assertLessEqual(content.width(), width, marker)
            self.assertLessEqual(content.minimumSizeHint().width(), width, marker)
            self.assertFalse(scroll.horizontalScrollBar().isVisible(), marker)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0, marker)

    def approval_reachable(self, page, area):
        from PySide6.QtCore import QPoint
        for scroll in area.areas:
            self.assertFalse(scroll.isAncestorOf(page.post_btn))
            scroll.verticalScrollBar().setValue(0)
        self.settle()
        pt = page.post_btn.mapTo(area, QPoint(0, 0))
        self.assertTrue(page.post_btn.isVisibleTo(page))
        self.assertGreaterEqual(pt.x(), 0)
        self.assertLessEqual(pt.x() + page.post_btn.width(), area.width())
        self.assertGreaterEqual(pt.y(), 0)
        self.assertLessEqual(pt.y() + page.post_btn.height(), area.height())

    def test_pos_narrow_reflows_and_draft_survives(self):
        from morv.infra import customers
        from morv.ui.adaptive_layout import InvoiceWorkspace
        customer = customers.create(self.con, self.session.user,
                                    customers.Customer(None, "عميل تجريبي", "01012345678"))
        win = self.window("pos")
        p = win.pages["pos"]
        p._pick_customer(customers.search(self.con, customer.mobile)[0])
        self.settle()
        area = self.area(p)
        self.fits(area, "POS_NARROW_CLIP")
        self.assertIsInstance(area, InvoiceWorkspace)
        self.assertEqual(area.columns, 1)
        p.extra_toggle.setChecked(True)
        p.note.setText("اختبار المسودة")
        item = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','ADAPT-S','خدمة','خدمة',1000,1000,1000)").lastrowid
        p._add_row(item, "service", "خدمة")
        p.pay1_received.setText("10.00")
        request = p._request_key
        self.settle()
        self.fits(area, "POS_NARROW_DRAFT_CLIP")
        self.approval_reachable(p, area)
        p.extra_toggle.setChecked(False)
        for width, height, collapsed in ((1366, 768, False), (1024, 640, True), (1024, 640, False)):
            win.resize(width, height)
            if win.collapsed != collapsed:
                win.toggle_sidebar()
            self.settle()
            self.fits(area, "POS_RELAYOUT_CLIP")
            margins = area.grid.contentsMargins()
            primary_min = max(area.primary.minimumWidth(), area.primary.minimumSizeHint().width())
            summary_min = max(area.summary.minimumWidth(), area.summary.minimumSizeHint().width())
            footer_min = area.footer.minimumSizeHint().width()
            needed = (primary_min + area.primary_area.verticalScrollBar().sizeHint().width()
                      + max(footer_min, summary_min + area.summary_area.verticalScrollBar().sizeHint().width())
                      + area.grid.horizontalSpacing() + margins.left() + margins.right())
            self.assertEqual(area.columns, 2 if area.width() >= needed else 1)
            if width == 1366:
                self.assertEqual(area.columns, 2)
            self.assertEqual(p._customer[0], customer.id)
            self.assertEqual(p.note.text(), "اختبار المسودة")
            self.assertEqual(p.pay1_received.text(), "10.00")
            self.assertEqual(len(p._rows), 1)
            self.assertEqual(p._request_key, request)
        self.assertIsNone(p.posted)

    def test_purchase_reuses_adaptive_cards_and_preserves_draft(self):
        from morv.infra import suppliers
        from morv.ui.adaptive_layout import InvoiceWorkspace
        s = suppliers.create(self.con, self.session.user,
                             suppliers.Supplier(None, "مورد تجريبي", "01012345678"))
        win = self.window("purchases")
        stack = win.pages["purchases"]
        stack.open_form()
        p = stack.form_page
        p._pick_supplier(s.id, s.name, s.mobile)
        self.settle()
        area = self.area(p)
        self.assertIsInstance(area, InvoiceWorkspace, "PURCHASE_SHARED_LAYOUT")
        self.fits(area, "PURCHASE_NARROW_CLIP")
        p.extra_toggle.setChecked(True)
        p.inv_no.setText("ADAPT-1")
        p.notes.setText("اختبار المسودة")
        item = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('battery','ADAPT-B','بطارية','بطارية',1000,1000,1000)").lastrowid
        p._add_row(item, "battery", "بطارية")
        p._rows[-1]["cost_edit"].setText("10.00")
        self.settle()
        self.fits(area, "PURCHASE_NARROW_DRAFT_CLIP")
        self.approval_reachable(p, area)
        p.extra_toggle.setChecked(False)
        request = p._request_key
        for width, height, collapsed in ((1366, 768, False), (1024, 640, True), (1024, 640, False)):
            win.resize(width, height)
            if win.collapsed != collapsed:
                win.toggle_sidebar()
            self.settle()
            self.fits(area, "PURCHASE_RELAYOUT_CLIP")
            if width == 1366:
                self.assertEqual(area.columns, 2)
            self.assertEqual(p._supplier_id, s.id)
            self.assertEqual(p.inv_no.text(), "ADAPT-1")
            self.assertEqual(p.notes.text(), "اختبار المسودة")
            self.assertEqual(len(p._rows), 1)
            self.assertEqual(p._rows[-1]["cost_edit"].text(), "10.00")
            self.assertEqual(p._request_key, request)
        self.assertIsNone(p.posted)

    def test_supplier_creation_clears_only_stale_warning(self):
        from morv.infra import suppliers
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.widgets import dispose
        stack = PurchasesPage(self.session)
        self.addCleanup(dispose, stack, self.app)
        stack.resize(1366, 768)
        stack.open_form()
        stack.show()
        p = stack.form_page
        self.settle()
        self.assertIn("لا يوجد موردون نشطون", p.notice.text())
        s = suppliers.create(self.con, self.session.user,
                             suppliers.Supplier(None, "مورد جديد", "01099998888"))
        p._pick_supplier(s.id, s.name, s.mobile)
        self.assertEqual(p.notice.text(), "", "STALE_SUPPLIER_NOTICE")
        self.assertTrue(p.notice.isHidden())
        p.notice.show_message("تحذير تجريبي آخر", "warn")
        p._pick_supplier(s.id, s.name, s.mobile)
        self.assertEqual(p.notice.text(), "تحذير تجريبي آخر")
        self.assertFalse(p.notice.isHidden())

    def test_pos_detail_summary_does_not_force_horizontal_scroll(self):
        from morv.infra import customers
        c = customers.create(self.con, self.session.user,
                             customers.Customer(None, "عميل تجريبي", "01012345678"))
        win = self.window("pos")
        p = win.pages["pos"]
        p._pick_customer(customers.search(self.con, c.mobile)[0])
        p.odometer.setText("125000")
        p.note.setText("اختبار")
        self.check_long_summary(win, p, p.plate, "POS_LONG_SUMMARY_CLIP")
        self.assertEqual(p.odometer.text(), "125000")
        self.assertEqual(p.note.text(), "اختبار")

    def test_purchase_detail_summary_does_not_force_horizontal_scroll(self):
        from morv.infra import suppliers
        s = suppliers.create(self.con, self.session.user,
                             suppliers.Supplier(None, "مورد تجريبي", "01012345678"))
        win = self.window("purchases")
        stack = win.pages["purchases"]
        stack.open_form()
        p = stack.form_page
        p._pick_supplier(s.id, s.name, s.mobile)
        p.notes.setText("اختبار")
        self.check_long_summary(win, p, p.inv_no, "PURCHASE_LONG_SUMMARY_CLIP")
        self.assertEqual(p.notes.text(), "اختبار")

    def check_long_summary(self, win, p, editor, marker):
        area = self.area(p)
        label = p.extra_summary
        editor.setText("AB-2026-" * 10)
        self.settle()
        self.fits(area, marker)
        for width, height in ((1024, 640), (1366, 768)):
            win.resize(width, height)
            for n in (8, 24, 48, 80):
                value = "AB-2026-" * (n // 8)
                editor.setText(value)
                for expanded in (False, True):
                    p.extra_toggle.setChecked(expanded)
                    self.settle()
                    self.fits(area, marker)
                    self.assertEqual(editor.text(), value)
                    self.assertEqual(label.text(), p._extra_summary_text())
                    self.assertEqual(label.toolTip(), label.text())
                    self.assertTrue(p.extra_toggle.isVisibleTo(p))
        win.resize(1024, 640)
        p.extra_toggle.setChecked(False)
        self.settle()
        self.assertEqual(editor.text(), "AB-2026-" * 10)
        self.assertGreater(label.width(), 0)
        full = label.text()
        shown = label.display_text()
        self.assertNotEqual(shown, full)
        self.assertIn("…", shown)
        self.assertLessEqual(label.fontMetrics().horizontalAdvance(shown), label.contentsRect().width())
        self.assertEqual(label.toolTip(), full)
        win.resize(1366, 768)
        editor.clear()
        self.settle()
        self.fits(area, marker)
        self.assertEqual(label.display_text(), label.text())
        editor.setText("AB-2026-" * 10)
        p.extra_toggle.setChecked(True)
        self.settle()
        self.fits(area, marker)
        self.assertEqual(editor.text(), "AB-2026-" * 10)
        self.assertEqual(label.text(), full)
        self.assertTrue(p.has_draft())
        self.assertIsNone(p.posted)
