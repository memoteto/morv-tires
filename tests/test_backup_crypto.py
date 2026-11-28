"""Encrypted backup tests: vault, device key, both locks, tampering, USB, retention."""
import os
import shutil
import sqlite3
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


class RestorePreflightTest(unittest.TestCase):
    """A file that is structurally fine can still be logically broken; it must not reach the live path."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _good_db(self) -> Path:
        from morv.infra.db.bootstrap import open_main_db
        path = self.dir / "source.db"
        con = open_main_db(path)
        con.close()
        return path

    def test_a_backup_with_broken_relations_is_refused(self):
        path = self._good_db()
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("INSERT INTO customer_vehicles (customer_id, make, plate, plate_norm) "
                    "VALUES (99999, 'تويوتا', 'ق س ط 1', 'QST1')")
        con.commit()
        con.close()
        ok, detail = backup.verify_db_file(path)
        self.assertFalse(ok)
        self.assertIn("علاقات مكسورة", detail)

    def test_a_backup_from_a_newer_build_is_refused_instead_of_half_understood(self):
        path = self._good_db()
        con = sqlite3.connect(path)
        con.execute("INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                    "VALUES (9999, 'from_the_future', 'x', '2030-01-01 00:00:00')")
        con.commit()
        con.close()
        ok, detail = backup.verify_db_file(path)
        self.assertFalse(ok)
        self.assertIn("9999", detail)

    def test_a_backup_whose_migration_was_edited_after_the_fact_is_refused(self):
        path = self._good_db()
        con = sqlite3.connect(path)
        con.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        con.commit()
        con.close()
        self.assertFalse(backup.verify_db_file(path)[0])

    def test_a_refused_backup_leaves_the_live_database_untouched(self):
        live = self._good_db()
        bad = self.dir / "bad.db"
        shutil.copyfile(live, bad)
        con = sqlite3.connect(bad)
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("INSERT INTO customer_vehicles (customer_id, make, plate, plate_norm) "
                    "VALUES (99999, 'تويوتا', 'ق س ط 1', 'QST1')")
        con.commit()
        con.close()
        before = live.read_bytes()
        with self.assertRaises(backup.BackupError):
            backup.restore_file(live, bad)
        self.assertEqual(before, live.read_bytes(), "the live database must stand after a refusal")
        self.assertFalse(live.with_name(live.name + ".restore-new").exists(), "no staged file left behind")


class _FlakyWriter:
    """Fails after a set number of writes, standing in for a disk that fills up mid-body."""

    def __init__(self, f, allowed: int):
        self._f, self._left = f, allowed

    def write(self, b):
        if self._left <= 0:
            raise OSError(28, "No space left on device")
        self._left -= 1
        return self._f.write(b)

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._f.__exit__(*exc)


class StreamingVaultTest(unittest.TestCase):
    """Sealing and opening must hold memory flat and never expose an unverified plaintext."""

    MIB = 1024 * 1024
    SIZE_MIB = 64
    BUDGET = 8 * MIB          # Streaming works a chunk at a time, so peak must not track file size

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.plain = self.dir / "plain.bin"
        with open(self.plain, "wb") as f:
            block = os.urandom(self.MIB)
            for _ in range(self.SIZE_MIB):
                f.write(block)
        self.rk = security.new_recovery_key()
        self.dek, self.ring = vault.new_keyring(PW, self.rk)
        self.sealed = self.dir / "sealed.mtb"

    def _peak_mib(self, fn) -> float:
        import tracemalloc
        tracemalloc.start()
        try:
            fn()
            return tracemalloc.get_traced_memory()[1] / self.MIB
        finally:
            tracemalloc.stop()

    def _legacy_seal(self, out: Path, overrides: dict | None = None) -> None:
        """Writes a .mtb exactly as the one-shot writer did, to prove old backups still open."""
        import base64
        import hashlib
        import json
        import secrets
        import struct

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        from morv import APP_ID
        data = self.plain.read_bytes()
        header = {
            "app_id": APP_ID,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "plain_size": len(data),
            "plain_sha256": hashlib.sha256(data).hexdigest(),
            "keyring": self.ring,
            "body_nonce": base64.b64encode(secrets.token_bytes(12)).decode("ascii"),
        }
        header.update(overrides or {})
        hdr = json.dumps(header, ensure_ascii=False, sort_keys=True).encode("utf-8")
        body = AESGCM(self.dek).encrypt(base64.b64decode(header["body_nonce"]), data, hdr)
        out.write_bytes(vault.MAGIC + struct.pack(">I", len(hdr)) + hdr + body)

    def _leftovers(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir() if p.name.endswith(".part"))

    def test_seal_peak_memory_does_not_track_file_size(self):
        peak = self._peak_mib(lambda: vault.seal(self.plain, self.sealed, self.dek, self.ring))
        self.assertLess(peak, self.BUDGET / self.MIB, f"seal held {peak:.0f} MiB of a {self.SIZE_MIB} MiB file")

    def test_open_peak_memory_does_not_track_file_size(self):
        vault.seal(self.plain, self.sealed, self.dek, self.ring)
        out = self.dir / "restored.bin"
        peak = self._peak_mib(lambda: vault.open_vault(self.sealed, out, dek=self.dek))
        self.assertEqual(out.read_bytes(), self.plain.read_bytes())
        self.assertLess(peak, self.BUDGET / self.MIB, f"open held {peak:.0f} MiB of a {self.SIZE_MIB} MiB file")

    def test_opens_with_streams_and_writes_nothing(self):
        vault.seal(self.plain, self.sealed, self.dek, self.ring)
        before = sorted(p.name for p in self.dir.iterdir())
        peak = self._peak_mib(lambda: self.assertTrue(vault.opens_with(self.sealed, self.dek)))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), before, "opens_with must not write")
        self.assertLess(peak, self.BUDGET / self.MIB, f"opens_with held {peak:.0f} MiB")
        self.assertFalse(vault.opens_with(self.sealed, bytes(32)))

    def test_legacy_one_shot_backup_still_opens(self):
        legacy = self.dir / "legacy.mtb"
        self._legacy_seal(legacy)
        out = self.dir / "from_legacy.bin"
        for secret in (PW, self.rk):
            vault.open_vault(legacy, out, secret=secret)
            self.assertEqual(out.read_bytes(), self.plain.read_bytes())
        vault.open_vault(legacy, out, dek=self.dek)
        self.assertEqual(out.read_bytes(), self.plain.read_bytes())
        self.assertTrue(vault.opens_with(legacy, self.dek))

    def test_header_digest_or_size_mismatch_is_refused(self):
        for bad in ({"plain_sha256": "0" * 64}, {"plain_size": 7}):
            with self.subTest(**bad):
                f = self.dir / "mismatch.mtb"
                self._legacy_seal(f, bad)
                out = self.dir / "never.bin"
                with self.assertRaises(vault.VaultError):
                    vault.open_vault(f, out, dek=self.dek)
                self.assertFalse(out.exists(), "an unverified plaintext must never be exposed")
                self.assertEqual(self._leftovers(), [])

    def test_failed_open_keeps_existing_output_and_leaves_no_part(self):
        vault.seal(self.plain, self.sealed, self.dek, self.ring)
        out = self.dir / "target.bin"
        out.write_bytes(b"an earlier restore that must survive")
        data = bytearray(self.sealed.read_bytes())
        data[len(data) // 2] ^= 0x01
        self.sealed.write_bytes(bytes(data))
        with self.assertRaises(vault.VaultError):
            vault.open_vault(self.sealed, out, dek=self.dek)
        self.assertEqual(out.read_bytes(), b"an earlier restore that must survive")
        self.assertEqual(self._leftovers(), [])

    def test_source_changing_between_passes_is_refused(self):
        import hashlib
        real = self.plain.read_bytes()
        digest = hashlib.sha256(real).hexdigest()

        def hash_then_change(path):
            self.plain.write_bytes(real[:-1] + bytes([real[-1] ^ 0x01]))
            return digest, len(real)

        with mock.patch.object(vault, "_hash_file", side_effect=hash_then_change):
            with self.assertRaises(vault.VaultError):
                vault.seal(self.plain, self.sealed, self.dek, self.ring)
        self.assertFalse(self.sealed.exists(), "a snapshot that moved under us must not be sealed")
        self.assertEqual(self._leftovers(), [])

    def test_disk_full_mid_seal_leaves_no_output(self):
        real_open = open

        def flaky(path, *a, **k):
            f = real_open(path, *a, **k)
            return _FlakyWriter(f, 5) if str(path).endswith(".part") else f

        with mock.patch("builtins.open", flaky), self.assertRaises(OSError):
            vault.seal(self.plain, self.sealed, self.dek, self.ring)
        self.assertFalse(self.sealed.exists())
        self.assertEqual(self._leftovers(), [])

    def test_disk_full_mid_open_leaves_no_output(self):
        vault.seal(self.plain, self.sealed, self.dek, self.ring)
        out = self.dir / "restored.bin"
        real_open = open

        def flaky(path, *a, **k):
            f = real_open(path, *a, **k)
            return _FlakyWriter(f, 5) if str(path).endswith(".part") else f

        with mock.patch("builtins.open", flaky), self.assertRaises(OSError):
            vault.open_vault(self.sealed, out, dek=self.dek)
        self.assertFalse(out.exists())
        self.assertEqual(self._leftovers(), [])

    def test_a_killed_restore_leaves_no_plaintext_part_for_the_next_one(self):
        vault.seal(self.plain, self.sealed, self.dek, self.ring)
        staged = self.dir / "morv_tires.db.restore-new"
        orphan = vault.part_path(staged)
        orphan.write_bytes(b"plaintext from a restore that was killed mid-stream")
        backup._discard(staged)
        self.assertFalse(orphan.exists(), "an interrupted restore must not leave decrypted bytes behind")
