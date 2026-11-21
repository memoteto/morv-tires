"""Data-layer permission checks: a user without the permission cannot write, whatever the UI does."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import security
from morv.infra import customers, items, shop, suppliers, users
from morv.infra.customers import CustomerError
from morv.infra.db.bootstrap import open_main_db
from morv.infra.items import ItemError
from morv.infra.suppliers import SupplierError

# Read-only or file-level permissions that have no data-layer write to guard yet.
UI_ONLY = {"items.cost_view", "treasury.view", "reports.view", "backup.restore"}


class PermissionsEnforcedTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "p.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        for perm in ("customers.manage",):
            users.set_permission(self.con, self.admin, self.cashier.id, perm, False)

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_every_permission_is_checked_in_infra(self):
        text = "".join(p.read_text(encoding="utf-8") for p in Path("morv/infra").rglob("*.py"))
        missing = [k for k in security.PERMISSIONS if k not in UI_ONLY and f'"{k}"' not in text]
        self.assertEqual(missing, [])

    def test_cashier_cannot_manage_users(self):
        with self.assertRaises(users.AuthError):
            users.create_user(self.con, self.cashier, "x1", "س", "Pass12345", "manager")
        with self.assertRaises(users.AuthError):
            users.create_user(self.con, None, "x2", "س", "Pass12345", "manager")   # not the first user
        with self.assertRaises(users.AuthError):
            users.set_role(self.con, self.cashier, self.cashier.id, "manager")
        with self.assertRaises(users.AuthError):
            users.set_permission(self.con, self.cashier, self.cashier.id, "sales.below_cost", True)
        with self.assertRaises(users.AuthError):
            users.set_password(self.con, self.cashier, self.admin.id, "Hacked12345")
        with self.assertRaises(users.AuthError):
            users.set_active(self.con, self.cashier, self.admin.id, False)

    def test_cashier_can_change_own_password(self):
        users.set_password(self.con, self.cashier, self.cashier.id, "NewPass12345")

    def test_cashier_cannot_edit_master_data_or_shop(self):
        # The permission check runs before the record is read, so None is enough as the record
        with self.assertRaises(CustomerError):
            customers.create(self.con, self.cashier, None)
        with self.assertRaises(SupplierError):
            suppliers.create(self.con, self.cashier, None)
        with self.assertRaises(ItemError):
            items.create(self.con, self.cashier, None)
        with self.assertRaises(shop.ShopError):
            shop.save(self.con, self.cashier, "محل", "01000000000", "")
