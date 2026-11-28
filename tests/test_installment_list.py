"""Tests for installment_view.list_view: per-contract urgency category, KPIs, search and filter."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from morv.core.sales_rules import Payment
from morv.infra import installment_view, installments, sales, users
from morv.infra.db.bootstrap import open_main_db
from morv.infra.sales import SaleLineIn

NID = "29001011201234"


class ListViewTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "il.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.n = 0
        self.contract = self._make_contract("عميل أول", "01000000011")

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _make_contract(self, name, mobile):
        self.n += 1
        cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) VALUES (?, ?, ?, 0) RETURNING id",
            (name, name, mobile)).fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key=f"sale-key-{self.n:04d}", customer_id=cust,
                               lines=[SaleLineIn(self.service, 1, 1200000)],
                               payments=[Payment("installment", 1200000)], sale_date="2026-11-10")
        return installments.post_contract(
            self.con, self.admin, request_key=f"ic-key-{self.n:04d}", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-12-13", national_id=NID, down_method="cash", contract_date="2026-11-13")

    def _row(self, no=1):
        return self.con.execute("SELECT id, no, amount FROM installment_rows WHERE contract_id=? AND no=?",
                                (self.contract.id, no)).fetchone()

    def test_before_first_due_date_is_active_with_no_kpi_alarms(self):
        view = installment_view.list_view(self.con, today="2026-11-13")
        self.assertEqual(len(view.rows), 1)
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("نشط", ""))
        self.assertEqual((view.kpis.late_count, view.kpis.today_count, view.kpis.week_count), (0, 0, 0))
        self.assertEqual(view.kpis.active_count, 1)

    def test_row_due_today_counts_as_today_category(self):
        view = installment_view.list_view(self.con, today="2026-12-13")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("مستحق اليوم", "warn"))
        self.assertEqual(view.kpis.today_count, 1)
        self.assertEqual(view.kpis.today_amount, self._row()[2])

    def test_past_due_date_counts_as_late(self):
        view = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("متأخر", "danger"))
        self.assertEqual(view.kpis.late_count, 1)
        self.assertEqual(view.kpis.late_amount, self._row()[2])

    def test_due_within_week_is_a_separate_kpi_without_forcing_status(self):
        # Row 1 is due 2026-12-13; three days before that is within the 7-day window but not "today"/late.
        view = installment_view.list_view(self.con, today="2026-12-10")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("نشط", ""))
        self.assertEqual(view.kpis.week_count, 1)
        self.assertEqual(view.kpis.week_amount, self._row()[2])

    def test_fully_paid_contract_is_done_and_excluded_from_active(self):
        total = self.con.execute("SELECT sum(amount) FROM installment_rows WHERE contract_id=?",
                                 (self.contract.id,)).fetchone()[0]
        installments.collect_payment(self.con, self.admin, request_key="ip-key-1", contract_id=self.contract.id,
                                     amount=total, method="cash", payment_date="2026-11-14")
        view = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual((view.rows[0].status, view.rows[0].tone), ("مكتمل", "ok"))
        self.assertEqual(view.kpis.active_count, 0)
        self.assertEqual(view.rows[0].can_collect, False)
        self.assertIn("10 من 10", view.rows[0].progress_text)

    def test_paying_one_row_advances_progress_text(self):
        row = self._row()
        installments.collect_payment(self.con, self.admin, request_key="ip-key-1", contract_id=self.contract.id,
                                     amount=row[2], method="cash", payment_date="2026-11-14")
        view = installment_view.list_view(self.con, today="2026-11-20")
        self.assertIn("1 من 10", view.rows[0].progress_text)

    def test_filter_key_restricts_rows_but_kpis_cover_every_matching_contract(self):
        other = self._make_contract("عميل ثان", "01000000022")
        view_all_today_late = installment_view.list_view(self.con, today="2026-12-20")
        self.assertEqual(view_all_today_late.kpis.late_count, 2)
        late_only = installment_view.list_view(self.con, filter_key="late", today="2026-12-20")
        self.assertEqual({r.contract_id for r in late_only.rows}, {self.contract.id, other.id})
        done_only = installment_view.list_view(self.con, filter_key="done", today="2026-12-20")
        self.assertEqual(done_only.rows, ())
        self.assertEqual(done_only.kpis.late_count, 2)   # kpis are not narrowed by filter_key

    def test_search_query_narrows_both_rows_and_kpis(self):
        self._make_contract("عميل ثان", "01000000022")
        view = installment_view.list_view(self.con, query="عميل أول", today="2026-12-20")
        self.assertEqual(len(view.rows), 1)
        self.assertEqual(view.rows[0].customer_name, "عميل أول")
        self.assertEqual(view.kpis.late_count, 1)   # the other contract's lateness isn't counted once searched out


def _reference_list_view(con, query="", filter_key="all", today=None):
    """The pre-paging algorithm, kept here as the oracle the SQL version must reproduce exactly."""
    from datetime import date, timedelta
    today = today or date.today().isoformat()
    week_end = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    like = f"%{(query or '').strip()}%"
    contracts = con.execute(
        "SELECT c.id, c.number, cu.name, coalesce(cu.mobile, ''), c.instalments_total "
        "FROM installment_contracts c JOIN customers cu ON cu.id = c.customer_id "
        "WHERE (? = '' OR cu.name LIKE ? OR cu.mobile LIKE ? OR c.number LIKE ?) ORDER BY c.id DESC",
        (query, like, like, like)).fetchall()
    by_contract = {}
    for cid, no, due_date, original, paid, canceled in con.execute(
            "SELECT r.contract_id, r.no, r.due_date, r.amount, coalesce((SELECT sum(l.amount) "
            "FROM installment_payment_lines l WHERE l.row_id = r.id), 0), "
            "coalesce((SELECT sum(a.amount) FROM installment_return_allocations a WHERE a.row_id=r.id),0) "
            "FROM installment_rows r ORDER BY r.contract_id, r.no"):
        by_contract.setdefault(cid, []).append((no, due_date, original - canceled, paid))
    returned = dict(con.execute(
        "SELECT contract_id, sum(principal) FROM installment_return_adjustments GROUP BY contract_id"))
    sale_prices = dict(con.execute("SELECT id, cash_price FROM installment_contracts"))
    late_count = late_amount = today_count = today_amount = 0
    week_count = week_amount = active_count = active_amount = 0
    result = []
    for cid, number, name, mobile, _total in contracts:
        rows = by_contract.get(cid, [])
        remaining_total = sum(a - p for _, _, a, p in rows)
        overdue_amt = sum(a - p for _, d, a, p in rows if a > p and d < today)
        due_today_amt = sum(a - p for _, d, a, p in rows if a > p and d == today)
        due_week_amt = sum(a - p for _, d, a, p in rows if a > p and today < d <= week_end)
        next_open = next(((d, a - p) for _, d, a, p in rows if a > p), None)
        if overdue_amt > 0:
            status, tone, cat = "متأخر", "danger", "late"
        elif due_today_amt > 0:
            status, tone, cat = "مستحق اليوم", "warn", "today"
        elif remaining_total <= 0:
            status, tone, cat = "مكتمل", "ok", "done"
        else:
            status, tone, cat = "نشط", "", "active"
        if returned.get(cid, 0) >= sale_prices[cid]:
            status, tone, cat = "مرتجع بالكامل", "ok", "done"
        paid_rows = sum(1 for _, _, a, p in rows if a > 0 and p >= a)
        if overdue_amt > 0:
            late_count += 1
            late_amount += overdue_amt
        if due_today_amt > 0:
            today_count += 1
            today_amount += due_today_amt
        if due_week_amt > 0:
            week_count += 1
            week_amount += due_week_amt
        if remaining_total > 0:
            active_count += 1
            active_amount += remaining_total
        if filter_key in ("all", cat) or (filter_key == "week" and due_week_amt > 0):
            result.append((cid, number, name, mobile, next_open[0] if next_open else "",
                           overdue_amt + due_today_amt, status, tone,
                           f"{paid_rows} من {len(rows)} قسطًا", remaining_total, remaining_total > 0))
    kpis = (late_count, late_amount, today_count, today_amount,
            week_count, week_amount, active_count, active_amount)
    return kpis, result


def _as_tuples(view):
    return [(r.contract_id, r.number, r.customer_name, r.customer_mobile, r.next_due, r.amount_now,
             r.status, r.tone, r.progress_text, r.remaining_total, r.can_collect) for r in view.rows]


def _kpi_tuple(k):
    return (k.late_count, k.late_amount, k.today_count, k.today_amount,
            k.week_count, k.week_amount, k.active_count, k.active_amount)


class ListViewParityTest(unittest.TestCase):
    """The paged SQL view must return exactly what the old per-contract Python pass returned."""

    DAYS = ("2026-11-13", "2026-12-10", "2026-12-13", "2026-12-20", "2027-06-20")
    FILTERS = ("all", "late", "today", "week", "done", "active")

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "parity.db")
        self.admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.service = self.con.execute(
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('service','SV-1','خدمة','خدمه',1200000,1200000,1200000) RETURNING id").fetchone()[0]
        self.n = 0

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _contract(self, name, mobile, qty=1):
        self.n += 1
        cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) VALUES (?, ?, ?, 0) RETURNING id",
            (name, name, mobile)).fetchone()[0]
        sale = sales.post_sale(self.con, self.admin, request_key=f"pk-sale-{self.n:04d}", customer_id=cust,
                               lines=[SaleLineIn(self.service, qty, 1200000)],
                               payments=[Payment("installment", 1200000 * qty)], sale_date="2026-11-10")
        contract = installments.post_contract(
            self.con, self.admin, request_key=f"pk-ic-{self.n:04d}", sale_id=sale.id, down=200000, rate_bp=1500,
            months=10, first_due="2026-12-13", national_id=NID, down_method="cash", contract_date="2026-11-13")
        return sale, contract

    def _mixed_book(self):
        """Untouched, part paid, fully paid and fully returned contracts; a part return is refused upstream."""
        from morv.infra import sale_returns
        from morv.infra.sale_returns import ReturnLineIn
        _sale, plain = self._contract("عميل ساكن", "01000000101")
        _sale2, part_paid = self._contract("عميل جزئي", "01000000102")
        one = self.con.execute("SELECT amount FROM installment_rows WHERE contract_id=? AND no=1",
                               (part_paid.id,)).fetchone()[0]
        installments.collect_payment(self.con, self.admin, request_key="pk-pay-1", contract_id=part_paid.id,
                                     amount=one + 500, method="cash", payment_date="2026-11-14")
        _sale3, settled = self._contract("عميل مكتمل", "01000000103")
        whole = self.con.execute("SELECT sum(amount) FROM installment_rows WHERE contract_id=?",
                                 (settled.id,)).fetchone()[0]
        installments.collect_payment(self.con, self.admin, request_key="pk-pay-2", contract_id=settled.id,
                                     amount=whole, method="cash", payment_date="2026-11-14")
        sale4, returned = self._contract("عميل مرتجع", "01000000104")
        line_id = self.con.execute("SELECT id FROM sale_lines WHERE sale_id=?", (sale4.id,)).fetchone()[0]
        sale_returns.post_return(self.con, self.admin, request_key="pk-ret-1", sale_id=sale4.id,
                                 lines=[ReturnLineIn(line_id, 1, "sound")], reason="عيب",
                                 return_date="2026-11-15", refund_method=None)
        return plain, part_paid, settled, returned

    def test_every_filter_and_day_matches_the_reference(self):
        self._mixed_book()
        for day in self.DAYS:
            for fk in self.FILTERS:
                with self.subTest(day=day, filter=fk):
                    ref_kpis, ref_rows = _reference_list_view(self.con, "", fk, day)
                    got = installment_view.list_view(self.con, "", fk, day)
                    self.assertEqual(_kpi_tuple(got.kpis), ref_kpis)
                    self.assertEqual(_as_tuples(got), ref_rows)

    def test_search_matches_the_reference_and_keeps_kpis_on_the_query(self):
        self._mixed_book()
        for query in ("عميل مرتجع", "01000000102", "عميل", "لا يوجد"):
            with self.subTest(query=query):
                ref_kpis, ref_rows = _reference_list_view(self.con, query, "all", "2026-12-20")
                got = installment_view.list_view(self.con, query, "all", "2026-12-20")
                self.assertEqual(_kpi_tuple(got.kpis), ref_kpis)
                self.assertEqual(_as_tuples(got), ref_rows)

    def test_a_page_is_a_window_on_the_unpaged_result(self):
        self._mixed_book()
        full = _as_tuples(installment_view.list_view(self.con, "", "all", "2026-12-20"))
        self.assertGreaterEqual(len(full), 4)
        for size in (1, 2, 3):
            with self.subTest(size=size):
                seen, offset = [], 0
                while True:
                    page = installment_view.list_view(self.con, "", "all", "2026-12-20",
                                                      limit=size, offset=offset)
                    if not page.rows:
                        break
                    self.assertLessEqual(len(page.rows), size)
                    seen.extend(_as_tuples(page))
                    offset += size
                self.assertEqual(seen, full, "paging must not drop, duplicate or reorder a contract")


class ListViewPagingTest(unittest.TestCase):
    """Paging over more contracts than one page holds must lose none and repeat none."""

    COUNT = 310                              # Above PAGE so the second page is exercised
    PAGE = 300                               # Mirrors ListPage.PAGE, which cannot be imported without Qt
    MONTHS = 10
    TODAY = "2027-06-20"                     # Months 1-6 are already past, so every contract is late

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.dir)
        self.con = open_main_db(self.dir / "paging.db")
        # Contracts are inserted straight in: this covers the read path only, and posting 310 real
        # sales would add minutes without touching what list_view does.
        self.con.execute("PRAGMA foreign_keys=OFF")
        customers, contracts, rows = [], [], []
        for i in range(1, self.COUNT + 1):
            customers.append((i, f"عميل {i}", f"عميل {i}", f"01{i:09d}"))
            cash, down, markup = 120000, 20000, 10000
            total = cash - down + markup
            contracts.append((i, f"PG-{i:05d}", f"pgkey-{i:06d}", "h", i, i, "2026-11-01", cash, down,
                              cash - down, 1500, markup, self.MONTHS, total, down + total,
                              "2026-12-15", "cash", NID))
            per = total // self.MONTHS
            for n in range(1, self.MONTHS + 1):
                month = ((n - 1) % 12) + 1
                rows.append((i, n, f"2027-{month:02d}-15", per - 500, 500, per, 0))
        self.con.executemany("INSERT INTO customers (id, name, name_norm, mobile) VALUES (?,?,?,?)", customers)
        self.con.executemany(
            "INSERT INTO installment_contracts (id, number, request_key, payload_hash, sale_id, customer_id,"
            " contract_date, cash_price, down, financed, rate_bp, markup, months, instalments_total,"
            " contract_total, first_due, down_method, national_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            contracts)
        self.con.executemany(
            "INSERT INTO installment_rows (contract_id, no, due_date, principal, markup, amount, balance_after)"
            " VALUES (?,?,?,?,?,?,?)", rows)
        self.con.commit()

    def tearDown(self):
        self.con.close()
        os.environ.pop("MORV_DATA_DIR", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pages_cover_every_contract_exactly_once(self):
        PAGE = self.PAGE
        seen, offset = [], 0
        while True:
            page = installment_view.list_view(self.con, "", "all", self.TODAY, limit=PAGE, offset=offset)
            if not page.rows:
                break
            self.assertLessEqual(len(page.rows), PAGE)
            seen.extend(r.contract_id for r in page.rows)
            offset += PAGE
        self.assertEqual(len(seen), self.COUNT)
        self.assertEqual(len(set(seen)), self.COUNT, "a contract appeared on two pages")
        self.assertEqual(seen, sorted(range(1, self.COUNT + 1), reverse=True), "newest first must hold across pages")

    def test_kpis_are_identical_on_every_page_and_cover_all_contracts(self):
        PAGE = self.PAGE
        whole = installment_view.list_view(self.con, "", "all", self.TODAY)
        first = installment_view.list_view(self.con, "", "all", self.TODAY, limit=PAGE, offset=0)
        second = installment_view.list_view(self.con, "", "all", self.TODAY, limit=PAGE, offset=PAGE)
        self.assertEqual(_kpi_tuple(first.kpis), _kpi_tuple(whole.kpis))
        self.assertEqual(_kpi_tuple(second.kpis), _kpi_tuple(whole.kpis))
        self.assertEqual(whole.kpis.active_count, self.COUNT)
        self.assertEqual(len(second.rows), self.COUNT - PAGE)

    def test_a_category_filter_pages_without_losing_contracts(self):
        late = installment_view.list_view(self.con, "", "late", self.TODAY)
        self.assertEqual(len(late.rows), self.COUNT, "every seeded contract has a past due row")
        seen, offset = [], 0
        while True:
            page = installment_view.list_view(self.con, "", "late", self.TODAY, limit=100, offset=offset)
            if not page.rows:
                break
            seen.extend(r.contract_id for r in page.rows)
            offset += 100
        self.assertEqual(seen, [r.contract_id for r in late.rows])
