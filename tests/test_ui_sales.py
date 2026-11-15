"""اختبارات شاشة المبيعات بلا شاشة: بناء القائمة والنافذة، والترحيل عبر المحرك، ورفض الفاتورة الفارغة."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class SalesUiTest(unittest.TestCase):
    """قاعدة مؤقتة بمدير وخدمة وهمية (الخدمة لا تحتاج مخزونًا)."""

    def setUp(self):
        """تجهيز."""
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, self.admin)
        self.app = get_app()
        from morv.core.text import norm_ar
        self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة وهمية',?,100,100,100)", (norm_ar("خدمة وهمية"),))

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def destroy_on_cleanup(self, widget):
        """تدمير النافذة صراحةً قبل خروج العملية."""
        def _destroy():
            """حذف النافذة وتفريغ الأحداث المؤجلة."""
            from PySide6.QtCore import QEvent
            widget.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(_destroy)
        return widget

    def test_page_builds_and_counts_zero_sales(self):
        """القائمة تُبنى وعدّادها صفر قبل أي فاتورة."""
        from morv.ui.sales_page import SalesPage
        page = self.destroy_on_cleanup(SalesPage(self.session))
        self.assertEqual(page.total(), "0 فاتورة بيع")

    def test_dialog_posts_service_sale_through_engine(self):
        """فاتورة خدمة بدفع نقدي تُرحَّل عبر المحرك وتظهر في القاعدة."""
        from morv.ui.pos_page import PosPage
        dlg = self.destroy_on_cleanup(PosPage(self.session))
        dlg.item_search.setText("خدمة")
        self.assertEqual(dlg.item_combo.count(), 1)
        dlg.price.setText("1.00")
        self.assertTrue(dlg.add_line())
        dlg.pay1_received.setText("2.00")
        dlg.post()
        self.assertIsNotNone(dlg.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT total FROM sales").fetchone()[0], 100)

    def test_pos_design_tabs_change_and_cancel(self):
        """تبويب الإطارات يخفي الخدمة، والباقي يُحسب من المستلم، والإلغاء يفرغ الفاتورة."""
        from morv.ui.pos_page import PosPage
        dlg = self.destroy_on_cleanup(PosPage(self.session))
        dlg._set_kind("tire")
        self.assertEqual(dlg.item_combo.count(), 0)
        dlg._set_kind(None)
        self.assertEqual(dlg.item_combo.count(), 1)
        dlg.price.setText("10.00")
        dlg.qty.setValue(2)
        self.assertTrue(dlg.add_line())
        self.assertIn("20.00", dlg.total_label.text())
        dlg.pay1_received.setText("50.00")
        self.assertIn("30.00", dlg.change_label.text())
        dlg.cancel_invoice()
        self.assertEqual(dlg.lines, [])
        self.assertIsNone(dlg.posted)

    def test_customer_found_by_typed_name_or_mobile(self):
        """كتابة اسم العميل أو موبايله تختاره وتملأ الموبايل وفئة السعر؛ الاسم غير المسجّل لا يُختار."""
        from morv.core.text import norm_ar
        self.con.execute("INSERT INTO customers (name, name_norm, mobile, price_level) VALUES (?,?,?,?)",
                         ("عميل وهمي", norm_ar("عميل وهمي"), "01000000001", "wholesale"))
        from morv.ui.pos_page import PosPage
        dlg = self.destroy_on_cleanup(PosPage(self.session))
        self.assertFalse(dlg.mobile.isReadOnly())
        dlg.customer.lineEdit().setText("عميل وهمي")
        dlg._on_customer_typed()
        self.assertEqual(dlg.customer.currentText(), "عميل وهمي")
        self.assertEqual(dlg.mobile.text(), "01000000001")
        self.assertEqual(dlg.tier, "wholesale")
        dlg.customer.setCurrentIndex(0)
        dlg.mobile.setText("01000000001")
        dlg._on_mobile_typed()
        self.assertEqual(dlg.customer.currentText(), "عميل وهمي")
        dlg.customer.lineEdit().setText("اسم غير مسجل")
        dlg._on_customer_typed()
        self.assertEqual(dlg.customer.currentText(), "عميل وهمي")

    def test_customer_stats_counts_previous_sales(self):
        """إحصاء العميل بلا فواتير: صفر وبلا تاريخ."""
        from morv.infra import sale_view
        self.assertEqual(sale_view.customer_stats(self.con, 999), (0, None))

    def test_dialog_refuses_empty_invoice(self):
        """الترحيل بلا أسطر يُرفض ولا يُنشئ فاتورة."""
        from morv.ui.pos_page import PosPage
        dlg = self.destroy_on_cleanup(PosPage(self.session))
        dlg.post()
        self.assertIsNone(dlg.posted)
        self.assertEqual(self.con.execute("SELECT count(*) FROM sales").fetchone()[0], 0)
