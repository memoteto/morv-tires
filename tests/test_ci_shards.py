"""Regression tests for complete, disjoint CI sharding and execution verification."""
import copy
import io
import unittest
from collections import Counter

from tools.ci_shards import TrackingResult, make_plan, verify_manifests


class ShardPlanTest(unittest.TestCase):
    def test_modules_stay_together_and_every_test_is_assigned_once(self):
        tests = []
        for module in ("alpha", "beta", "gamma", "delta"):
            case = type("Case", (unittest.TestCase,), {
                "__module__": module, "test_one": lambda self: None, "test_two": lambda self: None,
            })
            tests.extend(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        plan, loads = make_plan(tests, 2, {"alpha": 5, "beta": 5, "gamma": 1, "delta": 1})
        self.assertEqual(loads, [6, 6])
        self.assertEqual(Counter(test.id() for shard in plan for test in shard),
                         Counter(test.id() for test in tests))
        for module in ("alpha", "beta", "gamma", "delta"):
            self.assertEqual(sum(any(type(test).__module__ == module for test in shard)
                                 for shard in plan), 1)
        self.assertEqual(plan, make_plan(tests, 2, {"alpha": 5, "beta": 5, "gamma": 1, "delta": 1})[0])
        with self.assertRaises(ValueError):
            make_plan(tests + [tests[0]], 2, {})

    def test_actual_runner_records_started_tests(self):
        case = unittest.FunctionTestCase(lambda: None)
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=TrackingResult).run(case)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.started, [case.id()])

    def reports(self):
        return [{"index": index, "count": 2, "platform": "windows-latest",
                 "all_ids": ["a", "b"], "selected_ids": [name], "started_ids": [name], "success": True}
                for index, name in enumerate(("a", "b"))]

    def test_manifests_detect_missing_duplicate_and_unexecuted_tests(self):
        reports = self.reports()
        self.assertEqual(verify_manifests(reports, 2, "windows-latest"), 2)
        for key, value in (("started_ids", []), ("started_ids", ["b", "b"]),
                           ("all_ids", ["a"]), ("success", False), ("index", 0),
                           ("platform", "ubuntu-latest")):
            with self.subTest(key=key, value=value):
                broken = copy.deepcopy(reports)
                broken[1][key] = value
                with self.assertRaises(ValueError):
                    verify_manifests(broken, 2, "windows-latest")
        broken = copy.deepcopy(reports)
        broken[1]["selected_ids"] = broken[1]["started_ids"] = ["a"]
        with self.assertRaises(ValueError):
            verify_manifests(broken, 2, "windows-latest")
        with self.assertRaises(ValueError):
            verify_manifests(reports[:1], 2, "windows-latest")
