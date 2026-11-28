"""P04-4C.2: item VAT controls use the shared form and preserve hidden values."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.qt_app import get_app, require_qt


@require_qt()
class ItemVatDialogTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import items, users
        from morv.infra.db.bootstrap import open_main_db

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"MORV_DATA_DIR": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.con = open_main_db(Path(temp.name) / "item-vat.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "مدير", "Admin12345", "manager")
        self.item = items.create(self.con, self.admin, items.Item(
            None, "service", "VAT-SV", "خدمة اختبار", price_retail=114000,
            tax_treatment="standard", tax_rate=500))
        self.app = get_app()

    def dialog(self, by=None):
        from morv.infra import items
        from morv.ui.inventory_page import ItemDialog
        from morv.ui.session import Session
        from morv.ui.widgets import dispose

        dlg = ItemDialog(Session(self.con, by or self.admin), items.get(self.con, self.item.id))
        self.addCleanup(dispose, dlg, self.app)
        dlg.show()
        self.app.processEvents()
        return dlg

    def test_enabled_controls_follow_treatment_and_save_explicit_change(self):
        from morv.infra import items, settings
        settings.put(self.con, "tax.enabled", "1")
        dlg = self.dialog()
        self.assertTrue(dlg.tax_treatment.isEnabled())
        self.assertTrue(dlg.tax_rate.isVisible())
        self.assertEqual(dlg.tax_rate.text(), "5.00")
        dlg.tax_treatment.setCurrentIndex(dlg.tax_treatment.findData("exempt"))
        self.app.processEvents()
        self.assertTrue(dlg.tax_rate.isHidden())
        layout = dlg.fields_box.layout()
        self.assertTrue(layout.labelForField(dlg.tax_rate).isHidden())
        dlg.tax_treatment.setCurrentIndex(dlg.tax_treatment.findData("standard"))
        self.app.processEvents()
        self.assertTrue(dlg.tax_rate.isVisible())
        self.assertEqual(dlg.tax_rate.text(), "5.00")
        dlg.tax_treatment.setCurrentIndex(dlg.tax_treatment.findData("zero"))
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        saved = items.get(self.con, self.item.id)
        self.assertEqual((saved.tax_treatment, saved.tax_rate, saved.price_retail), ("zero", None, 114000))

    def test_disabled_form_preserves_saved_vat(self):
        from morv.infra import items
        dlg = self.dialog()
        self.assertIsNone(dlg.tax_treatment)
        self.assertIsNone(dlg.tax_rate)
        dlg.widgets["name"].setText("خدمة معدلة")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        saved = items.get(self.con, self.item.id)
        self.assertEqual((saved.tax_treatment, saved.tax_rate, saved.name),
                         ("standard", 500, "خدمة معدلة"))

    def test_non_manager_sees_read_only_vat_even_with_item_permissions(self):
        from morv.infra import items, settings, users
        settings.put(self.con, "tax.enabled", "1")
        cashier = users.create_user(self.con, self.admin, "cash", "كاشير", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "items.manage", True)
        users.set_permission(self.con, self.admin, cashier.id, "items.prices", True)
        dlg = self.dialog(cashier)
        self.assertFalse(dlg.tax_treatment.isEnabled())
        self.assertFalse(dlg.tax_rate.isEnabled())
        dlg.widgets["name"].setText("تعديل عادي")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        saved = items.get(self.con, self.item.id)
        self.assertEqual((saved.tax_treatment, saved.tax_rate, saved.name),
                         ("standard", 500, "تعديل عادي"))
