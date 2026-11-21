"""Stock adjustments: gains and losses, cost sourcing, reversal, replay, permissions and immutability."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import inventory, purchasing, sales, stock_adjustments, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.purchasing import PurchaseLine
from morv.infra.sales import SaleLineIn
from morv.infra.stock_adjustments import AdjustLine, StockAdjustError


class StockAdjustTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "a.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        self.sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("TR-1")
        self.fresh = self.item("TR-2")                  # never purchased: no recorded cost
        self.buy(self.tire, 2, 100000, "2024", "k-buy-0001")
        self.buy(self.tire, 2, 120000, "2124", "k-buy-0002")
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def item(self, code):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire',?,?,?,20000,18000,16000) RETURNING id", (code, code, code.lower())).fetchone()[0]

    def buy(self, item_id, qty, cost, dot, key):
        purchasing.post_invoice(self.con, self.admin, request_key=key, supplier_id=self.sup,
                                supplier_invoice_no=f"S-{key}", invoice_date="2026-11-01",
                                lines=[PurchaseLine(item_id, qty, cost, dot)])

    def adjust(self, lines, reason="جرد شهري", by=None):
        self.n += 1
        return stock_adjustments.post_adjustment(self.con, by or self.admin, request_key=f"adj-key-{self.n:04d}",
                                                 adjust_date="2026-11-10", reason=reason, lines=lines)

    def bal(self, code):
        return self.con.execute("SELECT coalesce(sum(debit - credit), 0) FROM journal_lines WHERE account_code=?",
                                (code,)).fetchone()[0]

    def test_loss_issues_fifo_cost_to_5270(self):
        r = self.adjust([AdjustLine(self.tire, "loss", 3)])
        self.assertEqual(r.loss_total, 2 * 100000 + 120000)
        self.assertEqual(self.bal("5270"), r.loss_total)
        self.assertEqual(self.bal("4220"), 0)
        self.assertEqual(inventory.on_hand(self.con, self.tire), 1)
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_gain_uses_source_lot_cost_then_last_purchase_then_manual(self):
        lot = self.con.execute("SELECT id FROM stock_lots WHERE dot='2024'").fetchone()[0]
        a = self.adjust([AdjustLine(self.tire, "gain", 2, dot="2024")])          # DOT matches a recorded lot
        self.assertEqual(a.gain_total, 200000)
        b = self.adjust([AdjustLine(self.tire, "gain", 1, source_lot_id=lot)])
        self.assertEqual(b.gain_total, 100000)
        c = self.adjust([AdjustLine(self.tire, "gain", 1)])                     # last purchase
        self.assertEqual(c.gain_total, 120000)
        self.assertEqual(self.bal("4220"), -(200000 + 100000 + 120000))
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_gain_without_any_reference_needs_a_positive_manual_cost(self):
        for bad in (None, 0, -5):
            with self.assertRaisesRegex(StockAdjustError, "تكلفة"):
                self.adjust([AdjustLine(self.fresh, "gain", 1, manual_unit_cost=bad)])
        self.assertEqual(self.adjust([AdjustLine(self.fresh, "gain", 2, manual_unit_cost=50000)]).gain_total, 100000)
        with self.assertRaisesRegex(StockAdjustError, "مرجع"):
            self.adjust([AdjustLine(self.tire, "gain", 1, manual_unit_cost=1)])
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_surplus_and_shortage_stay_in_separate_accounts(self):
        r = self.adjust([AdjustLine(self.tire, "loss", 1), AdjustLine(self.fresh, "gain", 1, manual_unit_cost=7000)])
        self.assertEqual((r.loss_total, r.gain_total), (100000, 7000))
        self.assertEqual((self.bal("5270"), self.bal("4220")), (100000, -7000))

    def test_loss_beyond_stock_is_refused_and_writes_nothing(self):
        before = self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0]
        with self.assertRaises(StockAdjustError):
            self.adjust([AdjustLine(self.tire, "loss", 99)])
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], before)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_adjustments").fetchone()[0], 0)

    def test_reversing_a_loss_restores_lots_layers_and_ledger(self):
        snap = self.con.execute("SELECT id, qty_on_hand FROM stock_lots ORDER BY id").fetchall()
        value = inventory.book_value(self.con)
        r = self.adjust([AdjustLine(self.tire, "loss", 3)])
        rev = stock_adjustments.reverse_adjustment(self.con, self.admin, request_key="rev-key-0001",
                                                   adjustment_id=r.id, reason="خطأ في العد")
        self.assertEqual(self.con.execute("SELECT id, qty_on_hand FROM stock_lots ORDER BY id").fetchall(), snap)
        self.assertEqual(inventory.book_value(self.con), value)
        self.assertEqual((self.bal("5270"), rev.gain_total), (0, r.loss_total))
        self.assertEqual(inventory.reconcile(self.con), [])
        with self.assertRaisesRegex(StockAdjustError, "معكوسة"):
            stock_adjustments.reverse_adjustment(self.con, self.admin, request_key="rev-key-0002",
                                                 adjustment_id=r.id, reason="مرة ثانية")

    def test_reversing_a_gain_is_refused_once_units_were_sold(self):
        g = self.adjust([AdjustLine(self.tire, "gain", 1, dot="2024")])
        sales.post_sale(self.con, self.admin, request_key="sale-key-0001", customer_id=None,
                        lines=[SaleLineIn(self.tire, 5, 20000)], payments=[Payment("cash", 100000, received=100000)],
                        sale_date="2026-11-11")
        with self.assertRaisesRegex(StockAdjustError, "صُرفت|بيعت"):
            stock_adjustments.reverse_adjustment(self.con, self.admin, request_key="rev-key-0003",
                                                 adjustment_id=g.id, reason="خطأ")
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_reversing_an_untouched_gain_removes_it(self):
        g = self.adjust([AdjustLine(self.tire, "gain", 2, dot="2024")])
        stock_adjustments.reverse_adjustment(self.con, self.admin, request_key="rev-key-0004",
                                             adjustment_id=g.id, reason="خطأ")
        self.assertEqual(inventory.on_hand(self.con, self.tire), 4)
        self.assertEqual((self.bal("4220"), self.bal("5270")), (0, 0))   # the reversal mirrors the original accounts
        self.assertEqual(inventory.reconcile(self.con), [])

    def test_replay_returns_same_document_and_changed_payload_is_refused(self):
        kw = dict(adjust_date="2026-11-10", reason="جرد", lines=[AdjustLine(self.tire, "loss", 1)])
        a = stock_adjustments.post_adjustment(self.con, self.admin, request_key="same-key-0001", **kw)
        b = stock_adjustments.post_adjustment(self.con, self.admin, request_key="same-key-0001", **kw)
        self.assertEqual((a.id, b.replayed), (b.id, True))
        with self.assertRaisesRegex(StockAdjustError, "بمحتوى مختلف"):
            stock_adjustments.post_adjustment(self.con, self.admin, request_key="same-key-0001", adjust_date="2026-11-10",
                                              reason="جرد", lines=[AdjustLine(self.tire, "loss", 2)])
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_adjustments").fetchone()[0], 1)

    def test_cashier_cannot_preview_post_or_reverse(self):
        with self.assertRaisesRegex(StockAdjustError, "صلاحية"):
            self.adjust([AdjustLine(self.tire, "loss", 1)], by=self.cashier)
        with self.assertRaisesRegex(StockAdjustError, "صلاحية"):
            stock_adjustments.preview(self.con, self.cashier, [AdjustLine(self.tire, "loss", 1)])
        r = self.adjust([AdjustLine(self.tire, "loss", 1)])
        with self.assertRaisesRegex(StockAdjustError, "صلاحية"):
            stock_adjustments.reverse_adjustment(self.con, self.cashier, request_key="rev-key-0005",
                                                 adjustment_id=r.id, reason="خطأ")

    def test_preview_writes_nothing_and_matches_the_post(self):
        lines = [AdjustLine(self.tire, "loss", 3)]
        p = stock_adjustments.preview(self.con, self.admin, lines)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_adjustments").fetchone()[0], 0)
        self.assertEqual(p[0].value, self.adjust(lines).loss_total)

    def test_one_line_per_item_and_reason_required(self):
        with self.assertRaisesRegex(StockAdjustError, "مكرر"):
            self.adjust([AdjustLine(self.tire, "loss", 1), AdjustLine(self.tire, "gain", 1)])
        with self.assertRaisesRegex(StockAdjustError, "سبب"):
            self.adjust([AdjustLine(self.tire, "loss", 1)], reason=" ")

    def test_posted_adjustment_is_immutable(self):
        r = self.adjust([AdjustLine(self.tire, "loss", 1)])
        for sql in ("UPDATE stock_adjustments SET reason='x' WHERE id=?", "DELETE FROM stock_adjustments WHERE id=?"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql, (r.id,))
