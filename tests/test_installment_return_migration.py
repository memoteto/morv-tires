"""Regression of posted synthetic returns during v12 -> v13 migration."""
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import inventory, ledger, purchasing, sale_returns, sales, users
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn


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
            purchasing.post_invoice(con, manager, request_key="old-purchase-01", supplier_id=sid,
                                    supplier_invoice_no="old1", invoice_date="2026-11-01",
                                    lines=[PurchaseLine(iid, 2, 5000, "2324")])
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
            self.assertEqual(inventory.reconcile(con), [])
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual({table: tuple(con.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                              for table in original}, original)
            self.assertEqual({code: ledger.account_balance(con, code)
                              for code in balance_before}, balance_before)
            self.assertEqual(current_version(con), 13)
        finally:
            if con is not None:
                con.close()
            shutil.rmtree(folder, ignore_errors=True)
