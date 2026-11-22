"""Regression guard: the POS screen keeps the elements it was redesigned with (round 2)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "morv" / "ui" / "pos_page.py").read_text(encoding="utf-8")

# Each entry is a label the current approved point-of-sale screen shows.
SECTIONS = ["بيانات الفاتورة والعميل", "الأصناف والخدمات", "ملخص الفاتورة", "الدفع"]

HEADER = ["فاتورة بيع جديدة", "مسودة", "نقطة البيع · المخزن الرئيسي · الكاشير:",
          "تعليق الفاتورة", "معاينة الطباعة", "إلغاء"]

INVOICE = ["رقم الفاتورة", "التاريخ والوقت", "فئة السعر",
           "قطاعي", "جملة", "جملة الجملة", "عداد الكيلومترات", "المركبة / اللوحة",
           "ملاحظة على الفاتورة", "رقم الموبايل", "عميل مسجّل", "تغيير العميل"]

ITEMS = ["الكل", "إطارات", "بطاريات", "خدمات", "إضافة سريعة:",
         "الكمية", "السعر", "الخصم", "الإجمالي", "الفاتورة فارغة"]

SUMMARY = ["الإجمالي قبل الخصم", "خصم على الفاتورة", "الضريبة",
           "المحل غير مسجّل — بدون ضريبة", "الإجمالي النهائي", "ج.م"]

PAYMENT = ["طريقة الدفع", "نقدي", "بطاقة / ماكينة POS", "تحويل / محفظة", "آجل", "تقسيط",
           "المبلغ المستلم", "رقم الإيصال / المرجع", "الباقي للعميل",
           "تقسيم الدفع على وسيلتين", "اعتماد وتحصيل", "اعتماد وطباعة", "حفظ كمسودة"]


class PosDesignV1Test(unittest.TestCase):
    def assert_present(self, group: str, labels: list[str]) -> None:
        missing = [t for t in labels if t not in SOURCE]
        self.assertEqual(missing, [], f"عناصر من التصميم المعتمد اختفت من {group}: {missing}")

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
        # The marking moved to the shared widgets.deferred_button; POS must keep using it
        self.assertIn("deferred_button(", SOURCE)
        widgets = (ROOT / "morv" / "ui" / "widgets.py").read_text(encoding="utf-8")
        body = widgets[widgets.index("def deferred_button("):widgets.index("def trail_icon(")]
        self.assertIn("setEnabled(False)", body)
        self.assertIn("setVisible(False)", body)      # owner decision 22 Nov 2026: unbuilt actions are hidden
        self.assertIn("قريبًا", body)

    def test_screen_is_a_page_not_a_dialog(self):
        """The owner's decision: the invoice lives in the POS screen, never in a popup."""
        self.assertIn("class PosPage(QWidget)", SOURCE)
        self.assertNotIn("class PosPage(Modal)", SOURCE)

    def test_invoice_number_is_shown_live_while_writing(self):
        """Owner feedback: the number must be visible while writing, not only after posting."""
        self.assertIn("sales.next_number_preview", SOURCE)
        self.assertNotIn("يُحدَّد عند الاعتماد", SOURCE)

    def test_item_add_is_inline_rows_not_a_dropdown(self):
        """Owner feedback: add items/services like the purchase screen — live search
        picks straight into a row with inline qty/price/discount, no combo-box step."""
        self.assertNotIn("self.item_combo", SOURCE)
        self.assertNotIn("الصنف المختار", SOURCE)
        self.assertIn("_ItemPopup", SOURCE)
        self.assertIn("_add_row", SOURCE)

    def test_delete_line_is_an_icon_not_a_text_button(self):
        self.assertNotIn("حذف السطر المختار", SOURCE)
        self.assertIn('icons.icon("trash"', SOURCE)

    def test_no_horizontal_scrollbar(self):
        """Owner feedback: the screen was too wide and needed scrolling sideways."""
        self.assertIn("ScrollBarAlwaysOff", SOURCE)


if __name__ == "__main__":
    unittest.main()
