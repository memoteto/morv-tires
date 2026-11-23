"""The installer must replace the program and never reach the shop's data."""
import re
import unittest
from pathlib import Path

ISS = Path(__file__).resolve().parent.parent / "tools" / "installer.iss"


class InstallerScriptTest(unittest.TestCase):
    def setUp(self):
        self.text = ISS.read_text(encoding="utf-8")
        # Comments explain why a place is avoided; only the directives are checked for naming it
        self.directives = "\n".join(ln for ln in self.text.splitlines()
                                    if not ln.strip().startswith(";"))

    def test_it_carries_a_fixed_id_so_a_new_version_replaces_the_old_one(self):
        found = re.search(r"^AppId=\{\{[0-9A-F-]{36}\}$", self.text, re.MULTILINE | re.IGNORECASE)
        self.assertIsNotNone(found, "without a fixed AppId every version installs beside the last")

    def test_it_deletes_nothing_of_its_own_on_uninstall(self):
        self.assertNotIn("[UninstallDelete]", self.directives,
                         "uninstalling must leave the database, the backups and the logs alone")

    def test_it_never_points_at_the_data_folder(self):
        for place in ("localappdata", "morv_tires.db", "backups", "userappdata"):
            self.assertNotIn(place, self.directives.lower(),
                             f"the installer has no business with {place}")

    def test_it_writes_the_setup_where_the_build_looks_for_it(self):
        self.assertIn(r"OutputDir=..\dist", self.directives,
                      "OutputDir is relative to this file, which sits in tools/")

    def test_it_closes_a_running_copy_rather_than_failing_the_upgrade(self):
        self.assertIn("CloseApplications=yes", self.text)

    def test_it_installs_what_the_build_produced(self):
        self.assertIn(r'Source: "..\dist\MorvTires\*"', self.directives)

    def test_its_version_is_given_at_build_time_not_written_twice(self):
        self.assertIn("AppVersion={#AppVersion}", self.text)
        self.assertEqual(1, self.text.count("#define AppVersion"),
                         "the version comes from morv.VERSION through ISCC, not from a second copy here")


if __name__ == "__main__":
    unittest.main()
