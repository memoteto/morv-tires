"""Regression test for migration 0032: backfilling settlement_amount must not trip immutability."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.infra import ledger, users
from morv.infra.db.connection import open_db, transaction
from morv.infra.db.migrator import current_version, load_migrations, migrate


class PurchaseReturnSettlementMigrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.addCleanup(lambda: (os.environ.pop("MORV_DATA_DIR", None), shutil.rmtree(self.dir, ignore_errors=True)))
        self.con = open_db(self.dir / "old.db", create=True)
        self.addCleanup(self.con.close)

        self.migrations = load_migrations()[:32]
        self.assertEqual((self.migrations[-1].version, self.migrations[-1].name),
                         (32, "purchase_return_settlement"))
        migrate(self.con, self.migrations[:-1])
        self.assertEqual(current_version(self.con), 31)

        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد قديم', 'مورد قديم', '01000000002')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.item_id = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire', 'OLD-1', 'OLD-1', 'old-1', 100, 100, 100)").lastrowid

        # Seed a purchase, a return and its reversal with the pre-0032 schema's own columns
        # (no settlement_amount, and no migration-0033 freight/discount columns) — exactly the
        # state a database carried before this migration, so the backfill has rows to touch.
        self.line_id = self._seed_purchase(total=400000, qty=4)
        self.return_total = 300000
        self.return_id = self._seed_return(self.return_total, qty=3)
        self.reversal_id = self._seed_reversal(self.return_id, self.return_total)

    def _seed_purchase(self, total: int, qty: int) -> int:
        with transaction(self.con):
            entry_id = ledger.post_entry(
                self.con, self.admin, "2026-10-01", "purchase_invoice", 1, "شراء قديم قبل 0032",
                [("1410", total, 0), ("2110", 0, total)])
            inv_id = self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, "
                "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, user_id) "
                "VALUES ('PI-000001', 'old-purchase-req-01', 'f00d', ?, 'OLD-INV-1', '2026-10-01', "
                "'2026-10-01', ?, ?, ?)", (self.sup, total, entry_id, self.admin.id)).lastrowid
            return self.con.execute(
                "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total) "
                "VALUES (?, 1, ?, '2324', ?, ?, ?)",
                (inv_id, self.item_id, qty, total // qty, total)).lastrowid

    def _seed_return(self, total: int, qty: int) -> int:
        with transaction(self.con):
            entry_id = ledger.post_entry(
                self.con, self.admin, "2026-10-15", "purchase_return", 1, "مرتجع قديم قبل 0032",
                [("2110", total, 0), ("1410", 0, total)])
            return_id = self.con.execute(
                "INSERT INTO purchase_returns (number, request_key, payload_hash, supplier_id, kind, "
                "return_date, total, reason, journal_entry_id, user_id) "
                "VALUES ('PRT-OLD-01', 'old-return-req-01', 'deadbeef', ?, 'return', '2026-10-15', ?, "
                "'عيب تصنيع', ?, ?)", (self.sup, total, entry_id, self.admin.id)).lastrowid
            self.con.execute(
                "INSERT INTO purchase_return_lines (return_id, line_no, purchase_line_id, qty, value) "
                "VALUES (?, 1, ?, ?, ?)", (return_id, self.line_id, qty, total))
        return return_id

    def _seed_reversal(self, return_id: int, total: int) -> int:
        with transaction(self.con):
            entry_id = ledger.post_entry(
                self.con, self.admin, "2026-10-20", "purchase_return", 2, "عكس مرتجع قديم قبل 0032",
                [("1410", total, 0), ("2110", 0, total)])
            reversal_id = self.con.execute(
                "INSERT INTO purchase_returns (number, request_key, payload_hash, supplier_id, kind, "
                "return_date, total, reason, reverses_id, journal_entry_id, user_id) "
                "VALUES ('PRT-OLD-02', 'old-reversal-req-01', 'cafebabe', ?, 'reversal', '2026-10-20', ?, "
                "'خطأ في الإدخال', ?, ?, ?)",
                (self.sup, total, return_id, entry_id, self.admin.id)).lastrowid
            self.con.execute(
                "INSERT INTO purchase_return_lines (return_id, line_no, purchase_line_id, qty, value) "
                "VALUES (?, 1, ?, 1, ?)", (reversal_id, self.line_id, total))
        return reversal_id

    def test_upgrade_backfills_settlement_without_tripping_immutability(self):
        self.assertEqual(migrate(self.con, self.migrations), [32])
        self.assertEqual(current_version(self.con), 32)
        rows = dict(self.con.execute("SELECT id, settlement_amount FROM purchase_returns"))
        self.assertEqual(rows, {self.return_id: self.return_total, self.reversal_id: self.return_total})
        # the trigger must still be the original, enforcing one: not left dropped by the migration.
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE purchase_returns SET total = 1 WHERE id=?", (self.return_id,))


if __name__ == "__main__":
    unittest.main()
