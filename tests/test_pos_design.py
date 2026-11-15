"""Regression guard: the approved POS design V1.0 keeps every section and element it was signed off with."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "morv" / "ui" / "pos_page.py").read_text(encoding="utf-8")

# Each entry is a label the approved design V1.0 shows on the point-of-sale screen.
SECTIONS = ["بيانات الفاتورة والعميل", "الأصناف والخدمات", "ملخص الفاتورة", "الدفع"]

HEADER = ["فاتورة بيع جديدة", "مسودة", "نقطة البيع · المخزن الرئيسي · الكاشير:",
          "تعليق الفاتورة", "معاينة الطباعة", "إلغاء"]

INVOICE = ["رقم الفاتورة", "يُحدَّد عند الاعتماد", "التاريخ والوقت", "فئة السعر",
           "قطاعي", "جملة", "جملة الجملة", "عداد الكيلومترات", "المركبة / اللوحة",
           "ملاحظة على الفاتورة", "رقم الموبايل", "عميل مسجّل", "تغيير العميل"]

ITEMS = ["الكل", "إطارات", "بطاريات", "خدمات", "إضافة سريعة:", "الصنف المختار",
         "الكمية", "السعر", "الخصم", "الإجمالي", "الفاتورة فارغة"]

SUMMARY = ["الإجمالي قبل الخصم", "خصم على الفاتورة", "الضريبة",
           "المحل غير مسجّل — بدون ضريبة", "الإجمالي النهائي", "ج.م"]

PAYMENT = ["طريقة الدفع", "نقدي", "بطاقة / ماكينة POS", "تحويل / محفظة", "آجل", "تقسيط",
           "المبلغ المستلم", "رقم الإيصال / المرجع", "الباقي للعميل",
           "تقسيم الدفع على وسيلتين", "اعتماد وتحصيل", "اعتماد وطباعة", "حفظ كمسودة"]


class PosDesignV1Test(unittest.TestCase):
    def assert_present(self, group: str, labels: list[str]) -> None:
        missing = [t for t in labels if t not in SOURCE]
        self.assertEqual(missing, [], f"عناصر من التصميم المعتمد V1.0 اختفت من {group}: {missing}")

    def test_sections(self):
        self.assert_present("أقسام الشاشة", SECTIONS)

    def test_header(self):
        self.assert_present("ترويسة الشاشة", HEADER)

    def test_invoice_and_customer(self):
        self.assert_present("بيانات الفاتورة والعميل", INVOICE)

    def test_items(self):
        self.assert_present("الأصناف والخدمات", ITEMS)

    def test_summary(self):
        self.assert_present("ملخص الفاتورة", SUMMARY)

    def test_payment(self):
        self.assert_present("الدفع", PAYMENT)

    def test_deferred_parts_stay_marked_and_disabled(self):
        self.assertIn("setEnabled(False)", SOURCE)
        self.assertIn("قريبًا", SOURCE)

    def test_screen_is_a_page_not_a_dialog(self):
        """The owner's decision: the invoice lives in the POS screen, never in a popup."""
        self.assertIn("class PosPage(QWidget)", SOURCE)
        self.assertNotIn("class PosPage(Modal)", SOURCE)


if __name__ == "__main__":
    unittest.main()
