"""Invoice header simplification and customer lookup regression tests."""
from morv.core.text import norm_ar
from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class InvoiceHeaderTest(PosFixture):

    def test_customer_field_accepts_mobile(self):
        cid = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile) VALUES (?,?,?)",
            ("عميل تجريبي", norm_ar("عميل تجريبي"), "01099999999"),
        ).lastrowid

        for number in ("01099999999", "٠١٠٩٩٩٩٩٩٩٩"):
            with self.subTest(number=number):
                p = self.page()
                edit = p.customer
                edit.setText(number)
                edit.accept_query()
                self.assertEqual(p._customer[0], cid)
                self.assertEqual(p.customer.text(), "عميل تجريبي")
                self.assertEqual(p._customer[2], "01099999999")
                self.assertIn("01099999999", p.cust_stats.text())

    def test_customer_price_tier_not_duplicated(self):
        from PySide6.QtWidgets import QLabel

        p = self.page()
        labels = [
            w.text() for w in p.findChildren(QLabel)
            if w.isVisibleTo(p) and w.text().startswith("فئة السعر:")
        ]
        self.assertLessEqual(len(labels), 1, labels)
        self.assertTrue(p.tier_combo.isVisibleTo(p))

    def test_customer_change_has_no_redundant_button(self):
        from PySide6.QtGui import QShortcut
        from PySide6.QtWidgets import QPushButton

        p = self.page()
        shortcuts = {
            s.key().toString() for s in p.findChildren(QShortcut)
        }
        self.assertIn("F4", shortcuts)
        self.assertTrue(p.customer.isVisibleTo(p))

        shown = [
            b.text() for b in p.findChildren(QPushButton)
            if b.isVisibleTo(p)
        ]
        self.assertNotIn("تغيير العميل", shown)

    def test_purchase_readonly_facts_use_header_chips(self):
        from datetime import date
        from morv.infra import purchasing
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.widgets import dispose

        self.con.execute(
            "INSERT INTO suppliers (name,name_norm,mobile) VALUES (?,?,?)",
            ("مورد تجريبي", norm_ar("مورد تجريبي"), "01000000001"),
        )

        stack = PurchasesPage(self.session)
        self.addCleanup(dispose, stack, self.app)
        stack.resize(1366, 768)
        stack.open_form()
        stack.show()
        self.app.processEvents()

        p = stack.form_page
        self.assertIsNotNone(p)

        for widget, value in (
            (p.entry_no, purchasing.next_number_preview(self.con)),
            (p.recv_date, date.today().isoformat()),
        ):
            with self.subTest(value=value):
                self.assertIn(value, widget.text())
                self.assertEqual(widget.objectName(), "chip")
                self.assertEqual(
                    widget.parentWidget().objectName(), "cardHead"
                )
                self.assertTrue(widget.isVisibleTo(p))

        for widget in (p.inv_no, p.inv_date, p.notes):
            self.assertFalse(widget.isVisibleTo(p))
        p.extra_toggle.setChecked(True)
        for widget in (p.inv_no, p.inv_date, p.notes):
            self.assertTrue(widget.isVisibleTo(p))
