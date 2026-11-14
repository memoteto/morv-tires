"""اختبارات شاشات المرحلة 2 بلا شاشة: العملاء والموردون والمخزون، والمعاينة بالضغط فقط، والصلاحيات."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class _Base(unittest.TestCase):
    """قاعدة مؤقتة ومدير وكاشير."""

    def setUp(self):
        """تجهيز."""
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.session = Session(self.con, self.admin)
        self.cashier_session = Session(self.con, self.cashier)
        self.app = get_app()

    def tearDown(self):
        """تنظيف."""
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class CustomersPageTest(_Base):
    """شاشة العملاء."""

    def test_preview_opens_on_click_only(self):
        """المعاينة مخفية افتراضيًا، تفتح بالضغط على صف، وتُغلق بالزر."""
        from morv.infra import customers
        from morv.infra.customers import Customer
        from morv.ui.customers_page import CustomersPage
        customers.create(self.con, self.admin, Customer(None, "أحمد محمود", "01012345678"))
        page = CustomersPage(self.session)
        self.assertFalse(page.preview.isVisibleTo(page))
        self.assertEqual(page.model.rowCount(), 1)
        page.show_preview(page.model.rows[0])
        self.assertTrue(page.preview.isVisibleTo(page))
        page.hide_preview()
        self.assertFalse(page.preview.isVisibleTo(page))

    def test_search_filters_rows(self):
        """البحث يصفّي القائمة بالاسم الموحّد وبالموبايل."""
        from morv.infra import customers
        from morv.infra.customers import Customer
        from morv.ui.customers_page import CustomersPage
        customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        customers.create(self.con, self.admin, Customer(None, "محمد", "01111111111"))
        page = CustomersPage(self.session)
        page.search.setText("احمد")
        page.reload()
        self.assertEqual([r[1] for r in page.model.rows], ["أحمد"])
        page.search.setText("0111")
        page.reload()
        self.assertEqual([r[1] for r in page.model.rows], ["محمد"])

    def test_dialog_rejects_duplicate_and_saves(self):
        """نافذة العميل تعرض خطأ التكرار ثم تحفظ عند التصحيح، مع مركبة."""
        from morv.infra import customers
        from morv.infra.customers import Customer, Vehicle
        from morv.ui.customers_page import CustomerDialog
        customers.create(self.con, self.admin, Customer(None, "أحمد", "01012345678"))
        dlg = CustomerDialog(self.session, None)
        dlg.name.setText("سامي")
        dlg.mobile.setText("01012345678")
        dlg.save()
        self.assertIn("أحمد", dlg.notice.text())
        dlg.mobile.setText("01555555555")
        dlg.vehicles.append(Vehicle("كيا", "سيراتو", 2018, "ب هـ ج 7310"))
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(dlg.saved.vehicles[0].plate, "ب هـ ج 7310")
        self.assertEqual(len(customers.search(self.con, "7310")), 1)

    def test_cashier_can_add_but_not_change_level(self):
        """الكاشير يضيف عملاء (افتراضي دوره) لكن فئة السعر معطّلة له."""
        from morv.ui.customers_page import CustomerDialog, CustomersPage
        page = CustomersPage(self.cashier_session)
        self.assertTrue(page.add_btn.isEnabled())
        dlg = CustomerDialog(self.cashier_session, None)
        self.assertFalse(dlg.level.isEnabled())


class SuppliersPageTest(_Base):
    """شاشة الموردين."""

    def test_cashier_read_only(self):
        """الكاشير بلا صلاحية الموردين لا يرى زر الإضافة مفعّلًا."""
        from morv.ui.suppliers_page import SuppliersPage
        cashier_page, admin_page = SuppliersPage(self.cashier_session), SuppliersPage(self.session)
        self.assertFalse(cashier_page.add_btn.isEnabled())
        self.assertTrue(admin_page.add_btn.isEnabled())

    def test_dialog_saves(self):
        """حفظ مورد من النافذة."""
        from morv.ui.suppliers_page import SupplierDialog
        dlg = SupplierDialog(self.session, None)
        dlg.name.setText("شركة الإطارات")
        dlg.mobile.setText("01222222222")
        dlg.save()
        self.assertEqual(dlg.saved.name, "شركة الإطارات")


class InventoryPageTest(_Base):
    """شاشة المخزون."""

    def test_tabs_and_item_dialog_from_fields(self):
        """التبويبات حسب الصلاحية، ونافذة الصنف تُبنى من الحقول وتفرض الإلزامي."""
        from morv.infra import items
        from morv.ui.inventory_page import InventoryPage, ItemDialog
        admin_page = InventoryPage(self.session)            # تبقى في متغير وإلا حذفها Qt مع تبويباتها
        cashier_page = InventoryPage(self.cashier_session)
        self.assertEqual(admin_page.tabs.count(), 3)        # حقول البطاقة انتقلت إلى الإعدادات
        self.assertEqual(cashier_page.tabs.count(), 3)
        dlg = ItemDialog(self.session, None)
        self.assertIn("size", dlg.widgets)          # حقل الإطار
        dlg.set_kind("battery")
        self.assertIn("ah", dlg.widgets)
        self.assertNotIn("size", dlg.widgets)
        dlg.set_kind("tire")
        dlg.widgets["brand"].setText("Michelin")
        dlg.widgets["model"].setText("Primacy 4")
        dlg.widgets["size"].setText("205/55r16")
        dlg.widgets["li"].setText("91")
        dlg.widgets["si"].setCurrentText("V")
        # الاسم والكود يُقترحان تلقائيًا كنص باهت، ويُقبلان عند الحفظ إن تُركا فارغين
        self.assertEqual(dlg.widgets["name"].placeholderText(), "إطار Michelin Primacy 4 205/55R16 91V")
        self.assertEqual(dlg.widgets["code"].placeholderText(), "TR-2055516-MI")
        keys = list(dlg.widgets)
        self.assertLess(keys.index("size"), keys.index("name"))   # حقول النوع قبل المشتركة
        dlg.widgets["price_retail"].setText("6450")
        dlg.save()
        self.assertIn("الفئة", dlg.notice.text())     # إلزامي ناقص
        dlg.widgets["cat"].setCurrentText("ملاكي")
        dlg.widgets["cons"].setCurrentText("Radial")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(dlg.saved.price_retail, 645000)
        self.assertEqual(dlg.saved.code, "TR-2055516-MI")
        self.assertEqual(items.count(self.con), 1)
        # بطارية بنفس النافذة
        dlg2 = ItemDialog(self.session, None)
        dlg2.set_kind("battery")
        dlg2.widgets["brand"].setText("Varta")
        dlg2.widgets["ah"].setText("70")
        dlg2.widgets["volt"].setCurrentText("12V")
        dlg2.widgets["cca"].setText("640")
        dlg2.widgets["tech"].setCurrentText("EFB")
        dlg2.widgets["box"].setText("L3")
        dlg2.widgets["pol"].setCurrentText("الموجب يمين")
        dlg2.widgets["dims"].setText("278×175×190")
        dlg2.widgets["price_retail"].setText("4200")
        dlg2.save()
        self.assertEqual(dlg2.saved.name, "بطارية Varta 70 أمبير")
        # خدمة
        dlg3 = ItemDialog(self.session, None)
        dlg3.set_kind("service")
        dlg3.widgets["name"].setText("تركيب إطار")
        dlg3.widgets["price_retail"].setText("50")
        dlg3.save()
        self.assertEqual(dlg3.saved.code, "SV-001")

    def test_scan_enter_opens_preview(self):
        """Enter في البحث بباركود يفتح معاينة الصنف مباشرة."""
        from morv.infra import items
        from morv.infra.items import Item
        from morv.ui.inventory_page import ItemsPage
        items.create(self.con, self.admin, Item(None, "service", "SV-001", "تركيب", barcode="12345678", price_retail=5000))
        page = ItemsPage(self.session)
        page.search.setText("12345678")
        page.scan_enter()
        self.assertTrue(page.preview.isVisibleTo(page))

    def test_fields_tab_toggle(self):
        """تبديل إظهار حقل من الجدول يُحفظ، والمقفل يرفض."""
        from morv.infra import items
        from morv.ui.inventory_page import ItemFieldsTab
        from PySide6.QtCore import Qt
        tab = ItemFieldsTab(self.session)
        tab.kind.setCurrentIndex(1)   # الإطار
        row = next(i for i in range(tab.table.rowCount()) if tab.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == "season")
        tab.table.item(row, 2).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(next(f for f in items.fields(self.con, "tire") if f.key == "season").show)
        row = next(i for i in range(tab.table.rowCount()) if tab.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == "size")
        self.assertFalse(tab.table.item(row, 2).flags() & Qt.ItemFlag.ItemIsEnabled)


class ModalTest(_Base):
    """النافذة المنبثقة الموحّدة."""

    def test_fits_screen_and_footer_visible(self):
        """خطأ من تجربة المالك: نافذة الصنف كانت أطول من الشاشة فاختفى زر الحفظ. الآن تتقيد بالشاشة
        ويبقى التذييل ظاهرًا والجسم يتمرر."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from morv.ui.inventory_page import ItemDialog
        from morv.ui.customers_page import CustomerDialog
        avail = QGuiApplication.primaryScreen().availableGeometry().height()
        for dlg in (ItemDialog(self.session, None), CustomerDialog(self.session, None)):
            dlg.show()
            self.app.processEvents()
            self.assertLessEqual(dlg.height(), avail - 60)
            self.assertTrue(dlg.footer.isVisibleTo(dlg))
            self.assertTrue(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint)   # بلا شريط عنوان من النظام
            dlg.close()


class ShellPagesTest(_Base):
    """الصفحات الحقيقية مربوطة في الشريط."""

    def test_real_pages_mounted(self):
        """العملاء والموردون والمخزون صفحات حقيقية لا لافتات."""
        from morv.ui.customers_page import CustomersPage
        from morv.ui.inventory_page import InventoryPage
        from morv.ui.shell import MainWindow
        from morv.ui.suppliers_page import SuppliersPage
        w = MainWindow(self.session, self.app)
        w.health_timer.stop()
        self.assertIsInstance(w.pages["customers"], CustomersPage)
        self.assertIsInstance(w.pages["suppliers"], SuppliersPage)
        self.assertIsInstance(w.pages["inventory"], InventoryPage)


if __name__ == "__main__":
    unittest.main()
