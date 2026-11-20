"""EIR journal posting, idempotence and fail-closed contract-timing tests."""
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from morv.core.sales_rules import Payment
from morv.infra import installments, interest_recognition, ledger, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.interest_recognition import RecognitionError, recognise_month
from morv.infra.sales import SaleLineIn


class EirPostingTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="morv_eir_posting_"))
        self.con = open_main_db(self.folder / "test.db")
        self.manager = users.create_user(self.con, None, "eir_admin", "مدير وهمي", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.manager, "eir_cashier", "كاشير", "Cash12345", "cashier")
        self.customer = self.con.execute(
            "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000031',0) RETURNING id"
        ).fetchone()[0]
        self.service = self.con.execute(
            "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
            "VALUES ('service','EIR-SVC','خدمة وهمية','خدمه وهميه',1200000,1200000,1200000) RETURNING id"
        ).fetchone()[0]
        self.serial = 0

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def contract(self, *, rate_bp=1500):
        self.serial += 1
        serial = self.serial
        sale = sales.post_sale(
            self.con, self.manager, request_key=f"eir-sale-{serial:04d}",
            customer_id=self.customer, lines=[SaleLineIn(self.service, 1, 1200000)],
            payments=[Payment("installment", 1200000)], sale_date="2025-07-10",
        )
        contract = installments.post_contract(
            self.con, self.manager, request_key=f"eir-contract-{serial:04d}",
            sale_id=sale.id, down=200000, rate_bp=rate_bp, months=10,
            first_due="2025-08-13", national_id="29001011201234",
            down_method="cash", contract_date="2025-07-13",
        )
        return contract, sale

    def state(self):
        return (
            tuple(self.con.execute(
                "SELECT id, contract_id, period, amount, journal_entry_id FROM interest_recognitions ORDER BY id"
            )),
            tuple(self.con.execute(
                "SELECT id, source_type, source_id, entry_date, total FROM journal_entries ORDER BY id"
            )),
            tuple(ledger.account_balance(self.con, code) for code in ("1320", "2410", "4140", "1110")),
        )

    def recognition(self, cid, period="2025-07", **kw):
        return recognise_month(self.con, self.manager, contract_id=cid, period=period, **kw)

    def test_first_closed_month_posts_correct_journal_without_changing_receivable(self):
        contract, _sale = self.contract()
        before = {code: ledger.account_balance(self.con, code) for code in ("1320", "2410", "4140")}
        posted = self.recognition(contract.id, entry_date="2025-08-01")
        self.assertFalse(posted.replayed)
        self.assertGreater(posted.amount, 0)
        self.assertEqual(ledger.account_balance(self.con, "1320"), before["1320"])
        self.assertEqual(ledger.account_balance(self.con, "2410"), before["2410"] + posted.amount)
        self.assertEqual(ledger.account_balance(self.con, "4140"), before["4140"] - posted.amount)
        self.assertEqual(self.con.execute(
            "SELECT debit, credit FROM journal_lines WHERE entry_id="
            "(SELECT journal_entry_id FROM interest_recognitions WHERE id=?) ORDER BY line_no",
            (posted.id,),
        ).fetchall(), [(posted.amount, 0), (0, posted.amount)])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(ledger.incomplete_entries(self.con), [])

    def test_replay_changes_nothing(self):
        contract, _sale = self.contract()
        first = self.recognition(contract.id)
        state = self.state()
        again = self.recognition(contract.id)
        self.assertEqual((first.id, first.amount), (again.id, again.amount))
        self.assertTrue(again.replayed)
        self.assertEqual(self.state(), state)

    def test_two_contracts_same_month_post_independently(self):
        first, _ = self.contract()
        second, _ = self.contract()
        a = self.recognition(first.id)
        b = self.recognition(second.id)
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.amount, b.amount)
        self.assertEqual(self.con.execute("SELECT count(*) FROM interest_recognitions").fetchone()[0], 2)
        self.assertEqual(ledger.account_balance(self.con, "4140"), -(a.amount + b.amount))

    def test_past_months_post_in_order_and_exact_due_date_collections_work(self):
        contract, _sale = self.contract()
        with self.assertRaisesRegex(RecognitionError, "السابقة"):
            self.recognition(contract.id, "2025-08")
        self.recognition(contract.id, "2025-07")
        with self.assertRaisesRegex(RecognitionError, "التحصيلات"):
            self.recognition(contract.id, "2025-08")
        self.con.execute("SELECT 1")  # The refusal must not close the database connection.
        installments.collect_payment(
            self.con, self.manager, request_key="eir-pmt-ontime-001",
            contract_id=contract.id, amount=115000, method="cash", payment_date="2025-08-13",
        )
        before_receivable = ledger.account_balance(self.con, "1320")
        august = self.recognition(contract.id, "2025-08")
        self.assertGreater(august.amount, 0)
        self.assertEqual(ledger.account_balance(self.con, "1320"), before_receivable)
        self.assertEqual(self.con.execute("SELECT count(*) FROM installment_payments").fetchone()[0], 1)

    def test_early_or_late_collection_is_refused_until_timing_policy_exists(self):
        for payment_date in ("2025-08-01", "2025-09-01"):
            with self.subTest(payment_date=payment_date):
                contract, _sale = self.contract()
                self.recognition(contract.id, "2025-07")
                installments.collect_payment(
                    self.con, self.manager, request_key=f"eir-pay-{self.serial:04d}",
                    contract_id=contract.id, amount=115000, method="cash", payment_date=payment_date,
                )
                before = self.state()
                with self.assertRaises(RecognitionError):
                    self.recognition(contract.id, "2025-08")
                self.assertEqual(self.state(), before)

    def test_partial_receipt_on_due_date_cannot_masquerade_as_full_receipt(self):
        contract, _ = self.contract()
        self.recognition(contract.id)
        installments.collect_payment(
            self.con, self.manager, request_key="eir-partial-pay-001",
            contract_id=contract.id, amount=20000, method="cash", payment_date="2025-08-13",
        )
        before = self.state()
        with self.assertRaisesRegex(RecognitionError, "التحصيلات"):
            self.recognition(contract.id, "2025-08")
        self.assertEqual(self.state(), before)

    def test_unclosed_period_and_backdating_are_refused_without_mutation(self):
        contract, _sale = self.contract()
        before = self.state()
        with self.assertRaisesRegex(RecognitionError, "لم ينته"):
            self.recognition(contract.id, date.today().strftime("%Y-%m"))
        with self.assertRaisesRegex(RecognitionError, "تاريخ القيد"):
            self.recognition(contract.id, "2025-07", entry_date="2025-07-25")
        self.assertEqual(self.state(), before)

    def test_cashier_cannot_post_ledger_recognition(self):
        contract, _sale = self.contract()
        before = self.state()
        with self.assertRaisesRegex(RecognitionError, "صلاحية"):
            recognise_month(self.con, self.cashier, contract_id=contract.id, period="2025-07")
        self.assertEqual(self.state(), before)

    def test_no_markup_creates_no_journal(self):
        contract, _sale = self.contract(rate_bp=0)
        before = self.state()
        self.assertIsNone(self.recognition(contract.id))
        self.assertEqual(self.state(), before)

    def test_existing_returned_sale_is_refused_even_if_old_data_was_posted(self):
        contract, sale = self.contract()
        sale_entry = self.con.execute(
            "SELECT journal_entry_id FROM sales WHERE id=?", (sale.id,),
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO sale_returns "
            "(number, request_key, payload_hash, sale_id, return_date, total, cost_total, "
            "reason, refund_method, journal_entry_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("LEGACY-RETURN-001", "legacy-return-test", "legacy", sale.id,
             "2025-07-20", 0, 0, "synthetic legacy return", "installment", sale_entry),
        )
        before = self.state()
        with self.assertRaisesRegex(RecognitionError, "مرتجع"):
            self.recognition(contract.id)
        self.assertEqual(self.state(), before)

    def test_ledger_failure_rolls_back_entire_recognition(self):
        contract, _sale = self.contract()
        before = self.state()
        with patch.object(interest_recognition.ledger, "post_entry",
                          side_effect=ledger.LedgerError("synthetic failure")):
            with self.assertRaises(RecognitionError):
                self.recognition(contract.id)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_posted_rows_are_immutable(self):
        contract, _ = self.contract()
        posted = self.recognition(contract.id)
        for sql in (
            "UPDATE interest_recognitions SET amount=1 WHERE id=?",
            "DELETE FROM interest_recognitions WHERE id=?",
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(sql, (posted.id,))

    def test_financial_totals_equal_contract_when_fully_paid_on_schedule(self):
        contract, _ = self.contract()
        months = ("2025-07", "2025-08", "2025-09", "2025-10", "2025-11",
                  "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05")
        for i in range(10):
            first = date(2025, 8, 13)
            due = first.replace(year=2026, month=i-4) if i >= 5 else first.replace(month=8+i)
            installments.collect_payment(
                self.con, self.manager, request_key=f"eir-fullpay-{i:04d}",
                contract_id=contract.id, amount=115000, method="cash",
                payment_date=due.isoformat(),
            )
        for period in months:
            self.recognition(contract.id, period)
        markup = self.con.execute("SELECT markup FROM installment_contracts WHERE id=?",
                                  (contract.id,)).fetchone()[0]
        recognized = self.con.execute(
            "SELECT coalesce(sum(amount),0) FROM interest_recognitions WHERE contract_id=?", (contract.id,),
        ).fetchone()[0]
        self.assertEqual(recognized, markup)
        self.assertEqual(ledger.account_balance(self.con, "2410"), 0)
        self.assertEqual(ledger.account_balance(self.con, "1320"), 0)
        self.assertEqual(ledger.account_balance(self.con, "4140"), -markup)


class HistoricMigrationTest(unittest.TestCase):
    def test_legacy_v13_schema_upgrades_to_v14_without_rewriting_posted_documents(self):
        folder = Path(tempfile.mkdtemp(prefix="morv_eir_old_db_"))
        con = None
        try:
            con = open_db(folder / "old.db", create=True)
            migrations = load_migrations()
            migrate(con, migrations[:13])
            self.assertEqual(current_version(con), 13)
            before = tuple(con.execute("SELECT version, checksum FROM schema_migrations ORDER BY version"))
            self.assertEqual(migrate(con, migrations[:14]), [14])
            self.assertEqual(current_version(con), 14)
            self.assertEqual(tuple(con.execute(
                "SELECT version, checksum FROM schema_migrations WHERE version<=13 ORDER BY version")), before)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(migrate(con, migrations[:16]), [15, 16])
            self.assertEqual(current_version(con), 16)
            self.assertEqual(migrate(con, migrations[:16]), [])
        finally:
            if con is not None:
                con.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
