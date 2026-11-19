"""Regression tests for visible stock-lot identity and movement provenance.

These tests intentionally describe the expected UI contract. Run them against
the current implementation before making any presentation changes.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class StockRowAssociationTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import purchasing, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.session import Session

        self.tmp = Path(tempfile.mkdtemp(prefix="morv-stock-association-"))
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "association.db")
        self.admin = users.create_user(
            self.con, None, "admin", "المدير", "Admin12345", "manager"
        )
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        self.con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile) VALUES (?,?,?)",
            ("مورد تجريبي", "مورد تجريبي", "01000000011"),
        )
        supplier_id = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.con.execute(
            "INSERT INTO items "
            "(kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-ASSOC','إطار الاختبار','إطار الاختبار',100,100,100)"
        ).lastrowid
        purchasing.post_invoice(
            self.con, self.admin,
            request_key="stock-association-invoice-01",
            supplier_id=supplier_id,
            supplier_invoice_no="TEST-ASSOC-01",
            invoice_date="2026-11-01",
            lines=[
                PurchaseLine(self.tire, 2, 100000, "2024"),
                PurchaseLine(self.tire, 4, 150000, "2024"),
                PurchaseLine(self.tire, 3, 120000, "2124"),
            ],
        )

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _page(self, page_type):
        from PySide6.QtCore import QEvent
        page = page_type(self.session)

        def cleanup():
            page.close()
            page.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

        self.addCleanup(cleanup)
        return page

    @staticmethod
    def _table_rows(table):
        model = table.model()
        return [
            " | ".join(str(model.data(model.index(row, col)) or "")
                       for col in range(model.columnCount()))
            for row in range(model.rowCount())
        ]

    def test_same_dot_lots_keep_reference_quantity_and_cost_in_each_table_row(self):
        from PySide6.QtWidgets import QTableView
        from morv.core.money import fmt
        from morv.infra import inventory
        from morv.ui.stock_pages import StockPage

        lots = inventory.list_lots(self.con, self.tire)
        same_dot = [lot for lot in lots if lot[2] == "2024"]
        self.assertEqual(len(same_dot), 2)
        self.assertEqual(len({lot[1] for lot in same_dot}), 2)
        self.assertEqual(len({lot[6] for lot in same_dot}), 2)

        page = self._page(StockPage)
        page.show_preview_id(self.tire)
        table = page.preview_body.findChild(QTableView, "summaryTable")
        self.assertIsNotNone(table)
        rendered_rows = self._table_rows(table)
        self.assertEqual(len(rendered_rows), len(lots))

        # A detached list of references is not sufficient: the reference must
        # be readable alongside its own DOT, quantity and historical unit cost.
        for _id, ref, dot, _received, _received_qty, on_hand, unit_cost in same_dot:
            with self.subTest(reference=ref):
                matches = [row for row in rendered_rows if ref in row]
                self.assertEqual(
                    len(matches), 1,
                    f"Lot reference {ref} is not associated with exactly one "
                    f"table row: {rendered_rows}",
                )
                self.assertIn(dot, matches[0])
                self.assertIn(str(on_hand), matches[0])
                self.assertIn(fmt(unit_cost), matches[0])

    def test_override_reason_is_attached_to_its_own_issue_row(self):
        from PySide6.QtWidgets import QTableView
        from morv.infra import inventory, stock_view
        from morv.ui.stock_pages import LotsPage

        lot_id = self.con.execute(
            "SELECT id FROM stock_lots WHERE item_id=? AND dot='2124'",
            (self.tire,),
        ).fetchone()[0]
        reason = "صرف استثنائي للدفعة الأحدث"
        inventory.issue(
            self.con, self.admin, self.tire, 1,
            source_type="stock-association-test",
            source_id=173, source_line_id=1,
            picks={lot_id: 1}, reason=reason,
            issue_date="2026-11-03",
        )
        movements = stock_view.lot_movements(self.con, lot_id)
        self.assertEqual(len(movements), 2)
        self.assertTrue(any(
            kind == "issue" and non_oldest and stored_reason == reason
            for kind, _qty, _at, non_oldest, stored_reason in movements
        ), "The engine did not store the expected override reason")

        page = self._page(LotsPage)
        lot_row = next(row for row in page.model.rows if row[0] == lot_id)
        page.show_preview(lot_row)
        table = page.preview_body.findChild(QTableView, "summaryTable")
        self.assertIsNotNone(table)
        rendered_rows = self._table_rows(table)
        self.assertEqual(len(rendered_rows), len(movements))

        issue_rows = [row for row in rendered_rows if "صرف" in row]
        receipt_rows = [row for row in rendered_rows if "استلام" in row]
        self.assertEqual(len(issue_rows), 1)
        self.assertEqual(len(receipt_rows), 1)
        self.assertIn(
            reason, issue_rows[0],
            f"Override reason must accompany its issue row, not a detached "
            f"note: {rendered_rows}",
        )
        self.assertNotIn(reason, receipt_rows[0])


if __name__ == "__main__":
    unittest.main()
