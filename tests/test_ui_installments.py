"""Offscreen instalments screen: the list, the contract form, its live schedule and its refusals."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt

NID = "29001011201234"


@require_qt()
class InstallmentsUiTest(unittest.TestCase):
    def setUp(self):
        from morv.core.sales_rules import Payment
        from morv.infra import sales, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.sales import SaleLineIn
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        self.sale = sales.post_sale(self.con, self.admin, request_key="ui-ic-sale-1", customer_id=self.cust,
                                    lines=[SaleLineIn(item, 1, 1200000)],
                                    payments=[Payment("installment", 1200000)], sale_date="2026-11-10")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self):
        from morv.ui.installments_page import InstallmentsPage
        p = InstallmentsPage(self.session)
        self.addCleanup(p.deleteLater)
        return p

    def opened_form(self):
        p = self.page()
        p.list_page.open_dialog(None)
        return p, p.form_page

    def test_list_counts_invoices_waiting_for_a_contract(self):
        p = self.page()
        self.assertIn("فاتورة بلا عقد", p.list_page.total())

    def test_form_opens_with_a_live_schedule(self):
        p, f = self.opened_form()
        self.assertIsNotNone(f)
        self.assertIs(p.currentWidget(), f)
        self.assertEqual(len(f.model.rows), 10)
        self.assertEqual(f._terms.markup, 180000)

    def test_schedule_follows_the_terms(self):
        _p, f = self.opened_form()
        f.down.setText("2000.00")
        f.months.setValue(10)
        f.rate.setText("15")
        self.assertEqual(len(f.model.rows), 10)
        self.assertEqual(f._terms.monthly, 115000)
        self.assertEqual(f._terms.contract_total, 1350000)

    def test_bad_terms_block_approval_without_crashing(self):
        _p, f = self.opened_form()
        f.down.setText("20000.00")
        self.assertIsNone(f._terms)
        self.assertFalse(f.post_btn.isEnabled())
        self.assertEqual(len(f.model.rows), 0)

    def test_posting_returns_to_the_list_and_counts_the_contract(self):
        p, f = self.opened_form()
        f.down.setText("2000.00")
        f.national_id.setText(NID)
        f.post()
        self.assertIsNotNone(f.posted)
        self.assertIs(p.currentWidget(), p.tabs)
        self.assertIs(p.tabs.currentWidget(), p.list_page)
        self.assertEqual(self.con.execute("SELECT count(*) FROM installment_contracts").fetchone()[0], 1)
        self.assertNotIn("فاتورة بلا عقد", p.list_page.total())

    def test_missing_national_id_is_refused_by_the_engine(self):
        _p, f = self.opened_form()
        f.down.setText("2000.00")
        f.post()
        self.assertIsNone(f.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM installment_contracts").fetchone()[0], 0)

    def test_guarantor_fields_appear_only_when_asked(self):
        _p, f = self.opened_form()
        self.assertFalse(f.guarantor.isVisibleTo(f))
        f.has_guarantor.setChecked(True)
        self.assertTrue(f.guarantor.isVisibleTo(f))


if __name__ == "__main__":
    unittest.main()


@require_qt()
class PosToContractTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000)")
        self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0)")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pos(self):
        from morv.ui.pos_page import PosPage
        p = PosPage(self.session)
        self.addCleanup(p.deleteLater)
        return p

    def test_instalment_method_is_offered_and_hides_the_cash_box(self):
        p = self.pos()
        idx = p.pay1_method.findData("installment")
        self.assertGreaterEqual(idx, 0)
        p.pay1_method.setCurrentIndex(idx)
        self.assertFalse(p.pay_box.isVisibleTo(p))
        self.assertFalse(p.split_btn.isVisibleTo(p))
        self.assertTrue(p.instalment_hint.isVisibleTo(p))

    def test_posting_on_instalments_asks_for_a_contract(self):
        p = self.pos()
        seen = []
        p.contract_needed.connect(seen.append)
        p.customer.setCurrentIndex(1)
        p.item_search.setText("خدمة")
        p.qty.setValue(1)
        p.add_line()
        p.pay1_method.setCurrentIndex(p.pay1_method.findData("installment"))
        p.post()
        self.assertIsNotNone(p.posted)
        self.assertEqual(len(seen), 1)
        row = self.con.execute("SELECT method, amount FROM sale_payments").fetchone()
        self.assertEqual(row, ("installment", 1200000))

    def test_instalment_sale_without_a_customer_is_refused(self):
        p = self.pos()
        p.item_search.setText("خدمة")
        p.add_line()
        p.pay1_method.setCurrentIndex(p.pay1_method.findData("installment"))
        p.post()
        self.assertIsNone(p.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 0)


@require_qt()
class DueAndCollectUiTest(unittest.TestCase):
    def setUp(self):
        from morv.core.sales_rules import Payment
        from morv.infra import installments, sales, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.sales import SaleLineIn
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key="due-sale-key-1", customer_id=self.cust,
                               lines=[SaleLineIn(item, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-11-10")
        self.contract = installments.post_contract(
            self.con, self.admin, request_key="due-contract-key-1", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-12-13", national_id=NID, down_method="cash", contract_date="2026-11-13")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self):
        from morv.ui.installments_page import InstallmentsPage
        p = InstallmentsPage(self.session)
        self.addCleanup(p.deleteLater)
        return p

    def test_due_tab_lists_every_unpaid_instalment(self):
        p = self.page()
        self.assertEqual(len(p.due_page.rows), 10)
        self.assertIn("10 قسط غير مسدَّد", p.due_page.count_label.text())

    def test_collecting_clears_the_oldest_instalment(self):
        from morv.infra import installment_collections as coll
        p = self.page()
        p.due_page.view.selectRow(0)
        row = p.due_page.selected()
        coll.collect(self.con, self.admin, request_key="ui-collect-key-1", contract_id=row[1],
                     amount=row[8], method="cash")
        p.due_page.refresh()
        self.assertEqual(len(p.due_page.rows), 9)

    def test_reminder_needs_a_selected_row(self):
        p = self.page()
        p.due_page.view.clearSelection()
        p.due_page.view.setCurrentIndex(p.due_page.view.model().index(-1, -1))
        self.assertIsNone(p.due_page.selected())
