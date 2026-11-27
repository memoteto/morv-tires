"""P04-4b: migration 0035 adds the purchase-invoice/line input-VAT columns only, inert by default."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from morv.core import dot as dotmod
from morv.infra import inventory, ledger, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db, transaction
from morv.infra.db.migrator import current_version, load_migrations, migrate


class FreshDbTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_purchase_vat_")
        self.addCleanup(self.temp.cleanup)
        self.con = open_main_db(Path(self.temp.name) / "fresh.db")
        self.addCleanup(self.con.close)

    def test_columns_exist_with_inert_defaults(self):
        cols = {r[1]: r[4] for r in self.con.execute("PRAGMA table_info(purchase_invoices)")}
        self.assertEqual((cols["prices_tax_inclusive"], cols["freight_taxable"], cols["tax_total"]),
                         ("0", "0", "0"))
        line_cols = {r[1]: r[4] for r in self.con.execute("PRAGMA table_info(purchase_lines)")}
        self.assertEqual((line_cols["tax_base"], line_cols["tax_rate"], line_cols["tax_amount"],
                          line_cols["deductible_tax_amount"]), ("0", "0", "0", "0"))

    def test_purchase_invoices_rejects_out_of_range_boolean(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, "
                "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, "
                "prices_tax_inclusive) VALUES ('X', 'xxxxxxxx', 'h', 1, 'S-1', '2026-11-27', "
                "'2026-11-27', 100, 1, 2)")


class OldDbUpgradeTest(unittest.TestCase):
    """A v34 DB with a real posted purchase must upgrade to v35 with zero change to data or balances."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_purchase_vat_old_")
        self.addCleanup(self.temp.cleanup)
        self.con = open_db(Path(self.temp.name) / "old.db", create=True)
        self.addCleanup(self.con.close)
        all_migrations = load_migrations()
        self.assertEqual((all_migrations[34].version, all_migrations[34].name), (35, "purchase_input_vat"))
        self.migrations = all_migrations[:35]
        migrate(self.con, self.migrations[:34])
        self.assertEqual(current_version(self.con), 34)

        self.manager = users.create_user(self.con, None, "pvat_old_admin", "مدير", "Admin12345", "manager")
        self.supplier_id = self.con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد', 'مورد', '01000000003') "
            "RETURNING id").fetchone()[0]
        self.purchase_item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire', 'OLDV-T1', 'x', 'x', 100, 100, 100) RETURNING id").fetchone()[0]
        self._seed_v34_purchase(qty=4, unit_cost=30000, dot="2324")

        self.before = (
            current_version(self.con),
            ledger.account_balance(self.con, "1410"),
            ledger.account_balance(self.con, "2110"),
            ledger.account_balance(self.con, "1520"),
            inventory.on_hand(self.con, self.purchase_item),
            self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0],
            self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0],
        )

    def _seed_v34_purchase(self, qty: int, unit_cost: int, dot: str) -> None:
        total = qty * unit_cost
        with transaction(self.con):
            entry_id = ledger.post_entry(
                self.con, self.manager, "2026-11-27", "purchase_invoice", 1, "فاتورة شراء PI-000001",
                [("1410", total, 0), ("2110", 0, total)])
            h = hashlib.sha256(b"purchase-vat-fixture-v34").hexdigest()
            inv_id = self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, "
                "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, user_id) "
                "VALUES ('PI-000001', 'pvat-old-key-01', ?, ?, 'S-OLD-1', '2026-11-27', '2026-11-27', "
                "?, ?, ?)", (h, self.supplier_id, total, entry_id, self.manager.id)).lastrowid
            line_id = self.con.execute(
                "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total, "
                "net_total) VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                (inv_id, self.purchase_item, dot, qty, unit_cost, total, total)).lastrowid
            lot_id = self.con.execute(
                "INSERT INTO stock_lots (ref_no, item_id, purchase_line_id, dot, mfg_key, received_date, "
                "qty_received, qty_on_hand, purchase_unit_cost) VALUES "
                "('PI-000001/1', ?, ?, ?, ?, '2026-11-27', ?, ?, ?)",
                (self.purchase_item, line_id, dot, dotmod.mfg_key(dot), qty, qty, unit_cost)).lastrowid
            self.con.execute(
                "INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_line_id, user_id) "
                "VALUES (?, ?, 'receipt', ?, ?, ?)", (self.purchase_item, lot_id, qty, line_id, self.manager.id))
            self.con.execute(
                "INSERT INTO cost_layers (item_id, purchase_line_id, received_date, qty_received, cost_total, "
                "qty_remaining, cost_remaining) VALUES (?, ?, '2026-11-27', ?, ?, ?, ?)",
                (self.purchase_item, line_id, qty, total, qty, total))

    def test_upgrade_defaults_new_columns_to_inert_values(self):
        migrate(self.con, self.migrations)
        row = self.con.execute(
            "SELECT prices_tax_inclusive, freight_taxable, tax_total FROM purchase_invoices").fetchone()
        self.assertEqual(row, (0, 0, 0))
        line_row = self.con.execute(
            "SELECT tax_base, tax_rate, tax_amount, deductible_tax_amount FROM purchase_lines").fetchone()
        self.assertEqual(line_row, (0, 0, 0, 0))

    def test_upgrade_changes_no_existing_balance_stock_or_posting(self):
        migrate(self.con, self.migrations)
        after = (
            current_version(self.con),
            ledger.account_balance(self.con, "1410"),
            ledger.account_balance(self.con, "2110"),
            ledger.account_balance(self.con, "1520"),
            inventory.on_hand(self.con, self.purchase_item),
            self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0],
            self.con.execute("SELECT count(*) FROM purchase_invoices").fetchone()[0],
        )
        self.assertEqual(after, (35,) + self.before[1:])


if __name__ == "__main__":
    unittest.main()
