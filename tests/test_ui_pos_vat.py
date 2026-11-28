"""P04-4C.3: the POS preview, payment and posted tax agree exactly."""
import unittest
from unittest.mock import patch

from tests.qt_app import require_qt
from tests.test_ui_sales import _Fixture


@require_qt()
class PosVatTest(_Fixture, unittest.TestCase):
    def page(self, enabled=True):
        from morv.infra import settings
        from morv.ui.pos_page import PosPage

        settings.put(self.con, "tax.enabled", "1" if enabled else "0")
        settings.put(self.con, "tax.rate", "1400")
        p = self.destroy_on_cleanup(PosPage(self.session))
        p.show()
        self.app.processEvents()
        item = self.con.execute("SELECT id FROM items WHERE code='SV-1'").fetchone()[0]
        p._add_row(item, "service", "خدمة خاضعة")
        p._rows[0]["price_edit"].setText("1140.00")
        return p

    def mixed(self):
        p = self.page()
        p._rows[0]["disc_edit"].setText("140.00")
        exempt = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,"
            "price_super,tax_treatment) "
            "VALUES ('service','EX','معفى','معفى',100000,100000,100000,'exempt') RETURNING id").fetchone()[0]
        p._add_row(exempt, "service", "خدمة معفاة")
        p.invoice_disc.setText("200.00")
        return p

    def customer(self, p):
        customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل','عميل','01012345678',500000) RETURNING id").fetchone()[0]
        p.customer.setText("01012345678")
        p.customer.accept_query()
        self.assertEqual(p._customer[0], customer)
        return customer

    def count_sales(self):
        return self.con.execute("SELECT count(*) FROM sales").fetchone()[0]

    def test_mixed_discounts_inclusive_exclusive_and_cash_post(self):
        p = self.mixed()
        self.assertTrue(p.tax_inclusive.isVisible())
        self.assertTrue(p.tax_inclusive.isChecked())
        self.assertTrue(p.tax_row.isVisible())
        self.assertEqual(p.total_minor(), 180000)
        self.assertIn("110.53", p.tax_value.text())
        self.assertIn("منها", p.tax_caption.text())
        p.tax_inclusive.setChecked(False)
        self.assertEqual(p.total_minor(), 192600)
        self.assertIn("126.00", p.tax_value.text())
        self.assertNotIn("منها", p.tax_caption.text())
        p._fill_exact_cash()
        self.assertEqual(p.pay1_received.text(), "1926.00")
        p.pay1_received.setText("2000.00")
        self.assertIn("74.00", p.change_label.text())
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        self.assertEqual(tuple(self.con.execute(
            "SELECT total,tax_total,prices_tax_inclusive FROM sales").fetchone()),
            (192600, 12600, 0))
        self.assertEqual(tuple(self.con.execute(
            "SELECT amount,received FROM sale_payments").fetchone()), (192600, 200000))
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT invoice_share,tax_amount,net_total FROM sale_lines ORDER BY line_no")],
            [(10000, 12600, 102600), (10000, 0, 90000)])
        self.assertTrue(p.tax_inclusive.isChecked())
        self.assertEqual(p.total_minor(), 0)

    def test_inclusive_rate_drift_refuses_even_when_payable_unchanged(self):
        from morv.infra import settings
        p = self.page()
        p._fill_exact_cash()
        self.assertIn("140.00", p.tax_value.text())
        settings.put(self.con, "tax.rate", "500")
        p.post()
        self.assertEqual(self.count_sales(), 0)
        self.assertIn("تغيّر", p.notice.text())
        self.assertEqual(p.total_minor(), 114000)
        self.assertIn("54.29", p.tax_value.text())
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        self.assertEqual(self.con.execute("SELECT tax_total FROM sales").fetchone()[0], 5429)

    def test_invalid_tax_preview_cannot_fill_or_post(self):
        p = self.page()
        p.invoice_disc.setText("1141.00")
        self.assertEqual(p.tax_value.text(), "—")
        self.assertEqual(p.total_label.text(), "—")
        self.assertFalse(p.total_box.isEnabled())
        p._fill_exact_cash()
        self.assertEqual(p.pay1_received.text(), "")
        p.post()
        self.assertEqual(self.count_sales(), 0)
        p.invoice_disc.clear()
        self.assertEqual(p.total_minor(), 114000)
        self.assertTrue(p.total_box.isEnabled())

    def test_split_credit_uses_exclusive_gross_and_exact_cash_share(self):
        p = self.page()
        self.customer(p)
        p.tax_inclusive.setChecked(False)
        p.split_btn.setChecked(True)
        p.pay2_method.setCurrentIndex(p.pay2_method.findData("credit"))
        p.pay1_amount.setText("300.00")
        p._fill_exact_cash()
        self.assertEqual(p.pay1_received.text(), "300.00")
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        self.assertEqual([tuple(r) for r in self.con.execute(
            "SELECT method,amount FROM sale_payments ORDER BY method")],
            [("cash", 30000), ("credit", 99960)])

    def test_installment_uses_exclusive_gross(self):
        p = self.page()
        self.customer(p)
        p.tax_inclusive.setChecked(False)
        p.pay1_method.setCurrentIndex(p.pay1_method.findData("installment"))
        emitted = []
        p.contract_needed.connect(emitted.append)
        p.post()
        self.assertIsNotNone(p.posted, p.notice.text())
        self.assertEqual(emitted, [p.posted.id])
        self.assertEqual(tuple(self.con.execute(
            "SELECT method,amount FROM sale_payments").fetchone()), ("installment", 129960))

    def test_tax_disabled_and_mode_change_require_review(self):
        from morv.infra import settings
        p = self.page(False)
        self.assertTrue(p.tax_inclusive.isHidden())
        self.assertTrue(p.tax_row.isHidden())
        self.assertTrue(p.post_print_btn.isEnabled())
        p._fill_exact_cash()
        settings.put(self.con, "tax.enabled", "1")
        p.post()
        self.assertEqual(self.count_sales(), 0)
        self.assertTrue(p.tax_inclusive.isVisible())
        self.assertTrue(p.tax_inclusive.isChecked())
        self.assertTrue(p.post_print_btn.isEnabled())
        p.tax_inclusive.setChecked(False)
        with patch("morv.ui.modal.ask", return_value=True):
            p.cancel_invoice()
        self.assertTrue(p.tax_inclusive.isChecked())
        self.assertFalse(p._rows)

    def test_post_and_print_uses_saved_tax_snapshot(self):
        from morv.infra import settings
        from morv.ui import printing

        p = self.page()
        p.tax_inclusive.setChecked(False)
        p.invoice_disc.setText("140.00")
        p._fill_exact_cash()
        with patch.object(printing, "send", return_value="TEST") as send:
            p.post(and_print=True)
        self.assertIsNotNone(p.posted, p.notice.text())
        send.assert_called_once()
        doc = send.call_args.args[1]
        self.assertEqual(dict(doc.totals)["ضريبة القيمة المضافة"], "140.00")
        self.assertEqual(dict(doc.totals)["الإجمالي المستحق"], "1,140.00 ج.م")
        settings.put(self.con, "tax.enabled", "0")
        settings.put(self.con, "tax.rate", "500")
        from morv.infra import sale_view, shop
        saved = sale_view.get_sale(self.con, p.posted.id)
        self.assertEqual(printing.sale_document(saved, shop.load(self.con)).totals, doc.totals)
        self.assertEqual(saved.lines[0].tax_rate, 1400)

    def test_transaction_guard_rejects_rate_change_after_preview(self):
        from morv.infra import sales, settings
        p = self.page()
        p._fill_exact_cash()
        post = sales.post_sale

        def changed(*args, **kw):
            settings.put(self.con, "tax.rate", "500")
            return post(*args, **kw)

        with patch("morv.ui.pos_page.sales.post_sale", side_effect=changed):
            p.post()
        self.assertEqual(self.count_sales(), 0)
        self.assertIn("تغيّر", p.notice.text())
        self.assertIn("54.29", p.tax_value.text())
