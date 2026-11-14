"""اختبارات البيانات الأساسية: التحقق، والعملاء، والموردون، والأصناف وحقولها."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core import validate
from morv.infra import customers, items, suppliers, users
from morv.infra.customers import Customer, Vehicle
from morv.infra.db.bootstrap import open_main_db
from morv.infra.items import Item
from morv.infra.suppliers import Supplier


class ValidateTest(unittest.TestCase):
    """قواعد التحقق النقية."""

    def test_mobile(self):
        """11 رقمًا تبدأ بـ 01، وتُقبل الأرقام العربية والفراغات."""
        self.assertEqual(validate.mobile("٠١٠ ١٢٣٤ ٥٦٧٨"), "01012345678")
        for bad in ("0101234567", "02012345678", "abc"):
            with self.assertRaises(validate.ValidationError):
                validate.mobile(bad)
        self.assertEqual(validate.mobile("", required=False), "")

    def test_national_id(self):
        """14 رقمًا بقرن 2/3 وتاريخ صحيح ومحافظة معروفة؛ فارغ مقبول."""
        self.assertEqual(validate.national_id("29001011201234"), "29001011201234")
        self.assertEqual(validate.national_id(""), "")
        for bad in ("1234", "19001011201234", "29013011201234", "29001019901234", "39901011201234"):
            with self.assertRaises(validate.ValidationError, msg=bad):
                validate.national_id(bad)
        self.assertEqual(validate.mask_national_id("29001011201234"), "**********1234")

    def test_tire_size(self):
        """المقاس المتري والبوصة، مع توحيد الكتابة."""
        self.assertEqual(validate.tire_size("205/55r16")["rim"], "16")
        self.assertEqual(validate.tire_size(" 205/55 ZR 16 ")["size"], "205/55ZR16")
        self.assertEqual(validate.tire_size("7.50R16")["aspect"], "")
        with self.assertRaises(validate.ValidationError):
            validate.tire_size("205-55-16")

    def test_suggestions(self):
        """الاسم والكود المقترحان."""
        self.assertEqual(validate.suggest_item_name("tire", "ميشلان", "Primacy 4", {"size": "205/55R16", "li": "91", "si": "V"}),
                         "إطار ميشلان Primacy 4 205/55R16 91V")
        self.assertEqual(validate.suggest_item_code("tire", "Michelin", {"size": "205/55R16"}, 1), "TR-2055516-MI")
        self.assertEqual(validate.suggest_item_code("battery", "ACDelco", {"ah": "70"}, 1), "BT-70-ACD")
        self.assertEqual(validate.suggest_item_code("service", "", {}, 3), "SV-003")


class _Db(unittest.TestCase):
    """قاعدة مؤقتة ومدير."""

    def setUp(self):
        """تجهيز."""
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "m.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)


class CustomersTest(_Db):
    """العملاء."""

    def test_create_search_update(self):
        """إنشاء عميل بمركبة، والبحث بالاسم الموحّد والموبايل واللوحة، والتعديل مع تسجيل فئة السعر."""
        c = customers.create(self.con, self.admin, Customer(None, "أحمد محمود", "01012345678", national_id="29001011201234",
                                                             vehicles=[Vehicle("تويوتا", "كورولا", 2019, "ق س ط 4826", "205/55R16")]))
        self.assertEqual(c.vehicles[0].title, "تويوتا كورولا 2019")
        self.assertEqual([r[1] for r in customers.search(self.con, "احمد")], ["أحمد محمود"])   # بلا همزة
        self.assertEqual(len(customers.search(self.con, "٠١٠١٢")), 1)                           # أرقام عربية
        self.assertEqual(len(customers.search(self.con, "4826")), 1)                            # رقم اللوحة
        self.assertEqual(customers.search(self.con, "محمد"), [])
        c.price_level = "wholesale"
        customers.update(self.con, self.admin, c)
        self.assertEqual(customers.get(self.con, c.id).price_level, "wholesale")
        last = users.audit_rows(self.con, 1)[0]
        self.assertEqual(last[3], "customer.update")
        self.assertIn("قطاعي → جملة", last[6])

    def test_duplicate_mobile_rejected_with_name(self):
        """الموبايل المكرر يُرفض وتذكر الرسالة العميل الموجود (قرار المالك)."""
        customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        with self.assertRaisesRegex(customers.CustomerError, "أحمد"):
            customers.create(self.con, self.admin, Customer(None, "آخر", "01012345678"))
        self.assertEqual(customers.count(self.con), 1)

    def test_validation(self):
        """الاسم والموبايل والرقم القومي."""
        for bad in (Customer(None, "", "01012345678"), Customer(None, "س", "123"),
                    Customer(None, "س", "01012345678", national_id="123")):
            with self.assertRaises(customers.CustomerError):
                customers.create(self.con, self.admin, bad)

    def test_deactivate_and_delete(self):
        """الموقوف يختفي من البحث الافتراضي، والحذف مسموح بلا مستندات."""
        c = customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        customers.set_active(self.con, self.admin, c.id, False)
        self.assertEqual(customers.search(self.con), [])
        self.assertEqual(len(customers.search(self.con, include_inactive=True)), 1)
        customers.delete(self.con, self.admin, c.id)
        with self.assertRaises(customers.CustomerError):
            customers.get(self.con, c.id)


class SuppliersTest(_Db):
    """الموردون."""

    def test_crud(self):
        """إنشاء وبحث وتكرار وتعديل."""
        s = suppliers.create(self.con, self.admin, Supplier(None, "شركة الإطارات", "01111111111", contact_person="سامي"))
        self.assertEqual(suppliers.search(self.con, "الاطارات")[0][1], "شركة الإطارات")
        with self.assertRaisesRegex(suppliers.SupplierError, "شركة الإطارات"):
            suppliers.create(self.con, self.admin, Supplier(None, "آخر", "01111111111"))
        s.address = "طنطا"
        self.assertEqual(suppliers.update(self.con, self.admin, s).address, "طنطا")
        with self.assertRaises(suppliers.SupplierError):
            suppliers.create(self.con, self.admin, Supplier(None, "", "01111111112"))


class ItemsTest(_Db):
    """الأصناف وحقولها."""

    def tire(self, **kw) -> Item:
        """إطار صالح للاختبارات."""
        base = dict(id=None, kind="tire", code="TR-2055516-MI", name="إطار ميشلان Primacy 4 205/55R16 91V",
                    barcode="6221234500011", brand="Michelin", model="Primacy 4", price_retail=645000,
                    attrs={"size": "205/55r16", "li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"})
        base.update(kw)
        return Item(**base)

    def test_create_tire_with_required_attrs(self):
        """الإطار يحتاج الحقول الإلزامية؛ المقاس يُوحَّد؛ الأسعار الأدنى تأخذ ما فوقها."""
        it = items.create(self.con, self.admin, self.tire())
        self.assertEqual(it.attrs["size"], "205/55R16")
        self.assertEqual((it.price_wholesale, it.price_super), (645000, 645000))
        with self.assertRaisesRegex(items.ItemError, "المقاس"):
            items.create(self.con, self.admin, self.tire(code="X1", barcode="", attrs={"li": "91", "si": "V", "cat": "ملاكي", "cons": "Radial"}))
        with self.assertRaisesRegex(items.ItemError, "الفئة"):
            items.create(self.con, self.admin, self.tire(code="X2", barcode="", attrs={"size": "205/55R16", "li": "91", "si": "V", "cat": "طائرة", "cons": "Radial"}))
        with self.assertRaisesRegex(items.ItemError, "الماركة"):
            items.create(self.con, self.admin, self.tire(code="X3", barcode="", brand=""))

    def test_unique_code_and_barcode_messages(self):
        """الكود (بلا حساسية لحالة الأحرف) والباركود فريدان برسالة تذكر الصنف."""
        items.create(self.con, self.admin, self.tire())
        with self.assertRaisesRegex(items.ItemError, "الكود مستخدم"):
            items.create(self.con, self.admin, self.tire(code="tr-2055516-mi", barcode=""))
        with self.assertRaisesRegex(items.ItemError, "الباركود مسجّل"):
            items.create(self.con, self.admin, self.tire(code="OTHER"))

    def test_service_and_battery(self):
        """الخدمة بلا ماركة ولا حقول إلزامية؛ البطارية تحتاج الأمبير وغيره."""
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
        """البحث بالاسم والكود والمقاس، والعثور بالباركود مع تصحيح الماسح."""
        items.create(self.con, self.admin, self.tire())
        self.assertEqual(len(items.search(self.con, "ميشلان")), 1)
        self.assertEqual(len(items.search(self.con, "205/55")), 1)
        self.assertEqual(items.search(self.con, "205/55")[0][4], "205/55R16")
        self.assertEqual(len(items.search(self.con, "tr-2055")), 1)
        self.assertIsNotNone(items.by_barcode(self.con, "6221234500011"))
        self.assertIsNotNone(items.by_barcode(self.con, "٦٢٢١٢٣٤٥٠٠٠١١"))
        self.assertIsNone(items.by_barcode(self.con, "0000"))

    def test_field_config(self):
        """إخفاء حقل يلغي إلزاميته ويتجاهل قيمته؛ الحقل الأساسي لا يُخفى؛ الحقل المخصص يُضاف ويُحترم."""
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
        """تغيير الأسعار يُسجَّل بالقيم القديمة والجديدة."""
        it = items.create(self.con, self.admin, self.tire())
        it.price_retail = 700000
        items.update(self.con, self.admin, it)
        self.assertIn("645000/645000/645000 → 700000/645000/645000", users.audit_rows(self.con, 1)[0][6])


if __name__ == "__main__":
    unittest.main()
