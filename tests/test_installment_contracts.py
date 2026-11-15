"""Instalment contract tests: the entry, the schedule, refusals and the ledger it leaves behind."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installments, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.installments import ContractError, Guarantor
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "ic.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash1", "كاشير", "Cash12345", "cashier")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]
        self.n = 0
        self.sale = self.sell(1200000)

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def sell(self, total, method="installment", customer=True):
        self.n += 1
        pay = Payment(method, total, received=total) if method == "cash" else Payment(method, total)
        return sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}",
                               customer_id=self.cust if customer else None,
                               lines=[SaleLineIn(self.service, 1, total)], payments=[pay], sale_date="2026-11-10")

    def contract(self, by=None, key=None, **kw):
        self.n += 1
        kw.setdefault("sale_id", self.sale.id)
        kw.setdefault("down", 200000)
        kw.setdefault("rate_bp", 1500)
        kw.setdefault("months", 10)
        kw.setdefault("first_due", "2026-12-13")
        kw.setdefault("national_id", NID)
        kw.setdefault("down_method", "cash")
        kw.setdefault("contract_date", "2026-11-13")
        return installments.post_contract(self.con, by or self.admin,
                                          request_key=key or f"ic-key-{self.n:04d}", **kw)

    def balance(self, code):
        return ledger.account_balance(self.con, code)

    def counts(self):
        return tuple(self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("installment_contracts", "installment_rows", "journal_entries"))

    # ---------- Accepted ----------
    def test_contract_books_down_and_markup(self):
        """The sale put 12000 on 1320; the contract collects 2000 and adds 1500 of deferred interest."""
        self.assertEqual(self.balance("1320"), 1200000)
        posted = self.contract()
        self.assertEqual(posted.number, "IC-2026-000001")
        self.assertEqual((posted.months, posted.monthly, posted.contract_total), (10, 115000, 1350000))
        self.assertEqual(self.balance("1110"), 200000)
        self.assertEqual(self.balance("2410"), -150000)
        self.assertEqual(self.balance("1320"), 1200000 - 200000 + 150000)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_schedule_is_written_whole(self):
        self.contract()
        rows = self.con.execute("SELECT no, due_date, amount, balance_after FROM installment_rows "
                                "ORDER BY no").fetchall()
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0][1], "2026-12-13")
        self.assertEqual(sum(r[2] for r in rows), 1150000)
        self.assertEqual(rows[-1][3], 0)

    def test_contract_without_down_or_markup_needs_no_entry(self):
        posted = self.contract(down=0, rate_bp=0, down_method=None)
        self.assertEqual(posted.contract_total, 1200000)
        self.assertIsNone(self.con.execute("SELECT journal_entry_id FROM installment_contracts").fetchone()[0])
        self.assertEqual(self.balance("1320"), 1200000)

    def test_guarantor_is_kept(self):
        self.contract(guarantor=Guarantor("ضامن وهمي", "01000000099", NID, "قريب"))
        row = self.con.execute("SELECT guarantor_name, guarantor_relation FROM installment_contracts").fetchone()
        self.assertEqual(row, ("ضامن وهمي", "قريب"))

    def test_replayed_key_returns_the_same_contract(self):
        first = self.contract(key="same-contract-key")
        again = self.contract(key="same-contract-key")
        self.assertEqual((again.id, again.replayed), (first.id, True))
        self.assertEqual(self.counts()[0], 1)

    # ---------- Refused ----------
    def test_second_contract_on_one_invoice_is_refused(self):
        self.contract()
        before = self.counts()
        with self.assertRaises(ContractError):
            self.contract()
        self.assertEqual(self.counts(), before)

    def test_invoice_not_sold_on_instalments_is_refused(self):
        other = self.sell(50000, method="cash")
        with self.assertRaises(ContractError):
            self.contract(sale_id=other.id)

    def test_invoice_without_a_customer_is_refused_at_the_sale(self):
        with self.assertRaises(sales.SaleError):
            self.sell(50000, customer=False)

    def test_cashier_cannot_approve(self):
        before = self.counts()
        with self.assertRaises(ContractError):
            self.contract(by=self.cashier)
        self.assertEqual(self.counts(), before)

    def test_bad_terms_are_refused_without_writing(self):
        before = self.counts()
        for kw in ({"down": 1300000}, {"months": 1}, {"rate_bp": 10001}, {"national_id": "123"},
                   {"first_due": "2026-11-01"}, {"contract_date": "2026-11-01"}):
            with self.assertRaises(ContractError, msg=str(kw)):
                self.contract(**kw)
        self.assertEqual(self.counts(), before)

    def test_down_without_a_method_is_refused(self):
        with self.assertRaises(ContractError):
            self.contract(down_method=None)

    def test_posted_contract_cannot_be_edited_or_deleted(self):
        self.contract()
        for sql in ("UPDATE installment_contracts SET down = 1", "DELETE FROM installment_contracts",
                    "UPDATE installment_rows SET amount = 1", "DELETE FROM installment_rows"):
            with self.assertRaises(sqlite3.DatabaseError, msg=sql):
                self.con.execute(sql)


if __name__ == "__main__":
    unittest.main()
