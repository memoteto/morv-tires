"""Invoice totals and posting controls stay visible while invoice content scrolls."""
from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class FixedInvoiceSummaryTest(PosFixture):
    def settle(self):
        from PySide6.QtTest import QTest
        self.app.processEvents()
        QTest.qWait(150)
        self.app.processEvents()

    def invoice(self, purchase=False):
        from PySide6.QtCore import Qt
        from morv.infra import settings, suppliers
        from morv.ui.shell import MainWindow
        from morv.ui.widgets import dispose

        old_direction = self.app.layoutDirection()
        self.addCleanup(self.app.setLayoutDirection, old_direction)
        self.app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        settings.put(self.con, "ui.sidebar_collapsed", "0")
        supplier = None
        if purchase:
            supplier = suppliers.create(
                self.con, self.session.user,
                suppliers.Supplier(None, "مورد تجريبي", "01012345678"))
        kind = "battery" if purchase else "service"
        ids = []
        for i in range(8):
            item_id = self.con.execute(
                "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
                "VALUES (?,?,?,?,1000,1000,1000)",
                (kind, f"FIX-{i}", f"Test {i}", f"test {i}")).lastrowid
            ids.append(item_id)

        win = MainWindow(self.session, self.app)
        win.health_timer.stop()
        self.addCleanup(dispose, win, self.app)
        win.resize(1024, 640)
        win.show()
        win.go_key("purchases" if purchase else "pos")
        if purchase:
            stack = win.pages["purchases"]
            stack.open_form()
            page = stack.form_page
            page._pick_supplier(supplier.id, supplier.name, supplier.mobile)
        else:
            page = win.pages["pos"]
        page.extra_toggle.setChecked(True)
        for i, item_id in enumerate(ids):
            page._add_row(item_id, kind, f"Test {i}")
            if purchase:
                page._rows[-1]["cost_edit"].setText("10.00")
        self.settle()
        return win, page

    def visible_inside_page(self, page, control):
        from PySide6.QtCore import QPoint, QRect
        if not control.isVisibleTo(page) or control.size().isEmpty():
            return False
        ancestor = control.parentWidget()
        while ancestor is not None:
            rect = QRect(control.mapTo(ancestor, QPoint()), control.size())
            if not ancestor.rect().contains(rect):
                return False
            if ancestor is page:
                return True
            ancestor = ancestor.parentWidget()
        return False

    def assert_pinned(self, page, controls, marker):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QScrollArea

        areas = [area for area in page.findChildren(QScrollArea)
                 if area.isVisibleTo(page) and area.verticalScrollBar().maximum() > 0]
        self.assertTrue(areas, "BASELINE_SETUP: invoice must overflow with eight rows")
        observations = []
        for bottom in (False, True, False):
            for area in areas:
                bar = area.verticalScrollBar()
                bar.setValue(bar.maximum() if bottom else 0)
            self.settle()
            observations.append(tuple(
                (control.mapTo(page, QPoint()).toTuple(), self.visible_inside_page(page, control))
                for control in controls))
        self.assertTrue(all(visible for state in observations for _, visible in state),
                        f"{marker}: clipped control: {observations}")
        self.assertEqual(observations[0], observations[1], f"{marker}: moved with scroll")
        self.assertEqual(observations[0], observations[2], f"{marker}: moved on return")
        self.assertIsNone(page.posted)

    def test_sale_approval_controls_remain_visible(self):
        _, page = self.invoice()
        self.assert_pinned(page, (page.post_btn, page.post_print_btn), "POS4_SALE_ACTIONS")

    def test_sale_total_remains_visible(self):
        _, page = self.invoice()
        self.assert_pinned(page, (page.total_box,), "POS4_SALE_TOTAL")

    def test_purchase_approval_remains_visible(self):
        _, page = self.invoice(purchase=True)
        self.assert_pinned(page, (page.post_btn,), "POS4_PURCHASE_ACTION")

    def test_purchase_total_remains_visible(self):
        _, page = self.invoice(purchase=True)
        self.assert_pinned(page, (page._total_lbl,), "POS4_PURCHASE_TOTAL")
