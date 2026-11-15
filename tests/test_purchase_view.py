"""Purchase read-model tests: list, search and details."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.infra import purchase_view, purchasing, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine


class PurchaseViewTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "m.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        ids = []
        for kind, code in (("tire", "TR-1"), ("battery", "BT-1")):
            ids.append(self.con.execute(
                "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
                "VALUES (?,?,?,?,100,100,100)", (kind, code, code, code)).lastrowid)
        purchasing.post_invoice(self.con, self.admin, request_key="key-000001", supplier_id=sup, supplier_invoice_no="A-1",
                                invoice_date="2026-11-01", lines=[PurchaseLine(ids[0], 4, 100050, "2324")])
        purchasing.post_invoice(self.con, self.admin, request_key="key-000002", supplier_id=sup, supplier_invoice_no="B-2",
                                invoice_date="2026-11-02", lines=[PurchaseLine(ids[1], 2, 5000), PurchaseLine(ids[0], 1, 90000, "0124")],
                                notes="ملاحظة")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_list_newest_first_and_count(self):
        rows = purchase_view.list_invoices(self.con)
        self.assertEqual([r[1] for r in rows], ["PI-000002", "PI-000001"])
        self.assertEqual(purchase_view.count(self.con), 2)
        self.assertEqual(rows[1][5], 4 * 100050)

    def test_search_by_number_supplier_and_supplier_invoice(self):
        self.assertEqual(len(purchase_view.list_invoices(self.con, "PI-000001")), 1)
        self.assertEqual(len(purchase_view.list_invoices(self.con, "مورد")), 2)
        self.assertEqual(len(purchase_view.list_invoices(self.con, "B-2")), 1)
        self.assertEqual(purchase_view.list_invoices(self.con, "لا يوجد"), [])

    def test_get_invoice_with_lines(self):
        inv = purchase_view.get_invoice(self.con, 2)
        self.assertEqual((inv.number, inv.supplier_invoice_no, inv.notes), ("PI-000002", "B-2", "ملاحظة"))
        self.assertEqual([(ln.line_no, ln.item_code, ln.dot, ln.qty, ln.line_total) for ln in inv.lines],
                         [(1, "BT-1", None, 2, 10000), (2, "TR-1", "0124", 1, 90000)])
        self.assertEqual(inv.total, 100000)
        with self.assertRaises(KeyError):
            purchase_view.get_invoice(self.con, 99)
