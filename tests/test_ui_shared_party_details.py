"""Shared invoice presentation, folded data persistence and supplier selection safety."""
from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class SharedPartyDetailsTest(PosFixture):
    def purchase(self):
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.widgets import dispose
        sid = self.con.execute("INSERT INTO suppliers (name,name_norm,mobile) VALUES (?,?,?)",
                               ("مورد تجريبي", "مورد تجريبي", "01012345678")).lastrowid
        stack = PurchasesPage(self.session)
        self.addCleanup(dispose, stack, self.app)
        stack.resize(1366, 768)
        stack.open_form()
        stack.show()
        self.app.processEvents()
        return stack.form_page, sid

    def add_battery(self, form):
        item = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('battery','QA-B','QA-B','qa-b',100,100,100)").lastrowid
        form._add_row(item, "battery", "QA-B")
        form._rows[-1]["cost_edit"].setText("10.00")

    def test_purchase_details_fold_and_post_without_losing_values(self):
        p, sid = self.purchase()
        self.assertTrue(all(not w.isVisibleTo(p) for w in (p.inv_no, p.inv_date, p.notes)))
        p.extra_toggle.click()
        self.app.processEvents()
        self.assertTrue(all(w.isVisibleTo(p) for w in (p.inv_no, p.inv_date, p.notes)))
        p.inv_no.setText("QA-42")
        p.inv_date.setText("2026-11-05")
        p.notes.setText("اختبار التفاصيل")
        p.extra_toggle.click()
        self.assertFalse(p.extra_box.isVisibleTo(p))
        self.assertTrue(p.has_draft())
        self.assertIn("QA-42", p.extra_summary.text())
        self.assertIn("2026-11-05", p.extra_summary.text())
        self.assertIn("ملاحظة", p.extra_summary.text())
        p.extra_toggle.click()
        self.assertEqual((p.inv_no.text(), p.inv_date.text(), p.notes.text()),
                         ("QA-42", "2026-11-05", "اختبار التفاصيل"))
        p.extra_toggle.click()
        p._pick_supplier(sid, "مورد تجريبي", "01012345678")
        self.add_battery(p)
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        self.assertEqual(self.con.execute(
            "SELECT supplier_id,supplier_invoice_no,invoice_date,notes FROM purchase_invoices"
        ).fetchone(), (sid, "QA-42", "2026-11-05", "اختبار التفاصيل"))

    def test_same_selected_field_and_strip_classes(self):
        from morv.infra import customers
        c = customers.create(self.con, self.session.user,
                             customers.Customer(None, "عميل تجريبي", "01012345678"))
        sale = self.page()
        sale.customer.setText(c.mobile)
        sale.customer.accept_query()
        p, sid = self.purchase()
        p._pick_supplier(sid, "مورد تجريبي", c.mobile)
        self.assertTrue(p.sup_search.isVisibleTo(p))
        self.assertEqual(p.sup_search.text(), "مورد تجريبي")
        self.assertFalse(p.sup_search.pending)
        self.assertIs(type(sale.party_info), type(p.party_info))
        self.assertIs(type(sale.extra_box), type(p.extra_box))
        self.assertEqual(p.party_info.kind.objectName(), sale.party_info.kind.objectName())
        self.assertIn(c.mobile, p.party_info.stats.text())
        self.assertIn(c.mobile, sale.party_info.stats.text())

    def test_pending_supplier_blocks_post_and_escape_restores(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        p, sid = self.purchase()
        p._pick_supplier(sid, "مورد تجريبي", "01012345678")
        self.add_battery(p)
        p.sup_search.setText("01099999999")
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0], 0)
        self.assertTrue(p.sup_search.pending)
        QTest.keyClick(p.sup_search, Qt.Key.Key_Escape)
        self.assertFalse(p.sup_search.pending)
        self.assertEqual(p.sup_search.text(), "مورد تجريبي")
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        self.assertEqual(self.con.execute("SELECT supplier_id FROM purchase_invoices").fetchone()[0], sid)

    def test_shared_quick_dialog_keeps_supplier_optional_fields(self):
        from morv.ui.pos_page import QuickCustomerDialog
        from morv.ui.purchases_page import _NewSupplierDialog
        from morv.ui.widgets import dispose
        c = QuickCustomerDialog(self.session, "01099998888")
        self.addCleanup(dispose, c, self.app)
        s = _NewSupplierDialog("مورد جديد", "01099997777", self.session)
        self.addCleanup(dispose, s, self.app)
        self.assertEqual(type(c).__base__.__name__, "QuickPartyDialog")
        self.assertIs(type(c).__base__, type(s).__base__)
        self.assertEqual(c.width(), s.width())
        self.assertEqual(c.mobile_edit.text(), "01099998888")
        self.assertEqual(s.name_edit.text(), "مورد جديد")
        s.addr_edit.setText("عنوان تجريبي")
        s.notes_edit.setText("ملاحظة تجريبية")
        s._save()
        self.assertIsNotNone(s.created, s.notice.text())
        self.assertEqual(self.con.execute("SELECT address,notes FROM suppliers").fetchone(),
                         ("عنوان تجريبي", "ملاحظة تجريبية"))
