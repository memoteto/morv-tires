"""Tests for dashboard_view.get(): period KPIs, alerts, recent activity and payment/top-seller breakdowns."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import date

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

    def test_account_balances_include_prior_days_and_internal_transfers(self):
        from morv.infra import ledger
        from morv.infra.db.connection import transaction
        self._sale("2026-11-10")
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, "2026-11-11", "test_transfer", 1, "Transfer",
                              [("1110", 0, 600000), ("1210", 300000, 0),
                               ("1220", 200000, 0), ("1230", 100000, 0)])
        expected = {"1110": -100000, "1130": 0, "1210": 300000, "1220": 200000, "1230": 100000}
        for period in ("today", "week", "month"):
            with self.subTest(period=period):
                view = dashboard_view.get(self.con, period=period, today="2026-11-16")
                self.assertEqual(dict(view.account_balances), expected)
                self.assertEqual(view.cash_balance, 500000)
                self.assertEqual(sum(dict(view.account_balances).values()), view.cash_balance)

    def test_empty_accounts_are_present_with_zero_balances(self):
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(dict(view.account_balances), {"1110": 0, "1130": 0, "1210": 0, "1220": 0, "1230": 0})

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
        self.assertEqual((old_dot[0].count, old_dot[0].detail), (4, "إطار"))

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

    def _contract(self):
        sale = self._sale("2026-09-01", [Payment("installment", 500000)])
        return installments.post_contract(
            self.con, self.admin, request_key="contract-replay-key", sale_id=sale.id,
            down=100000, rate_bp=0, months=4, first_due="2026-10-16", national_id=NID,
            down_method="cash", contract_date="2026-09-02")

    def test_receipts_use_payment_dates_and_exclude_financed_sales(self):
        contract = self._contract()
        self.assertEqual(dashboard_view.get(self.con, today="2026-09-01").collected_total, 0)
        self.assertEqual(dashboard_view.get(self.con, today="2026-09-02").collected_total, 100000)
        for _ in range(2):
            installments.collect_payment(self.con, self.admin, request_key="payment-replay-key",
                                         contract_id=contract.id, amount=25000, method="transfer",
                                         payment_date="2026-11-16")
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(view.collected_total, 25000)
        self.assertEqual(dict(view.pay_today), {"transfer": 25000})
        self.assertEqual(dashboard_view.get(self.con, today="2026-11-15").collected_total, 0)

    def test_overdue_and_due_today_contract_is_counted_once_and_named(self):
        self._contract()
        view = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual((view.due_count, view.due_amount), (1, 200000))
        today_alert = next(a for a in view.alerts if a.kind == "due_today")
        self.assertEqual((today_alert.count, today_alert.amount, today_alert.detail),
                         (1, 100000, "عميل وهمي"))

    def test_recent_dot_is_not_old_and_boundary_waits_for_full_week(self):
        for n, dot in enumerate(("4626", "4623", "4523")):
            purchasing.post_invoice(self.con, self.admin, request_key=f"dot-purchase-{n}",
                                    supplier_id=self.supplier, supplier_invoice_no=f"DOT-{n}",
                                    invoice_date="2026-11-10", lines=[PurchaseLine(self.tire, 4, 300000, dot=dot)])
        view = dashboard_view.get(self.con, today="2026-11-16")
        alert = next(a for a in view.alerts if a.kind == "old_dot")
        self.assertEqual(alert.count, 4)
        view = dashboard_view.get(self.con, today="2026-11-19")
        self.assertEqual(next(a for a in view.alerts if a.kind == "old_dot").count, 8)

    def test_dot_cutoff_handles_leap_day(self):
        self.assertIsNone(dashboard_view._old_dot_alert(self.con, date(2024, 2, 29)))

    def test_collections_combine_sales_down_payments_and_receipts_without_transfers(self):
        from morv.infra import ledger
        from morv.infra.db.connection import transaction
        contract = self._contract()
        self._sale("2026-09-02", [Payment("card", 500000)])
        installments.collect_payment(self.con, self.admin, request_key="same-day-payment",
                                     contract_id=contract.id, amount=25000, method="wallet",
                                     payment_date="2026-09-02")
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, "2026-09-02", "test_transfer", 1, "Transfer",
                              [("1110", 0, 10000), ("1210", 10000, 0)])
        view = dashboard_view.get(self.con, today="2026-09-02")
        self.assertEqual(view.collected_total, 625000)
        self.assertEqual(dict(view.pay_today), {"cash": 100000, "card": 500000, "wallet": 25000})
        self.assertEqual(sum(amount for _, amount in view.pay_today), view.collected_total)

    def test_failed_collection_does_not_change_dashboard(self):
        import sqlite3
        contract = self._contract()
        before = dashboard_view.get(self.con, today="2026-11-16")
        self.con.execute("CREATE TRIGGER reject_payment BEFORE INSERT ON installment_payment_lines "
                         "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            installments.collect_payment(self.con, self.admin, request_key="failed-payment-key",
                                         contract_id=contract.id, amount=25000, method="cash",
                                         payment_date="2026-11-16")
        after = dashboard_view.get(self.con, today="2026-11-16")
        self.assertEqual(after.collected_total, before.collected_total)
        self.assertEqual(after.account_balances, before.account_balances)
        self.assertEqual(after.due_amount, before.due_amount)
