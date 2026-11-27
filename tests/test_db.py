"""Database tests: protection, migrations, chart of accounts, backup."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv import APP_ID
from morv.infra import backup, settings
from morv.infra.db import connection
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.migrator import MigrationError, load_migrations, migrate, pending


class _TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="morv_test_"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Guard(_TempDir):
    def test_refuses_old_live_db_by_name(self):
        with self.assertRaises(connection.UnsafeDatabase):
            connection.open_db(self.dir / "tire_shop_live.db", create=True)

    def test_refuses_foreign_db_untouched(self):
        f = self.dir / "other.db"
        c = sqlite3.connect(f); c.execute("CREATE TABLE x(a)"); c.commit(); c.close()
        before = f.read_bytes()
        with self.assertRaises(connection.UnsafeDatabase):
            connection.open_db(f, create=True)
        self.assertEqual(f.read_bytes(), before)

    def test_missing_without_create(self):
        with self.assertRaises(FileNotFoundError):
            connection.open_db(self.dir / "none.db")
        self.assertFalse((self.dir / "none.db").exists())


class Migrations(_TempDir):
    def test_fresh_db_and_idempotent(self):
        con = open_main_db(self.dir / "m.db")
        self.assertEqual(pending(con), [])
        self.assertEqual(migrate(con), [])
        self.assertEqual(con.execute("SELECT v FROM app_meta WHERE k='app_id'").fetchone()[0], APP_ID)
        con.close()
        con = open_main_db(self.dir / "m.db")   # the second open passes the guard because the mark is there
        con.close()

    def test_changed_migration_is_refused(self):
        con = open_main_db(self.dir / "m.db")
        migs = load_migrations()
        tampered = [type(m)(m.version, m.name, m.sql + "\n-- edit", "0" * 64) if m.version == 1 else m for m in migs]
        with self.assertRaises(MigrationError):
            pending(con, tampered)
        con.close()

    def test_failed_migration_rolls_back(self):
        con = open_main_db(self.dir / "m.db")
        bad = load_migrations()
        from morv.infra.db.migrator import Migration
        bad.append(Migration(len(bad) + 1, "broken", "CREATE TABLE ok_part(a);\nTHIS IS NOT SQL;", "f" * 64))
        with self.assertRaises(MigrationError):
            migrate(con, bad)
        self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='ok_part'").fetchone())
        self.assertFalse(con.in_transaction)
        con.close()

    def test_numbering_has_no_gaps(self):
        migs = load_migrations()
        self.assertEqual([m.version for m in migs], list(range(1, len(migs) + 1)))

    def test_backup_before_upgrade(self):
        from morv.infra.db.migrator import Migration
        con = open_main_db(self.dir / "m.db")
        extra = load_migrations() + [Migration(len(load_migrations()) + 1, "extra", "CREATE TABLE extra_t(a);", "e" * 64)]
        calls = []
        migrate(con, extra, before_apply=lambda: calls.append(backup.create_backup(con, "pre_migration", self.dir / "b")))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].ok)
        con.close()


class ChartOfAccounts(_TempDir):
    def setUp(self):
        super().setUp()
        self.con = open_main_db(self.dir / "c.db")

    def tearDown(self):
        self.con.close()
        super().tearDown()

    def test_required_codes_kept(self):
        codes = {r[0] for r in self.con.execute("SELECT code FROM accounts")}
        for c in ("1110", "1210", "1220", "1310", "1410", "2110", "3100", "3300"):
            self.assertIn(c, codes)

    def test_structure(self):
        kinds = {"1": "asset", "2": "liability", "3": "equity", "4": "revenue", "5": "expense"}
        for code, parent, kind, postable in self.con.execute("SELECT code, parent_code, kind, postable FROM accounts"):
            self.assertEqual(postable == 1, len(code) == 4, code)
            self.assertEqual(kinds[code[0]], kind, code)
            if parent:
                self.assertTrue(code.startswith(parent), code)
            else:
                self.assertEqual(len(code), 1, code)

    def test_no_unused_accounts(self):
        rows = self.con.execute("SELECT code, name FROM accounts").fetchall()
        names = " ".join(r[1] for r in rows)
        self.assertNotIn("مقدمات العملاء", names)
        # P04-4a (claude/p04-vat-design-2026-11-27.md) is the one owner-approved exception:
        # exactly 1520/2350 may name a VAT account; any other such account is still unapproved speculation.
        tax_codes = {code for code, name in rows if "ضريبة" in name}
        self.assertEqual(tax_codes, {"1520", "2350"})


class Backups(_TempDir):
    def setUp(self):
        super().setUp()
        self.con = open_main_db(self.dir / "b.db")
        settings.put(self.con, "ui.theme", "dark")

    def tearDown(self):
        self.con.close()
        super().tearDown()

    def test_backup_verify_restore_test(self):
        r = backup.create_backup(self.con, "manual", self.dir / "bk")
        self.assertTrue(r.ok and r.path.exists() and len(r.sha256) == 64)
        c = sqlite3.connect(r.path)
        self.assertEqual(c.execute("SELECT value FROM settings WHERE key='ui.theme'").fetchone()[0], "dark")
        c.close()
        t = backup.restore_test(self.con, r.path, r.sha256)
        self.assertTrue(t.ok, t.detail)
        rows = self.con.execute("SELECT action, status FROM backup_runs ORDER BY id").fetchall()
        self.assertEqual(rows, [("backup", "ok"), ("restore_test", "ok")])
        self.assertEqual(list((self.dir / "bk").glob("*.part")), [])

    def test_corrupted_backup_detected(self):
        r = backup.create_backup(self.con, "manual", self.dir / "bk")
        data = bytearray(r.path.read_bytes())
        # Damage the first pages after the file header: SQLite's check misses empty-page damage,
        # but the encrypted backup catches any byte; see test_backup_crypto
        for i in range(100, 12000):
            data[i] = 0xFF
        r.path.write_bytes(bytes(data))
        self.assertFalse(backup.restore_test(self.con, r.path, r.sha256).ok)
        self.assertFalse(backup.restore_test(self.con, r.path).ok)
        last = self.con.execute("SELECT status FROM backup_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual(last, "failed")

    def test_foreign_file_is_not_a_backup(self):
        f = self.dir / "x.db"
        c = sqlite3.connect(f); c.execute("CREATE TABLE x(a)"); c.commit(); c.close()
        ok, _ = backup.verify_db_file(f)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
