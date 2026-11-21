"""Shared date field: ISO text in and out, calendar picker, optional blank, invalid input kept for the engine."""
import unittest
from datetime import date, timedelta

from tests.qt_app import get_app, require_qt


@require_qt()
class DateEditTest(unittest.TestCase):
    def setUp(self):
        self.app = get_app()

    def make(self, **kw):
        from morv.ui.widgets import DateEdit, dispose
        w = DateEdit(**kw)
        self.addCleanup(dispose, w, self.app)
        return w

    def test_defaults_to_today_as_iso_text_with_a_calendar(self):
        w = self.make()
        self.assertEqual(w.text(), date.today().isoformat())
        self.assertTrue(w.calendarPopup())

    def test_value_reads_left_to_right_in_an_rtl_app_while_the_calendar_stays_rtl(self):
        from PySide6.QtCore import Qt
        w = self.make()
        self.assertEqual(w.layoutDirection(), Qt.LayoutDirection.LeftToRight)
        self.assertEqual(w.calendarWidget().layoutDirection(), Qt.LayoutDirection.RightToLeft)

    def test_set_and_read_back_iso(self):
        w = self.make()
        seen = []
        w.textChanged.connect(seen.append)
        w.setText("2026-11-05")
        self.assertEqual(w.text(), "2026-11-05")
        self.assertEqual(seen[-1], "2026-11-05")

    def test_posting_fields_offer_no_future_date(self):
        w = self.make()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        w.setText(tomorrow)
        self.assertEqual(w.text(), tomorrow)              # kept as typed, so the engine refuses it
        self.assertEqual(w.maximumDate().toString("yyyy-MM-dd"), date.today().isoformat())

    def test_future_allowed_where_asked(self):
        w = self.make(allow_future=True)
        later = (date.today() + timedelta(days=40)).isoformat()
        w.setText(later)
        self.assertEqual(w.date().toString("yyyy-MM-dd"), later)

    def test_invalid_text_is_kept_until_a_valid_date_replaces_it(self):
        w = self.make()
        w.setText("2026-13-45")
        self.assertEqual(w.text(), "2026-13-45")
        w.setText("2026-11-01")
        self.assertEqual(w.text(), "2026-11-01")

    def test_optional_field_starts_blank_and_clears(self):
        w = self.make(optional=True)
        self.assertEqual(w.text(), "")
        w.setText("2026-10-31")
        self.assertEqual(w.text(), "2026-10-31")
        w.clear()
        self.assertEqual(w.text(), "")

    def test_no_text_date_fields_left_in_the_ui(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "morv" / "ui"
        left = [p.name for p in root.glob("*.py") if 'ltr_edit("YYYY-MM-DD")' in p.read_text(encoding="utf-8")]
        self.assertEqual(left, [])
