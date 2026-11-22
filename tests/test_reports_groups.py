"""Every report belongs to exactly one group, so a new one joins a group instead of landing at the end."""
import unittest

from morv.infra import reports


class ReportGroupsTest(unittest.TestCase):
    def test_every_report_has_a_group_and_only_one(self):
        grouped = [key for _group, keys in reports.GROUPS for key in keys]
        self.assertEqual(sorted(grouped), sorted(reports.TITLES),
                         "a report is missing from GROUPS, or GROUPS names one that no longer exists")
        self.assertEqual(len(grouped), len(set(grouped)), "a report appears in two groups")

    def test_keys_follow_the_grouped_order(self):
        self.assertEqual(list(reports.KEYS), [key for _group, keys in reports.GROUPS for key in keys])

    def test_no_group_is_empty_and_each_has_a_title(self):
        for group, keys in reports.GROUPS:
            self.assertTrue(group.strip(), "a group has no title")
            self.assertTrue(keys, f"the group {group} holds no report")
