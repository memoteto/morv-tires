"""اختبارات شاشة نقطة البيع بلا شاشة: نافذة الفاتورة (الأسعار حسب الفئة، التحصيل المختلط، الترحيل، المنع) وصلاحية الزر."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class SalesUiTest(unittest.TestCase):
    """قاعدة مؤقتة بمدير وكاشير وأصناف وعميل جملة ومخزون وهميين."""

    def setUp(self):
        """تجهيز: إطار 4 بتكلفة 5,000.00 وبطارية 2 وخدمة، وعميل جملة بحد آجل."""
        from morv.infra import purchasing, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.purchasing import PurchaseLine
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.session = Session(self.con, self.admin)
        self.cashier_session = Session(self.con, self.cashier)
        self.app = get_app()
        self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد وهمي','مورد وهمي','01000000001')")
        sup = self.con.execute("SELECT id FROM suppliers").fetchone()[0]
        self.tire = self.item("tire", "TR-1", "إطار وهمي", 645000, 612000)
        self.battery = self.item("battery", "BT-1", "بطارية وهمية", 300000, 300000)
        self.service = self.item("service", "SV-1", "خدمة وهمية", 50000, 50000)
        self.whole = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, price_level, credit_limit) "
            "VALUES ('عميل جملة','عميل جملة','01000000012','wholesale',5000000)").lastrowid
        purchasing.post_invoice(self.con, self.admin, request_key="buy-key-000001", supplier_id=sup,
                                supplier_invoice_no="S-1", invoice_date="2026-11-01",
                                lines=[PurchaseLine(self.tire, 4, 500000, "2324"), PurchaseLine(self.battery, 2, 200000, None)])

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def item(self, kind, code, name, retail, wholesale):
        """صنف وهمي مباشرة بالجدول."""
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?)", (kind, code, name, name, retail, wholesale, wholesale)).lastrowid

    def destroy_on_cleanup(self, widget):
        """تدمير النافذة صراحةً قبل خروج العملية (بقاء النوافذ حيّة كان ينهار عند الإغلاق)."""
        def _destroy():
            """حذف النافذة وتفريغ الأحداث المؤجلة."""
            from PySide6.QtCore import QEvent
            widget.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(_destroy)
        return widget

    def dialog(self, session=None):
        """نافذة فاتورة جديدة."""
        from morv.ui.sales_page import SaleDialog
        return self.destroy_on_cleanup(SaleDialog(session or self.session))

    def pick(self, dlg, item_id):
        """اختيار صنف في قائمة النافذة بمعرّفه."""
        for i in range(dlg.item_combo.count()):
            if dlg.item_combo.itemData(i)[0] == item_id:
                dlg.item_combo.setCurrentIndex(i)
                return
        self.fail(f"الصنف {item_id} غير ظاهر في قائمة الاختيار")

    def add(self, dlg, item_id, qty, price=None):
        """إدخال سطر وإضافته؛ price نص اختياري يغيّر السعر الافتراضي."""
        self.pick(dlg, item_id)
        dlg.qty.setValue(qty)
        if price is not None:
            dlg.price.setText(price)
        return dlg.add_line()

    def sales(self):
        """عدد الفواتير المرحّلة."""
        return self.con.execute("SELECT count(*) FROM sales").fetchone()[0]

    def test_default_price_follows_customer_level_and_lists_all_kinds(self):
        """كل الأنواع تظهر؛ السعر الافتراضي قطاعي ثم يتبع فئة العميل عند اختياره، وتلميح الدفعة المقترحة يظهر."""
        dlg = self.dialog()
        ids = {dlg.item_combo.itemData(i)[0] for i in range(dlg.item_combo.count())}
        self.assertEqual(ids, {self.tire, self.battery, self.service})
        self.pick(dlg, self.tire)
        self.assertEqual(dlg.price.text(), "6,450.00")
        self.assertIn("2324", dlg.stock_hint.text())
        dlg.customer.setCurrentIndex(dlg.customer.findData(self.whole))
        self.assertEqual(dlg.price.text(), "6,120.00")
        self.pick(dlg, self.service)
        self.assertIn("خدمة", dlg.stock_hint.text())

    def test_post_mixed_sale_end_to_end(self):
        """إطار وبطارية وخدمة بتحصيل نقدي وبطاقة وآجل: الإجمالي والآجل يظهران، والترحيل ينشئ الفاتورة بمطابقة كاملة."""
        from morv.infra import inventory, ledger, selling
        dlg = self.dialog()
        dlg.customer.setCurrentIndex(dlg.customer.findData(self.whole))
        self.assertTrue(self.add(dlg, self.tire, 1))
        self.assertTrue(self.add(dlg, self.battery, 1))
        self.assertTrue(self.add(dlg, self.service, 1))
        self.assertEqual(dlg.total_minor(), 962000)
        self.assertIn("9,620.00", dlg.total_label.text())
        dlg.cash.setText("3,000")
        dlg.card.setText("2,950")
        self.assertIn("3,670.00", dlg.credit_label.text())
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual((dlg.posted.total, dlg.posted.paid, dlg.posted.credit), (962000, 595000, 367000))
        self.assertEqual(self.sales(), 1)
        self.assertEqual(ledger.account_balance(self.con, "1310"), 367000)
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(selling.reconcile_sales(self.con), [])

    def test_price_override_only_for_permitted_user(self):
        """الكاشير لا يغيّر السعر (الحقل للقراءة فقط)؛ المدير يغيّره فيُحفظ السعر المُدخل ويُسجَّل."""
        self.assertTrue(self.dialog(self.cashier_session).price.isReadOnly())
        dlg = self.dialog()
        self.assertFalse(dlg.price.isReadOnly())
        self.assertTrue(self.add(dlg, self.battery, 1, "2,500"))
        dlg.cash.setText("2,500")
        dlg.post()
        self.assertEqual(self.con.execute("SELECT list_price, unit_price FROM sale_lines").fetchone(), (300000, 250000))
        self.assertEqual(self.con.execute("SELECT count(*) FROM audit_log WHERE action='sale.price_override'").fetchone()[0], 1)

    def test_double_post_creates_one_invoice(self):
        """ضغط الترحيل مرتين بنفس النموذج لا يكرر الفاتورة (مفتاح الطلب)."""
        dlg = self.dialog()
        self.assertTrue(self.add(dlg, self.battery, 1))
        dlg.cash.setText("3,000")
        dlg.post()
        dlg.post()
        self.assertEqual(self.sales(), 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_issues WHERE source_type='sale'").fetchone()[0], 1)

    def test_rejections_keep_inputs_and_save_nothing(self):
        """بلا أسطر، آجل بلا عميل، مبلغ تحصيل غير صالح، مخزون غير كافٍ: رسالة ولا حفظ وتبقى الأسطر."""
        dlg = self.dialog()
        dlg.post()
        self.assertIn("سطرًا واحدًا", dlg.notice.text())
        self.assertTrue(self.add(dlg, self.battery, 1))
        dlg.post()                                    # بلا تحصيل وبلا عميل = آجل بلا عميل
        self.assertIn("عميلًا", dlg.notice.text())
        dlg.cash.setText("abc")
        dlg.post()
        self.assertIn("مبلغ", dlg.notice.text())
        dlg.cash.setText("9,000")                            # تحصيل كامل فلا يسبق الآجل رسالة المخزون
        self.assertTrue(self.add(dlg, self.battery, 2))      # 3 بطاريات والمتاح 2
        dlg.post()
        self.assertIn("السطر 2", dlg.notice.text())
        self.assertEqual((self.sales(), dlg.lines_model.rowCount(), dlg.posted), (0, 2, None))
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_issues WHERE source_type='sale'").fetchone()[0], 0)

    def test_pos_page_button_follows_permission(self):
        """زر الفاتورة الجديدة يعمل لمن يملك sales.post، ويُعطَّل بسحبها مع رسالة."""
        from morv.infra import users
        from morv.ui.sales_page import PosPage
        page = self.destroy_on_cleanup(PosPage(self.cashier_session))
        self.assertTrue(page.new_btn.isEnabled())
        users.set_permission(self.con, self.admin, self.cashier.id, "sales.post", False)
        page2 = self.destroy_on_cleanup(PosPage(self.cashier_session))
        self.assertFalse(page2.new_btn.isEnabled())
        self.assertIn("لا تملك", page2.hint.text())


if __name__ == "__main__":
    unittest.main()
