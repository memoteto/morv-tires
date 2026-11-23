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


class DataOutsideTheInstallTest(unittest.TestCase):
    """Reinstalling or upgrading replaces the program folder, so no shop data may live inside it."""

    def test_the_data_folder_is_never_inside_the_program_folder(self):
        import os
        from morv.infra import paths
        saved = os.environ.pop("MORV_DATA_DIR", None)
        try:
            data = paths.data_dir().resolve()
        finally:
            if saved is not None:
                os.environ["MORV_DATA_DIR"] = saved
        program = Path(__file__).resolve().parent.parent
        self.assertNotIn(program, data.parents, f"{data} would be wiped by a reinstall")
        self.assertNotEqual(program, data)

    def test_every_file_the_shop_owns_sits_under_that_one_folder(self):
        import os
        from morv.infra import paths
        saved = os.environ.get("MORV_DATA_DIR")
        os.environ["MORV_DATA_DIR"] = str(Path(__file__).resolve().parent.parent / ".tmp_data_check")
        try:
            root = paths.data_dir().resolve()
            for place in (paths.db_path(), paths.backups_dir(), paths.logs_dir()):
                self.assertIn(root, Path(place).resolve().parents, str(place))
        finally:
            import shutil
            shutil.rmtree(os.environ["MORV_DATA_DIR"], ignore_errors=True)
            if saved is None:
                os.environ.pop("MORV_DATA_DIR", None)
            else:
                os.environ["MORV_DATA_DIR"] = saved
