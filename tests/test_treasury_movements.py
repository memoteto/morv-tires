"""Partners and manual treasury movements: accounts, validation, idempotency, warnings, reversal."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from morv.infra import ledger, treasury, treasury_view, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.db.connection import transaction
from morv.infra.treasury import NeedsConfirmation, TreasuryError


class TreasuryMovementsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = open_main_db(Path(self.tmp.name) / "morv_tires.db")
        self.addCleanup(self.con.close)
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.cashier = users.create_user(self.con, self.admin, "karim", "كريم", "Karim12345", "cashier")
        self.n = 0
        self.fund("1110", 1_000_000)

    def key(self):
        self.n += 1
        return f"test-key-{self.n:04d}"

    def fund(self, account, amount):
        with transaction(self.con):
            ledger.post_entry(self.con, self.admin, "2026-11-01", "sale", self.n + 900, "رصيد افتتاحي وهمي",
                              [(account, amount, 0), ("4110", 0, amount)])

    def move(self, **kw):
        base = dict(request_key=self.key(), memo="بيان", money_account="1110")
        base.update(kw)
        return treasury.post_movement(self.con, self.admin, **base)

    def supplier(self, name="مورد وهمي"):
        return self.con.execute("INSERT INTO suppliers (name, name_norm, mobile) VALUES (?,?,?)",
                                (name, name, f"010000000{self.n}{len(name)}")).lastrowid

    def invoice(self, supplier_id, total):
        self.n += 1
        with transaction(self.con):
            e = ledger.post_entry(self.con, self.admin, "2026-11-02", "purchase_invoice", self.n + 500, "فاتورة وهمية",
                                  [("1410", total, 0), ("2110", 0, total)])
            self.con.execute(
                "INSERT INTO purchase_invoices (number, request_key, payload_hash, supplier_id, supplier_invoice_no, "
                "invoice_date, received_date, total, journal_entry_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"PI-{self.n}", f"inv-key-{self.n:04d}", "h", supplier_id, f"S-{self.n}", "2026-11-02", "2026-11-02", total, e))

    def lines(self, movement_id):
        return self.con.execute(
            "SELECT account_code, debit, credit FROM journal_lines l JOIN treasury_movements m "
            "ON m.journal_entry_id = l.entry_id WHERE m.id = ? ORDER BY l.line_no", (movement_id,)).fetchall()

    # ---------- partners ----------

    def test_new_database_retires_the_shared_capital_and_owner_accounts(self):
        active = dict(self.con.execute("SELECT code, active FROM accounts WHERE code IN ('3100','3300')"))
        self.assertEqual(active, {"3100": 0, "3300": 0})

    def test_adding_a_partner_creates_capital_and_current_accounts_with_the_same_number(self):
        a = treasury.add_partner(self.con, self.admin, "أحمد")
        b = treasury.add_partner(self.con, self.admin, "محمود")
        self.assertEqual((a.capital_account, a.account), ("3101", "3301"))
        self.assertEqual((b.capital_account, b.account), ("3102", "3302"))
        names = dict(self.con.execute("SELECT code, name FROM accounts WHERE code IN ('3101','3301')"))
        self.assertEqual(names, {"3101": "رأس مال أحمد", "3301": "جاري أحمد"})

    def test_a_sole_owner_is_just_one_partner(self):
        treasury.add_partner(self.con, self.admin, "المالك")
        self.assertEqual(len(treasury.list_partners(self.con, active_only=True)), 1)

    def test_partner_name_must_be_unique_and_non_empty(self):
        treasury.add_partner(self.con, self.admin, "أحمد")
        for bad in ("أحمد", "  ", ""):
            with self.assertRaises(TreasuryError):
                treasury.add_partner(self.con, self.admin, bad)
        self.assertEqual(len(treasury.list_partners(self.con)), 1)

    def test_deactivating_a_partner_blocks_new_movements_but_keeps_history(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        self.move(kind="partner_deposit", amount=5000, partner_id=p.id)
        treasury.set_partner_active(self.con, self.admin, p.id, False)
        with self.assertRaises(TreasuryError):
            self.move(kind="partner_withdrawal", amount=100, partner_id=p.id)
        self.assertEqual(treasury.partner_balance(self.con, p.id), 5000)
        treasury.set_partner_active(self.con, self.admin, p.id, True)
        self.move(kind="partner_withdrawal", amount=100, partner_id=p.id)

    def test_partner_rows_cannot_be_deleted(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM partners WHERE id=?", (p.id,))

    def test_only_managers_manage_partners_and_movements(self):
        with self.assertRaises(TreasuryError):
            treasury.add_partner(self.con, self.cashier, "أحمد")
        with self.assertRaises(TreasuryError):
            treasury.post_movement(self.con, self.cashier, request_key=self.key(), kind="transfer", amount=100,
                                   memo="x", money_account="1110", other_account="1210")

    # ---------- the six movement types ----------

    def test_transfer_moves_money_between_accounts(self):
        m = self.move(kind="transfer", amount=40000, other_account="1210", memo="إيداع بنكي")
        self.assertEqual(self.lines(m.id), [("1210", 40000, 0), ("1110", 0, 40000)])
        self.assertEqual(dict(treasury_view.balances(self.con))["1210"], 40000)

    def test_expense_debits_the_chosen_expense_account(self):
        m = self.move(kind="expense", amount=25000, other_account="5210", memo="إيجار نوفمبر", reference="إيصال 77")
        self.assertEqual(self.lines(m.id), [("5210", 25000, 0), ("1110", 0, 25000)])
        self.assertEqual(self.con.execute("SELECT reference FROM treasury_movements WHERE id=?", (m.id,)).fetchone()[0],
                         "إيصال 77")

    def test_expense_cannot_use_inventory_difference_or_non_expense_accounts(self):
        for acc in ("5270", "5280", "4110", "1410", None):
            with self.subTest(acc=acc), self.assertRaises(TreasuryError):
                self.move(kind="expense", amount=100, other_account=acc)

    def test_partner_deposit_credits_the_current_account_and_capital_the_capital_account(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        d = self.move(kind="partner_deposit", amount=70000, partner_id=p.id, money_account="1210", memo="تمويل")
        c = self.move(kind="partner_capital", amount=300000, partner_id=p.id, memo="زيادة رأس المال", reference="عقد 5")
        self.assertEqual(self.lines(d.id), [("1210", 70000, 0), (p.account, 0, 70000)])
        self.assertEqual(self.lines(c.id), [("1110", 300000, 0), (p.capital_account, 0, 300000)])
        self.assertEqual(treasury.partner_balance(self.con, p.id), 70000)

    def test_partner_withdrawal_debits_the_current_account(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        self.move(kind="partner_deposit", amount=50000, partner_id=p.id)
        w = self.move(kind="partner_withdrawal", amount=20000, partner_id=p.id)
        self.assertEqual(self.lines(w.id), [(p.account, 20000, 0), ("1110", 0, 20000)])
        self.assertEqual(treasury.partner_balance(self.con, p.id), 30000)

    def test_supplier_payment_is_tied_to_the_supplier_and_reduces_what_is_owed(self):
        s = self.supplier()
        self.invoice(s, 90000)
        m = self.move(kind="supplier_payment", amount=30000, supplier_id=s)
        self.assertEqual(self.lines(m.id), [("2110", 30000, 0), ("1110", 0, 30000)])
        self.assertEqual(treasury.supplier_owed(self.con, s), 60000)
        self.assertEqual(self.con.execute("SELECT supplier_id FROM treasury_movements WHERE id=?", (m.id,)).fetchone()[0], s)

    # ---------- validation ----------

    def test_invalid_input_is_refused_and_writes_nothing(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        s = self.supplier()
        entries_before = self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0]
        cases = [
            dict(kind="transfer", amount=0, other_account="1210"),
            dict(kind="transfer", amount=-5, other_account="1210"),
            dict(kind="transfer", amount=10.5, other_account="1210"),
            dict(kind="transfer", amount=100, other_account="1110"),
            dict(kind="transfer", amount=100, other_account="4110"),
            dict(kind="transfer", amount=100, other_account="1210", money_account="4110"),
            dict(kind="transfer", amount=100, other_account="1210", memo="  "),
            dict(kind="transfer", amount=100, other_account="1210", movement_date="2999-01-01"),
            dict(kind="transfer", amount=100, other_account="1210", movement_date="غير صالح"),
            dict(kind="nonsense", amount=100),
            dict(kind="partner_withdrawal", amount=100),
            dict(kind="partner_withdrawal", amount=100, partner_id=9999),
            dict(kind="supplier_payment", amount=100),
            dict(kind="supplier_payment", amount=100, supplier_id=9999),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(TreasuryError):
                self.move(**case)
        self.assertEqual(self.con.execute("SELECT count(*) FROM journal_entries").fetchone()[0], entries_before)
        self.assertEqual(self.con.execute("SELECT count(*) FROM treasury_movements").fetchone()[0], 0)
        self.assertEqual((p.id, s) > (0, 0), True)

    def test_inactive_supplier_cannot_be_paid(self):
        s = self.supplier()
        self.con.execute("UPDATE suppliers SET active=0 WHERE id=?", (s,))
        with self.assertRaises(TreasuryError):
            self.move(kind="supplier_payment", amount=100, supplier_id=s)

    # ---------- idempotency and warnings ----------

    def test_same_request_key_replays_instead_of_posting_twice(self):
        key = self.key()
        first = self.move(request_key=key, kind="transfer", amount=1000, other_account="1210")
        again = self.move(request_key=key, kind="transfer", amount=1000, other_account="1210")
        self.assertEqual((again.id, again.replayed, first.replayed), (first.id, True, False))
        self.assertEqual(self.con.execute("SELECT count(*) FROM treasury_movements").fetchone()[0], 1)
        self.assertEqual(dict(treasury_view.balances(self.con))["1210"], 1000)

    def test_reusing_a_key_for_different_content_is_refused(self):
        key = self.key()
        self.move(request_key=key, kind="transfer", amount=1000, other_account="1210")
        with self.assertRaises(TreasuryError):
            self.move(request_key=key, kind="transfer", amount=2000, other_account="1210")

    def test_short_request_key_is_refused(self):
        with self.assertRaises(TreasuryError):
            self.move(request_key="abc", kind="transfer", amount=1, other_account="1210")

    def test_paying_from_an_account_that_cannot_cover_it_needs_confirmation(self):
        with self.assertRaises(NeedsConfirmation) as ctx:
            self.move(kind="transfer", amount=5_000_000, other_account="1210")
        self.assertIn("لا يكفي", ctx.exception.warnings[0])
        self.assertEqual(self.con.execute("SELECT count(*) FROM treasury_movements").fetchone()[0], 0)
        self.move(kind="transfer", amount=5_000_000, other_account="1210", confirmed=True)

    def test_supplier_payment_above_the_balance_is_a_confirmed_advance(self):
        s = self.supplier()
        self.invoice(s, 10000)
        with self.assertRaises(NeedsConfirmation):
            self.move(kind="supplier_payment", amount=15000, supplier_id=s)
        self.move(kind="supplier_payment", amount=15000, supplier_id=s, confirmed=True)
        self.assertEqual(treasury.supplier_owed(self.con, s), -5000)

    def test_withdrawal_beyond_the_partner_balance_is_allowed_with_confirmation(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        with self.assertRaises(NeedsConfirmation) as ctx:
            self.move(kind="partner_withdrawal", amount=1000, partner_id=p.id)
        self.assertIn("مدينًا للشركة", " ".join(ctx.exception.warnings))
        self.move(kind="partner_withdrawal", amount=1000, partner_id=p.id, confirmed=True)
        self.assertEqual(treasury.partner_balance(self.con, p.id), -1000)

    def test_a_look_alike_movement_the_same_day_needs_confirmation(self):
        s = self.supplier()
        self.invoice(s, 100000)
        self.move(kind="supplier_payment", amount=20000, supplier_id=s, movement_date="2026-11-10")
        with self.assertRaises(NeedsConfirmation) as ctx:
            self.move(kind="supplier_payment", amount=20000, supplier_id=s, movement_date="2026-11-10")
        self.assertIn("مماثلة", " ".join(ctx.exception.warnings))
        self.move(kind="supplier_payment", amount=20000, supplier_id=s, movement_date="2026-11-10", confirmed=True)

    # ---------- reversal and immutability ----------

    def test_reversal_mirrors_the_entry_and_restores_balances(self):
        s = self.supplier()
        self.invoice(s, 50000)
        m = self.move(kind="supplier_payment", amount=20000, supplier_id=s)
        r = treasury.reverse_movement(self.con, self.admin, request_key=self.key(), movement_id=m.id, memo="دفعة بالخطأ")
        self.assertEqual(self.lines(r.id), [("1110", 20000, 0), ("2110", 0, 20000)])
        self.assertEqual(treasury.supplier_owed(self.con, s), 50000)
        self.assertEqual(dict(treasury_view.balances(self.con))["1110"], 1_000_000)

    def test_a_movement_is_reversed_once_and_a_reversal_is_not_reversed(self):
        m = self.move(kind="transfer", amount=1000, other_account="1210")
        r = treasury.reverse_movement(self.con, self.admin, request_key=self.key(), movement_id=m.id, memo="خطأ")
        with self.assertRaises(TreasuryError):
            treasury.reverse_movement(self.con, self.admin, request_key=self.key(), movement_id=m.id, memo="مرة ثانية")
        with self.assertRaises(TreasuryError):
            treasury.reverse_movement(self.con, self.admin, request_key=self.key(), movement_id=r.id, memo="عكس العكس")
        with self.assertRaises(TreasuryError):
            treasury.reverse_movement(self.con, self.admin, request_key=self.key(), movement_id=m.id + 99, memo="x")
        with self.assertRaises(TreasuryError):
            treasury.reverse_movement(self.con, self.admin, request_key=self.key(), movement_id=m.id, memo=" ")

    def test_posted_movements_cannot_be_edited_or_deleted(self):
        m = self.move(kind="transfer", amount=1000, other_account="1210")
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("UPDATE treasury_movements SET amount=5 WHERE id=?", (m.id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM treasury_movements WHERE id=?", (m.id,))

    def test_a_supplier_with_payments_cannot_be_deleted(self):
        from morv.infra import suppliers
        s = self.supplier()
        self.assertFalse(suppliers.has_documents(self.con, s))
        self.invoice(s, 1000)
        self.assertTrue(suppliers.has_documents(self.con, s))
        s2 = self.supplier("مورد آخر")
        self.move(kind="supplier_payment", amount=100, supplier_id=s2, confirmed=True)
        self.assertTrue(suppliers.has_documents(self.con, s2))

    def test_every_movement_entry_is_balanced_and_complete(self):
        p = treasury.add_partner(self.con, self.admin, "أحمد")
        self.move(kind="partner_capital", amount=1000, partner_id=p.id)
        self.move(kind="transfer", amount=500, other_account="1220")
        self.move(kind="expense", amount=100, other_account="5250")
        self.assertEqual(ledger.unbalanced_entries(self.con), [])
        self.assertEqual(ledger.incomplete_entries(self.con), [])
        self.assertEqual(ledger.open_entries(self.con), [])


class RetiredAccountsMigrationTest(unittest.TestCase):
    def test_shared_accounts_with_postings_stay_active_after_upgrade(self):
        from morv.infra.db.connection import open_db
        from morv.infra.db.migrator import load_migrations, migrate
        with tempfile.TemporaryDirectory() as tmp:
            con = open_db(Path(tmp) / "old.db", create=True)
            try:
                migrations = load_migrations()
                migrate(con, migrations[:16])
                with transaction(con):
                    e = con.execute("INSERT INTO journal_entries (entry_date, source_type, source_id, line_count, total, status) "
                                    "VALUES ('2026-11-01','legacy',1,2,100,'open')").lastrowid
                    con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,1,'1110',100,0)", (e,))
                    con.execute("INSERT INTO journal_lines (entry_id,line_no,account_code,debit,credit) VALUES (?,2,'3100',0,100)", (e,))
                    con.execute("UPDATE journal_entries SET status='posted' WHERE id=?", (e,))
                self.assertEqual(migrate(con, migrations[:17]), [17])
                active = dict(con.execute("SELECT code, active FROM accounts WHERE code IN ('3100','3300')"))
                self.assertEqual(active, {"3100": 1, "3300": 0})
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
