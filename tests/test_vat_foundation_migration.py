"""P04-4a: migration 0034 adds the VAT accounts, settings defaults and per-item treatment only."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.infra import settings
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate


class FreshDbTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_vat_foundation_")
        self.addCleanup(self.temp.cleanup)
        self.con = open_main_db(Path(self.temp.name) / "fresh.db")
        self.addCleanup(self.con.close)

    def account(self, code):
        return self.con.execute(
            "SELECT parent_code, kind, normal_side, postable FROM accounts WHERE code=?", (code,)
        ).fetchone()

    def test_input_vat_account_under_other_current_assets(self):
        self.assertEqual(self.account("1520"), ("15", "asset", "debit", 1))

    def test_output_vat_account_under_accruals_not_suppliers(self):
        self.assertEqual(self.account("2350"), ("23", "liability", "credit", 1))

    def test_tax_disabled_by_default_with_14pct_general_rate(self):
        self.assertEqual(settings.get(self.con, "tax.enabled"), "0")
        self.assertEqual(settings.get(self.con, "tax.rate"), "1400")

    def test_item_defaults_to_standard_treatment_no_rate_override(self):
        item_id = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire', 'VAT-T1', 'x', 'x', 100, 100, 100)").lastrowid
        row = self.con.execute(
            "SELECT tax_treatment, tax_rate FROM items WHERE id=?", (item_id,)).fetchone()
        self.assertEqual(row, ("standard", None))

    def test_item_rejects_unknown_treatment(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, "
                "price_super, tax_treatment) VALUES ('tire', 'VAT-T2', 'x', 'x', 100, 100, 100, 'half')")

    def test_item_rejects_negative_rate_override(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, "
                "price_super, tax_rate) VALUES ('tire', 'VAT-T3', 'x', 'x', 100, 100, 100, -1)")


class OldDbUpgradeTest(unittest.TestCase):
    """An item posted before 0034 must backfill to the safe default, not break or need recoding."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_vat_foundation_old_")
        self.addCleanup(self.temp.cleanup)
        self.con = open_db(Path(self.temp.name) / "old.db", create=True)
        self.addCleanup(self.con.close)
        self.migrations = load_migrations()
        self.assertEqual((self.migrations[33].version, self.migrations[33].name), (34, "vat_foundation"))
        migrate(self.con, self.migrations[:33])
        self.assertEqual(current_version(self.con), 33)
        self.old_item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('battery', 'OLD-B1', 'x', 'x', 100, 100, 100)").lastrowid

    def test_upgrade_backfills_standard_treatment_on_existing_item(self):
        migrate(self.con, self.migrations)
        self.assertEqual(current_version(self.con), 34)
        row = self.con.execute(
            "SELECT tax_treatment, tax_rate FROM items WHERE id=?", (self.old_item,)).fetchone()
        self.assertEqual(row, ("standard", None))
        self.assertEqual(settings.get(self.con, "tax.enabled"), "0")


if __name__ == "__main__":
    unittest.main()
