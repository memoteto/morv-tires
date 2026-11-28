"""Regression test for migration 0032: backfilling settlement_amount must not trip immutability."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core import dot
from morv.infra import inventory, ledger, purchasing, users
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
        self.assertEqual(inventory.reconcile(self.con), [])
        self.reversal_id = self._seed_reversal(self.return_id, self.return_total, qty=3)
        self.assertEqual(inventory.reconcile(self.con), [])

    def _seed_purchase(self, total: int, qty: int) -> int:
        with transaction(self.con):
            entry_id = ledger.post_entry(
                self.con, self.admin, "2026-10-01", "purchase_invoice", 1, "شراء قديم قبل 0032",
                [("1410", total, 0), ("2110", 0, total)])
            body = [self.sup, "old-inv-1", "2026-10-01", "2026-10-01",
                    [[self.item_id, qty, total // qty, "2324"]]]
            self.old_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()
            self.inv_id = self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, "
                "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, user_id) "
                "VALUES ('PI-000001', 'old-purchase-req-01', ?, ?, 'OLD-INV-1', '2026-10-01', "
                "'2026-10-01', ?, ?, ?)", (self.old_hash, self.sup, total, entry_id, self.admin.id)).lastrowid
            line_id = self.con.execute(
                "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total) "
                "VALUES (?, 1, ?, '2324', ?, ?, ?)",
                (self.inv_id, self.item_id, qty, total // qty, total)).lastrowid
            self.lot_id = self.con.execute(
                "INSERT INTO stock_lots (ref_no, item_id, purchase_line_id, dot, mfg_key, received_date, "
                "qty_received, qty_on_hand, purchase_unit_cost) VALUES ('PI-000001/1', ?, ?, '2324', ?, "
                "'2026-10-01', ?, ?, ?)",
                (self.item_id, line_id, dot.mfg_key("2324"), qty, qty, total // qty)).lastrowid
            self.layer_id = self.con.execute(
                "INSERT INTO cost_layers (item_id, purchase_line_id, received_date, qty_received, cost_total, "
                "qty_remaining, cost_remaining) VALUES (?, ?, '2026-10-01', ?, ?, ?, ?)",
                (self.item_id, line_id, qty, total, qty, total)).lastrowid
            self.con.execute(
                "INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_line_id, user_id) "
                "VALUES (?, ?, 'receipt', ?, ?, ?)",
                (self.item_id, self.lot_id, qty, line_id, self.admin.id))
            return line_id

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
            issue_id = self.con.execute(
                "INSERT INTO stock_issues (item_id, qty, cost_total, source_type, source_id, source_line_id, "
                "journal_entry_id, user_id) VALUES (?, ?, ?, 'purchase_return', ?, 1, ?, ?)",
                (self.item_id, qty, total, return_id, entry_id, self.admin.id)).lastrowid
            self.con.execute(
                "INSERT INTO purchase_return_lines (return_id, line_no, purchase_line_id, qty, value, issue_id) "
                "VALUES (?, 1, ?, ?, ?, ?)", (return_id, self.line_id, qty, total, issue_id))
            self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand - ? WHERE id=?", (qty, self.lot_id))
            self.con.execute(
                "INSERT INTO stock_movements (item_id, lot_id, kind, qty, issue_id, user_id) "
                "VALUES (?, ?, 'issue', ?, ?, ?)", (self.item_id, self.lot_id, -qty, issue_id, self.admin.id))
            self.con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining - ?, cost_remaining = cost_remaining - ? "
                             "WHERE id=?", (qty, total, self.layer_id))
            self.con.execute("INSERT INTO cost_allocations (issue_id, layer_id, qty, cost) VALUES (?, ?, ?, ?)",
                             (issue_id, self.layer_id, qty, total))
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
            line_pk = self.con.execute(
                "INSERT INTO purchase_return_lines (return_id, line_no, purchase_line_id, qty, value) "
                "VALUES (?, 1, ?, ?, ?)", (reversal_id, self.line_id, qty, total)).lastrowid
            self.con.execute("UPDATE stock_lots SET qty_on_hand = qty_on_hand + ? WHERE id=?", (qty, self.lot_id))
            self.con.execute(
                "INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_return_line_id, user_id) "
                "VALUES (?, ?, 'restock', ?, ?, ?)", (self.item_id, self.lot_id, qty, line_pk, self.admin.id))
            self.con.execute("UPDATE cost_layers SET qty_remaining = qty_remaining + ?, cost_remaining = cost_remaining + ? "
                             "WHERE id=?", (qty, total, self.layer_id))
            self.con.execute("INSERT INTO cost_allocations (purchase_return_line_id, layer_id, qty, cost) VALUES (?, ?, ?, ?)",
                             (line_pk, self.layer_id, -qty, -total))
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

    def test_upgrade_0033_preserves_balances_and_legacy_purchase_replay(self):
        tables = ("journal_entries", "journal_lines", "stock_lots", "stock_movements", "stock_issues", "cost_layers",
                  "cost_allocations", "purchase_return_lines")
        before = {table: self.con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() for table in tables}
        self.assertEqual(migrate(self.con, load_migrations()[:33]), [32, 33])
        self.assertEqual(current_version(self.con), 33)
        self.assertEqual(self.con.execute(
            "SELECT total, freight_amount, freight_basis, invoice_discount, payload_hash FROM purchase_invoices "
            "WHERE id=?", (self.inv_id,)).fetchone(), (400000, 0, "qty", 0, self.old_hash))
        self.assertEqual(self.con.execute(
            "SELECT line_total, line_discount, invoice_share, freight_share, net_total FROM purchase_lines "
            "WHERE id=?", (self.line_id,)).fetchone(), (400000, 0, 0, 0, 400000))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE purchase_lines SET net_total = 1 WHERE id=?", (self.line_id,))
        kw = dict(request_key="old-purchase-req-01", supplier_id=self.sup, supplier_invoice_no="OLD-INV-1",
                  invoice_date="2026-10-01", lines=[purchasing.PurchaseLine(self.item_id, 4, 100000, "2324")])
        replay = purchasing.post_invoice(self.con, self.admin, **kw)
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.id, replay.total), (self.inv_id, 400000))
        for changes in ({"freight_amount": 1}, {"invoice_discount": 1}, {"freight_basis": "value"},
                        {"lines": [purchasing.PurchaseLine(self.item_id, 4, 100000, "2324", 1)]},
                        {"lines": [purchasing.PurchaseLine(self.item_id, 3, 100000, "2324")]}):
            with self.subTest(changes=changes), self.assertRaises(purchasing.PurchaseError):
                purchasing.post_invoice(self.con, self.admin, **(kw | changes))
        self.assertEqual(self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0], 1)
        self.assertEqual(before, {table: self.con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                                  for table in tables})
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_upgrade_0039_preserves_legacy_returns_reversals_and_postings(self):
        self.assertEqual(migrate(self.con, load_migrations()[:38]), list(range(32, 39)))
        tables = ("purchase_returns", "purchase_return_lines", "purchase_invoices", "purchase_lines",
                  "journal_entries", "journal_lines", "stock_lots", "stock_movements", "stock_issues",
                  "cost_layers", "cost_allocations")
        columns = {t: [r[1] for r in self.con.execute(f"PRAGMA table_info({t})")] for t in tables}

        def snapshot():
            return {t: self.con.execute(f"SELECT {','.join(columns[t])} FROM {t} ORDER BY id").fetchall()
                    for t in tables}

        before = snapshot()
        self.assertEqual(migrate(self.con, load_migrations()[:39]), [39])
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.con.execute(
            "SELECT tax_total,deductible_tax_total,credit_note_ref,settlement_reason FROM purchase_returns"
        ).fetchall(), [(0, 0, "", ""), (0, 0, "", "")])
        self.assertEqual(self.con.execute(
            "SELECT tax_allocated,tax_amount,deductible_tax_amount,tax_mode FROM purchase_return_lines"
        ).fetchall(), [(0, 0, 0, "legacy"), (0, 0, 0, "legacy")])
        for sql in ("UPDATE purchase_returns SET tax_total=1", "DELETE FROM purchase_returns",
                    "UPDATE purchase_return_lines SET tax_amount=1", "DELETE FROM purchase_return_lines"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.con.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])


if __name__ == "__main__":
    unittest.main()
