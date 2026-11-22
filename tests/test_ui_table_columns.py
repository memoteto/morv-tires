"""The document tables read in one order, so the eye finds the date and the number in the same place."""
import unittest

from tests.qt_app import require_qt


@require_qt()
class DocumentTableColumnsTest(unittest.TestCase):
    """Imports the page classes only for their column lists, which need no Qt widget."""

    def heads(self, page_class) -> list:
        return [head for head, _getter in page_class.columns]

    def test_sales_and_purchases_open_with_the_number_then_the_date(self):
        from morv.ui.purchases_page import PurchasesListPage
        from morv.ui.sales_page import SalesPage
        for page in (SalesPage, PurchasesListPage):
            self.assertEqual(["الفاتورة", "التاريخ"], self.heads(page)[:2], page.__name__)

    def test_the_party_comes_straight_after_the_date(self):
        from morv.ui.purchases_page import PurchasesListPage
        from morv.ui.sales_page import SalesPage
        self.assertEqual("العميل", self.heads(SalesPage)[2])
        self.assertEqual("المورد", self.heads(PurchasesListPage)[2])

    def test_money_columns_come_last(self):
        from morv.ui.purchases_page import PurchasesListPage
        from morv.ui.sales_page import SalesPage
        for page in (SalesPage, PurchasesListPage):
            self.assertEqual(["الإجمالي", "المرتجع"], self.heads(page)[-2:], page.__name__)

    def test_the_treasury_leads_with_the_date_having_no_document_number(self):
        from morv.ui.treasury_page import TreasuryPage
        heads = self.heads(TreasuryPage)
        self.assertEqual("التاريخ", heads[0])
        self.assertEqual(["وارد", "صادر"], heads[-2:])

    def test_master_tables_still_lead_with_the_name_and_end_with_the_state(self):
        from morv.ui.customers_page import CustomersPage
        from morv.ui.suppliers_page import SuppliersPage
        for page, first in ((CustomersPage, "العميل"), (SuppliersPage, "المورد")):
            heads = self.heads(page)
            self.assertEqual(first, heads[0], page.__name__)
            self.assertEqual("الموبايل", heads[1], page.__name__)
            self.assertEqual("الحالة", heads[-1], page.__name__)
