"""Master-data tests: validation, customers, suppliers, items and their fields."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import tire_catalog, validate
from morv.infra import customers, items, suppliers, users
from morv.infra.customers import Customer, Vehicle
from morv.infra.db.bootstrap import open_main_db
from morv.infra.items import Item
from morv.infra.suppliers import Supplier


class ValidateTest(unittest.TestCase):
    def test_mobile(self):
        self.assertEqual(validate.mobile("٠١٠ ١٢٣٤ ٥٦٧٨"), "01012345678")
        for bad in ("0101234567", "02012345678", "abc"):
            with self.assertRaises(validate.ValidationError):
                validate.mobile(bad)
        self.assertEqual(validate.mobile("", required=False), "")

    def test_national_id(self):
        self.assertEqual(validate.national_id("29001011201234"), "29001011201234")
        self.assertEqual(validate.national_id(""), "")
        for bad in ("1234", "19001011201234", "29013011201234", "29001019901234", "39901011201234"):
            with self.assertRaises(validate.ValidationError, msg=bad):
                validate.national_id(bad)
        self.assertEqual(validate.mask_national_id("29001011201234"), "**********1234")

    def test_tire_size(self):
        self.assertEqual(validate.tire_size("205/55r16")["rim"], "16")
        self.assertEqual(validate.tire_size(" 205/55 ZR 16 ")["size"], "205/55ZR16")
        self.assertEqual(validate.tire_size("7.50R16")["aspect"], "")
        with self.assertRaises(validate.ValidationError):
            validate.tire_size("205-55-16")

    def test_egyptian_catalogue_formats_and_suggestions(self):
        for size in tire_catalog.SIZES:
            with self.subTest(size=size):
                self.assertEqual(validate.tire_size(size)["size"], size)
                self.assertIn(tire_catalog.DEFAULT_CATEGORY[size], tire_catalog.CATEGORIES)
        self.assertEqual(validate.tire_size("٧٥٠r١٦")["size"], "7.50R16")
        for size in ("185R14C", "195/70R15C", "12R22.5", "31X10.50R15", "7.50-16", "90/90-17"):
            self.assertEqual(validate.tire_size(size)["size"], size)
        for size in ("205-55-16", "bad", "195/70R", "195R14CCC"):
            with self.assertRaises(validate.ValidationError):
                validate.tire_size(size)
        key = tire_catalog.size_search_key
        self.assertEqual(key("٢"), key("2"))
        self.assertEqual(key("205 55 16"), key("205/55R16"))
        self.assertEqual(key("750R16"), key("7.50R16"))
        self.assertEqual(tire_catalog.suggested_category("205/55R16", {}), "ملاكي")
        self.assertEqual(tire_catalog.suggested_category("205/55R16", {"فان": 2, "ملاكي": 1}), "فان")
        self.assertEqual(tire_catalog.suggested_category("205/55R16", {"فان": 1, "ملاكي": 1}), "ملاكي")
        self.assertEqual(tire_catalog.suggested_category("195/80R16", {"فان": 1, "جامبو": 1}), "")
        self.assertEqual(tire_catalog.suggested_category("195/80R16", {"نقل ثقيل": 5}), "")

    def test_suggestions(self):
        self.assertEqual(validate.suggest_item_name("tire", "ميشلان", "Primacy 4", {"size": "205/55R16", "li": "91", "si": "V"}),
                         "إطار ميشلان Primacy 4 205/55R16 91V")
        self.assertEqual(validate.suggest_item_code("tire", "Michelin", {"size": "205/55R16"}, 1), "TR-2055516-MI")
        self.assertEqual(validate.suggest_item_code("battery", "ACDelco", {"ah": "70"}, 1), "BT-70-ACD")
        self.assertEqual(validate.suggest_item_code("service", "", {}, 3), "SV-003")
        self.assertEqual(validate.suggest_item_code("tire", "ميشلان", {"size": "205/55R16"}, 7), "TR-2055516-007")  # an Arabic brand


class _Db(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "m.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)


class CustomersTest(_Db):
    def test_create_search_update(self):
        c = customers.create(self.con, self.admin, Customer(None, "أحمد محمود", "01012345678", national_id="29001011201234",
                                                             vehicles=[Vehicle("تويوتا", "كورولا", 2019, "ق س ط 4826", "205/55R16")]))
        self.assertEqual(c.vehicles[0].title, "تويوتا كورولا 2019")
        self.assertEqual([r[1] for r in customers.search(self.con, "احمد")], ["أحمد محمود"])   # without hamza
        self.assertEqual(len(customers.search(self.con, "٠١٠١٢")), 1)                           # Arabic digits
        self.assertEqual(len(customers.search(self.con, "4826")), 1)                            # plate number
        self.assertEqual(customers.search(self.con, "محمد"), [])
        c.price_level = "wholesale"
        customers.update(self.con, self.admin, c)
        self.assertEqual(customers.get(self.con, c.id).price_level, "wholesale")
        last = users.audit_rows(self.con, 1)[0]
        self.assertEqual(last[3], "customer.update")
        self.assertIn("قطاعي → جملة", last[6])

    def test_duplicate_mobile_rejected_with_name(self):
        customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        with self.assertRaisesRegex(customers.CustomerError, "أحمد"):
            customers.create(self.con, self.admin, Customer(None, "آخر", "01012345678"))
        self.assertEqual(customers.count(self.con), 1)

    def test_validation(self):
        for bad in (Customer(None, "", "01012345678"), Customer(None, "س", "123"),
                    Customer(None, "س", "01012345678", national_id="123")):
            with self.assertRaises(customers.CustomerError):
                customers.create(self.con, self.admin, bad)

    def test_deactivate_and_delete(self):
        c = customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        customers.set_active(self.con, self.admin, c.id, False)
        self.assertEqual(customers.search(self.con), [])
        self.assertEqual(len(customers.search(self.con, include_inactive=True)), 1)
        customers.delete(self.con, self.admin, c.id)
        with self.assertRaises(customers.CustomerError):
            customers.get(self.con, c.id)


class SuppliersTest(_Db):
    def test_crud(self):
        s = suppliers.create(self.con, self.admin, Supplier(None, "شركة الإطارات", "01111111111", contact_person="سامي"))
        self.assertEqual(suppliers.search(self.con, "الاطارات")[0][1], "شركة الإطارات")
        with self.assertRaisesRegex(suppliers.SupplierError, "شركة الإطارات"):
            suppliers.create(self.con, self.admin, Supplier(None, "آخر", "01111111111"))
        s.address = "طنطا"
        self.assertEqual(suppliers.update(self.con, self.admin, s).address, "طنطا")
        with self.assertRaises(suppliers.SupplierError):
            suppliers.create(self.con, self.admin, Supplier(None, "", "01111111112"))


class ItemsTest(_Db):
    def tire(self, **kw) -> Item:
        base = dict(id=None, kind="tire", code="TR-2055516-MI", name="إطار ميشلان Primacy 4 205/55R16 91V",
                    barcode="6221234500011", brand="Michelin", model="Primacy 4", price_retail=645000,
                    attrs={"size": "205/55r16", "li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"})
        base.update(kw)
        return Item(**base)

    def test_item_vat_defaults_and_explicit_roundtrip(self):
        from morv.infra import sales, settings
        from morv.infra.sales import SaleLineIn

        item = items.create(self.con, self.admin, self.tire())
        self.assertEqual((item.tax_treatment, item.tax_rate), ("standard", None))
        item.tax_rate = 500
        item = items.update(self.con, self.admin, item)
        self.assertEqual((item.tax_treatment, item.tax_rate), ("standard", 500))
        self.assertIn("ضريبة المخرجات", users.audit_rows(self.con, 1)[0][6])
        settings.put(self.con, "tax.enabled", "1")
        quote = sales.compute_sale_totals(
            self.con, [SaleLineIn(item.id, 1, 10000)], prices_tax_inclusive=False)
        self.assertEqual((quote.tax_total, quote.total), (500, 10500))
        for treatment in ("exempt", "zero"):
            item.tax_treatment, item.tax_rate = treatment, None
            item = items.update(self.con, self.admin, item)
            self.assertEqual((item.tax_treatment, item.tax_rate), (treatment, None))
            quote = sales.compute_sale_totals(
                self.con, [SaleLineIn(item.id, 1, 10000)], prices_tax_inclusive=False)
            self.assertEqual((quote.lines[0].tax_treatment, quote.tax_total), (treatment, 0))
        self.assertEqual(item.price_retail, 645000)

    def test_ordinary_item_edit_omits_and_preserves_vat_even_when_disabled(self):
        item = items.create(self.con, self.admin, self.tire(tax_treatment="standard", tax_rate=500))
        replacement = self.tire(id=item.id, notes="ordinary edit")
        saved = items.update(self.con, self.admin, replacement)
        self.assertEqual((saved.tax_treatment, saved.tax_rate, saved.notes), ("standard", 500, "ordinary edit"))
        self.assertEqual(saved.attrs, item.attrs)

    def test_vat_changes_require_current_manager_even_with_item_permissions(self):
        cashier = users.create_user(self.con, self.admin, "cash", "كاشير", "Cash12345", "cashier")
        users.set_permission(self.con, self.admin, cashier.id, "items.manage", True)
        users.set_permission(self.con, self.admin, cashier.id, "items.prices", True)
        with self.assertRaisesRegex(items.ItemError, "المدير"):
            items.create(self.con, cashier, self.tire(tax_treatment="exempt"))
        self.assertEqual(items.count(self.con), 0)
        item = items.create(self.con, self.admin, self.tire(tax_treatment="zero"))
        item.notes = "allowed"
        saved = items.update(self.con, cashier, item)
        self.assertEqual((saved.tax_treatment, saved.notes), ("zero", "allowed"))
        saved.tax_treatment, saved.tax_rate = "standard", 500
        saved.notes = "must not persist"
        with self.assertRaisesRegex(items.ItemError, "المدير"):
            items.update(self.con, cashier, saved)
        actual = items.get(self.con, item.id)
        self.assertEqual((actual.tax_treatment, actual.tax_rate, actual.notes), ("zero", None, "allowed"))

    def test_invalid_vat_values_are_rejected_atomically(self):
        for treatment, rate in (("unknown", None), ("standard", -1), ("standard", True),
                                ("standard", 1.5), ("exempt", 500), (None, 500)):
            with self.subTest(treatment=treatment, rate=rate), self.assertRaises(items.ItemError):
                items.create(self.con, self.admin, self.tire(tax_treatment=treatment, tax_rate=rate))
            self.assertEqual(items.count(self.con), 0)

    def test_create_tire_with_required_attrs(self):
        it = items.create(self.con, self.admin, self.tire())
        self.assertEqual(it.attrs["size"], "205/55R16")
        self.assertEqual((it.price_wholesale, it.price_super), (645000, 645000))
        with self.assertRaisesRegex(items.ItemError, "المقاس"):
            items.create(self.con, self.admin, self.tire(code="X1", barcode="", attrs={"li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"}))
        with self.assertRaisesRegex(items.ItemError, "الفئة"):
            items.create(self.con, self.admin, self.tire(code="X2", barcode="", attrs={"size": "205/55R16", "li": "91", "si": "V", "cat": "طائرة", "cons": "Radial"}))
        with self.assertRaisesRegex(items.ItemError, "الماركة"):
            items.create(self.con, self.admin, self.tire(code="X3", barcode="", brand=""))

    def test_catalogue_learns_saved_sizes_and_preserves_legacy(self):
        original = self.tire()
        original.attrs.update(size="195/80R16", cat="فان")
        self.assertNotIn("195/80R16", items.tyre_catalog(self.con))
        saved = items.create(self.con, self.admin, original)
        self.assertEqual(items.tyre_catalog(self.con)["195/80R16"], {"فان": 1})
        for n in (2, 3):
            other = self.tire(code=f"T{n}", barcode="")
            other.attrs.update(size="195/80R16", cat="جامبو")
            items.create(self.con, self.admin, other)
        counts = items.tyre_catalog(self.con)["195/80R16"]
        self.assertEqual(tire_catalog.suggested_category("195/80R16", counts), "جامبو")
        options = next(f.options for f in items.fields(self.con, "tire") if f.key == "cat")
        self.assertEqual(tuple(options), tire_catalog.CATEGORIES)
        for legacy in tire_catalog.LEGACY_CATEGORIES:
            with self.subTest(legacy=legacy):
                self.con.execute("UPDATE item_attrs SET value=? WHERE item_id=? AND field_key='cat'", (legacy, saved.id))
                current = items.get(self.con, saved.id)
                current.notes = "test"
                self.assertEqual(items.update(self.con, self.admin, current).attrs["cat"], legacy)
                new = self.tire(code="NEW", barcode="")
                new.attrs["cat"] = legacy
                with self.assertRaisesRegex(items.ItemError, "الفئة"):
                    items.create(self.con, self.admin, new)
        current = items.get(self.con, saved.id)
        current.attrs["cat"] = "ملاكي"
        self.assertEqual(items.update(self.con, self.admin, current).attrs["cat"], "ملاكي")
        current.attrs["cat"] = "نقل ثقيل"
        with self.assertRaisesRegex(items.ItemError, "الفئة"):
            items.update(self.con, self.admin, current)

    def test_unique_code_and_barcode_messages(self):
        items.create(self.con, self.admin, self.tire())
        with self.assertRaisesRegex(items.ItemError, "الكود مستخدم"):
            items.create(self.con, self.admin, self.tire(code="tr-2055516-mi", barcode=""))
        with self.assertRaisesRegex(items.ItemError, "الباركود مسجّل"):
            items.create(self.con, self.admin, self.tire(code="OTHER"))

    def test_service_and_battery(self):
        s = items.create(self.con, self.admin, Item(None, "service", "SV-001", "تركيب إطار", price_retail=5000))
        self.assertEqual(s.attrs, {})
        with self.assertRaisesRegex(items.ItemError, "السعة"):
            items.create(self.con, self.admin, Item(None, "battery", "BT-70-ACD", "بطارية", brand="ACDelco", price_retail=420000,
                                                   attrs={"volt": "12V", "cca": "640", "tech": "EFB", "box": "L3", "pol": "الموجب يمين", "dims": "278×175×190"}))
        b = items.create(self.con, self.admin, Item(None, "battery", "BT-70-ACD", "بطارية ACDelco 70 أمبير", brand="ACDelco", price_retail=420000,
                                                   attrs={"volt": "12V", "ah": "٧٠", "cca": "640", "tech": "EFB", "box": "L3", "pol": "الموجب يمين", "dims": "278×175×190", "ss": "نعم"}))
        self.assertEqual(b.attrs["ah"], "70")
        self.assertEqual(b.attrs["ss"], "1")

    def test_search_and_barcode_lookup(self):
        items.create(self.con, self.admin, self.tire())
        self.assertEqual(len(items.search(self.con, "ميشلان")), 1)
        self.assertEqual(len(items.search(self.con, "205/55")), 1)
        self.assertEqual(items.search(self.con, "205/55")[0][4], "205/55R16")
        self.assertEqual(len(items.search(self.con, "tr-2055")), 1)
        self.assertIsNotNone(items.by_barcode(self.con, "6221234500011"))
        self.assertIsNotNone(items.by_barcode(self.con, "٦٢٢١٢٣٤٥٠٠٠١١"))
        self.assertIsNone(items.by_barcode(self.con, "0000"))

    def test_field_config(self):
        items.set_field(self.con, self.admin, "tire", "cons", show=False)
        it = items.create(self.con, self.admin, self.tire(attrs={"size": "205/55R16", "li": "91", "si": "V", "cat": "ملاكي"}))
        self.assertNotIn("cons", it.attrs)
        with self.assertRaisesRegex(items.ItemError, "أساسي"):
            items.set_field(self.con, self.admin, "tire", "size", show=False)
        key = items.add_custom_field(self.con, self.admin, "tire", "رقم الرف", "text")
        items.set_field(self.con, self.admin, "tire", key, required=True)
        with self.assertRaisesRegex(items.ItemError, "رقم الرف"):
            items.create(self.con, self.admin, self.tire(code="T2", barcode="", attrs={"size": "205/55R16", "li": "91", "si": "V", "cat": "ملاكي"}))
        it2 = items.create(self.con, self.admin, self.tire(code="T2", barcode="", attrs={"size": "205/55R16", "li": "91", "si": "V", "cat": "ملاكي", key: "A3"}))
        self.assertEqual(it2.attrs[key], "A3")

    def test_price_change_audited(self):
        it = items.create(self.con, self.admin, self.tire())
        it.price_retail = 700000
        items.update(self.con, self.admin, it)
        self.assertIn("645000/645000/645000 → 700000/645000/645000", users.audit_rows(self.con, 1)[0][6])


if __name__ == "__main__":
    unittest.main()
