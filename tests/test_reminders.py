"""Reminder tests: the approved template, late days and the wa.me link."""
import unittest

from morv.core import reminders
from morv.core.reminders import ReminderError


class RenderTest(unittest.TestCase):
    def test_every_variable_is_filled(self):
        text = reminders.render(reminders.LATE_TEMPLATE, {
            reminders.CUSTOMER: "أحمد", reminders.CONTRACT: "IC-2026-000012", reminders.AMOUNT: "1,900.00",
            reminders.DUE_DATE: "2026-10-05", reminders.LATE_DAYS: "39", reminders.SHOP: "محل وهمي"})
        for name in reminders.VARIABLES:
            self.assertNotIn(name, text)
        self.assertIn("أحمد", text)
        self.assertIn("IC-2026-000012", text)
        self.assertIn("محل وهمي", text)

    def test_a_missing_value_leaves_no_placeholder_behind(self):
        text = reminders.render(reminders.LATE_TEMPLATE, {})
        for name in reminders.VARIABLES:
            self.assertNotIn(name, text)


class LateDaysTest(unittest.TestCase):
    def test_counts_days_past_the_due_date(self):
        self.assertEqual(reminders.late_days("2026-10-05", "2026-11-13"), 39)

    def test_an_instalment_not_yet_due_is_late_by_nothing(self):
        self.assertEqual(reminders.late_days("2026-12-01", "2026-11-13"), 0)


class LinkTest(unittest.TestCase):
    def test_egyptian_mobile_becomes_international(self):
        self.assertTrue(reminders.wa_link("01234567890", "x").startswith("https://wa.me/201234567890?text="))

    def test_an_international_number_is_kept(self):
        self.assertTrue(reminders.wa_link("00201234567890", "x").startswith("https://wa.me/201234567890?text="))

    def test_arabic_text_is_escaped(self):
        link = reminders.wa_link("01234567890", "مرحبا")
        self.assertNotIn("مرحبا", link)
        self.assertIn("%D9%85", link)

    def test_no_mobile_is_refused(self):
        with self.assertRaises(ReminderError):
            reminders.wa_link("", "x")


if __name__ == "__main__":
    unittest.main()
