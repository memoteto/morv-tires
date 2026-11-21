"""Supplier return tests: stock and layers, ledger, reversal, caps, replay, balances and permission."""
import sqlite3
import unittest

from morv.infra import inventory, ledger, purchase_returns, reports, sales, treasury
from morv.infra.purchase_returns import PurchaseReturnError, ReturnLine
from morv.core.sales_rules import Payment
from morv.infra.sales import SaleLineIn
from tests.test_purchasing import Base


class PurchaseReturnsTest(Base):
    def line_id(self, item_id):
        return self.con.execute("SELECT id FROM purchase_lines WHERE item_id=? ORDER BY id DESC",
                                (item_id,)).fetchone()[0]

    def send(self, line, qty, key=None, **kw):
        self.n += 1
        args = dict(request_key=key or f"ret-key-{self.n:04d}", supplier_id=self.sup,
                    lines=[ReturnLine(line, qty)], reason="عيب تصنيع", return_date="2026-11-10")
        args.update(kw)
        return purchase_returns.post_return(self.con, self.admin, **args)

    def balance(self, code):
        return ledger.account_balance(self.con, code)

    def test_return_moves_stock_layer_and_ledger(self):
        self.buy(self.tire, 4, 100000)
        line = self.line_id(self.tire)
        r = self.send(line, 3)
        self.assertEqual(r.total, 300000)
        self.assertEqual(self.balance("1410"), 100000)
        self.assertEqual(self.balance("2110"), -100000)
        self.assertEqual(self.con.execute("SELECT qty_on_hand FROM stock_lots").fetchone()[0], 1)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 100000)
        self.assertReconciled()

    def test_caps_and_validation(self):
        self.buy(self.tire, 2, 100000)
        line = self.line_id(self.tire)
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 3)
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 0)
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 1, reason="x")
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 1, return_date="2026-10-01")     # before the lot was received
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 1, supplier_id=9999)
        self.assertEqual(self.con.execute("SELECT count(*) FROM purchase_returns").fetchone()[0], 0)
        self.assertReconciled()

    def test_cannot_return_what_was_sold(self):
        self.buy(self.tire, 2, 100000)
        line = self.line_id(self.tire)
        sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=None, sale_date="2026-11-10",
                        lines=[SaleLineIn(self.tire, 2, 150000)], payments=[Payment("cash", 300000, received=300000)])
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 1)
        self.assertReconciled()

    def test_cap_when_pick_and_fifo_diverge(self):
        self.buy(self.tire, 2, 100000, "2024")
        a = self.line_id(self.tire)
        self.buy(self.tire, 2, 120000, "2124")
        b = self.line_id(self.tire)
        lot_b = self.lot_ids(self.tire)[1]
        inventory.issue(self.con, self.admin, self.tire, 1, source_type="test", source_id=1, source_line_id=1,
                        picks={lot_b: 1}, reason="طلب العميل دفعة أحدث")
        for line in (a, b):                      # lot A keeps 2 but its layer lost 1; lot B lost 1 but its layer kept 2
            with self.assertRaises(PurchaseReturnError):
                self.send(line, 2)
            self.send(line, 1)
        self.assertReconciled()

    def test_reversal_restores_everything(self):
        self.buy(self.tire, 4, 100000)
        line = self.line_id(self.tire)
        before = (self.balance("1410"), self.balance("2110"))
        r = self.send(line, 3)
        rev = purchase_returns.reverse_return(self.con, self.admin, request_key="rev-key-0001", return_id=r.id,
                                              reason="خطأ في الإدخال")
        self.assertEqual((self.balance("1410"), self.balance("2110")), before)
        self.assertEqual(self.con.execute("SELECT qty_on_hand FROM stock_lots").fetchone()[0], 4)
        self.assertEqual(treasury.supplier_owed(self.con, self.sup), 400000)
        self.assertReconciled()
        with self.assertRaises(PurchaseReturnError):
            purchase_returns.reverse_return(self.con, self.admin, request_key="rev-key-0002", return_id=r.id,
                                            reason="مرة ثانية")
        with self.assertRaises(PurchaseReturnError):
            purchase_returns.reverse_return(self.con, self.admin, request_key="rev-key-0003", return_id=rev.id,
                                            reason="عكس العكس")
        self.send(line, 4)                                    # the whole lot can go again
        self.assertReconciled()

    def test_replay_and_key_conflict(self):
        self.buy(self.tire, 4, 100000)
        line = self.line_id(self.tire)
        a = self.send(line, 1, key="same-key-0001")
        b = self.send(line, 1, key="same-key-0001")
        self.assertTrue(b.replayed and a.id == b.id)
        self.assertEqual(self.con.execute("SELECT qty_on_hand FROM stock_lots").fetchone()[0], 3)
        with self.assertRaises(PurchaseReturnError):
            self.send(line, 2, key="same-key-0001")

    def test_permission_and_immutability(self):
        self.buy(self.tire, 2, 100000)
        line = self.line_id(self.tire)
        with self.assertRaises(PurchaseReturnError):
            purchase_returns.post_return(self.con, self.cashier, request_key="perm-key-0001", supplier_id=self.sup,
                                         lines=[ReturnLine(line, 1)], reason="عيب تصنيع")
        r = self.send(line, 1)
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE purchase_returns SET total = 1 WHERE id=?", (r.id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM purchase_returns WHERE id=?", (r.id,))

    def test_supplier_report_ties_to_ledger(self):
        self.buy(self.tire, 4, 100000)
        line = self.line_id(self.tire)
        self.send(line, 3)
        t = reports.build(self.con, "suppliers", "2000-01-01", "2026-11-21")
        self.assertIn("يطابق الدفتر", t.subtitle)
        self.assertEqual(t.rows[-1][1][1], 100000)

    def test_battery_and_duplicate_line(self):
        self.buy(self.battery, 2, 50000, None)
        line = self.line_id(self.battery)
        with self.assertRaises(PurchaseReturnError):
            purchase_returns.post_return(self.con, self.admin, request_key="dup-key-0001", supplier_id=self.sup,
                                         lines=[ReturnLine(line, 1), ReturnLine(line, 1)], reason="عيب تصنيع")
        self.send(line, 2)
        self.assertReconciled()

    def test_history_flags_reversed(self):
        self.buy(self.tire, 2, 100000)
        line = self.line_id(self.tire)
        r = self.send(line, 1)
        purchase_returns.reverse_return(self.con, self.admin, request_key="rev-key-0009", return_id=r.id,
                                        reason="خطأ في الإدخال")
        h = purchase_returns.history(self.con, self.sup)
        self.assertEqual([(x[2], x[7]) for x in h], [("reversal", 0), ("return", 1)])


if __name__ == "__main__":
    unittest.main()
