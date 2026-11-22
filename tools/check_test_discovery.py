"""Fails unless pytest and unittest discover the same tests, so run_tests.py can be the only runner."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def unittest_ids() -> set:
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    found: set = set()

    def walk(item) -> None:
        if isinstance(item, unittest.TestSuite):
            for child in item:
                walk(child)
            return
        name = item.id()
        # A module that fails to import is reported as a fake test; that is a real error, not a mismatch
        if name.startswith(("unittest.loader._FailedTest", "_FailedTest")):
            raise SystemExit(f"a test module failed to import: {name}")
        found.add(name)

    walk(suite)
    return found


def pytest_ids() -> set:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit(f"pytest could not collect the tests:\n{out.stdout}\n{out.stderr}")
    found = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        path, *rest = line.split("::")
        found.add(".".join([path.replace("/", ".").replace("\\", ".")[:-3], *rest]))
    return found


def main() -> int:
    by_unittest, by_pytest = unittest_ids(), pytest_ids()
    if by_unittest == by_pytest:
        print(f"discovery matches: {len(by_unittest)} tests seen by both runners", flush=True)
        return 0
    for name in sorted(by_pytest - by_unittest):
        print(f"only pytest sees it, so run_tests.py would skip it: {name}", flush=True)
    for name in sorted(by_unittest - by_pytest):
        print(f"only unittest sees it: {name}", flush=True)
    print("add the test as a unittest.TestCase, or teach run_tests.py to find it", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
