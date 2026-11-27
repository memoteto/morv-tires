"""POS-5 regression gates: long names, aligned columns and kind-specific DOT."""
from tests.qt_app import require_qt
from tests import test_ui_adaptive_invoices as adaptive
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class InvoiceLineLayoutTest(PosFixture):
    window = adaptive.AdaptiveInvoiceTest.window
    settle = adaptive.AdaptiveInvoiceTest.settle
    fits = adaptive.AdaptiveInvoiceTest.fits
    approval_reachable = adaptive.AdaptiveInvoiceTest.approval_reachable

    def invoice(self, purchase=False):
        from morv.infra import suppliers

        samples = [
            ("tire", "MICHELIN Primacy 4+ 205/55 R16 91V France"),
            ("battery", "VARTA Blue Dynamic 70Ah 12V 630A Germany"),
            ("tire", "BRIDGESTONE Turanza T005 225/45 R17 94W XL"),
        ]
        if not purchase:
            samples.append(("service", "ترصيص الإطارات وضبط زوايا العجل الأمامي والخلفي"))
        items = []
        for i, (kind, name) in enumerate(samples):
            item_id = self.con.execute(
                "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
                "VALUES (?,?,?,?,1250000,1250000,1250000)",
                (kind, f"POS5-{i}", name, name.casefold())).lastrowid
            items.append((item_id, kind, name))
        supplier = suppliers.create(self.con, self.session.user,
                                    suppliers.Supplier(None, "مورد تجريبي", "01012345678"))
        win = self.window("purchases" if purchase else "pos")
        if purchase:
            stack = win.pages["purchases"]
            stack.open_form()
            page = stack.form_page
            page._pick_supplier(supplier.id, supplier.name, supplier.mobile)
        else:
            page = win.pages["pos"]
        for item_id, kind, name in items:
            page._add_row(item_id, kind, name)
            if purchase:
                row = page._rows[-1]
                row["cost_edit"].setText("10500.00")
                if kind == "tire":
                    row["dot_edit"].setText("2324")
        self.settle()
        self.assertEqual(len(page._rows), len(items))
        return win, page

    def check_narrow(self, purchase):
        win, page = self.invoice(purchase)
        win.resize(1024, 640)
        self.settle()
        self.fits(page.invoice_layout, "POS5_LONG_NAME_HORIZONTAL_OVERFLOW")
        self.approval_reachable(page, page.invoice_layout)
        self.assertIsNone(page.posted)

    def test_sale_long_names_fit_narrow(self):
        self.check_narrow(False)

    def test_purchase_long_names_fit_narrow(self):
        self.check_narrow(True)

    def check_medium(self, purchase):
        win, page = self.invoice(purchase)
        win.resize(1366, 768)
        self.settle()
        self.assertEqual(page.invoice_layout.columns, 2, "POS5_LONG_NAME_REFLOW")
        self.fits(page.invoice_layout, "POS5_MEDIUM_CLIP")
        self.approval_reachable(page, page.invoice_layout)

    def test_sale_long_names_keep_medium_summary(self):
        self.check_medium(False)

    def test_purchase_long_names_keep_medium_summary(self):
        self.check_medium(True)

    def name_label(self, page, row):
        from PySide6.QtWidgets import QLabel
        labels = [label for label in page.findChildren(QLabel)
                  if label.text() == row["name"]]
        self.assertEqual(len(labels), 1, "SETUP: unique item name label")
        return labels[0]

    def check_names(self, purchase):
        win, page = self.invoice(purchase)
        for width, height in ((1024, 640), (1366, 768), (1920, 1080)):
            win.resize(width, height)
            self.settle()
            for row in page._rows:
                with self.subTest(width=width, name=row["name"]):
                    label = self.name_label(page, row)
                    self.assertTrue(label.wordWrap(), "POS5_NAME_WRAP")
                    self.assertGreater(label.contentsRect().width(), 0)
                    price = row["cost_edit" if purchase else "price_edit"]
                    self.assertGreaterEqual(label.width(), price.width(), "POS5_NAME_PRIORITY")
                    required = label.heightForWidth(label.width())
                    self.assertGreater(required, 0, "POS5_NAME_HEIGHT_HINT")
                    self.assertGreaterEqual(label.height(), required - 1, "POS5_NAME_HEIGHT")
        self.assertIsNone(page.posted)

    def test_sale_names_wrap_and_have_priority(self):
        self.check_names(False)

    def test_purchase_names_wrap_and_have_priority(self):
        self.check_names(True)

    def check_alignment(self, purchase):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QLabel

        win, page = self.invoice(purchase)
        price_key = "cost_edit" if purchase else "price_edit"
        headings = ("#", "الصنف" if purchase else "الصنف / الخدمة", "الكمية",
                    "تكلفة الوحدة" if purchase else "السعر",
                    "DOT" if purchase else "الخصم")
        headings += (("الخصم", "الشحن") if purchase else ()) + ("الإجمالي",)
        headers = []
        for heading in headings:
            labels = [label for label in page.findChildren(QLabel) if label.text() == heading]
            self.assertEqual(len(labels), 1, f"SETUP: header {heading}")
            headers.append(labels[0])

        def edges(widget):
            left = widget.mapTo(page, QPoint()).x()
            return left, left + widget.width()

        for width, height in ((1024, 640), (1366, 768), (1920, 1080)):
            win.resize(width, height)
            self.settle()
            for row in page._rows:
                cells = [row["num_lbl"], self.name_label(page, row).parentWidget(),
                         row["qty_spin"].parentWidget(),
                         row[price_key].parentWidget() if purchase else row[price_key],
                         row["dot_edit"] if purchase else row["disc_edit"], row["total_lbl"]]
                if purchase:
                    cells.insert(5, row["disc_edit"].parentWidget())
                    cells.insert(6, row["freight_row"])
                for i, (header, cell) in enumerate(zip(headers, cells, strict=True)):
                    if purchase and i == 4 and row["kind"] == "battery":
                        continue
                    if purchase and ((i == 5 and page._line_table.compact_discount)
                                     or (i == 6 and page._line_table.compact_freight)):
                        self.assertFalse(header.isVisibleTo(page))
                        self.assertTrue(cell.isVisibleTo(page))
                        continue
                    with self.subTest(width=width, name=row["name"], column=headings[i]):
                        delta = max(abs(a - b) for a, b in zip(edges(header), edges(cell), strict=True))
                        self.assertLessEqual(delta, 1, "POS5_COLUMN_ALIGNMENT")

    def test_sale_headers_and_rows_share_column_edges(self):
        self.check_alignment(False)

    def test_purchase_headers_and_rows_share_column_edges(self):
        self.check_alignment(True)

    def test_purchase_discount_has_room_and_keeps_both_modes(self):
        from PySide6.QtCore import QPoint

        from morv.ui.tokens import LAYOUT

        win, page = self.invoice(True)
        row = page._rows[0]
        edit, toggle = row["disc_edit"], row["disc_unit_btn"]
        self.assertEqual(toggle.text(), "%")
        self.assertFalse(toggle.isChecked())
        edit.setText("200")
        for width, height in ((1024, 640), (1366, 768), (1920, 1080), (1024, 640)):
            win.resize(width, height)
            self.settle()
            required = edit.fontMetrics().horizontalAdvance("999,999.99") + LAYOUT["invoice_line_edit_inset"]
            self.assertGreaterEqual(edit.width(), required, "PURCHASE_DISCOUNT_WIDTH")
            self.assertEqual(edit.text(), "200")
            self.assertFalse(toggle.isChecked())
            compact = page._line_table.compact_discount
            if width == 1024:
                self.assertTrue(compact)
            elif width == 1920:
                self.assertFalse(compact)
            if compact:
                self.assertGreater(edit.mapTo(page, QPoint()).y(), row["dot_edit"].mapTo(page, QPoint()).y())
            else:
                self.assertLess(edit.mapTo(page, QPoint()).x(), row["dot_edit"].mapTo(page, QPoint()).x())
            self.fits(page.invoice_layout, "PURCHASE_DISCOUNT_NO_CLIP")
        lines, error = page._validate_lines()
        self.assertIsNone(error)
        self.assertEqual(lines[0].line_discount, 20000)
        edit.setText("5")
        toggle.setChecked(True)
        lines, error = page._validate_lines()
        self.assertIsNone(error)
        self.assertEqual(lines[0].line_discount, 52500)
        self.assertEqual(toggle.text(), "%")
        self.assertIsNone(page.posted)

    def test_purchase_freight_column_reflows_and_preserves_manual_draft(self):
        from PySide6.QtCore import QPoint

        from morv.ui.tokens import LAYOUT

        win, page = self.invoice(True)
        row = page._rows[0]
        edit, value = row["freight_edit"], row["freight_value"]
        page.ship.setText("300")
        self.assertIn("100.00", value.text())
        self.assertTrue(value.isVisibleTo(page))
        self.assertFalse(edit.isVisibleTo(page))
        page.freight_basis.setCurrentIndex(page.freight_basis.findData("manual"))
        for entry in page._rows:
            entry["freight_edit"].setText("100")
        for width, height in ((1024, 640), (1366, 768), (1920, 1080), (1024, 640)):
            win.resize(width, height)
            self.settle()
            self.assertTrue(edit.isVisibleTo(page))
            self.assertFalse(value.isVisibleTo(page))
            self.assertEqual(edit.text(), "100")
            required = edit.fontMetrics().horizontalAdvance("999,999.99") + LAYOUT["invoice_line_edit_inset"]
            self.assertGreaterEqual(edit.width(), required, "PURCHASE_FREIGHT_WIDTH")
            if width == 1024:
                self.assertTrue(page._line_table.compact_freight)
            elif width == 1920:
                self.assertFalse(page._line_table.compact_freight)
            if page._line_table.compact_freight:
                self.assertGreater(edit.mapTo(page, QPoint()).y(), row["cost_edit"].mapTo(page, QPoint()).y())
            else:
                self.assertLess(edit.mapTo(page, QPoint()).x(), row["disc_edit"].mapTo(page, QPoint()).x())
                self.assertGreater(edit.mapTo(page, QPoint()).x(), row["total_lbl"].mapTo(page, QPoint()).x())
            self.fits(page.invoice_layout, "PURCHASE_FREIGHT_NO_CLIP")
        page.freight_basis.setCurrentIndex(page.freight_basis.findData("value"))
        self.assertTrue(value.isVisibleTo(page))
        self.assertIn("100.00", value.text())
        page.freight_basis.setCurrentIndex(page.freight_basis.findData("manual"))
        self.assertEqual(edit.text(), "100")
        self.assertIsNone(page.posted)

    def test_purchase_battery_dot_hidden_and_tire_dot_visible(self):
        _, page = self.invoice(True)
        tire, battery, other_tire = page._rows
        self.assertTrue(tire["dot_edit"].isVisibleTo(page))
        self.assertTrue(other_tire["dot_edit"].isVisibleTo(page))
        self.assertFalse(battery["dot_edit"].isVisibleTo(page), "POS5_BATTERY_DOT_VISIBLE")

    def test_purchase_tire_dot_validation_and_battery_without_dot(self):
        _, page = self.invoice(True)
        tire = page._rows[0]
        for invalid in ("", "9999"):
            tire["dot_edit"].setText(invalid)
            lines, error = page._validate_lines()
            self.assertIsNone(lines)
            self.assertIn("DOT", error)
        tire["dot_edit"].setText("2324")
        lines, error = page._validate_lines()
        self.assertIsNone(error)
        self.assertEqual([line.dot for line in lines], ["2324", None, "2324"])
        self.assertIsNone(page.posted)

    def check_vertical_alignment(self, purchase, *, cost_note=False):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QLabel

        win, page = self.invoice(purchase)
        if purchase:
            page.ship.setText("300")
        if cost_note:
            page._rows[0]["last_cost"] = 1000000
            page._on_row_changed()
            self.settle()
            self.assertTrue(page._rows[0]["cost_note"].isVisibleTo(page),
                            "SETUP: cost comparison must be visible")

        def center_y(widget):
            rect = widget.contentsRect()
            return widget.mapTo(page, QPoint(0, rect.y())).y() + (rect.height() - 1) / 2

        for dark in (False, True):
            if win.dark != dark:
                win.toggle_theme()
            for width, height in ((1024, 640), (1366, 768), (1920, 1080)):
                win.resize(width, height)
                self.settle()
                for row in page._rows:
                    editor = row["cost_edit" if purchase else "price_edit"]
                    name = self.name_label(page, row)
                    icons = [label for label in name.parentWidget().findChildren(QLabel)
                             if label is not name and label.pixmap() is not None
                             and not label.pixmap().isNull()]
                    self.assertEqual(len(icons), 1, "SETUP: item icon")
                    keys = ("num_lbl", "total_lbl", "qty_spin", "disc_edit")
                    if purchase:
                        keys += ("dot_edit", "freight_value")
                    for key in keys:
                        cell = row[key]
                        if not cell.isVisibleTo(page):
                            continue
                        with self.subTest(dark=dark, width=width, name=row["name"], cell=key):
                            if purchase and key == "disc_edit" and page._line_table.compact_discount:
                                self.assertGreater(center_y(cell), center_y(editor), "PURCHASE_DISCOUNT_SECOND_LINE")
                            elif purchase and key == "freight_value" and page._line_table.compact_freight:
                                self.assertGreater(center_y(cell), center_y(editor), "PURCHASE_FREIGHT_SECOND_LINE")
                            else:
                                self.assertLessEqual(abs(center_y(cell) - center_y(editor)), 1,
                                                     "POS5_VERTICAL_FIELD_ALIGNMENT")
                    with self.subTest(dark=dark, width=width, name=row["name"], cell="item_icon"):
                        self.assertLessEqual(abs(center_y(icons[0]) - center_y(name)), 1,
                                             "POS5_NAME_ICON_ALIGNMENT")
                    if name.heightForWidth(name.width()) <= name.fontMetrics().height() + 2:
                        with self.subTest(dark=dark, width=width, name=row["name"], cell="single_line_name"):
                            self.assertLessEqual(abs(center_y(name) - center_y(editor)), 1,
                                                 "POS5_SINGLE_LINE_NAME_ALIGNMENT")
                self.fits(page.invoice_layout, "POS5_VERTICAL_NO_CLIP")
        self.assertIsNone(page.posted)

    def test_sale_vertical_alignment(self):
        self.check_vertical_alignment(False)

    def test_purchase_vertical_alignment(self):
        self.check_vertical_alignment(True)

    def test_purchase_vertical_alignment_with_cost_note(self):
        self.check_vertical_alignment(True, cost_note=True)
