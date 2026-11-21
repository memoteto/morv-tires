"""Hardening: money cap, CSV formula injection, single instance, plain pre-upgrade copy removal."""
import csv
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import money
from morv.infra import instance_lock, ledger, reports, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction


class MoneyCapTest(unittest.TestCase):
    def test_parse_money_rejects_huge_amounts(self):
        self.assertEqual(money.parse_money("10000000000"), money.MAX_MINOR)
        with self.assertRaises(money.MoneyError):
            money.parse_money("10000000001")
        with self.assertRaises(money.MoneyError):
            money.parse_money("99999999999999999999")

    def test_ledger_rejects_huge_amounts_with_a_domain_error(self):
        tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(tmp)
        con = open_main_db(tmp / "h.db")
        try:
            admin = users.create_user(con, None, "admin", "المدير", "Admin12345", "manager")
            big = money.MAX_MINOR + 1
            with self.assertRaises(ledger.LedgerError), transaction(con):
                ledger.post_entry(con, admin, "2026-11-01", "manual", 1, "x", [("1110", big, 0), ("4110", 0, big)])
        finally:
            con.close()
            os.environ.pop("MORV_DATA_DIR", None)
            shutil.rmtree(tmp, ignore_errors=True)


class CsvInjectionTest(unittest.TestCase):
    def test_formula_cells_are_neutralised_but_money_is_untouched(self):
        t = reports.Table("k", "=تقرير", "", ("الاسم", "المبلغ"), (reports.TEXT, reports.MONEY),
                          [("row", ["=HYPERLINK(\"http://x\")", -12345]), ("row", ["+cmd", 100]), ("row", ["عادي", 5])],
                          (0,))
        tmp = Path(tempfile.mkdtemp())
        try:
            path = str(tmp / "r.csv")
            reports.to_csv(t, path)
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(rows[0][0], "'=تقرير")
        self.assertEqual(rows[3], ["'=HYPERLINK(\"http://x\")", "-123.45"])
        self.assertEqual(rows[4][0], "'+cmd")
        self.assertEqual(rows[5], ["عادي", "0.05"])


class InstanceLockTest(unittest.TestCase):
    def test_second_acquire_is_refused_until_release(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.assertTrue(instance_lock.acquire(tmp))
            self.assertFalse(instance_lock.acquire(tmp))
            instance_lock.release_all()
            self.assertTrue(instance_lock.acquire(tmp))
        finally:
            instance_lock.release_all()
            shutil.rmtree(tmp, ignore_errors=True)


class PlainPreMigrationCopyTest(unittest.TestCase):
    def test_plain_copy_is_removed_after_a_successful_upgrade(self):
        from unittest import mock
        tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(tmp)
        try:
            con = open_main_db(tmp / "m.db")
            con.close()
            seen = []

            def fake_migrate(c, before_apply=None):
                before_apply()
                seen.extend((tmp / "backups").glob("*"))

            with mock.patch("morv.infra.db.bootstrap.migrate", fake_migrate):
                open_main_db(tmp / "m.db").close()
            self.assertTrue(seen, "the plain copy existed while the upgrade ran")
            self.assertEqual(list((tmp / "backups").glob("*")), [])
        finally:
            os.environ.pop("MORV_DATA_DIR", None)
            shutil.rmtree(tmp, ignore_errors=True)
