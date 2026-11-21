"""Independent original-EIR modification valuation and safe v14 -> v15 upgrade."""
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path

from morv.core.eir import calculate
from morv.core.partial_eir import ModificationError, remeasure
from morv.core.sales_rules import Payment
from morv.infra import installments, interest_recognition, ledger, sale_returns, sales, users
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.sale_returns import ReturnLineIn
from morv.infra.sales import SaleLineIn


class PartialEirMathTest(unittest.TestCase):
    ORIGINAL = [("2027-01-01", 5500), ("2028-01-01", 6050)]

    def value(self, **kwargs):
        arguments = dict(
            effective_date="2026-01-01",
            original_annual_rate=Decimal("0.1"),
            original_future=self.ORIGINAL,
            modified_future=[("2027-01-01", 2750), ("2028-01-01", 3025)],
            carrying_before=10000,
            received=2000,
            approved_total_due=7775,
            classification="non_derecognition",
        )
        arguments.update(kwargs)
        return remeasure(**arguments)

    def test_original_rate_remeasures_modified_future_without_prorating_interest(self):
        result = self.value()
        self.assertEqual(result.original_future_pv, 10000)
        self.assertEqual(result.modified_future_pv, 5000)
        self.assertEqual(result.modification_gain_loss, -5000)
        self.assertEqual((result.applied_receipts, result.refundable_excess,
                          result.remaining_claim), (2000, 0, 5775))
        self.assertEqual(result.modified_future, tuple(self.value().modified_future))
        self.assertEqual(len(result.fingerprint), 64)

    def test_original_contract_rate_matches_the_separate_eir_engine(self):
        reference = calculate(10000, "2026-01-01", self.ORIGINAL)
        result = self.value(original_annual_rate=reference.annual_rate,
                            modified_future=self.ORIGINAL,
                            received=0, approved_total_due=11550)
        self.assertEqual((result.original_future_pv, result.modified_future_pv,
                          result.modification_gain_loss), (10000, 10000, 0))

    def test_settled_receipts_do_not_generate_cash_refunds_without_an_excess(self):
        result = self.value(received=5775, approved_total_due=11550)
        self.assertEqual((result.applied_receipts, result.refundable_excess,
                          result.remaining_claim), (5775, 0, 5775))

    def test_refund_only_the_excess_over_signed_claim_not_a_value_proration(self):
        result = self.value(original_future=[], modified_future=[],
                            carrying_before=0, received=8000, approved_total_due=6000)
        self.assertEqual((result.refundable_excess, result.remaining_claim,
                          result.modification_gain_loss), (2000, 0, 0))

    def test_zero_rate_uses_simple_present_value(self):
        result = self.value(
            original_annual_rate=Decimal(0),
            original_future=[("2026-02-01", 60), ("2026-03-01", 40)],
            modified_future=[("2026-03-01", 50)],
            carrying_before=100, received=10, approved_total_due=60,
        )
        self.assertEqual((result.original_future_pv, result.modified_future_pv,
                          result.modification_gain_loss), (100, 50, -50))

    def test_leap_day_uses_actual_days_over_365(self):
        # Reconcile with independent fractional-year calculation instead of fixed months.
        with localcontext() as ctx:
            ctx.prec = 50
            elapsed = (date(2028, 3, 1) - date(2027, 3, 1)).days
            self.assertEqual(elapsed, 366)
            old_pv = int((Decimal(1100000) / Decimal("1.1") **
                          (Decimal(elapsed) / Decimal(365))).quantize(
                              Decimal(1), rounding=ROUND_HALF_UP))
        base = self.value(
            effective_date="2027-03-01", original_annual_rate=Decimal("0.1"),
            original_future=[("2028-03-01", 1100000)],
            modified_future=[("2028-03-01", 550000)],
            carrying_before=old_pv, received=0, approved_total_due=550000,
        )
        self.assertEqual(base.original_future_pv, old_pv)

    def test_stale_carrying_amount_refuses_to_value_modified_schedule(self):
        with self.assertRaisesRegex(ModificationError, "carrying"):
            self.value(carrying_before=9990)

    def test_unagreed_schedule_totals_are_rejected(self):
        for amended in (
            [("2027-01-01", 2750)],
            [("2027-01-01", 2750), ("2028-01-01", 3026)],
        ):
            with self.subTest(amended=amended), self.assertRaises(ModificationError):
                self.value(modified_future=amended)

    def test_invalid_money_rates_dates_and_classifications_fail_closed(self):
        bad = [
            {"classification": "derecognition"},
            {"classification": ""},
            {"original_annual_rate": 0.1},
            {"original_annual_rate": Decimal("Infinity")},
            {"original_annual_rate": Decimal("-0.1")},
            {"effective_date": "20260101"},
            {"original_future": [("2025-12-31", 10000)]},
            {"original_future": list(reversed(self.ORIGINAL))},
            {"modified_future": [("2027-01-01", 2750), ("2027-01-01", 3025)]},
            {"modified_future": [("2027-01-01", 0), ("2028-01-01", 5775)]},
            {"modified_future": [("2027-01-01", True), ("2028-01-01", 5774)]},
            {"received": -1},
            {"carrying_before": True},
            {"approved_total_due": 7775.0},
        ]
        for update in bad:
            with self.subTest(update=update), self.assertRaises(ModificationError):
                self.value(**update)

    def test_fingerprint_changes_when_signed_terms_change(self):
        one = self.value()
        another = self.value(modified_future=[
            ("2027-02-01", 2750), ("2028-01-01", 3025)
        ])
        self.assertNotEqual(one.fingerprint, another.fingerprint)


class PartialModificationMigrationTest(unittest.TestCase):
    def test_populated_v14_history_survives_v15_and_schema_is_immutable(self):
        folder = Path(tempfile.mkdtemp(prefix="morv_partial_eir_migration_"))
        con = None
        try:
            con = open_db(folder / "old.db", create=True)
            all_migrations = load_migrations()
            self.assertEqual(all_migrations[15].version, 16)
            migrate(con, all_migrations[:14])
            self.assertEqual(current_version(con), 14)
            manager = users.create_user(con, None, "partial_eir_manager",
                                        "مدير اختبار", "Admin12345", "manager")
            customer_id = con.execute(
                "INSERT INTO customers (name,name_norm,mobile,credit_limit) "
                "VALUES ('عميل اختبار','عميل اختبار','01000000064',0) RETURNING id"
            ).fetchone()[0]
            service_id = con.execute(
                "INSERT INTO items (kind,code,name,name_norm,price_retail,price_wholesale,price_super) "
                "VALUES ('service','PART-EIR-SVC','خدمة','خدمه',1200000,1200000,1200000) "
                "RETURNING id"
            ).fetchone()[0]
            sale = sales.post_sale(
                con, manager, request_key="part-eir-sale-test",
                customer_id=customer_id, lines=[SaleLineIn(service_id, 1, 1200000)],
                payments=[Payment("installment", 1200000)], sale_date="2025-07-10",
            )
            contract = installments.post_contract(
                con, manager, request_key="part-eir-contract-test",
                sale_id=sale.id, down=200000, rate_bp=1500, months=10,
                first_due="2025-08-13", national_id="29001011201234",
                down_method="cash", contract_date="2025-07-13",
            )
            # v14 predates reversals; current code reads the view, so an old schema gets the equivalent one
            con.execute("CREATE VIEW active_interest_recognitions AS SELECT * FROM interest_recognitions")
            interest_recognition.recognise_month(
                con, manager, contract_id=contract.id, period="2025-07",
                entry_date="2025-08-01",
            )
            installments.collect_payment(
                con, manager, request_key="part-eir-payment-test",
                contract_id=contract.id, amount=115000, method="cash",
                payment_date="2025-08-13",
            )
            line_id = con.execute("SELECT id FROM sale_lines WHERE sale_id=?",
                                  (sale.id,)).fetchone()[0]
            ret = sale_returns.post_return(
                con, manager, request_key="part-eir-return-test", sale_id=sale.id,
                lines=[ReturnLineIn(line_id, 1)],
                reason="مرتجع اختبار", return_date="2025-09-02",
            )
            con.execute("DROP VIEW active_interest_recognitions")
            tables = (
                "schema_migrations", "installment_contracts", "installment_rows",
                "installment_payments", "installment_payment_lines",
                "interest_recognitions", "sale_returns", "sale_return_lines",
                "installment_return_adjustments", "installment_return_refunds",
                "installment_return_allocations", "journal_entries", "journal_lines",
            )
            before = {
                name: tuple(con.execute(f"SELECT * FROM {name} ORDER BY rowid"))
                for name in tables
            }
            balances = {
                code: ledger.account_balance(con, code)
                for code in ("1110", "1220", "1320", "2410", "4140", "4190")
            }
            self.assertEqual(migrate(con, all_migrations[:15]), [15])
            self.assertEqual(current_version(con), 15)
            self.assertEqual({
                name: tuple(con.execute(f"SELECT * FROM {name} ORDER BY rowid"))
                for name in tables if name != "schema_migrations"
            }, {name: value for name, value in before.items() if name != "schema_migrations"})
            self.assertEqual(tuple(con.execute(
                "SELECT * FROM schema_migrations WHERE version<=14 ORDER BY rowid"
            )), before["schema_migrations"])
            self.assertEqual({code: ledger.account_balance(con, code) for code in balances},
                             balances)
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(migrate(con, all_migrations[:15]), [])

            entry_id = con.execute(
                "SELECT journal_entry_id FROM sale_returns WHERE id=?", (ret.id,),
            ).fetchone()[0]
            con.execute(
                "INSERT INTO installment_modifications "
                "(contract_id,return_id,effective_date,agreement_reference,"
                "original_annual_rate,carrying_before,original_future_pv,"
                "modified_future_pv,modification_gain_loss,received,approved_total_due,"
                "applied_receipts,refundable_excess,remaining_claim,schedule_hash,"
                "journal_entry_id,approved_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (contract.id, ret.id, "2025-09-02", "TEST-SIGNED-001", "0.1",
                 10000, 10000, 5000, -5000, 2000, 7775, 2000, 0, 5775,
                 "a" * 64, entry_id, manager.id),
            )
            modification_id = con.execute(
                "SELECT id FROM installment_modifications"
            ).fetchone()[0]
            con.execute(
                "INSERT INTO installment_modified_cashflows "
                "(modification_id,row_no,due_date,amount) VALUES (?,?,?,?)",
                (modification_id, 1, "2025-10-13", 5775),
            )
            for statement, args in (
                ("UPDATE installment_modifications SET modified_future_pv=100 WHERE id=?",
                 (modification_id,)),
                ("DELETE FROM installment_modifications WHERE id=?", (modification_id,)),
                ("UPDATE installment_modified_cashflows SET amount=20 WHERE modification_id=?",
                 (modification_id,)),
                ("DELETE FROM installment_modified_cashflows WHERE modification_id=?",
                 (modification_id,)),
            ):
                with self.assertRaises(sqlite3.DatabaseError):
                    con.execute(statement, args)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            if con is not None:
                con.close()
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
