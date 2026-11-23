"""A shipped build must create no administrator of its own, whatever switch it is given."""
import unittest
from pathlib import Path

from tests.qt_app import require_qt

ROOT = Path(__file__).resolve().parent.parent


class EntryArgumentsTest(unittest.TestCase):
    def test_the_old_test_switch_is_refused_before_anything_is_opened(self):
        from morv.app.main import main
        self.assertEqual(2, main(["morv", "--smoke"]))

    def test_the_refusal_survives_a_build_that_has_no_streams(self):
        import sys
        from morv.app.main import main
        saved = sys.stderr
        sys.stderr = None                 # what PyInstaller leaves a windowed build
        try:
            self.assertEqual(2, main(["morv", "--smoke"]))
        finally:
            sys.stderr = saved

    def test_any_unknown_switch_is_refused_rather_than_ignored(self):
        from morv.app.main import main
        for switch in ("--demo", "--reset", "-x"):
            self.assertEqual(2, main(["morv", switch]), switch)

    def test_no_fixed_credential_is_left_anywhere_in_the_program(self):
        leaks = []
        for path in (ROOT / "morv").rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):       # a comment saying why it was removed is not a leak
                    continue
                for secret in ("Smoke12345", "Smoke-Backup", "--smoke"):
                    if secret in line:
                        leaks.append(f"{path.relative_to(ROOT)}:{number} {secret}")
        self.assertEqual([], leaks, "a built copy would ship with these")


if __name__ == "__main__":
    unittest.main()


@require_qt()
class ScheduledBackupThreadTest(unittest.TestCase):
    def test_the_worker_is_handed_a_name_and_never_a_connection(self):
        import inspect
        from morv.ui.backup_runner import BackupRunner
        self.assertEqual(["self", "username", "parent"],
                         list(inspect.signature(BackupRunner.__init__).parameters),
                         "a sqlite connection must not cross a thread boundary")

    def test_the_job_opens_and_closes_a_connection_of_its_own(self):
        import inspect
        from morv.infra import backup_job
        source = inspect.getsource(backup_job.run_scheduled_alone)
        self.assertIn("open_db", source)
        self.assertIn("con.close()", source)
