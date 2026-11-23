"""Encrypted backup tests: vault, device key, both locks, tampering, USB, retention."""
import os
import shutil
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta
from pathlib import Path

from morv.core import security
from morv.infra import backup, keystore, settings, users, vault
from morv.infra.db.bootstrap import open_main_db

PW = "Morv-Tires-2026"


class _Device(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.rk = security.new_recovery_key()
        backup.setup_encryption(self.con, PW, self.rk)

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)


class VaultTest(_Device):
    def test_open_with_password_recovery_and_dek(self):
        r = backup.create_backup(self.con, "manual")
        self.assertTrue(r.ok and r.path.suffix == vault.EXT)
        self.assertNotIn(b"morv_tires", r.path.read_bytes()[200:])   # no readable database text
        out = self.dir / "plain.db"
        for secret in (PW, self.rk, self.rk.lower().replace("-", " ")):
            vault.open_vault(r.path, out, secret=secret)
            self.assertTrue(backup.verify_db_file(out)[0])
        vault.open_vault(r.path, out, dek=keystore.load_dek())
        with self.assertRaisesRegex(vault.VaultError, "غير صحيح"):
            vault.open_vault(r.path, out, secret="wrong-password-1")

    def test_any_tamper_is_detected(self):
        r = backup.create_backup(self.con, "manual")
        data = bytearray(r.path.read_bytes())
        for pos in (20, len(data) // 2, len(data) - 1):
            bad = bytearray(data)
            bad[pos] ^= 0x01
            r.path.write_bytes(bytes(bad))
            out = self.dir / "t.db"
            with self.assertRaises(vault.VaultError):
                vault.open_vault(r.path, out, secret=PW)
            self.assertFalse(out.exists())
            self.assertFalse(backup.restore_test(self.con, r.path).ok)
        r.path.write_bytes(bytes(data))
        self.assertTrue(backup.restore_test(self.con, r.path).ok)

    def test_foreign_file_refused(self):
        f = self.dir / "x.mtb"
        f.write_bytes(b"MORVBK1\n" + b"\x00\x00\x00\x02{}" + b"junk")
        with self.assertRaises(vault.VaultError):
            vault.read_header(f)
        f.write_bytes(b"not a vault at all")
        with self.assertRaises(vault.VaultError):
            vault.open_vault(f, self.dir / "o.db", secret=PW)


class KeysTest(_Device):
    def test_change_password_keeps_old_backups_openable(self):
        old = backup.create_backup(self.con, "manual").path
        backup.change_backup_password(self.con, PW, "New-Password-77")
        new = backup.create_backup(self.con, "manual").path
        out = self.dir / "o.db"
        vault.open_vault(old, out, secret=PW)
        vault.open_vault(old, out, secret=self.rk)
        vault.open_vault(new, out, secret="New-Password-77")
        vault.open_vault(new, out, secret=self.rk)
        with self.assertRaises(vault.VaultError):
            vault.open_vault(new, out, secret=PW)
        with self.assertRaises(vault.VaultError):
            backup.change_backup_password(self.con, "wrong-secret-99", "x")

    def test_regenerate_recovery_key(self):
        old = backup.create_backup(self.con, "manual").path
        rk2 = security.new_recovery_key()
        backup.regenerate_recovery_key(self.con, PW, rk2)
        new = backup.create_backup(self.con, "manual").path
        out = self.dir / "o.db"
        vault.open_vault(new, out, secret=rk2)
        vault.open_vault(old, out, secret=self.rk)
        with self.assertRaises(vault.VaultError):
            vault.open_vault(new, out, secret=self.rk)

    def test_relink_device_after_key_loss(self):
        keystore.forget_dek()
        self.assertIsNone(backup.device_dek(self.con))
        with self.assertRaisesRegex(backup.BackupError, "مفتاح الجهاز"):
            backup.create_backup(self.con, "manual")
        backup.relink_device(self.con, self.rk)
        self.assertTrue(backup.create_backup(self.con, "manual").ok)


class ScheduleTest(_Device):
    def test_daily_once_and_prune(self):
        self.assertIn("نسخة يومية", backup.run_scheduled(self.con))
        self.assertNotIn("نسخة يومية", backup.run_scheduled(self.con))
        d = backup.list_backups()[0].path.parent
        # 100 fake dailies with empty files; retention reads the name only
        start = datetime(2026, 1, 1)
        for i in range(100):
            (d / f"morv_tires_{(start + timedelta(days=i)).strftime('%Y%m%d_%H%M%S')}_000000_daily.mtb").write_bytes(b"x")
        for i in range(8):
            (d / f"morv_tires_{(start + timedelta(hours=i)).strftime('%Y%m%d_%H%M%S')}_{i:06d}_manual.mtb").write_bytes(b"x")
        backup.prune(d)
        kept = backup.list_backups(d)
        dailies = [b for b in kept if b.kind == "daily"]
        # the newest 14, plus the last backup of January and of February
        self.assertEqual(len(dailies), backup.KEEP_DAILY + 2)
        self.assertEqual(len([b for b in kept if b.kind == "manual"]), backup.KEEP_OTHER)

    def test_same_drive_warning(self):
        self.assertTrue(backup.same_drive_as_data(self.dir / "fake_usb"))

    def test_usb_copy_and_health(self):
        self.assertEqual(backup.health(self.con)[0], "danger")        # no backup yet
        backup.run_scheduled(self.con)
        state, text = backup.health(self.con)
        self.assertEqual("warn", state, "a copy that lives on the same machine is not a backup of the machine")
        self.assertIn("خارج الجهاز", text)
        usb = self.dir / "usb"
        settings.put(self.con, "backup.usb_dir", str(usb))
        self.assertEqual("danger", backup.health(self.con)[0])        # a folder is set, nothing ever reached it
        with self.assertRaisesRegex(backup.BackupError, "غير موصولة"):
            backup.copy_to_usb(self.con)
        usb.mkdir()
        r = backup.copy_to_usb(self.con)
        self.assertTrue(r.ok and r.path.parent == usb)
        state, text = backup.health(self.con)
        self.assertEqual("warn", state, "the temp folder shares the machine's disk")
        self.assertIn("قرص الجهاز نفسه", text)
        settings.put(self.con, "backup.last_ok_at", "2020-01-01 00:00:00")
        self.assertEqual(backup.health(self.con)[0], "danger")        # an old backup

    def test_the_scheduler_carries_a_copy_off_the_machine_without_being_asked(self):
        usb = self.dir / "usb"
        usb.mkdir()
        settings.put(self.con, "backup.usb_dir", str(usb))
        self.assertIn("نسخة على الفلاشة", backup.run_scheduled(self.con))
        self.assertEqual(1, len(list(usb.glob("*.mtb"))))
        self.assertNotIn("نسخة على الفلاشة", backup.run_scheduled(self.con), "one copy a day is enough")

    def test_the_restore_test_reads_the_copy_a_shop_would_actually_restore_from(self):
        usb = self.dir / "usb"
        usb.mkdir()
        settings.put(self.con, "backup.usb_dir", str(usb))
        self.assertIn("اختبار استعادة من الفلاشة: نجح", backup.run_scheduled(self.con))

    def test_an_off_device_copy_that_stopped_arriving_turns_red(self):
        usb = self.dir / "usb"
        usb.mkdir()
        settings.put(self.con, "backup.usb_dir", str(usb))
        backup.run_scheduled(self.con)
        # A temp folder always shares the machine's disk, so stand in for a real external drive
        with mock.patch.object(backup, "same_drive_as_data", return_value=False):
            for days, expected in ((0, "ok"), (8, "warn"), (20, "danger")):
                settings.put(self.con, "backup.last_usb_at",
                             (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"))
                self.assertEqual(expected, backup.off_device_state(self.con)[0], f"{days} days old")


class NewDeviceRestoreTest(unittest.TestCase):
    def setUp(self):
        self.a = Path(tempfile.mkdtemp())
        self.b = Path(tempfile.mkdtemp())
        self.usb = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.a)
        con = open_main_db(self.a / "morv_tires.db")
        boss = users.create_user(con, None, "admin", "صاحب المحل", "Admin12345", "manager")
        users.create_user(con, boss, "karim", "كريم", "Karim12345", "cashier")
        settings.put(con, "shop.name", "مورف للإطارات")
        self.rk = security.new_recovery_key()
        backup.setup_encryption(con, PW, self.rk)
        settings.put(con, "backup.usb_dir", str(self.usb))
        backup.create_backup(con, "manual")
        backup.copy_to_usb(con)
        self.expected = con.execute("SELECT username, display_name, role FROM users ORDER BY id").fetchall()
        con.close()

    def tearDown(self):
        os.environ.pop("MORV_DATA_DIR", None)
        for d in (self.a, self.b, self.usb):
            shutil.rmtree(d, ignore_errors=True)

    def _restore_on_b(self, secret):
        shutil.rmtree(self.b, ignore_errors=True)
        self.b.mkdir()
        os.environ["MORV_DATA_DIR"] = str(self.b)
        self.assertIsNone(keystore.load_dek())
        f = next(self.usb.glob("*.mtb"))
        open_main_db(self.b / "morv_tires.db").close()   # as the app does: an empty database before the wizard
        safety = backup.restore_file(self.b / "morv_tires.db", f, secret=secret)
        self.assertIsNone(safety)                     # an empty database needs no safety copy
        con = open_main_db(self.b / "morv_tires.db")
        try:
            self.assertEqual(con.execute("SELECT username, display_name, role FROM users ORDER BY id").fetchall(),
                             self.expected)
            self.assertEqual(settings.get(con, "shop.name"), "مورف للإطارات")
            self.assertEqual(users.login(con, "karim", "Karim12345").display_name, "كريم")
            backup.relink_device(con, secret)         # automatic backup works again
            self.assertTrue(backup.create_backup(con, "manual").ok)
        finally:
            con.close()

    def test_restore_with_password_then_with_recovery_key(self):
        self._restore_on_b(PW)
        self._restore_on_b(self.rk)
        shutil.rmtree(self.b, ignore_errors=True)
        self.b.mkdir()
        with self.assertRaises(vault.VaultError):
            backup.restore_file(self.b / "morv_tires.db", next(self.usb.glob("*.mtb")), secret="wrong-secret-00")
        self.assertFalse((self.b / "morv_tires.db").exists())

    def test_restore_over_existing_db_makes_safety_copy(self):
        os.environ["MORV_DATA_DIR"] = str(self.b)
        con = open_main_db(self.b / "morv_tires.db")
        users.create_user(con, None, "other", "جهاز آخر", "Other12345", "manager")
        con.close()
        f = next(self.usb.glob("*.mtb"))
        safety = backup.restore_file(self.b / "morv_tires.db", f, secret=PW)
        self.assertTrue(safety and safety.exists() and safety.suffix == vault.EXT)
        vault.open_vault(safety, self.b / "s.db", secret=PW)
        import sqlite3
        c = sqlite3.connect(self.b / "s.db")
        self.assertEqual(c.execute("SELECT username FROM users").fetchall(), [("other",)])
        c.close()


if __name__ == "__main__":
    unittest.main()
