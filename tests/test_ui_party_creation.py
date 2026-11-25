"""Quick customer creation, permission and pending selection safety."""
from unittest.mock import patch

from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class PartyCreationTest(PosFixture):
    def test_quick_creation_prefills_and_selects(self):
        from morv.ui.pos_page import QuickCustomerDialog
        p = self.page()
        p.customer.setText("01099998888")

        def save(dialog):
            self.assertEqual(dialog.mobile_edit.text(), "01099998888")
            dialog.name_edit.setText("عميل جديد")
            dialog.save()
            self.assertIsNotNone(dialog.created)
            return dialog.result()

        p.customer.search()
        action = next(b for b in p.customer.popup.buttons if "عميل جديد" in b.text())
        with patch.object(QuickCustomerDialog, "exec", save):
            action.click()
        self.assertEqual(p._customer[1:4], ("عميل جديد", "01099998888", "retail"))
        self.assertFalse(p.customer.pending)
        self.assertIn("01099998888", p.cust_stats.text())
        self.assertEqual(self.con.execute("SELECT count(*) FROM customers").fetchone()[0], 1)

    def test_cancel_and_duplicate_do_not_create(self):
        from morv.infra import customers
        from morv.ui.pos_page import QuickCustomerDialog
        customers.create(self.con, self.session.user,
                         customers.Customer(None, "موجود", "01099998888"))
        p = self.page()
        p.customer.setText("اسم جديد")

        def cancel(dialog):
            self.assertEqual(dialog.name_edit.text(), "اسم جديد")
            dialog.mobile_edit.setText("01099998888")
            dialog.save()
            self.assertIsNone(dialog.created)
            self.assertIn("بنفس الرقم", dialog.notice.text())
            dialog.reject()
            return dialog.result()

        p.customer.search()
        action = next(b for b in p.customer.popup.buttons if "عميل جديد" in b.text())
        with patch.object(QuickCustomerDialog, "exec", cancel):
            action.click()
        self.assertEqual(self.con.execute("SELECT count(*) FROM customers").fetchone()[0], 1)
        self.assertIsNone(p._customer[0])
        self.assertTrue(p.customer.pending)

    def test_revoked_permission_hides_add_and_refuses_creation(self):
        from morv.infra import users
        from morv.ui.session import Session
        from morv.ui.pos_page import QuickCustomerDialog
        admin = self.session.user
        cashier = users.create_user(self.con, admin, "qa_cashier", "QA", "Qa12345678", "cashier")
        users.set_permission(self.con, admin, cashier.id, "customers.manage", False)
        self.session = Session(self.con, cashier)
        p = self.page()
        p.customer.setText("ممنوع")
        p.customer.search()
        self.assertFalse(any("جديد" in b.text() for b in p.customer.popup.buttons))
        with patch.object(QuickCustomerDialog, "exec") as execute:
            p._open_new_customer_dialog("ممنوع")
            execute.assert_not_called()
        self.assertEqual(self.con.execute("SELECT count(*) FROM customers").fetchone()[0], 0)

    def test_pending_customer_blocks_post_to_previous_account(self):
        from morv.infra import customers
        c = customers.create(self.con, self.session.user,
                             customers.Customer(None, "عميل سابق", "01011112222"))
        item = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','QA','QA','qa',100,100,100)"
        ).lastrowid
        p = self.page()
        p.customer.setText(c.mobile)
        p.customer.accept_query()
        self.assertEqual(p._customer[0], c.id)
        p._add_row(item, "service", "QA")
        p.pay1_received.setText("1.00")
        p.customer.setText("01099999999")
        p.post()
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 0)
        self.assertIsNone(p.posted)
        self.assertTrue(p.customer.pending)
        self.assertTrue(p.has_draft())

    def test_ambiguous_name_requires_selection_and_escape_restores_cash(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from morv.infra import customers
        for mobile in ("01011112222", "01011113333"):
            customers.create(self.con, self.session.user,
                             customers.Customer(None, "اسم متكرر", mobile))
        p = self.page()
        p.customer.setFocus()
        p.customer.setText("اسم متكرر")
        QTest.keyClick(p.customer, Qt.Key.Key_Return)
        self.assertIsNone(p._customer[0])
        self.assertTrue(p.customer.pending)
        self.assertTrue(p.customer.popup.isVisible())
        QTest.keyClick(p.customer, Qt.Key.Key_Escape)
        self.assertFalse(p.customer.pending)
        self.assertFalse(p.customer.popup.isVisible())
        self.assertEqual(p.customer.text(), "عميل نقدي")

    def test_arabic_partial_mobile_and_keyboard_selection(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from morv.infra import customers
        c = customers.create(self.con, self.session.user,
                             customers.Customer(None, "عميل تجريبي", "01012345678"))
        p = self.page()
        p.customer.setFocus()
        p.customer.setText("٠١٠١٢٣٤")
        QTest.qWait(400)
        self.assertTrue(p.customer.popup.isVisible())
        self.assertIsNone(p._customer[0])
        QTest.keyClick(p.customer, Qt.Key.Key_Down)
        QTest.keyClick(p.customer, Qt.Key.Key_Return)
        self.assertEqual(p._customer[0], c.id)
        self.assertFalse(p.customer.pending)
        self.assertFalse(p.customer.popup.isVisible())

    def test_supplier_uses_shared_live_lookup(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLabel
        from morv.ui.purchases_page import PurchasesPage
        from morv.ui.widgets import dispose
        from morv.ui.party_search import PartySearch
        sid = self.con.execute("INSERT INTO suppliers (name,name_norm,mobile) VALUES (?,?,?)",
                               ("مورد تجريبي", "مورد تجريبي", "01099998888")).lastrowid
        stack = PurchasesPage(self.session)
        self.addCleanup(dispose, stack, self.app)
        stack.resize(1366, 768)
        stack.open_form()
        stack.show()
        self.app.processEvents()
        form = stack.form_page
        self.assertIsInstance(form.sup_search, PartySearch)
        form.sup_search.setFocus()
        form.sup_search.insert("0109999")
        QTest.qWait(400)
        self.assertTrue(form.sup_search.popup.isVisible())
        button = next(b for b in form.sup_search.popup.buttons
                      if any("01099998888" in l.text() for l in b.findChildren(QLabel)))
        button.click()
        self.assertEqual(form._supplier_id, sid)
        self.assertFalse(form.sup_search.popup.isVisible())
