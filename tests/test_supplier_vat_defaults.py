"""Explicit supplier VAT defaults and safe additive upgrade from v35."""
import tempfile
import unittest
from pathlib import Path

from morv.infra import suppliers, users
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate


class SupplierVatDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_supplier_vat_")
        self.addCleanup(self.temp.cleanup)
        self.con = open_db(Path(self.temp.name) / "old.db", create=True)
        self.addCleanup(self.con.close)
        self.migrations = load_migrations()
        self.assertEqual(self.migrations[35].version, 36)
        migrate(self.con, self.migrations[:35])
        self.supplier_id = self.con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile, tax_id) "
            "VALUES ('قديم', 'قديم', '01000000001', '123456789') RETURNING id").fetchone()[0]

    def test_upgrade_does_not_infer_registration_from_tax_number(self):
        migrate(self.con, self.migrations[:36])
        self.assertEqual(current_version(self.con), 36)
        supplier = suppliers.get(self.con, self.supplier_id)
        self.assertEqual(supplier.tax_id, "123456789")
        self.assertEqual(supplier.vat_mode, "unknown")
        self.assertEqual(supplier.name, "قديم")

    def test_profile_create_update_and_validation(self):
        migrate(self.con, self.migrations[:36])
        admin = users.create_user(self.con, None, "admin", "مدير", "Admin12345", "manager")
        supplier = suppliers.create(self.con, admin, suppliers.Supplier(
            None, "جديد", "01000000002", vat_status="registered", vat_prices_inclusive=True))
        self.assertEqual(supplier.vat_mode, "inclusive")
        supplier.vat_status = "unregistered"
        supplier = suppliers.update(self.con, admin, supplier)
        self.assertEqual(supplier.vat_mode, "none")
        supplier.vat_status = "invalid"
        with self.assertRaises(suppliers.SupplierError):
            suppliers.update(self.con, admin, supplier)
        self.assertEqual(suppliers.get(self.con, supplier.id).vat_mode, "none")
