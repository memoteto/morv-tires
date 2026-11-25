"""POS regression and acceptance tests for cancellation, payment state and credit sales."""
import unittest
from unittest.mock import patch

from tests.qt_app import require_qt
from tests.test_ui_sales import _Fixture


@require_qt()
class PosPaymentSafetyTest(_Fixture, unittest.TestCase):
    def page(self, customer=None):
        from morv.ui.pos_page import PosPage
        p = self.destroy_on_cleanup(PosPage(self.session))
        item = self.con.execute("SELECT id FROM items WHERE code='SV-1'").fetchone()[0]
        p._add_row(item, "service", "خدمة وهمية")
        p._rows[0]["qty_spin"].setValue(100)
        if customer is not None:
            self.select_customer(p, customer)
            self.assertEqual(p._customer[0], customer)
        self.assertEqual(p.total_minor(), 10000)
        return p

    def select_customer(self, p, customer):
        from morv.infra import customers
        c = customers.get(self.con, customer)
        p.customer.setText(c.mobile)
        p.customer.accept_query()
        self.assertEqual(p._customer[0], customer)

    def customer(self, mobile, limit):
        return self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل اختبار','عميل اختبار',?,?) RETURNING id", (mobile, limit)).fetchone()[0]

    def method(self, combo, method):
        index = combo.findData(method)
        self.assertGreaterEqual(index, 0, f"Payment option absent: {method}")
        combo.setCurrentIndex(index)
        self.assertEqual(combo.currentData(), method)

    def payment_rows(self, p):
        self.assertIsNotNone(p.posted, p.notice.text())
        return self.con.execute(
            "SELECT method,amount,received FROM sale_payments WHERE sale_id=? ORDER BY method",
            (p.posted.id,)).fetchall()

    def state(self):
        tables = ("sales", "sale_lines", "sale_payments", "journal_entries", "journal_lines",
                  "stock_issues", "stock_lots", "cost_allocations")
        return {table: self.con.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}

    def assert_credit_balance(self, customer, debt, cash):
        from morv.infra import customer_receipts
        self.assertEqual(customer_receipts.customer_balance(self.con, customer), debt)
        for code, expected in (("1310", debt), ("1110", cash), ("4130", -debt-cash)):
            amount = self.con.execute(
                "SELECT coalesce(sum(debit-credit),0) FROM journal_lines WHERE account_code=?",
                (code,)).fetchone()[0]
            self.assertEqual(amount, expected, code)
        self.assertEqual(self.con.execute(
            "SELECT coalesce(sum(debit-credit),0) FROM journal_lines").fetchone()[0], 0)

    def test_cancel_declined_preserves_draft(self):
        p = self.page()
        p.pay1_received.setText("100.00")
        key, before = p._request_key, self.state()
        with patch("morv.ui.modal.ask", return_value=False) as confirm:
            p.cancel_btn.click()
        self.assertEqual(len(p._rows), 1)
        self.assertEqual(p.total_minor(), 10000)
        self.assertEqual(p.pay1_received.text(), "100.00")
        self.assertEqual(p._request_key, key)
        self.assertEqual(self.state(), before)
        confirm.assert_called_once()

    def test_cancel_confirmed_clears_draft_without_posting(self):
        p = self.page()
        key, before = p._request_key, self.state()
        with patch("morv.ui.modal.ask", return_value=True) as confirm:
            p.cancel_btn.click()
        self.assertFalse(p._rows)
        self.assertNotEqual(p._request_key, key)
        self.assertEqual(self.state(), before)
        confirm.assert_called_once()

    def test_new_invoice_defaults_to_cash_after_card_sale(self):
        p = self.page()
        self.method(p.pay1_method, "card")
        p.pay1_ref.setText("TEST-CARD")
        p.post()
        self.assertEqual(self.payment_rows(p), [("card", 10000, None)])
        self.assertEqual(p.pay1_method.currentData(), "cash")
        self.assertFalse(p._rows)
        self.assertEqual(p.pay1_ref.text(), "")

    def test_collapsing_split_cannot_post_hidden_second_method(self):
        p = self.page()
        p.split_btn.setChecked(True)
        p.pay1_amount.setText("40.00")
        self.method(p.pay2_method, "card")
        p.pay2_amount.setText("60.00")
        p.pay1_received.setText("100.00")
        p.split_btn.setChecked(False)
        p.post()
        self.assertEqual(self.payment_rows(p), [("cash", 10000, 10000)])

    def test_card_switch_does_not_send_old_cash_received(self):
        p = self.page()
        p.pay1_received.setText("150.00")
        self.method(p.pay1_method, "card")
        p.pay1_ref.setText("TEST-CARD")
        p.post()
        self.assertEqual(self.payment_rows(p), [("card", 10000, None)])

    def test_cash_controls_hidden_for_card_only(self):
        p = self.page()
        self.method(p.pay1_method, "card")
        self.assertFalse(p.pay1_received.isVisibleTo(p))

    def test_cash_second_method_accepts_received_cash(self):
        p = self.page()
        self.method(p.pay1_method, "card")
        p.pay1_ref.setText("TEST-CARD")
        p.split_btn.setChecked(True)
        p.pay1_amount.setText("40.00")
        self.method(p.pay2_method, "cash")
        p.pay2_amount.setText("60.00")
        p.pay1_received.setText("70.00")
        p.post()
        self.assertEqual(self.payment_rows(p), [("card", 4000, None), ("cash", 6000, 7000)])

    def test_installment_not_offered_as_second_method(self):
        p = self.page()
        self.assertEqual(p.pay2_method.findData("installment"), -1)

    def test_full_credit_posts_customer_debt_not_cash(self):
        customer = self.customer("01000000021", 15000)
        p = self.page(customer)
        self.method(p.pay1_method, "credit")
        self.assertFalse(p.pay1_received.isVisibleTo(p))
        p.post()
        self.assertEqual(self.payment_rows(p), [("credit", 10000, None)])
        self.assert_credit_balance(customer, 10000, 0)

    def test_partial_credit_posts_only_unpaid_part_as_debt(self):
        customer = self.customer("01000000022", 6500)
        p = self.page(customer)
        p.split_btn.setChecked(True)
        p.pay1_amount.setText("40.00")
        p.pay1_received.setText("40.00")
        self.method(p.pay2_method, "credit")
        p.post()
        self.assertEqual(self.payment_rows(p), [("cash", 4000, 4000), ("credit", 6000, None)])
        self.assert_credit_balance(customer, 6000, 4000)

    def test_credit_rejects_anonymous_customer_without_writes(self):
        p = self.page()
        self.method(p.pay1_method, "credit")
        before = self.state()
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.state(), before)
        self.assertIn("عميل", p.notice.text())
        self.assertEqual(len(p._rows), 1)

    def test_credit_rejects_limit_overrun_without_writes(self):
        customer = self.customer("01000000023", 9999)
        p = self.page(customer)
        self.method(p.pay1_method, "credit")
        before = self.state()
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.state(), before)
        self.assertIn("ائتمان", p.notice.text())
        self.assertEqual(len(p._rows), 1)

    def test_credit_rejects_inactive_customer_without_writes(self):
        customer = self.customer("01000000024", 15000)
        p = self.page(customer)
        self.method(p.pay1_method, "credit")
        self.con.execute("UPDATE customers SET active=0 WHERE id=?", (customer,))
        before = self.state()
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.state(), before)
        self.assertIn("عميل", p.notice.text())

    def test_cash_shortage_still_refused_without_writes(self):
        p = self.page()
        p.pay1_received.setText("99.00")
        before = self.state()
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.state(), before)

    def click_total(self, p):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.mouseClick(p.total_label.parentWidget(), Qt.MouseButton.LeftButton,
                         pos=p.total_label.geometry().center())

    def test_click_total_fills_exact_cash_without_extra_chip(self):
        p = self.page()
        self.click_total(p)
        self.assertEqual(p.pay1_received.text(), "100.00")
        self.assertIn("0.00", p.change_label.text())
        self.assertNotIn("المبلغ بالضبط", [b.text() for b in p.amount_chips])

    def test_click_total_fills_only_cash_share_when_split(self):
        p = self.page()
        p.split_btn.setChecked(True)
        p.pay1_amount.setText("40.00")
        self.method(p.pay2_method, "card")
        self.click_total(p)
        self.assertEqual(p.pay1_received.text(), "40.00")
        self.assertIn("0.00", p.change_label.text())

    def test_click_total_does_not_fill_non_cash(self):
        p = self.page()
        self.method(p.pay1_method, "card")
        self.click_total(p)
        self.assertEqual(p.pay1_received.text(), "")

    def test_click_total_uses_cash_second_share(self):
        p = self.page()
        self.method(p.pay1_method, "card")
        p.split_btn.setChecked(True)
        p.pay1_amount.setText("40.00")
        self.method(p.pay2_method, "cash")
        self.click_total(p)
        self.assertEqual(p.pay1_received.text(), "60.00")

    def test_split_without_second_method_cannot_post(self):
        p = self.page()
        p.split_btn.setChecked(True)
        p.pay1_received.setText("100.00")
        before = self.state()
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.state(), before)

    def test_second_card_reference_is_saved_without_cash_received(self):
        p = self.page()
        p.split_btn.setChecked(True)
        p.pay1_amount.setText("40.00")
        self.method(p.pay2_method, "card")
        p.pay2_ref.setText("TEST-SECOND")
        p.pay1_received.setText("40.00")
        p.post()
        self.assertEqual(self.payment_rows(p), [("card", 6000, None), ("cash", 4000, 4000)])
        self.assertEqual(self.con.execute(
            "SELECT reference FROM sale_payments WHERE method='card'").fetchone()[0], "TEST-SECOND")

    def test_credit_summary_shows_limit_debt_and_available(self):
        customer = self.customer("01000000025", 15000)
        p = self.page(customer)
        self.method(p.pay1_method, "credit")
        self.assertTrue(p.credit_hint.isVisibleTo(p))
        for value in ("150.00", "0.00"):
            self.assertIn(value, p.credit_hint.text())
        p.post()
        self.assert_credit_balance(customer, 10000, 0)
        self.assertFalse(p.credit_hint.isVisibleTo(p))
        self.assertEqual(p.pay1_method.currentData(), "cash")
        self.select_customer(p, customer)
        self.method(p.pay1_method, "credit")
        for value in ("150.00", "100.00", "50.00"):
            self.assertIn(value, p.credit_hint.text())
