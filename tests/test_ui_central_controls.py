"""Central controls: combo arrow from the theme, dialogs use the screen height, list tables stay quiet."""
import unittest

from tests.qt_app import get_app, require_qt


@require_qt()
class CentralControlsTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def test_theme_draws_one_combo_arrow_from_an_existing_file(self):
        import re
        from pathlib import Path
        from morv.ui import theme
        for dark in (False, True):
            css = theme.qss(dict(theme.colors(dark), fs=14))
            paths = re.findall(r"QComboBox::down-arrow \{ image:url\(([^)]+)\)", css)
            self.assertEqual(len(paths), 1)
            self.assertTrue(Path(paths[0]).exists())
            self.assertIn("QComboBox#statementFilter::down-arrow { image:none", css)   # keeps its own indicator

    def test_tall_dialog_uses_the_screen_height_not_two_thirds(self):
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QLineEdit
        from morv.ui.modal import Modal
        from morv.ui.widgets import dispose
        dlg = Modal("نافذة طويلة")
        self.addCleanup(dispose, dlg, self.app)
        for _ in range(40):
            dlg.body.addWidget(QLineEdit())
        dlg.show()
        for _ in range(3):
            self.app.processEvents()
        screen_h = QGuiApplication.primaryScreen().availableGeometry().height()
        self.assertGreater(dlg.height(), screen_h * 2 // 3 + 10)
        self.assertLessEqual(dlg.height(), dlg.maximumHeight())

    def test_short_dialog_keeps_its_natural_height(self):
        from PySide6.QtWidgets import QLineEdit
        from morv.ui.modal import Modal
        from morv.ui.widgets import dispose
        dlg = Modal("نافذة قصيرة")
        self.addCleanup(dispose, dlg, self.app)
        dlg.body.addWidget(QLineEdit())
        dlg.show()
        self.app.processEvents()
        self.assertLess(dlg.height(), 300)

    def test_list_tables_have_no_grid_and_leading_titles(self):
        from PySide6.QtCore import Qt
        from morv.ui.listpage import ListPage
        from morv.ui.widgets import dispose

        class Sample(ListPage):
            columns = [("الاسم", lambda r: r[1])]

            def fetch(self, con, query, filter_key, offset):
                return []

            def build_preview(self, row_id):
                pass

            def open_dialog(self, row_id):
                pass

        page = Sample(None)
        self.addCleanup(dispose, page, self.app)
        self.assertFalse(page.view.showGrid())
        self.assertTrue(page.view.horizontalHeader().defaultAlignment() & Qt.AlignmentFlag.AlignLeading)


    def test_returns_use_same_document_layout_and_editor(self):
        from morv.ui.document_table import DocumentEditor, DocumentTableDialog
        from morv.ui.purchase_return_dialogs import PurchaseReturnDialog, PurchaseReturnEditor
        from morv.ui.sale_return_dialog import ReturnEditor, SaleReturnDialog

        self.assertTrue(issubclass(PurchaseReturnDialog, DocumentTableDialog))
        self.assertTrue(issubclass(SaleReturnDialog, DocumentTableDialog))
        self.assertTrue(issubclass(PurchaseReturnEditor, DocumentEditor))
        self.assertTrue(issubclass(ReturnEditor, DocumentEditor))
        for cls in (PurchaseReturnDialog, SaleReturnDialog):
            for member in ("fields", "add_document_table", "amount_row", "add_document_metrics", "add_quantity_toolbar",
                           "document_card"):
                self.assertNotIn(member, cls.__dict__)


    def test_document_metrics_are_single_line_and_compact(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel
        from morv.core.text import ltr
        from morv.ui.document_table import DocumentTableDialog
        from morv.ui.widgets import card, dispose, muted

        dlg = DocumentTableDialog("اختبار المستند")
        self.addCleanup(dispose, dlg, self.app)
        labels = dlg.add_document_metrics([("رقم الفاتورة", ltr("PI-000001")), ("اسم المورد", "مورد الاختبار")])
        old, layout = card(compact=True)
        self.addCleanup(dispose, old, self.app)
        layout.addWidget(muted("رقم الفاتورة"))
        old_value = QLabel(ltr("PI-000001"))
        old_value.setObjectName("cardTitle")
        layout.addWidget(old_value)
        old.ensurePolished()
        dlg.show()
        self.app.processEvents()
        for label in labels.values():
            box = label.parentWidget()
            self.assertTrue(label.alignment() & Qt.AlignmentFlag.AlignRight)
            self.assertTrue(label.alignment() & Qt.AlignmentFlag.AlignAbsolute)
            self.assertLess(box.sizeHint().height(), old.sizeHint().height())
            self.assertEqual(box.layout().itemAt(0).layout().count(), 2)
