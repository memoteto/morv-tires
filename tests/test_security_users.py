"""Password, recovery-key, user, login and audit-trail tests."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from morv.core import security
from morv.infra import users
from morv.infra.db.bootstrap import open_main_db


class SecurityTest(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        h1, h2 = security.hash_password("Secret123"), security.hash_password("Secret123")
        self.assertNotEqual(h1, h2)
        self.assertTrue(security.verify_password("Secret123", h1))
        self.assertFalse(security.verify_password("secret123", h1))
        self.assertFalse(security.verify_password("Secret123", "garbage"))
        with self.assertRaises(ValueError):
            security.hash_password("short")

    def test_recovery_key_format(self):
        k = security.new_recovery_key()
        parts = k.split("-")
        self.assertEqual(len(parts), 6)
        self.assertTrue(all(len(p) == 4 for p in parts))
        self.assertFalse(any(c in k for c in "0O1I"))
        self.assertEqual(security.normalize_recovery_key(k.lower().replace("-", " ")), k)
        self.assertEqual(security.normalize_recovery_key(k.replace("A", "a")), k)
        with self.assertRaises(ValueError):
            security.normalize_recovery_key("ABCD-1234")

    def test_backup_password_policy(self):
        self.assertIsNotNone(security.check_backup_password("short1"))
        self.assertIsNotNone(security.check_backup_password("onlylettersxx"))
        self.assertIsNone(security.check_backup_password("Morv-Tires-2026"))

    def test_strength(self):
        self.assertEqual(security.password_strength(""), 0)
        self.assertEqual(security.password_strength("abcdefgh"), 2)
        self.assertEqual(security.password_strength("abcdefgh1234"), 4)


class UsersTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "u.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_create_and_login(self):
        u = users.login(self.con, "admin", "Admin12345")
        self.assertTrue(u.is_manager)
        with self.assertRaises(users.AuthError):
            users.login(self.con, "admin", "wrong")
        with self.assertRaises(users.AuthError):
            users.login(self.con, "nobody", "x")
        actions = [r[3] for r in users.audit_rows(self.con)]
        self.assertEqual(actions[:3], ["login.failed", "login.failed", "login.ok"])
        self.assertIn("user.create", actions)

    def test_lockout_after_five_failures(self):
        for _ in range(4):
            with self.assertRaises(users.AuthError):
                users.login(self.con, "admin", "bad")
        with self.assertRaisesRegex(users.AuthError, "إيقاف الدخول"):
            users.login(self.con, "admin", "bad")
        with self.assertRaisesRegex(users.AuthError, "موقوف مؤقتًا"):
            users.login(self.con, "admin", "Admin12345")
        future = datetime.now() + timedelta(minutes=security.LOCK_MINUTES + 1)
        users._now = lambda: future
        try:
            self.assertEqual(users.login(self.con, "admin", "Admin12345").username, "admin")
        finally:
            users._now = datetime.now

    def test_duplicate_username_and_validation(self):
        with self.assertRaisesRegex(users.AuthError, "مستخدم من قبل"):
            users.create_user(self.con, self.admin, "ADMIN", "آخر", "Pass12345", "cashier")
        with self.assertRaises(users.AuthError):
            users.create_user(self.con, self.admin, "ab", "قصير", "Pass12345", "cashier")
        with self.assertRaises(users.AuthError):
            users.create_user(self.con, self.admin, "karim", "كريم", "short", "cashier")
        self.assertEqual(users.count_users(self.con), 1)

    def test_last_manager_protected(self):
        with self.assertRaisesRegex(users.AuthError, "مدير واحد"):
            users.set_role(self.con, self.admin, self.admin.id, "cashier")
        with self.assertRaisesRegex(users.AuthError, "حسابك الحالي"):
            users.set_active(self.con, self.admin, self.admin.id, False)
        second = users.create_user(self.con, self.admin, "boss2", "مدير ثانٍ", "Boss123456", "manager")
        users.set_active(self.con, second, self.admin.id, False)   # now another manager is active
        with self.assertRaisesRegex(users.AuthError, "مدير واحد"):
            users.set_active(self.con, self.admin, second.id, False)

    def test_permissions(self):
        c = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.assertEqual(users.permissions(self.con, self.admin), set(security.PERMISSIONS))
        self.assertEqual(users.permissions(self.con, c), {"customers.manage", "sales.post"})   # the cashier default
        users.set_permission(self.con, self.admin, c.id, "backup.restore", True)
        self.assertTrue(users.has_permission(self.con, c, "backup.restore"))
        users.set_permission(self.con, self.admin, c.id, "backup.restore", False)
        self.assertFalse(users.has_permission(self.con, c, "backup.restore"))
        with self.assertRaises(users.AuthError):
            users.set_permission(self.con, self.admin, c.id, "sale.void", True)

    def test_password_change(self):
        with self.assertRaises(users.AuthError):
            users.change_own_password(self.con, self.admin, "wrong", "NewPass12345")
        with self.assertRaisesRegex(users.AuthError, "تختلف"):   # found in the owner's testing
            users.change_own_password(self.con, self.admin, "Admin12345", "Admin12345")
        users.change_own_password(self.con, self.admin, "Admin12345", "NewPass12345")
        self.assertEqual(users.login(self.con, "admin", "NewPass12345").id, self.admin.id)
        c = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        users.set_password(self.con, self.admin, c.id, "Temp123456", must_change=True)
        self.assertTrue(users.login(self.con, "karim", "Temp123456").must_change_pw)


if __name__ == "__main__":
    unittest.main()
