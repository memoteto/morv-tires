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

    def test_shown_text_starts_with_the_year_in_both_directions(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDateEdit
        self.addCleanup(self.app.setLayoutDirection, self.app.layoutDirection())
        for direction in (Qt.LayoutDirection.RightToLeft, Qt.LayoutDirection.LeftToRight):
            with self.subTest(direction=direction):
                self.app.setLayoutDirection(direction)
                w = self.make()
                w.setText("2026-11-05")
                self.assertEqual(QDateEdit.text(w), "2026-11-05")      # what the box shows
                self.assertEqual(w.text(), "2026-11-05")               # the ISO value

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

    def test_blank_optional_field_opens_its_calendar_on_this_month(self):
        w = self.make(optional=True)
        cal = w.calendarWidget()
        popup = cal.window()                  # the field opens the calendar inside its popup window
        popup.show()
        self.app.processEvents()
        self.addCleanup(popup.hide)
        today = date.today()
        self.assertEqual((cal.yearShown(), cal.monthShown()), (today.year, today.month))
        self.assertEqual(w.text(), "")                       # opening the calendar picks no date

    def test_no_text_date_fields_left_in_the_ui(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "morv" / "ui"
        left = [p.name for p in root.glob("*.py") if 'ltr_edit("YYYY-MM-DD")' in p.read_text(encoding="utf-8")]
        self.assertEqual(left, [])
