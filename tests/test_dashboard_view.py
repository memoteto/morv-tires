"""Tests for dashboard_view.get(): period KPIs, alerts, recent activity and payment/top-seller breakdowns."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import dashboard_view, installments, purchasing, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class DashboardViewTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "dv.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',500000,500000,500000) RETURNING id").fetchone()[0]
        self.tire = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','T-1','إطار','اطار',500000,500000,500000) RETURNING id").fetchone()[0]
        self.supplier = self.con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000099') "
            "RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _sale(self, sale_date, payments=None):
        self.n += 1
        payments = list(payments) if payments is not None else [Payment("cash", 500000, 500000)]
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", customer_id=self.cust,
                               lines=[SaleLineIn(self.service, 1, 500000)], payments=payments, sale_date=sale_date)

    def test_sales_kpi_counts_only_the_selected_period(self):
        self._sale("2026-11-16")      # today
        self._sale("2026-11-10")      # earlier this month, outside "today"/"week"
        view_today = dashboard_view.get(self.con, period="today", today="2026-11-16")
        self.assertEqual((view_today.sales_count, view_today.sales_total), (1, 500000))
        view_month = dashboard_view.get(self.con, period="month", today="2026-11-16")
        self.assertEqual(view_month.sales_count, 2)

    def test_collected_total_excludes_credit_payments(self):
        self.con.execute("UPDATE customers SET credit_limit = 1000000 WHERE id=?", (self.cust,))
        self._sale("2026-11-16", payments=[Payment("credit", 500000)])
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(view.collected_total, 0)

    def test_cash_balance_reflects_posted_sales(self):
        self._sale("2026-11-16")
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(view.cash_balance, 500000)

    def test_seven_day_chart_is_oldest_first_and_covers_today(self):
        self._sale("2026-11-16")
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(len(view.chart_days), 7)
        self.assertEqual(view.chart_days[0][0], "2026-11-10")
        self.assertEqual(view.chart_days[-1], ("2026-11-16", 500000))

    def test_late_installment_contract_raises_a_late_alert_with_oldest_days(self):
        sale = sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=self.cust,
                               lines=[SaleLineIn(self.service, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-11-10")
        installments.post_contract(self.con, self.admin, request_key="ic-key-0001", sale_id=sale.id, down=200000,
                                   rate_bp=1500, months=10, first_due="2026-12-13", national_id=NID,
                                   down_method="cash", contract_date="2026-11-13")
        view = dashboard_view.get(self.con, today="2026-12-20")
        late = [a for a in view.alerts if a.kind == "late"]
        self.assertEqual(len(late), 1)
        self.assertEqual((late[0].count, late[0].tone), (1, "bad"))
        self.assertEqual(late[0].amount, view.due_amount)

    def test_old_dot_tire_in_stock_raises_an_alert_naming_it(self):
        purchasing.post_invoice(self.con, self.admin, request_key="pi-key-0001", supplier_id=self.supplier,
                                supplier_invoice_no="SUP-1", invoice_date="2020-01-10",
                                lines=[PurchaseLine(self.tire, 4, 300000, dot="0120")])
        view = dashboard_view.get(self.con, today="2026-11-16")
        old_dot = [a for a in view.alerts if a.kind == "old_dot"]
        self.assertEqual(len(old_dot), 1)
        self.assertEqual((old_dot[0].count, old_dot[0].detail), (1, "إطار"))

    def test_backup_never_run_always_raises_a_backup_alert(self):
        view = dashboard_view.get(self.con, today="2026-11-16")
        backup_alerts = [a for a in view.alerts if a.kind == "backup"]
        self.assertEqual(len(backup_alerts), 1)
        self.assertEqual(backup_alerts[0].tone, "bad")

    def test_recent_invoices_are_newest_first(self):
        self._sale("2026-11-10")
        second = self._sale("2026-11-16")
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(view.recent[0][0], second.number)

    def test_pay_today_groups_by_method(self):
        self._sale("2026-11-16", payments=[Payment("cash", 300000, 300000), Payment("transfer", 200000)])
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(dict(view.pay_today), {"cash": 300000, "transfer": 200000})

    def test_top_month_ranks_items_by_quantity_sold(self):
        other = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-2','خدمة ثانية','خدمه ثانيه',400000,400000,400000) RETURNING id").fetchone()[0]
        self.n += 1
        sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", customer_id=self.cust,
                        lines=[SaleLineIn(self.service, 3, 500000), SaleLineIn(other, 1, 400000)],
                        payments=[Payment("cash", 1900000, 1900000)], sale_date="2026-11-16")
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(view.top_month[0], ("خدمة", 3))
