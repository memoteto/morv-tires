"""Regression of posted synthetic returns during v12 -> v13 migration."""
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import dot as dotmod
from morv.core.sales_rules import Payment
from morv.infra import inventory, ledger, sale_returns, sales, users
from morv.infra.db.connection import open_db, transaction
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn


def _seed_v12_purchase(con, by, *, supplier_id: int, item_id: int, qty: int, unit_cost: int, dot: str) -> None:
    """Writes a purchase exactly as post_invoice did at schema v12, before P01/P02 added
    freight/discount columns (migration 0033) — purchasing.post_invoice now requires those
    columns, so a fixture meant to sit at v12 seeds this one step with the matching raw SQL
    instead, the same way this test already seeds suppliers/items directly."""
    total = qty * unit_cost
    with transaction(con):
        inv_id = con.execute("SELECT coalesce(max(id), 0) + 1 FROM purchase_invoices").fetchone()[0]
        number = f"PI-{inv_id:06d}"
        entry_id = ledger.post_entry(con, by, "2026-11-01", "purchase_invoice", inv_id, f"فاتورة شراء {number}",
                                     [("1410", total, 0), ("2110", 0, total)])
        con.execute(
            "INSERT INTO purchase_invoices (id, number, request_key, payload_hash, supplier_id, "
            "supplier_invoice_no, invoice_date, received_date, total, journal_entry_id, user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (inv_id, number, "old-purchase-01", "seed-hash-v12", supplier_id, "old1", "2026-11-01",
             "2026-11-01", total, entry_id, by.id))
        line_id = con.execute(
            "INSERT INTO purchase_lines (invoice_id, line_no, item_id, dot, qty, unit_cost, line_total) "
            "VALUES (?,1,?,?,?,?,?)", (inv_id, item_id, dot, qty, unit_cost, total)).lastrowid
        lot_id = con.execute(
            "INSERT INTO stock_lots (ref_no, item_id, purchase_line_id, dot, mfg_key, received_date, "
            "qty_received, qty_on_hand, purchase_unit_cost) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"{number}/1", item_id, line_id, dot, dotmod.mfg_key(dot), "2026-11-01", qty, qty,
             unit_cost)).lastrowid
        con.execute("INSERT INTO stock_movements (item_id, lot_id, kind, qty, purchase_line_id, user_id) "
                    "VALUES (?,?,'receipt',?,?,?)", (item_id, lot_id, qty, line_id, by.id))
        con.execute("INSERT INTO cost_layers (item_id, purchase_line_id, received_date, qty_received, "
                    "cost_total, qty_remaining, cost_remaining) VALUES (?,?,?,?,?,?,?)",
                    (item_id, line_id, "2026-11-01", qty, total, qty, total))


class MigrationOfExistingData(unittest.TestCase):
    def test_existing_posted_return_and_stock_links_survive_upgrade(self):
        folder = Path(tempfile.mkdtemp(prefix="morv_old_return_upgrade_"))
        con = None
        try:
            con = open_db(folder / "old_synthetic.db", create=True)
            migrate(con, load_migrations()[:12])
            self.assertEqual(current_version(con), 12)
            manager = users.create_user(con, None, "oldadmin", "مدير وهمي", "Admin12345", "manager")
            sid = con.execute(
                "INSERT INTO suppliers (name,name_norm,mobile) "
                "VALUES ('مورد وهمي','مورد وهمي','01000000001')").lastrowid
            iid = con.execute(
                "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
                "VALUES ('tire','TR-OLD','إطار وهمي','اطار وهمي',10000,10000,10000)").lastrowid
            _seed_v12_purchase(con, manager, supplier_id=sid, item_id=iid, qty=2, unit_cost=5000, dot="2324")
            sale = sales.post_sale(con, manager, request_key="old-sale-001", customer_id=None,
                                   lines=[SaleLineIn(iid, 1, 10000)],
                                   payments=[Payment("cash", 10000, received=10000)],
                                   sale_date="2026-11-10")
            line_id = con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
            old_return = sale_returns.post_return(
                con, manager, request_key="old-return-01", sale_id=sale.id,
                lines=[ReturnLineIn(line_id, 1)], reason="مرتجع وهمي",
                return_date="2026-11-12")
            original = {table: tuple(con.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                        for table in ("sale_returns", "sale_return_lines", "stock_movements",
                                      "cost_allocations", "journal_entries", "journal_lines")}
            balance_before = {code: ledger.account_balance(con, code)
                              for code in ("1110", "1410", "4190")}
            before = con.execute("SELECT id,total,refund_method FROM sale_returns").fetchall()
            self.assertEqual(len(before), 1)
            self.assertEqual(migrate(con, load_migrations()[:13]), [13])
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute("SELECT id,total,refund_method FROM sale_returns").fetchall(), before)
            self.assertEqual(con.execute("SELECT return_id FROM sale_return_lines").fetchone()[0], old_return.id)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual({table: tuple(con.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                              for table in original}, original)
            self.assertEqual({code: ledger.account_balance(con, code)
                              for code in balance_before}, balance_before)
            self.assertEqual(current_version(con), 13)
            migrate(con)      # reconcile reads tables of the latest schema, so it runs after the full upgrade
            self.assertEqual(inventory.reconcile(con), [])
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            if con is not None:
                con.close()
            shutil.rmtree(folder, ignore_errors=True)
