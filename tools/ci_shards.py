"""Run disjoint unittest module shards and verify their combined execution manifests."""
import argparse
import json
import os
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEIGHTS = Path(__file__).with_name("ci_shard_weights.json")


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def make_plan(tests, count, weights):
    if count < 1:
        raise ValueError("Shard count must be positive")
    groups = {}
    ids = []
    for test in tests:
        name = test.id()
        if name.startswith(("unittest.loader._FailedTest", "_FailedTest")):
            raise ValueError(f"Test discovery failed: {name}")
        ids.append(name)
        groups.setdefault(type(test).__module__, []).append(test)
    if len(set(ids)) != len(ids) or len(groups) < count:
        raise ValueError("Duplicate test IDs or too few modules for the requested shards")
    loads = [0] * count
    owners = {}
    for module in sorted(groups, key=lambda k: (-weights.get(k, len(groups[k])), k)):
        index = min(range(count), key=lambda i: (loads[i], i))
        owners[module] = index
        loads[index] += weights.get(module, len(groups[module]))
    # Preserve discovery order and keep each module's fixtures in one process.
    return [[test for test in tests if owners[type(test).__module__] == index]
            for index in range(count)], loads


class TrackingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = []

    def startTest(self, test):
        self.started.append(test.id())
        super().startTest(test)


def verify_manifests(reports, count, platform):
    if len(reports) != count or {r["index"] for r in reports} != set(range(count)):
        raise ValueError("Missing or duplicate shard manifests")
    expected = Counter(reports[0]["all_ids"])
    if not expected or any(n != 1 for n in expected.values()):
        raise ValueError("Empty or duplicate full discovery IDs")
    executed = Counter()
    for report in reports:
        if (report["platform"] != platform or report["count"] != count
                or Counter(report["all_ids"]) != expected or report["success"] is not True):
            raise ValueError("Inconsistent discovery, platform, count or unsuccessful shard")
        assigned = Counter(report["selected_ids"])
        started = Counter(report["started_ids"])
        if not assigned or assigned != started or any(n != 1 for n in started.values()):
            raise ValueError(f"Incomplete or duplicate execution in shard {report['index']}")
        executed.update(started)
    if executed != expected:
        raise ValueError("Combined execution does not match full test discovery")
    return sum(executed.values())


def run_shard(args):
    if not 0 <= args.index < args.count:
        raise ValueError("Shard index out of range")
    sys.path.insert(0, str(ROOT))
    discovered = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    tests = list(iter_tests(discovered))
    weights = json.loads(WEIGHTS.read_text(encoding="utf-8"))["weights"][args.platform]
    plan, loads = make_plan(tests, args.count, weights)
    selected = plan[args.index]
    selected_ids = [test.id() for test in selected]
    all_ids = [test.id() for test in tests]
    print(f"Shard {args.index + 1}/{args.count}: {len(selected)} of {len(tests)} tests; "
          f"estimated module loads: {loads}", flush=True)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=TrackingResult)
    result = runner.run(unittest.TestSuite(selected))
    success = result.wasSuccessful() and Counter(result.started) == Counter(selected_ids)
    report = {"index": args.index, "count": args.count, "platform": args.platform,
              "all_ids": all_ids, "selected_ids": selected_ids, "started_ids": result.started,
              "success": success}
    args.report.write_text(json.dumps(report), encoding="utf-8")
    if not success:
        print("Shard failed or did not execute every assigned test", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    # Match run_tests.py: avoid Qt shutdown changing the test process exit status.
    os._exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    verify = sub.add_parser("verify")
    for cmd in (run, verify):
        cmd.add_argument("--platform", choices=("ubuntu-latest", "windows-latest"), required=True)
        cmd.add_argument("--count", type=int, required=True)
    run.add_argument("--index", type=int, required=True)
    run.add_argument("--report", type=Path, required=True)
    verify.add_argument("--reports", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        run_shard(args)
    else:
        reports = [json.loads(path.read_text(encoding="utf-8"))
                   for path in sorted(args.reports.rglob("shard-report.json"))]
        total = verify_manifests(reports, args.count, args.platform)
        print(f"Verified {total} distinct executed tests across {args.count} {args.platform} shards")


if __name__ == "__main__":
    main()
