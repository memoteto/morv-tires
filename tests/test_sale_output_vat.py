"""P04-4C.1: output VAT, immutable snapshots, payment and migration safety."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installments, inventory, ledger, purchasing, sale_returns, sales, settings, users
from morv.infra.db.connection import open_db
from morv.infra.db.migrator import current_version, load_migrations, migrate
from morv.infra.purchasing import PurchaseLine
from morv.infra.sale_returns import ReturnError, ReturnLineIn
from morv.infra.sales import SaleError, SaleLineIn


class _Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="morv_sale_vat_")
        self.addCleanup(self.temp.cleanup)
        self.con = open_db(Path(self.temp.name) / "test.db", create=True)
        self.addCleanup(self.con.close)
        self.migrations = load_migrations()
        migrate(self.con, self.migrations[:self.version])
        self.admin = users.create_user(self.con, None, "admin", "مدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "cash", "كاشير", "Cash12345", "cashier")
        self.supplier = self.con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile) VALUES ('مورد','مورد','01000000001') "
            "RETURNING id").fetchone()[0]
        self.customer = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل','عميل','01000000002',500000) RETURNING id").fetchone()[0]
        self.tire = self.item("tire", "VAT-T", 114000)
        self.service = self.item("service", "VAT-S", 100000)
        purchasing.post_invoice(self.con, self.admin, request_key="vat-buy-001",
                                supplier_id=self.supplier, supplier_invoice_no="VAT-BUY-1",
                                invoice_date="2026-11-01",
                                lines=[PurchaseLine(self.tire, 10, 80000, "2324")])

    def item(self, kind, code, price):
        return self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES (?,?,?,?,?,?,?) RETURNING id",
            (kind, code, code, code.lower(), price, price, price)).fetchone()[0]

    def post(self, key, lines, payments, **kw):
        by = kw.pop("by", self.admin)
        kw.setdefault("customer_id", None)
        return sales.post_sale(self.con, by, request_key=key, lines=lines, payments=payments,
                               sale_date="2026-11-10", **kw)

    def balance(self, code):
        return ledger.account_balance(self.con, code)

    def snapshot(self, tables):
        return {table: tuple(self.con.execute(f"SELECT * FROM {table} ORDER BY id"))
                for table in tables}


class OutputVatTest(_Fixture, unittest.TestCase):
    version = 37

    def enable(self):
        settings.put(self.con, "tax.enabled", "1")

    def test_inclusive_and_exclusive_have_identical_revenue_tax_and_fifo_cost(self):
        self.enable()
        for mode, price, key in ((True, 114000, "vat-inclusive"), (False, 100000, "vat-exclusive")):
            with self.subTest(inclusive=mode):
                before = {c: self.balance(c) for c in ("1110", "4110", "2350", "5110", "1410")}
                lines = [SaleLineIn(self.tire, 1, price)]
                quote = sales.compute_sale_totals(self.con, lines, prices_tax_inclusive=mode)
                self.assertEqual((quote.total, quote.tax_total, quote.lines[0].revenue),
                                 (114000, 14000, 100000))
                posted = self.post(key, lines, [Payment("cash", 114000, received=114000)],
                                   prices_tax_inclusive=mode)
                self.assertEqual(posted.total, quote.total)
                self.assertEqual({c: self.balance(c) - v for c, v in before.items()},
                                 {"1110": 114000, "4110": -100000, "2350": -14000,
                                  "5110": 80000, "1410": -80000})
                header = self.con.execute(
                    "SELECT tax_enabled, prices_tax_inclusive, tax_total, total FROM sales WHERE id=?",
                    (posted.id,)).fetchone()
                self.assertEqual(header, (1, int(mode), 14000, 114000))
                line = self.con.execute(
                    "SELECT tax_treatment, tax_rate, tax_amount, net_total FROM sale_lines WHERE sale_id=?",
                    (posted.id,)).fetchone()
                self.assertEqual(line, ("standard", 1400, 14000, 114000))
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_mixed_treatments_and_both_discounts_use_post_discount_base(self):
        self.enable()
        exempt = self.item("service", "VAT-E", 10000)
        zero = self.item("service", "VAT-Z", 10000)
        override = self.item("service", "VAT-O", 10000)
        self.con.execute("UPDATE items SET tax_treatment='exempt' WHERE id=?", (exempt,))
        self.con.execute("UPDATE items SET tax_treatment='zero' WHERE id=?", (zero,))
        self.con.execute("UPDATE items SET tax_rate=500 WHERE id=?", (override,))
        lines = [SaleLineIn(self.service, 1, 11000, line_discount=1000),
                 SaleLineIn(exempt, 1, 10000), SaleLineIn(zero, 1, 10000),
                 SaleLineIn(override, 1, 10000)]
        quote = sales.compute_sale_totals(self.con, lines, 4000, prices_tax_inclusive=False)
        self.assertEqual([line.invoice_share for line in quote.lines], [1000] * 4)
        self.assertEqual([line.revenue for line in quote.lines], [9000] * 4)
        self.assertEqual([line.tax_amount for line in quote.lines], [1260, 0, 0, 450])
        self.assertEqual((quote.total, quote.tax_total), (37710, 1710))
        self.post("vat-mixed", lines, [Payment("card", 37710)],
                  invoice_discount=4000, prices_tax_inclusive=False)
        self.assertEqual(self.balance("4130"), -36000)
        self.assertEqual(self.balance("2350"), -1710)

    def test_disabled_and_replay_preserve_original_snapshot(self):
        lines = [SaleLineIn(self.service, 1, 100000)]
        cash = [Payment("cash", 100000, received=100000)]
        legacy = self.post("vat-legacy", lines, cash)
        self.enable()
        self.assertTrue(self.post("vat-legacy", lines, cash).replayed)
        self.assertEqual(self.con.execute("SELECT tax_enabled, tax_total FROM sales WHERE id=?",
                                          (legacy.id,)).fetchone(), (0, 0))
        paid = [Payment("cash", 114000, received=114000)]
        original = self.post("vat-replay", lines, paid, prices_tax_inclusive=False)
        saved = self.snapshot(("sales", "sale_lines", "journal_entries", "journal_lines"))
        settings.put(self.con, "tax.rate", "1000")
        settings.put(self.con, "tax.enabled", "0")
        again = self.post("vat-replay", lines, paid, prices_tax_inclusive=False)
        self.assertEqual((again.id, again.total, again.replayed), (original.id, 114000, True))
        with self.assertRaisesRegex(SaleError, "محتوى مختلف"):
            self.post("vat-replay", lines, paid, prices_tax_inclusive=True)
        self.assertEqual(self.snapshot(saved), saved)

    def test_invalid_payments_and_missing_price_basis_leave_no_records(self):
        self.enable()
        lines = [SaleLineIn(self.tire, 1, 100000)]
        before = self.snapshot(("sales", "sale_lines", "sale_payments", "stock_lots",
                                "cost_layers", "journal_entries", "journal_lines"))
        with self.assertRaisesRegex(SaleError, "أساس"):
            self.post("vat-no-mode", lines, [Payment("cash", 114000, received=114000)])
        with self.assertRaisesRegex(SaleError, "مجموع الدفع"):
            self.post("vat-wrong-payment", lines, [Payment("cash", 100000, received=100000)],
                      prices_tax_inclusive=False)
        self.assertEqual(self.snapshot(before), before)

    def test_below_cost_checks_net_revenue_not_gross_and_rolls_back(self):
        self.enable()
        users.set_permission(self.con, self.admin, self.cashier.id, "sales.price_override", True)
        before = self.snapshot(("sales", "sale_lines", "sale_payments", "stock_lots",
                                "cost_layers", "journal_entries", "journal_lines"))
        # Gross 850 > FIFO cost 800, but the real revenue is only 745.61.
        with self.assertRaisesRegex(SaleError, "التكلفة"):
            self.post("vat-below-cost", [SaleLineIn(self.tire, 1, 85000)],
                      [Payment("cash", 85000, received=85000)], by=self.cashier,
                      prices_tax_inclusive=True)
        self.assertEqual(self.snapshot(before), before)

    def test_credit_and_installment_principal_use_gross_payable(self):
        self.enable()
        lines = [SaleLineIn(self.service, 1, 100000)]
        self.post("vat-credit", lines, [Payment("cash", 14000, received=14000),
                                       Payment("credit", 100000)], customer_id=self.customer,
                  prices_tax_inclusive=False)
        sale = self.post("vat-instalment", lines, [Payment("installment", 114000)],
                         customer_id=self.customer, prices_tax_inclusive=False)
        self.assertEqual((self.balance("1110"), self.balance("1310"), self.balance("1320")),
                         (14000, 100000, 114000))
        # A zero-interest contract tests only principal; VAT on financing is a later decision.
        contract = installments.post_contract(
            self.con, self.admin, request_key="vat-contract", sale_id=sale.id,
            down=0, rate_bp=0, months=2, first_due="2026-12-10",
            national_id="29001011201234", contract_date="2026-11-10")
        self.assertEqual(contract.contract_total, 114000)
        self.assertEqual(self.balance("2350"), -28000)
        self.assertEqual(ledger.unbalanced_entries(self.con), [])

    def test_taxable_return_temporarily_refused_even_if_setting_disabled(self):
        self.enable()
        sale = self.post("vat-return-source", [SaleLineIn(self.tire, 1, 114000)],
                         [Payment("cash", 114000, received=114000)], prices_tax_inclusive=True)
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale.id,)).fetchone()[0]
        settings.put(self.con, "tax.enabled", "0")
        before = self.snapshot(("sale_returns", "sale_return_lines", "stock_lots", "journal_entries"))
        with self.assertRaisesRegex(ReturnError, "الضريبة"):
            sale_returns.post_return(self.con, self.admin, request_key="vat-return-block",
                                     sale_id=sale.id, lines=[ReturnLineIn(line, 1)],
                                     reason="اختبار", return_date="2026-11-11")
        self.assertEqual(self.snapshot(before), before)


class OutputVatUpgradeTest(_Fixture, unittest.TestCase):
    version = 36

    def test_upgrade_preserves_posted_sales_returns_contracts_and_all_links(self):
        cash = self.post("old-sale", [SaleLineIn(self.tire, 2, 114000)],
                         [Payment("cash", 228000, received=228000)])
        line = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (cash.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key="old-return", sale_id=cash.id,
                                 lines=[ReturnLineIn(line, 1)], reason="اختبار",
                                 return_date="2026-11-11")
        credit = self.post("old-instalment", [SaleLineIn(self.service, 1, 100000)],
                           [Payment("installment", 100000)], customer_id=self.customer)
        installments.post_contract(self.con, self.admin, request_key="old-contract", sale_id=credit.id,
                                   down=10000, rate_bp=1000, months=2, first_due="2026-12-12",
                                   national_id="29001011201234", down_method="cash",
                                   contract_date="2026-11-12")
        tables = ("sales", "sale_lines", "sale_payments", "sale_returns", "sale_return_lines",
                  "journal_entries", "journal_lines", "stock_lots", "stock_issues",
                  "stock_movements", "cost_layers", "cost_allocations",
                  "installment_contracts", "installment_rows")
        columns = {table: [r[1] for r in self.con.execute(f"PRAGMA table_info({table})")]
                   for table in tables}

        def snapshot_original():
            return {table: tuple(self.con.execute(
                f"SELECT {', '.join(names)} FROM {table} ORDER BY id"))
                for table, names in columns.items()}

        before = snapshot_original()
        self.assertEqual(current_version(self.con), 36)
        self.assertEqual((self.migrations[36].version, self.migrations[36].name), (37, "sale_output_vat"))
        self.assertEqual(migrate(self.con, self.migrations[:37]), [37])
        self.assertEqual(snapshot_original(), before)
        self.assertEqual(self.con.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.con.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.con.execute(
            "SELECT DISTINCT tax_enabled, prices_tax_inclusive, tax_total FROM sales").fetchall(), [(0, 0, 0)])
        self.assertEqual(self.con.execute(
            "SELECT DISTINCT tax_treatment, tax_rate, tax_amount FROM sale_lines").fetchall(), [("none", 0, 0)])
        self.assertEqual(inventory.reconcile(self.con), [])
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        for statement in ("UPDATE sales SET total=1", "DELETE FROM sales",
                          "UPDATE sale_lines SET tax_amount=1", "DELETE FROM sale_lines"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                self.con.execute(statement)
        replay = self.post("old-sale", [SaleLineIn(self.tire, 2, 114000)],
                           [Payment("cash", 228000, received=228000)])
        self.assertEqual((replay.id, replay.replayed), (cash.id, True))
        self.assertEqual(snapshot_original(), before)
