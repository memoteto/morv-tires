"""The purchase views report what came back per invoice: a return adds to it, its reversal takes it away."""
from morv.infra import purchase_returns, purchase_view
from morv.infra.purchase_returns import ReturnLine
from tests.test_purchasing import Base


class PurchaseViewReturnsTest(Base):
    def line_id(self, item_id) -> int:
        return self.con.execute("SELECT id FROM purchase_lines WHERE item_id=? ORDER BY id DESC",
                                (item_id,)).fetchone()[0]

    def returned_in_list(self, invoice_id) -> int:
        rows = {r[0]: r[6] for r in purchase_view.list_invoices(self.con)}
        return rows[invoice_id]

    def test_an_untouched_invoice_shows_nothing_returned(self):
        inv = self.buy(self.tire, 4, 100000)
        self.assertEqual(0, self.returned_in_list(inv.id))
        self.assertEqual(0, purchase_view.get_invoice(self.con, inv.id).returned)

    def test_a_return_shows_its_value_against_its_own_invoice(self):
        inv = self.buy(self.tire, 4, 100000)
        purchase_returns.post_return(self.con, self.admin, request_key="vret-key-0001", supplier_id=self.sup,
                                     lines=[ReturnLine(self.line_id(self.tire), 3)], reason="عيب تصنيع",
                                     return_date="2026-11-10")
        self.assertEqual(300000, self.returned_in_list(inv.id))
        self.assertEqual(300000, purchase_view.get_invoice(self.con, inv.id).returned)

    def test_reversing_the_return_takes_it_back_off(self):
        inv = self.buy(self.tire, 4, 100000)
        r = purchase_returns.post_return(self.con, self.admin, request_key="vret-key-0002", supplier_id=self.sup,
                                         lines=[ReturnLine(self.line_id(self.tire), 3)], reason="عيب تصنيع",
                                         return_date="2026-11-10")
        purchase_returns.reverse_return(self.con, self.admin, request_key="vrev-key-0002", return_id=r.id,
                                        reason="المورد رفض الاستلام")
        self.assertEqual(0, self.returned_in_list(inv.id), "a reversed return must not keep counting")
        self.assertEqual(0, purchase_view.get_invoice(self.con, inv.id).returned)

    def test_a_return_counts_only_against_the_invoice_it_came_from(self):
        first = self.buy(self.tire, 4, 100000)
        line = self.line_id(self.tire)
        second = self.buy(self.tire, 2, 100000)
        purchase_returns.post_return(self.con, self.admin, request_key="vret-key-0003", supplier_id=self.sup,
                                     lines=[ReturnLine(line, 1)], reason="عيب تصنيع", return_date="2026-11-10")
        self.assertEqual(100000, self.returned_in_list(first.id))
        self.assertEqual(0, self.returned_in_list(second.id), "the other invoice of the same item is untouched")
