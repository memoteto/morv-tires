"""Scoped shop saves preserve the other section and commit the audit atomically."""
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from morv.infra import shop, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db

IDENTITY = {"name", "activity", "phone", "phone2", "whatsapp", "address", "hours", "website",
            "tax_registered", "tax_id", "commercial_register"}
PRINTING = {"thanks", "policy", "paper", "printer", "show_logo", "show_whatsapp", "show_hours", "show_policy"}


class ShopSectionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"MORV_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.path = Path(tmp.name) / "shop-sections.db"
        self.con = open_main_db(self.path)
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "Admin", "Admin12345", "manager")
        self.original = shop.Shop(name="Original", activity="Tires", phone="01000000000",
                                  phone2="01222222222", whatsapp="01111111111", address="Cairo",
                                  hours="9-10", website="example.invalid", tax_registered=True,
                                  tax_id="123", commercial_register="456", thanks="Thanks", policy="Policy",
                                  paper="r80", printer="Saved printer", show_logo=False,
                                  show_whatsapp=False, show_hours=False, show_policy=False)

    def rows(self):
        return {key.removeprefix("shop."): (value, stamp) for key, value, stamp in self.con.execute(
            "SELECT key, value, updated_at FROM settings WHERE key LIKE 'shop.%'")}

    def saved_values(self, fields):
        rows = self.rows()
        return {name: rows[name] for name in fields}

    def audits(self):
        return self.con.execute("SELECT detail FROM audit_log WHERE action='shop.save' ORDER BY id").fetchall()

    def seed(self):
        shop.save(self.con, self.admin, self.original)
        self.con.execute("UPDATE settings SET updated_at='2000-01-01 00:00:00' WHERE key LIKE 'shop.%'")

    def test_full_save_remains_a_full_replacement(self):
        self.seed()
        stale = shop.load(self.con)
        shop.save(self.con, self.admin, replace(stale, paper="a5"))
        result = shop.save(self.con, self.admin, replace(stale, name="New identity"))
        self.assertEqual(result.name, "New identity")
        self.assertEqual(result.paper, "r80")
        self.assertEqual(self.audits(), [])

    def test_independent_saves_from_two_stale_drafts(self):
        self.seed()
        other = open_db(self.path)
        self.addCleanup(other.close)
        identity_draft = shop.load(self.con)
        print_draft = shop.load(other)
        old_identity = self.saved_values(IDENTITY)
        printing = replace(print_draft, thanks="New thanks", policy="New policy", paper="a5",
                           printer="Other printer", show_logo=True, show_whatsapp=True,
                           show_hours=True, show_policy=True)
        shop.save_print(other, self.admin, printing)
        self.assertEqual(self.saved_values(IDENTITY), old_identity)
        new_print = self.saved_values(PRINTING)
        identity = replace(identity_draft, name="New name", activity="Batteries", phone="01123456789",
                           phone2="01234567890", whatsapp="01012345678", address="Alexandria",
                           hours="8-9", website="new.invalid", tax_registered=False,
                           tax_id="789", commercial_register="999")
        expected_identity = replace(identity, tax_id="", commercial_register="")
        saved = shop.save_identity(self.con, self.admin, identity)
        self.assertEqual(self.saved_values(PRINTING), new_print)
        for name in IDENTITY:
            self.assertEqual(getattr(saved, name), getattr(expected_identity, name), name)
        for name in PRINTING:
            self.assertEqual(getattr(saved, name), getattr(printing, name), name)
        new_identity = self.saved_values(IDENTITY)
        shop.save_print(other, self.admin, replace(print_draft, paper="r58"))
        self.assertEqual(self.saved_values(IDENTITY), new_identity)
        self.assertEqual(shop.load(self.con).paper, "r58")
        self.assertEqual(set(self.rows()), IDENTITY | PRINTING)
        self.assertEqual(self.audits(), [("printing",), ("identity",), ("printing",)])

    def test_printing_before_identity_is_configured(self):
        saved = shop.save_print(self.con, self.admin, shop.Shop(thanks=" Thanks ", policy=" Policy ",
                                                               paper="invalid", printer=" Missing printer "))
        self.assertEqual((saved.name, saved.phone), ("", ""))
        self.assertEqual((saved.thanks, saved.policy, saved.paper, saved.printer),
                         ("Thanks", "Policy", "a4", "Missing printer"))
        self.assertEqual(set(self.rows()), PRINTING)
        with self.assertRaises(shop.ShopError):
            shop.save_identity(self.con, self.admin, saved)
        self.assertEqual(set(self.rows()), PRINTING)
        self.assertEqual(self.audits(), [("printing",)])

    def test_identity_validation_and_normalization(self):
        self.seed()
        old = self.rows()
        for invalid in (replace(self.original, name=" "), replace(self.original, phone="12"),
                        replace(self.original, phone2="12"), replace(self.original, whatsapp="12"),
                        replace(self.original, address="١٢٣")):
            with self.subTest(invalid=invalid), self.assertRaises(shop.ShopError):
                shop.save_identity(self.con, self.admin, invalid)
            self.assertEqual(self.rows(), old)
        saved = shop.save_identity(self.con, self.admin, replace(
            self.original, name="  Shop  ", phone="٠١٠ ١٢٣٤٥٦٧٨", phone2="٠١١١١١١١١١١",
            whatsapp="٠١٢٢٢٢٢٢٢٢٢", tax_id="١٢٣", commercial_register="٤٥٦"))
        self.assertEqual((saved.name, saved.phone, saved.phone2, saved.whatsapp),
                         ("Shop", "01012345678", "01111111111", "01222222222"))
        self.assertEqual((saved.tax_id, saved.commercial_register), ("123", "456"))
        self.assertEqual(self.audits(), [("identity",)])

    def test_permission_checked_on_each_save(self):
        self.seed()
        cashier = users.create_user(self.con, self.admin, "cashier", "Cashier", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", False)
        old = self.rows()
        for save in (shop.save_identity, shop.save_print):
            with self.assertRaises(shop.ShopError):
                save(self.con, cashier, None)
        self.assertEqual(self.rows(), old)
        self.assertEqual(self.audits(), [])
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", True)
        shop.save_identity(self.con, cashier, replace(self.original, name="Granted"))
        shop.save_print(self.con, cashier, replace(self.original, paper="r58"))
        self.assertEqual(self.audits(), [("identity",), ("printing",)])
        users.set_permission(self.con, self.admin, cashier.id, "settings.edit", False)
        old = self.rows()
        for save in (shop.save_identity, shop.save_print):
            with self.assertRaises(shop.ShopError):
                save(self.con, cashier, self.original)
        self.assertEqual(self.rows(), old)
        self.assertEqual(self.audits(), [("identity",), ("printing",)])

    def test_audit_failure_rolls_back_settings_and_audit(self):
        self.seed()
        before = self.rows()

        def fail(*args, **kwargs):
            users.audit(*args, **kwargs)
            raise shop.ShopError("Injected audit failure")

        for save in (shop.save_identity, shop.save_print):
            with self.subTest(save=save.__name__):
                with patch.object(shop, "audit", side_effect=fail), self.assertRaises(shop.ShopError):
                    save(self.con, self.admin, replace(self.original, name="Changed", paper="a5"))
                self.assertEqual(self.rows(), before)
                self.assertEqual(self.audits(), [])
                self.assertFalse(self.con.in_transaction)
