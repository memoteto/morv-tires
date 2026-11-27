"""Regression test for migration 0032: backfilling settlement_amount must not trip immutability."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.infra import inventory, ledger, purchasing, users
from morv.infra.db.connection import open_db, transaction
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.purchasing import PurchaseLine


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

        # A purchase posted while 0032's own code (purchasing.post_invoice) is unaffected by it.
        inv = purchasing.post_invoice(
            self.con, self.admin, request_key="old-purchase-0001", supplier_id=self.sup,
            supplier_invoice_no="OLD-INV-1", invoice_date="2026-10-01",
            lines=[PurchaseLine(self.item_id, 4, 100000, "2324")])
        self.line_id, self.lot_id, self.layer_id = self.con.execute(
            "SELECT pl.id, l.id, c.id FROM purchase_lines pl JOIN stock_lots l ON l.purchase_line_id = pl.id "
            "JOIN cost_layers c ON c.purchase_line_id = pl.id WHERE pl.invoice_id = ?", (inv.id,)).fetchone()

        # A return and its reversal, seeded exactly as pre-0032 code would have posted them: no
        # settlement_amount column yet, so the only journal lines are the supplier/inventory pair.
        self.return_total = 300000     # 3 of the 4 units
        self.return_id = self._seed_return(self.return_total, qty=3)
        self.reversal_id = self._seed_reversal(self.return_id, self.return_total, qty=3)

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
            self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - ? WHERE id=?", (qty, self.lot_id))
            self.con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining - ?, cost_remaining = cost_remaining - ? "
                             "WHERE id=?", (qty, total, self.layer_id))
        return return_id

    def _seed_reversal(self, return_id: int, total: int, qty: int) -> int:
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
                "VALUES (?, 1, ?, ?, ?)", (reversal_id, self.line_id, qty, total))
            self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + ? WHERE id=?", (qty, self.lot_id))
            self.con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining + ?, cost_remaining = cost_remaining + ? "
                             "WHERE id=?", (qty, total, self.layer_id))
        return reversal_id

    def test_upgrade_backfills_settlement_without_tripping_immutability(self):
        self.assertEqual(migrate(self.con, self.migrations), [32])
        self.assertEqual(current_version(self.con), 32)
        rows = dict(self.con.execute("SELECT id, settlement_amount FROM purchase_returns"))
        self.assertEqual(rows, {self.return_id: self.return_total, self.reversal_id: self.return_total})
        # the trigger must still be the original, enforcing one: not left dropped by the migration.
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE purchase_returns SET total = 1 WHERE id=?", (self.return_id,))
        self.assertEqual(inventory.reconcile(self.con), [])


if __name__ == "__main__":
    unittest.main()
