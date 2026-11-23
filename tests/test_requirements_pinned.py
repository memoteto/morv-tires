"""Every shipped dependency names one version, or two builds of the same commit differ."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^[A-Za-z0-9._-]+==[0-9][0-9A-Za-z.+-]*$")


class RequirementsTest(unittest.TestCase):
    def _lines(self, name: str) -> list[str]:
        return [ln.strip() for ln in (ROOT / name).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")]

    def test_what_ships_with_the_program_is_pinned_to_one_version(self):
        loose = [ln for ln in self._lines("requirements.txt") if not PIN.match(ln)]
        self.assertEqual([], loose, "a range makes the built copy depend on the day it was built")

    def test_the_build_and_test_tools_are_pinned_too(self):
        loose = [ln for ln in self._lines("requirements-dev.txt") if not PIN.match(ln)]
        self.assertEqual([], loose)


if __name__ == "__main__":
    unittest.main()
