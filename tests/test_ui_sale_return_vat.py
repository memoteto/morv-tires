"""Direct sales-return editing, original VAT previews, fixed footer and stale-draft protection."""
import unittest
from types import SimpleNamespace

from morv.core.sales_rules import Payment
from morv.infra import installments, ledger, sale_returns, sale_view, settings
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn
from tests.qt_app import get_app, require_qt
from tests.test_sale_output_vat import _Fixture


@require_qt()
class SaleReturnUiTest(_Fixture, unittest.TestCase):
    version = 38

    def setUp(self):
        super().setUp()
        self.app = get_app()
        self.dialogs = []
        self.parents = []
        self.addCleanup(self.close_dialogs)

    def close_dialogs(self):
        from PySide6.QtCore import QEvent

        for dlg in self.dialogs:
            dlg.close()
            dlg.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for parent in self.parents:
            parent.close()
            parent.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def make_dialog(self, vat=True, parent=None):
        from morv.ui.sale_return_dialog import SaleReturnDialog

        settings.put(self.con, "tax.enabled", "1" if vat else "0")
        sale = self.post(f"return-ui-source-{vat}", [SaleLineIn(self.service, 3, 10000)],
                         [Payment("cash", 10000, received=10000)], invoice_discount=20000,
                         prices_tax_inclusive=True if vat else None)
        settings.put(self.con, "tax.enabled", "0" if vat else "1")
        dlg = SaleReturnDialog(SimpleNamespace(con=self.con, user=self.admin),
                               sale_view.get_sale(self.con, sale.id),
                               sale_view.returnable_lines(self.con, sale.id),
                               sale_view.refund_methods(self.con, sale.id), parent)
        dlg.return_date.setText("2026-11-11")
        dlg.reason.setCurrentText("اختبار المرتجع")
        self.dialogs.append(dlg)
        return sale, dlg

    def test_quantity_tax_and_post_match_original_after_disabling_vat(self):
        sale, dlg = self.make_dialog()
        model = dlg.model
        self.assertFalse(dlg.post_btn.isEnabled())
        self.assertFalse(dlg.view.isColumnHidden(model.TAX))
        qty = model.index(0, model.QTY)
        self.assertFalse(model.setData(qty, 4))
        self.assertTrue(model.setData(qty, 1))
        self.assertEqual(model.amounts(0), (3333, 409))
        self.assertTrue(dlg.post_btn.isEnabled())
        self.assertIn("4.09", dlg.tax_label.text())
        self.assertIn("33.33", dlg.total_label.text())
        self.assertTrue(model.setData(model.index(0, model.CONDITION), "defective"))
        self.assertTrue(model.setData(qty, 0))
        self.assertFalse(dlg.post_btn.isEnabled())
        dlg.all_btn.click()
        self.assertEqual(model.amounts(0), (10000, 1228))
        self.assertEqual(dlg.lines[0].condition, "defective")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.con.execute(
            "SELECT total,tax_total FROM sale_returns WHERE sale_id=?", (sale.id,)).fetchone(), (10000, 1228))
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_bulk_clear_keeps_conditions_and_disables_approval(self):
        _, dlg = self.make_dialog()
        self.assertFalse(dlg.clear_btn.isEnabled())
        dlg.all_btn.click()
        model = dlg.model
        self.assertTrue(model.setData(model.index(0, model.CONDITION), "defective"))
        dlg.clear_btn.click()
        self.assertEqual(model.quantities, [0])
        self.assertEqual(model.conditions, ["defective"])
        self.assertFalse(dlg.clear_btn.isEnabled())
        self.assertFalse(dlg.post_btn.isEnabled())
        dlg.all_btn.click()
        self.assertEqual(dlg.lines[0].condition, "defective")

    def test_legacy_invoice_hides_tax_even_when_tax_enabled_now(self):
        _, dlg = self.make_dialog(False)
        self.assertTrue(dlg.view.isColumnHidden(dlg.model.TAX))
        self.assertTrue(dlg.tax_label.parentWidget().isHidden())
        self.assertTrue(dlg.before_tax_label.parentWidget().isHidden())
        dlg.all_btn.click()
        self.assertEqual(dlg.model.amounts(0), (10000, 0))

    def test_open_quantity_editor_is_committed_before_approval(self):
        sale, dlg = self.make_dialog()
        dlg.show()
        self.app.processEvents()
        index = dlg.model.index(0, dlg.model.QTY)
        dlg.view.setCurrentIndex(index)
        if dlg.editor.active_editor is None:
            dlg.view.edit(index)
        self.app.processEvents()
        editor = dlg.editor.active_editor
        self.assertIsNotNone(editor)
        editor.setValue(1)
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.con.execute(
            "SELECT total,tax_total FROM sale_returns WHERE sale_id=?", (sale.id,)).fetchone(), (3333, 409))

    def test_taxable_interest_return_is_blocked_in_preview(self):
        from morv.ui.sale_return_dialog import SaleReturnDialog

        settings.put(self.con, "tax.enabled", "1")
        sale = self.post("ui-vat-installment", [SaleLineIn(self.service, 2, 50000)],
                         [Payment("installment", 114000)], customer_id=self.customer,
                         prices_tax_inclusive=False)
        installments.post_contract(
            self.con, self.admin, request_key="ui-vat-contract", sale_id=sale.id,
            down=0, rate_bp=1000, months=2, first_due="2026-12-10",
            national_id="29001011201234", contract_date="2026-11-10")
        dlg = SaleReturnDialog(SimpleNamespace(con=self.con, user=self.admin),
                               sale_view.get_sale(self.con, sale.id),
                               sale_view.returnable_lines(self.con, sale.id),
                               sale_view.refund_methods(self.con, sale.id))
        self.dialogs.append(dlg)
        dlg.return_date.setText("2026-11-11")
        dlg.all_btn.click()
        self.assertFalse(dlg.post_btn.isEnabled())
        self.assertIn("بفوائد", dlg.refund_breakdown.text())
        self.assertEqual(self.con.execute("SELECT count(*) FROM sale_returns").fetchone()[0], 0)

    def test_stale_preview_is_refused_atomically(self):
        sale, dlg = self.make_dialog()
        self.assertTrue(dlg.model.setData(dlg.model.index(0, dlg.model.QTY), 1))
        sale_returns.post_return(self.con, self.admin, request_key="another-return",
                                 sale_id=sale.id, lines=[ReturnLineIn(dlg.lines[0].sale_line_id, 1)],
                                 reason="اختبار", return_date="2026-11-11")
        before = self.snapshot(("sale_returns", "sale_return_lines", "journal_entries", "journal_lines"))
        dlg.post()
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.snapshot(before), before)

    def test_fixed_footer_and_screen_bounds_with_offset_parent(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        self.parents.append(parent)
        available = self.app.primaryScreen().availableGeometry()
        parent.resize(300, 240)
        parent.move(available.right() - 100, available.bottom() - 100)
        parent.show()
        _, dlg = self.make_dialog(parent=parent)
        dlg.show()
        self.app.processEvents()
        self.assertIsNone(dlg.scroll)
        self.assertTrue(available.contains(dlg.frameGeometry()))
        self.assertTrue(dlg.footer.isVisible())
        self.assertTrue(dlg.rect().contains(dlg.post_btn.mapTo(dlg, QPoint(0, 0))))
        self.assertTrue(dlg.rect().contains(dlg.post_btn.mapTo(
            dlg, QPoint(dlg.post_btn.width() - 1, dlg.post_btn.height() - 1))))
