"""Regression tests for migration 0016: sale-payment lookup index only."""
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installment_view, installments, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.sales import SaleLineIn


class SalePaymentsLookupMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_sale_payment_index_")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.con = open_db(self.folder / "old.db", create=True)
        self.addCleanup(self.con.close)

        migrations = load_migrations()
        self.assertEqual((migrations[-1].version, migrations[-1].name),
                         (16, "sale_payments_lookup"))
        migrate(self.con, migrations[:-1])
        self.assertEqual(current_version(self.con), 15)

        self.manager = users.create_user(
            self.con, None, "payment_index_admin", "مدير اختبار", "Admin12345", "manager"
        )
        self.service_id = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, "
            "price_retail, price_wholesale, price_super) "
            "VALUES ('service', 'IDX-SV', 'خدمة', 'خدمه', 1200000, 1200000, 1200000) "
            "RETURNING id"
        ).fetchone()[0]
        self.customer_id = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل اختبار', 'عميل اختبار', '01000000123', 0) RETURNING id"
        ).fetchone()[0]

        self.pending = self._post_sale("installment", "index-pending-sale")
        self.contracted = self._post_sale("installment", "index-contracted-sale")
        self.cash = self._post_sale("cash", "index-cash-sale")
        installments.post_contract(
            self.con, self.manager, request_key="index-contract-0001",
            sale_id=self.contracted.id, down=200000, rate_bp=1500, months=10,
            first_due="2026-12-13", national_id="29001011201234",
            down_method="cash", contract_date="2026-11-13",
        )

    def _post_sale(self, method, key):
        payment = Payment(method, 1200000, received=1200000) if method == "cash" else Payment(method, 1200000)
        return sales.post_sale(
            self.con, self.manager, request_key=key, customer_id=self.customer_id,
            lines=[SaleLineIn(self.service_id, 1, 1200000)],
            payments=[payment], sale_date="2026-11-10",
        )

    def _posted_data(self):
        tables = (
            "sales", "sale_payments", "journal_entries", "journal_lines",
            "installment_contracts", "installment_rows",
        )
        return {
            table: tuple(self.con.execute(f"SELECT * FROM {table} ORDER BY id"))
            for table in tables
        }

    def test_upgrade_preserves_posted_records_and_ledger(self):
        self.assertEqual(self.con.execute("PRAGMA index_list(sale_payments)").fetchall(), [])
        before_data = self._posted_data()
        before_balances = {
            code: ledger.account_balance(self.con, code)
            for code in ("1110", "1320", "2410")
        }
        before_waiting = installment_view.invoices_without_contract(self.con)
        self.assertEqual([row[0] for row in before_waiting], [self.pending.id])

        self.assertEqual(migrate(self.con), [16])
        self.assertEqual(current_version(self.con), 16)
        self.assertEqual(migrate(self.con), [])
        self.assertEqual(self._posted_data(), before_data)
        self.assertEqual(
            {code: ledger.account_balance(self.con, code) for code in before_balances},
            before_balances,
        )
        self.assertEqual(installment_view.invoices_without_contract(self.con), before_waiting)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.con.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_index_covers_real_installment_query(self):
        self.assertEqual(migrate(self.con), [16])
        indexes = {
            row[1]: row for row in self.con.execute("PRAGMA index_list(sale_payments)")
        }
        self.assertIn("sale_payments_sale_method", indexes)
        columns = [
            row[2] for row in self.con.execute("PRAGMA index_info(sale_payments_sale_method)")
        ]
        self.assertEqual(columns, ["sale_id", "method"])

        statements = []
        self.con.set_trace_callback(statements.append)
        try:
            waiting = installment_view.invoices_without_contract(self.con)
        finally:
            self.con.set_trace_callback(None)
        self.assertEqual([row[0] for row in waiting], [self.pending.id])
        statement = next(
            sql for sql in statements
            if "FROM sales s" in sql and "FROM sale_payments p" in sql
        )
        plan = self.con.execute("EXPLAIN QUERY PLAN " + statement).fetchall()
        # Newer SQLite (>=3.5x) inserts "EXISTS" into the step's text for an index-only EXISTS
        # lookup ("SEARCH p EXISTS USING COVERING INDEX ..."); match loosely across versions.
        self.assertTrue(
            any(row[3].startswith("SEARCH p") and "COVERING INDEX sale_payments_sale_method" in row[3]
                for row in plan),
            plan,
        )
        self.assertEqual(
            installment_view.invoice_for_contract(self.con, self.pending.id),
            waiting[0],
        )

    def test_fresh_database_applies_index(self):
        fresh = open_main_db(self.folder / "fresh.db")
        try:
            self.assertEqual(current_version(fresh), 16)
            columns = [
                row[2] for row in fresh.execute("PRAGMA index_info(sale_payments_sale_method)")
            ]
            self.assertEqual(columns, ["sale_id", "method"])
        finally:
            fresh.close()


if __name__ == "__main__":
    unittest.main()
