"""Reports screen: income statement rows by period, permission gate."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class ReportsPageTest(unittest.TestCase):
    def setUp(self):
        from datetime import date

        from morv.infra import ledger, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.db.connection import transaction
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        cashier = users.create_user(self.con, admin, "karim", "كريم", "Karim12345", "cashier")
        self.session, self.cashier_session = Session(self.con, admin), Session(self.con, cashier)
        self.app = get_app()
        with transaction(self.con):
            ledger.post_entry(self.con, admin, date.today().isoformat(), "sale", 1, "بيع",
                              [("1110", 100000, 0), ("4110", 0, 100000)])

    def page(self, session=None):
        from morv.ui.reports_page import ReportsPage
        from morv.ui.widgets import dispose
        page = ReportsPage(session or self.session)
        self.addCleanup(dispose, page, self.app)
        return page

    def test_manager_sees_the_statement_ending_with_the_net_result(self):
        page = self.page()
        self.assertEqual(page.model.rows[-1][1], "grand")
        self.assertEqual(page.model.rows[-1][2][2], 100000)

    def test_partner_report_lists_partners_and_account_report_lists_accounts(self):
        page = self.page()
        page.report_combo.setCurrentIndex(page.report_combo.findData("ledger"))
        self.assertGreater(page.account_combo.count(), 10)
        page.report_combo.setCurrentIndex(page.report_combo.findData("partner"))
        self.assertEqual(page.account_combo.count(), 0)          # no partners in this fixture
        self.assertIn("شريكًا", page.count_label.text())
        page.report_combo.setCurrentIndex(page.report_combo.findData("ledger"))
        self.assertGreater(page.account_combo.count(), 10)

    def test_report_picker_switches_tables_and_exports(self):
        import os
        from morv.infra.reports import KEYS as reports_keys
        self.con.execute(          # the item card needs one stocked item to open on
            "INSERT INTO items (kind, code, name, name_norm, price_retail, price_wholesale, price_super) "
            "VALUES ('tire','TR-1','إطار','اطار',100,100,100)")
        page = self.page()
        # the picker is grouped now: headers carry no data, and the reports keep the catalogue's order
        from morv.infra.reports import GROUPS
        combo = page.report_combo
        self.assertEqual([combo.itemData(i) for i in range(combo.count()) if combo.itemData(i)],
                         list(reports_keys))
        self.assertEqual([combo.itemText(i).strip() for i in range(combo.count()) if not combo.itemData(i)],
                         [group for group, _keys in GROUPS])
        for key in ("trial", "ledger", "sales_profit", "aging", "suspended", "daily_sales", "top_items",
                    "slow_moving", "journal", "suppliers", "income", "credit_aging", "stock_valuation",
                    "expenses", "collections", "purchases", "returns", "item_card", "supplier_aging", "vat"):
            page.report_combo.setCurrentIndex(page.report_combo.findData(key))
            self.assertEqual(page.table.key, key)
            self.assertEqual(page.model.columnCount(), len(page.table.headers))
        self.assertTrue(page.account_combo.isHidden())
        page.report_combo.setCurrentIndex(page.report_combo.findData("ledger"))
        self.assertFalse(page.account_combo.isHidden())
        for ext, fn in (("csv", page.export_csv_to), ("pdf", page.export_pdf_to)):
            path = os.path.join(self.tmp, f"r.{ext}")
            fn(path)
            self.assertGreater(os.path.getsize(path), 100)
        with open(os.path.join(self.tmp, "r.pdf"), "rb") as f:
            self.assertEqual(f.read(4), b"%PDF")

    def test_report_header_tracks_the_loaded_report(self):
        page = self.page()
        for key in ("vat", "income", "trial"):
            with self.subTest(key=key):
                page.report_combo.setCurrentIndex(page.report_combo.findData(key))
                self.assertEqual(page.table.key, key)
                self.assertEqual(page.count_label.text(), page.total())
                self.assertIn(page.table.title, page.count_label.text())
                self.assertIn(page.table.subtitle, page.count_label.text())

    def test_switching_reports_closes_old_preview_even_with_matching_row_ids(self):
        page = self.page()
        page.resize(900, 550)
        page.show()
        self.app.processEvents()
        page.show_preview_id(page.model.rows[0][0])
        self.assertFalse(page.preview.isHidden())
        page.report_combo.setCurrentIndex(page.report_combo.findData("vat"))
        self.assertEqual(page.table.key, "vat")
        self.assertIsNone(page.current_id)
        self.assertTrue(page.preview.isHidden())
        self.assertFalse(page.list_panel.isHidden())
        page.show_preview_id(page.model.rows[1][0])
        self.assertEqual(page.preview_title.text(), "ضريبة المبيعات")
        page.report_combo.setCurrentIndex(page.report_combo.findData("income"))
        self.assertTrue(page.preview.isHidden())
        self.assertIsNone(page.current_id)
        self.assertFalse(page.list_panel.isHidden())

    def test_vat_report_uses_the_existing_picker_and_export(self):
        page = self.page()
        page.report_combo.setCurrentIndex(page.report_combo.findData("vat"))
        self.assertEqual(page.table.key, "vat")
        self.assertTrue(page.account_combo.isHidden())
        self.assertTrue(page.compare_box.isHidden())
        for ext, fn in (("csv", page.export_csv_to), ("pdf", page.export_pdf_to)):
            path = self.tmp / f"vat.{ext}"
            fn(str(path))
            self.assertGreater(path.stat().st_size, 100)
        self.assertIn("صافي ضريبة الفترة", (self.tmp / "vat.csv").read_text(encoding="utf-8-sig"))
        self.assertEqual((self.tmp / "vat.pdf").read_bytes()[:4], b"%PDF")

    def test_pdf_header_is_a_visible_paragraph_with_ltr_dates(self):
        from morv.ui.report_export import table_html
        page = self.page()
        html = table_html(page.table)
        self.assertNotIn("<h2>", html)
        self.assertIn("font-size:16pt", html)
        self.assertIn("<span dir='ltr'>", html.split("</p>")[1])      # the subtitle line

    def test_report_row_styles_follow_theme_and_leave_detail_rows_plain(self):
        from PySide6.QtCore import Qt
        from morv.ui import theme, tokens
        page = self.page()
        page.report_combo.setCurrentIndex(page.report_combo.findData("vat"))
        previous = self.app.palette()
        try:
            for colors in (tokens.LIGHT, tokens.DARK):
                self.app.setPalette(theme.palette(colors))
                for row, (_, kind, _) in enumerate(page.model.rows):
                    index = page.model.index(row, 0)
                    font = page.model.data(index, Qt.ItemDataRole.FontRole)
                    brush = page.model.data(index, Qt.ItemDataRole.BackgroundRole)
                    if kind == "line":
                        self.assertIsNone(font)
                        self.assertIsNone(brush)
                    else:
                        self.assertTrue(font.bold())
                        self.assertEqual(brush.color().name(), colors[tokens.REPORT_ROW_BACKGROUND[kind]].lower())
        finally:
            self.app.setPalette(previous)

    def test_pdf_columns_and_signed_values_follow_reading_order(self):
        from PySide6.QtGui import QTextDocument
        from morv.infra.reports import CODE, DATE, MONEY, TEXT, Table
        from morv.ui.report_export import table_html
        table = Table("test", "تقرير", "من 2026-11-01 إلى 2026-11-28",
                      ("البيان", "المستند", "التاريخ", "المبلغ"), (TEXT, CODE, DATE, MONEY),
                      (("grand", ("اختبار", "INV-123", "2026-11-01", -20000)),))
        doc = QTextDocument()
        doc.setHtml(table_html(table, shop_name="محل اختبار", printed_on="2026-11-28"))
        doc.setTextWidth(800)
        doc.documentLayout().documentSize()
        frame = doc.rootFrame().childFrames()[0]
        self.assertEqual(frame.columns(), len(table.headers))
        blocks = [frame.cellAt(0, col).firstCursorPosition().block() for col in range(4)]
        self.assertEqual([block.text() for block in blocks], list(reversed(table.headers)))
        xs = [doc.documentLayout().blockBoundingRect(block).x() for block in blocks]
        self.assertEqual(xs, sorted(set(xs)))
        cells = [frame.cellAt(1, col).firstCursorPosition().block() for col in range(4)]
        for block, expected in zip(cells, ("-200.00", "2026-11-01", "INV-123", "اختبار"), strict=True):
            self.assertEqual(block.text().strip("\u202a\u202c"), expected)
        amount = cells[0]
        line = amount.layout().lineAt(0)
        sign = amount.text().index("-")
        self.assertLess(line.cursorToX(sign)[0], line.cursorToX(sign + 1)[0])
        self.assertIn("محل اختبار", doc.toPlainText())
        self.assertIn("تاريخ الطباعة", doc.toPlainText())

    def test_report_pdf_receives_the_configured_shop_name(self):
        from unittest.mock import patch
        from morv.infra import settings
        settings.put(self.con, "shop.name", "محل اختبار")
        page = self.page()
        with patch("morv.ui.report_export.export_pdf") as export:
            page.export_pdf_to("unused.pdf")
        export.assert_called_once_with(page.table, "unused.pdf", shop_name="محل اختبار")

    def test_slow_moving_offers_day_thresholds_and_journal_exports_everything(self):
        import os
        page = self.page()
        page.report_combo.setCurrentIndex(page.report_combo.findData("slow_moving"))
        self.assertEqual([page.account_combo.itemData(i) for i in range(page.account_combo.count())],
                         ["30", "60", "90", "180", "365"])
        self.assertEqual(page.account_combo.currentData(), "90")
        page.report_combo.setCurrentIndex(page.report_combo.findData("journal"))
        path = os.path.join(self.tmp, "journal.csv")
        page.export_csv_to(path)
        self.assertGreater(os.path.getsize(path), 50)

    def test_compare_checkbox_adds_the_previous_period_columns_for_income_only(self):
        page = self.page()
        self.assertFalse(page.compare_box.isHidden())
        page.compare_box.setChecked(True)
        self.assertEqual(page.model.columnCount(), 6)
        page.report_combo.setCurrentIndex(page.report_combo.findData("trial"))
        self.assertTrue(page.compare_box.isHidden())
        self.assertEqual(page.model.columnCount(), 4)

    def test_custom_period_shows_dates_and_reports_bad_input_inline(self):
        page = self.page()
        page.set_filter("custom")
        self.assertFalse(page.from_edit.isHidden())
        page.from_edit.setText("2026-02-01")
        page.to_edit.setText("2026-01-01")
        page.reload()
        self.assertIn("بعد", page.count_label.text())
        page.to_edit.setText("2026-12-31")
        page.reload()
        self.assertIsNotNone(page.table)

    def test_period_filter_is_a_dropdown_that_switches_the_statement(self):
        page = self.page()
        self.assertIsNotNone(page.filter_combo)
        self.assertEqual(page.filter_buttons, {})
        page.filter_combo.setCurrentIndex(page.filter_combo.findData("last_year"))
        self.assertEqual(page.filter_key, "last_year")
        page.set_filter("month")
        self.assertEqual(page.filter_combo.currentData(), "month")

    def test_cashier_sees_nothing(self):
        self.assertEqual(self.page(self.cashier_session).model.rows, [])

    def test_lock_dialog_locks_and_reports_page_shows_it(self):
        from morv.infra import ledger
        from morv.ui.period_dialogs import PeriodLockDialog
        from morv.ui.widgets import dispose
        from morv.ui.treasury_page import TreasuryPage
        treasury = TreasuryPage(self.session)
        self.addCleanup(dispose, treasury, self.app)
        self.assertIsNotNone(treasury.lock_btn, "the period lock lives in the treasury now")
        self.assertFalse(self.page().can_edit(), "a report line must not open a financial dialog")
        dlg = PeriodLockDialog(self.session)
        self.addCleanup(dispose, dlg, self.app)
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(ledger.locked_through(self.con), dlg.through.text())
        self.assertIn("مقفلة حتى", self.page().total())


@require_qt()
class ProfitUiTest(unittest.TestCase):
    def setUp(self):
        from datetime import date, timedelta

        from morv.infra import cash_closing, ledger, period_lock, treasury, users
        from morv.infra.db.bootstrap import open_main_db
        from morv.infra.db.connection import transaction
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        cashier = users.create_user(self.con, admin, "karim", "كريم", "Karim12345", "cashier")
        self.session, self.cashier_session = Session(self.con, admin), Session(self.con, cashier)
        self.app = get_app()
        self.a = treasury.add_partner(self.con, admin, "أحمد").id
        self.b = treasury.add_partner(self.con, admin, "بهاء").id
        old = (date.today() - timedelta(days=8)).isoformat()
        through = (date.today() - timedelta(days=6)).isoformat()
        with transaction(self.con):
            ledger.post_entry(self.con, admin, old, "sale", 1, "بيع", [("1110", 100000, 0), ("4110", 0, 100000)])
        cash_closing.close_day(self.con, admin, request_key="ui-close-0001", closing_date=old, counted=100000,
                               expected_book=100000)
        period_lock.lock_period(self.con, admin, request_key="ui-lock-0001", through_date=through)
        self.old = old

    def test_share_dialog_distribution_dialog_and_permissions(self):
        from morv.infra import profit, treasury
        from morv.ui.partners_tab import PartnersTab
        from morv.ui.profit_dialogs import DistributionDialog, ShareSetDialog
        from morv.ui.widgets import dispose
        sd = ShareSetDialog(self.session)
        self.addCleanup(dispose, sd, self.app)
        sd.edits[self.a].setText("60")
        sd.edits[self.b].setText("40")
        sd.date.setText(self.old)
        sd.memo.setText("محضر")
        sd.save()
        self.assertIsNotNone(sd.saved)
        dd = DistributionDialog(self.session)
        self.addCleanup(dispose, dd, self.app)
        self.assertIsNotNone(dd._preview)
        dd.memo.setText("توزيع")
        from unittest import mock
        with mock.patch("morv.ui.profit_dialogs.ask", return_value=True):
            dd.save()
        self.assertIsNotNone(dd.saved)
        self.assertEqual(treasury.partner_balance(self.con, self.a), 60000)
        self.assertEqual(profit.latest_distribution(self.con)[3], 100000)
        from morv.ui.treasury_page import TreasuryPage
        page = TreasuryPage(self.session)
        self.addCleanup(dispose, page, self.app)
        self.assertIsNotNone(page.distribute_btn)
        other = TreasuryPage(self.cashier_session)
        self.addCleanup(dispose, other, self.app)
        self.assertIsNone(other.distribute_btn)
        tab = PartnersTab(self.session)
        self.addCleanup(dispose, tab, self.app)
        tab.reserve_edit.setText("5")
        tab.save_reserve()
        self.assertEqual(profit.reserve_bp(self.con), 500)
