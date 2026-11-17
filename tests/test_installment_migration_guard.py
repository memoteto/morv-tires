"""Fail-closed migration tests for divergent installment paths (synthetic data only).

These tests validate the migrator's safeguards. They do NOT claim to convert
real databases made by PR #27 or PR #28.
"""
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installments, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import (
    MIGRATIONS_DIR, Migration, MigrationError, load_migrations, migrate,
)
from morv.infra.sales import SaleLineIn


class InstallmentMigrationGuardTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="morv_installment_guard_"))
        self.con = open_main_db(self.folder / "main.db")

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    @staticmethod
    def _synthetic_migration(version, name, sql):
        """Distinct branch-shaped SQL; NOT a copy of any shipped migration."""
        checksum = hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        return Migration(version, name, sql, checksum)

    @staticmethod
    def _state(con):
        return (
            tuple(con.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")),
            tuple(con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")),
            tuple(con.execute("SELECT id, entry_date, source_type, source_id, total FROM journal_entries ORDER BY id")),
            tuple(con.execute("SELECT id, contract_id, amount, journal_entry_id FROM installment_payments ORDER BY id"))
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='installment_payments'").fetchone()
            else (),
        )

    def test_main_uses_only_the_approved_payment_engine(self):
        migrations = load_migrations()
        self.assertEqual(migrations[-1].version, 15)
        self.assertEqual(migrations[-1].name, "installment_modifications")
        self.assertEqual(migrations[13].name, "interest_eir"
        self.assertEqual(migrations[12].name, "installment_returns")
        self.assertEqual(migrations[11].name, "installment_payments")
        names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("installment_payments", names)
        self.assertIn("installment_payment_lines", names)
        self.assertNotIn("installment_collections", names)
        self.assertNotIn("installment_allocations", names)

    def test_existing_posted_payment_is_unchanged_on_repeat_migration(self):
        manager = users.create_user(self.con, None, "guard_admin", "مدير اختبار", "Admin12345", "manager")
        item_id = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','GUARD-SV','خدمة وهمية','خدمه وهميه',1200000,1200000,1200000) "
            "RETURNING id"
        ).fetchone()[0]
        customer_id = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل اختبار','عميل اختبار','01000000071',0) RETURNING id"
        ).fetchone()[0]
        sale = sales.post_sale(
            self.con, manager, request_key="guard-sale-0001", customer_id=customer_id,
            lines=[SaleLineIn(item_id, 1, 1200000)],
            payments=[Payment("installment", 1200000)], sale_date="2026-11-10",
        )
        contract = installments.post_contract(
            self.con, manager, request_key="guard-contract-0001", sale_id=sale.id,
            down=200000, rate_bp=1500, months=10, first_due="2026-12-13",
            national_id="29001011201234", down_method="cash", contract_date="2026-11-13",
        )
        installments.collect_payment(
            self.con, manager, request_key="guard-payment-0001",
            contract_id=contract.id, amount=115000, method="cash",
            payment_date="2026-11-14",
        )
        before = self._state(self.con)
        balances = tuple(ledger.account_balance(self.con, code) for code in ("1110", "1320", "2410"))
        self.assertEqual(migrate(self.con), [])
        self.assertEqual(self._state(self.con), before)
        self.assertEqual(tuple(ledger.account_balance(self.con, code)
                               for code in ("1110", "1320", "2410")), balances)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_alternative_0012_path_is_refused_without_mutation(self):
        """A branch-shaped v12 on a fake database must not turn into main's v12."""
        other = open_db(self.folder / "alternate12.db", create=True)
        try:
            main_migrations = load_migrations()
            alternate_sql = (
                "CREATE TABLE installment_collections (id INTEGER PRIMARY KEY, amount INTEGER NOT NULL);"
                "INSERT INTO installment_collections (amount) VALUES (4200);"
            )
            migrate(other, main_migrations[:11] + [
                self._synthetic_migration(12, "installment_collections", alternate_sql),
            ])
            before = self._state(other)
            with self.assertRaisesRegex(MigrationError, "0012"):
                migrate(other, main_migrations)
            self.assertEqual(self._state(other), before)
            self.assertEqual(other.execute("SELECT amount FROM installment_collections").fetchall(), [(4200,)])
            self.assertEqual(other.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            other.close()

    def test_alternative_0013_path_is_refused_without_mutation(self):
        other = open_db(self.folder / "alternate13.db", create=True)
        try:
            migrations = load_migrations()
            old_interest = self._synthetic_migration(
                13, "interest_recognition",
                "CREATE TABLE interest_recognitions (id INTEGER PRIMARY KEY, amount INTEGER NOT NULL);"
                "INSERT INTO interest_recognitions (amount) VALUES (12345);",
            )
            migrate(other, migrations[:12] + [old_interest])
            before = self._state(other)
            with self.assertRaisesRegex(MigrationError, "0013"):
                migrate(other, migrations)
            self.assertEqual(self._state(other), before)
            self.assertEqual(other.execute("SELECT amount FROM interest_recognitions").fetchall(), [(12345,)])
            self.assertEqual(other.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            other.close()

    def test_duplicate_migration_numbers_in_bundle_are_rejected(self):
        folder = self.folder / "conflicting_package"
        folder.mkdir()
        for source in MIGRATIONS_DIR.glob("*.sql"):
            shutil.copyfile(source, folder / source.name)
        (folder / "0012_installment_collections.sql").write_text(
            "CREATE TABLE legacy_collections (id INTEGER PRIMARY KEY);", encoding="utf-8"
        )
        with self.assertRaisesRegex(MigrationError, "ترقيم الترقيات"):
            load_migrations(folder)
        (folder / "0012_installment_collections.sql").unlink()
        (folder / "0013_interest_recognition.sql").write_text(
            "CREATE TABLE interest_stub (id INTEGER PRIMARY KEY);", encoding="utf-8"
        )
        with self.assertRaisesRegex(MigrationError, "ترقيم الترقيات"):
            load_migrations(folder)


if __name__ == "__main__":
    unittest.main()
