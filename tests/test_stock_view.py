"""اختبارات قراءة المخزون للعرض (بلا واجهة): الدفعات والملخص والإجماليات والمطابقة مع المحرك."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.infra import inventory, purchase_view, purchasing, stock_view, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine


class StockViewTest(unittest.TestCase):
    """استعلامات العرض تطابق المحرك."""

    def setUp(self):
        """فاتورة بدفعتين DOT وبطارية، ثم صرف."""
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "m.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        ids = [self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super, min_qty) "
            "VALUES (?,?,?,?,100,100,100,?)", (k, c, c, c, m)).lastrowid
            for k, c, m in (("tire", "TR-1", 10), ("battery", "BT-1", 0), ("service", "SV-1", 0))]
        self.tire, self.battery, self.service = ids
        purchasing.post_invoice(self.con, self.admin, request_key="key-000001", supplier_id=self.sup,
                                supplier_invoice_no="S-1", invoice_date="2026-11-01",
                                lines=[PurchaseLine(self.tire, 4, 100000, "2124"), PurchaseLine(self.tire, 2, 90000, "2024"),
                                       PurchaseLine(self.battery, 3, 50000)])
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=1, source_line_id=1)

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_lots_order_and_filters(self):
        """الأقدم تصنيعًا أولًا، والمستنفدة تُستثنى افتراضيًا، والبحث بـ DOT والمرجع."""
        rows = stock_view.lots(self.con)
        self.assertEqual([(r[3], r[4], r[7]) for r in rows], [("BT-1", None, 3), ("TR-1", "2024", 1), ("TR-1", "2124", 4)])
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="t", source_id=2, source_line_id=1)
        self.assertEqual(len(stock_view.lots(self.con)), 2)
        self.assertEqual(len(stock_view.lots(self.con, in_stock_only=False)), 3)
        self.assertEqual([r[4] for r in stock_view.lots(self.con, "2124")], ["2124"])
        self.assertEqual(len(stock_view.lots(self.con, "PI-000001/3", in_stock_only=False)), 1)

    def test_movements(self):
        """حركات الدفعة: استلام ثم صرف."""
        lot = self.con.execute("SELECT id FROM stock_lots WHERE dot='2024'").fetchone()[0]
        self.assertEqual([(m[0], m[1]) for m in stock_view.lot_movements(self.con, lot)], [("receipt", 2), ("issue", -1)])

    def test_summary_matches_engine(self):
        """الملخص يطابق on_hand وbook_value، والخدمات خارجه، وفلتر حد الطلب."""
        rows = {r[2]: r for r in stock_view.summary(self.con)}
        self.assertEqual(set(rows), {"TR-1", "BT-1"})
        for code, item in (("TR-1", self.tire), ("BT-1", self.battery)):
            self.assertEqual(rows[code][4], inventory.on_hand(self.con, item))
            self.assertEqual(rows[code][6], inventory.book_value(self.con, item))
        self.assertEqual([r[2] for r in stock_view.summary(self.con, filter_key="low")], ["TR-1"])
        self.assertEqual(stock_view.totals(self.con), (8, inventory.book_value(self.con)))
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_supplier_totals(self):
        """عدد فواتير المورد وإجماليها."""
        self.assertEqual(purchase_view.supplier_totals(self.con, self.sup), (1, 4 * 100000 + 2 * 90000 + 3 * 50000))
        self.assertEqual(purchase_view.supplier_totals(self.con, 999), (0, 0))
