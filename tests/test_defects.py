"""Fault-injection tests: one permanent test per defect found in review."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from morv.core import security
from morv.core.money import MoneyError, parse_money
from morv.infra import backup, keystore, settings, setup, users, vault
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import NestedTransaction, transaction

PW = "Morv-Tires-2026"
ROOT = Path(__file__).resolve().parent.parent


class _Device(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.db = self.dir / "morv_tires.db"
        self.con = open_main_db(self.db)
        users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.rk = security.new_recovery_key()
        backup.setup_encryption(self.con, PW, self.rk)

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)


class Defect6MoneyParse(unittest.TestCase):
    def test_accepts_valid_forms(self):
        self.assertEqual(parse_money("1,234.50"), 123450)
        self.assertEqual(parse_money("١٬٢٣٤٫٥٠"), 123450)
        self.assertEqual(parse_money(".5"), 50)

    def test_rejects_misparsed_inputs(self):
        for bad in ("1,50", "1 2", "1e5", "Infinity", "NaN", "1.234", "1,23,456", ""):
            with self.subTest(value=bad), self.assertRaises(MoneyError):
                parse_money(bad)


class Defect5VaultHeader(_Device):
    def test_truncated_files_raise_vault_error(self):
        f = next(Path(backup.create_backup(self.con, "manual").path).parent.glob("*.mtb"))
        data = f.read_bytes()
        cut = self.dir / "cut.mtb"
        header_end = 8 + 4 + int.from_bytes(data[8:12], "big")
        for n in (0, 3, 9, 11, 20, header_end - 1):          # inside the header: the header reader refuses it
            with self.subTest(header_cut=n):
                cut.write_bytes(data[:n])
                with self.assertRaises(vault.VaultError):
                    vault.read_header(cut)
        for n in (0, 3, 9, 11, 20, header_end - 1, header_end + 5, len(data) // 2, len(data) - 1):
            with self.subTest(length=n):                      # every length: opening refuses it
                cut.write_bytes(data[:n])
                with self.assertRaises(vault.VaultError):
                    vault.open_vault(cut, self.dir / "out.db", secret=PW)


class Defect1AtomicRestore(_Device):
    def _backup_file(self):
        return Path(backup.create_backup(self.con, "manual").path)

    def test_failed_replace_leaves_db_byte_identical(self):
        f = self._backup_file()
        users.create_user(self.con, None, "later", "لاحق", "Later12345", "cashier")
        self.con.close()
        before = self.db.read_bytes()
        with mock.patch("morv.infra.backup.os.replace", side_effect=OSError("قرص ممتلئ")):
            with self.assertRaises(backup.BackupError):
                backup.restore_file(self.db, f, secret=PW)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(self.db.with_name(self.db.name + ".restore-new").exists())
        self.con = open_main_db(self.db)

    def test_bad_backup_leaves_db_byte_identical(self):
        f = self._backup_file()
        self.con.close()
        before = self.db.read_bytes()
        with mock.patch("morv.infra.backup.verify_db_file", return_value=(False, "مُحاكى")):
            with self.assertRaises(backup.BackupError):
                backup.restore_file(self.db, f, secret=PW)
        self.assertEqual(self.db.read_bytes(), before)
        self.con = open_main_db(self.db)

    def test_successful_restore_replaces_and_keeps_safety_copy(self):
        f = self._backup_file()
        users.create_user(self.con, None, "later", "لاحق", "Later12345", "cashier")
        self.con.close()
        safety = backup.restore_file(self.db, f, secret=PW)
        self.assertTrue(safety and safety.exists())
        self.con = open_main_db(self.db)
        names = {r[0] for r in self.con.execute("SELECT username FROM users")}
        self.assertNotIn("later", names)


class Defect2KeyGuard(_Device):
    def test_mismatched_device_key_refuses_backup(self):
        before = {p.name for p in backup.list_backups()}
        keystore.save_dek(os.urandom(32))
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.con, "manual")
        self.assertEqual({p.name for p in backup.list_backups()}, before)
        self.assertEqual(backup.health(self.con)[0], "danger")

    def test_relink_with_secret_restores_backups(self):
        keystore.save_dek(os.urandom(32))
        backup.relink_device(self.con, PW)
        self.assertTrue(backup.create_backup(self.con, "manual").ok)

    def _strip_fingerprint(self):
        raw = json.loads(settings.get(self.con, "backup.keyring"))
        raw.pop("fp", None)
        settings.put(self.con, "backup.keyring", json.dumps(raw))
        return raw

    def test_legacy_ring_is_proved_by_an_old_backup(self):
        backup.create_backup(self.con, "manual")                  # a backup from before the fingerprint was lost
        raw = self._strip_fingerprint()
        self.assertIsNone(vault.matches(raw, keystore.load_dek()))
        self.assertTrue(backup.create_backup(self.con, "manual").ok)
        self.assertIn("fp", json.loads(settings.get(self.con, "backup.keyring")))

    def test_legacy_ring_without_proof_refuses_with_hint(self):
        before = {p.name for p in backup.list_backups()}
        self._strip_fingerprint()
        with self.assertRaisesRegex(backup.BackupError, "إعادة الربط|أعد ربط"):
            backup.create_backup(self.con, "manual")
        self.assertEqual({p.name for p in backup.list_backups()}, before)


class Defect3ResumableSetup(_Device):
    def test_failed_device_key_leaves_nothing_written(self):
        con = open_main_db(self.dir / "fresh.db")
        try:
            with mock.patch("morv.infra.keystore.save_dek", side_effect=keystore.KeystoreError("مرفوض")):
                with self.assertRaises(backup.BackupError):
                    setup.run_first_setup(con, **self._args())
            self.assertEqual(users.count_users(con), 0)
            self.assertFalse(setup.is_complete(con))
            self.assertFalse(backup.is_encryption_ready(con))
        finally:
            con.close()

    def test_resume_reuses_admin_and_completes(self):
        con = open_main_db(self.dir / "resume.db")
        try:
            users.create_user(con, None, "admin", "قديم", "Old123456", "manager")
            setup.run_first_setup(con, **self._args())
            self.assertEqual(users.count_users(con), 1)
            self.assertTrue(setup.is_complete(con))
            self.assertTrue(backup.is_encryption_ready(con))
        finally:
            con.close()

    def test_completed_setup_refuses_second_run(self):
        con = open_main_db(self.dir / "twice.db")
        try:
            setup.run_first_setup(con, **self._args())
            with self.assertRaises(setup.SetupError):
                setup.run_first_setup(con, **self._args())
        finally:
            con.close()

    def test_migration_0005_marks_existing_database_complete(self):
        sql = (ROOT / "morv/infra/db/migrations/0005_setup_flag.sql").read_text(encoding="utf-8")
        self.con.execute("DELETE FROM settings WHERE key='setup.completed_at'")
        self.con.executescript(sql)
        self.assertTrue(setup.is_complete(self.con))
        bare = open_main_db(self.dir / "bare.db")
        try:
            bare.execute("DELETE FROM settings WHERE key='setup.completed_at'")
            bare.executescript(sql)
            self.assertFalse(setup.is_complete(bare))       # no user and no keyring
        finally:
            bare.close()

    def _args(self):
        return dict(shop_name="محل اختبار", shop_phone="01000000000", shop_address="",
                    admin_username="owner", admin_name="المالك", admin_password="Owner12345",
                    backup_password="Backup-Pass-2026", recovery_key=security.new_recovery_key())


class Defect4Transactions(_Device):
    def test_failed_role_change_rolls_back_and_connection_recovers(self):
        cashier = users.create_user(self.con, None, "karim", "كريم", "Karim12345", "cashier")
        admin = users.get_user(self.con, 1)
        with mock.patch("morv.infra.users.audit", side_effect=RuntimeError("فشل مُحاكى")):
            with self.assertRaises(RuntimeError):
                users.set_role(self.con, admin, cashier.id, "manager")
        self.assertFalse(self.con.in_transaction)
        self.assertEqual(users.get_user(self.con, cashier.id).role, "cashier")
        users.create_user(self.con, None, "next", "التالي", "Next12345", "cashier")   # must work

    def test_nested_transaction_is_refused(self):
        with self.assertRaises(NestedTransaction):
            with transaction(self.con):
                with transaction(self.con):
                    pass
        self.assertFalse(self.con.in_transaction)

    def test_no_manual_transaction_statements_outside_connection(self):
        offenders = []
        for f in (ROOT / "morv").rglob("*.py"):
            # connection.py defines the helper, and migrator.py runs scripted migrations in one transaction
            if f.name in ("connection.py", "migrator.py") or "__pycache__" in f.parts:
                continue
            text = f.read_text(encoding="utf-8")
            for word in ('execute("BEGIN', 'execute("COMMIT', 'execute("ROLLBACK'):
                if word in text:
                    offenders.append(f.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
