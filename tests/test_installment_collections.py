"""Collection tests: oldest instalment first, no overpayment, and the ledger it leaves behind."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from morv.core.installments import InstalmentError, spread
from morv.core.sales_rules import Payment
from morv.infra import installment_collections as coll
from morv.infra import installments, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.installment_collections import CollectionError
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class CollectionTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "rc.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        item = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key="rc-sale-key-1", customer_id=self.cust,
                               lines=[SaleLineIn(item, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-11-10")
        self.contract = installments.post_contract(
            self.con, self.admin, request_key="rc-contract-key-1", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-12-13", national_id=NID, down_method="cash", contract_date="2026-11-13")
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def take(self, amount, by=None, key=None, **kw):
        self.n += 1
        kw.setdefault("method", "cash")
        kw.setdefault("collect_date", date.today().isoformat())
        return coll.collect(self.con, by or self.admin, request_key=key or f"rc-key-{self.n:04d}",
                            contract_id=self.contract.id, amount=amount, **kw)

    def paid_per_row(self):
        return [r[0] for r in self.con.execute(
            "SELECT coalesce(sum(a.amount), 0) FROM installment_rows r "
            "LEFT JOIN installment_allocations a ON a.row_id = r.id WHERE r.contract_id=? "
            "GROUP BY r.id ORDER BY r.no", (self.contract.id,))]

    def counts(self):
        return tuple(self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("installment_collections", "installment_allocations", "journal_entries"))

    def test_a_full_instalment_clears_the_oldest_row(self):
        posted = self.take(115000)
        self.assertEqual(posted.number, "RC-2026-000001")
        self.assertEqual(posted.remaining, 1150000 - 115000)
        self.assertEqual(self.paid_per_row()[:2], [115000, 0])
        self.assertEqual(ledger.account_balance(self.con, "1110"), 200000 + 115000)
        self.assertEqual(ledger.account_balance(self.con, "1320"), 1150000 - 115000)

    def test_a_part_payment_sits_on_the_oldest_row(self):
        self.take(40000)
        self.assertEqual(self.paid_per_row()[:2], [40000, 0])
        self.assertEqual(coll.remaining(self.con, self.contract.id), 1110000)

    def test_a_big_payment_spills_onto_the_next_rows(self):
        self.take(250000)
        self.assertEqual(self.paid_per_row()[:3], [115000, 115000, 20000])

    def test_paying_the_whole_contract_leaves_nothing(self):
        posted = self.take(1150000)
        self.assertEqual(posted.remaining, 0)
        self.assertEqual(ledger.account_balance(self.con, "1320"), 0)

    def test_collection_never_touches_deferred_interest(self):
        """Interest moves to income by the monthly entry, not by collecting cash."""
        before = ledger.account_balance(self.con, "2410")
        self.take(115000)
        self.assertEqual(ledger.account_balance(self.con, "2410"), before)
        self.assertEqual(ledger.account_balance(self.con, "4140"), 0)

    def test_replayed_key_returns_the_same_receipt(self):
        first = self.take(115000, key="same-receipt-key")
        again = self.take(115000, key="same-receipt-key")
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.counts()[0], 1)

    def test_overpaying_is_refused_and_writes_nothing(self):
        before = self.counts()
        with self.assertRaises(CollectionError):
            self.take(1150001)
        self.assertEqual(self.counts(), before)

    def test_paying_after_the_contract_is_settled_is_refused(self):
        self.take(1150000)
        with self.assertRaises(CollectionError):
            self.take(100)

    def test_refusals(self):
        before = self.counts()
        for kw, amount in (({"method": "credit"}, 1000), ({"collect_date": "2026-11-01"}, 1000), ({}, 0)):
            with self.assertRaises(CollectionError):
                self.take(amount, **kw)
        self.assertEqual(self.counts(), before)

    def test_cashier_cannot_collect_by_default(self):
        with self.assertRaises(CollectionError):
            self.take(115000, by=self.cashier)

    def test_posted_receipt_cannot_be_edited_or_deleted(self):
        self.take(115000)
        for sql in ("UPDATE installment_collections SET amount = 1", "DELETE FROM installment_collections",
                    "UPDATE installment_allocations SET amount = 1", "DELETE FROM installment_allocations"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.con.execute(sql)


class SpreadTest(unittest.TestCase):
    def test_oldest_first(self):
        self.assertEqual(spread([(1, 100), (2, 100)], 150), [(1, 100), (2, 50)])

    def test_refuses_more_than_owed(self):
        with self.assertRaises(InstalmentError):
            spread([(1, 100)], 101)

    def test_refuses_a_non_positive_amount(self):
        with self.assertRaises(InstalmentError):
            spread([(1, 100)], 0)


if __name__ == "__main__":
    unittest.main()
