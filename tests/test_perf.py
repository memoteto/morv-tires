"""قياس أداء القوائم على بيانات وهمية كبيرة (حدود التجربة الأولى: بحث 300ms، صفحة 100ms).

يعمل بلا Qt (طبقة البيانات فقط)، وهي التي تحدد سرعة القوائم لأن الواجهة تعرض 300 صف فقط.
الأرقام تُقاس على جهاز CI الأبطأ من جهاز المحل عادةً.
"""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from morv.infra import customers, items, suppliers, users
from morv.infra.db.bootstrap import open_main_db

N_CUSTOMERS, N_ITEMS, N_SUPPLIERS = 3000, 1500, 200


class PerfTest(unittest.TestCase):
    """زرع البيانات مرة واحدة ثم قياس كل استعلام."""

    @classmethod
    def setUpClass(cls):
        """قاعدة وهمية كبيرة."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tools.seed_fake import seed
        cls.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(cls.dir)
        cls.con = open_main_db(cls.dir / "perf.db")
        admin = users.create_user(cls.con, None, "admin", "المدير", "Admin12345", "manager")
        t = time.perf_counter()
        seed(cls.con, admin, N_CUSTOMERS, N_ITEMS, N_SUPPLIERS)
        cls.seed_ms = (time.perf_counter() - t) * 1000

    @classmethod
    def tearDownClass(cls):
        """تنظيف."""
        cls.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _time(self, fn, budget_ms: int, label: str):
        """تشغيل 5 مرات وأخذ الوسيط ومقارنته بالحد."""
        times = []
        for _ in range(5):
            t = time.perf_counter()
            fn()
            times.append((time.perf_counter() - t) * 1000)
        med = sorted(times)[2]
        self.assertLess(med, budget_ms, f"{label}: {med:.0f}ms > {budget_ms}ms")

    def test_counts(self):
        """الزرع نجح بالأعداد المطلوبة."""
        self.assertEqual(customers.count(self.con), N_CUSTOMERS)
        self.assertEqual(items.count(self.con), N_ITEMS)

    def test_customer_search(self):
        """بحث بالاسم والموبايل واللوحة على 3000 عميل."""
        self._time(lambda: customers.search(self.con, "احمد"), 300, "بحث عميل بالاسم")
        self._time(lambda: customers.search(self.con, "0100000"), 300, "بحث عميل بالموبايل")
        self._time(lambda: customers.search(self.con, "1500"), 300, "بحث عميل باللوحة")
        self._time(lambda: customers.search(self.con, "", offset=600), 100, "صفحة عملاء")

    def test_item_search(self):
        """بحث الأصناف بالاسم والمقاس والباركود على 1500 صنف."""
        self._time(lambda: items.search(self.con, "ميشلان"), 300, "بحث صنف بالاسم")
        self._time(lambda: items.search(self.con, "205/55"), 300, "بحث صنف بالمقاس")
        self._time(lambda: items.by_barcode(self.con, "6220000000100"), 150, "باركود")
        self._time(lambda: items.get(self.con, 100), 50, "فتح صنف")

    def test_supplier_search(self):
        """بحث الموردين."""
        self._time(lambda: suppliers.search(self.con, "مورد"), 300, "بحث مورد")


if __name__ == "__main__":
    unittest.main()
