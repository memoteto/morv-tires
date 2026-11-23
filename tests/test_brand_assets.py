"""The approved logo and icon ship at full quality and are wired into the app and the build."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "morv" / "ui" / "assets"


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


def ico_sizes(path: Path) -> set[int]:
    data = path.read_bytes()
    count = struct.unpack("<H", data[4:6])[0]
    # A width byte of 0 means 256 in the ICO directory
    return {(data[6 + 16 * i] or 256) for i in range(count)}


class BrandAssetsTest(unittest.TestCase):
    def test_sources_are_the_original_resolution_not_thumbnails(self):
        self.assertEqual(png_size(ASSETS / "app_icon.png"), (1024, 1024))
        self.assertEqual(png_size(ASSETS / "sidebar_logo.png"), (939, 487))

    def test_ico_carries_every_size_windows_asks_for(self):
        self.assertTrue({16, 24, 32, 48, 64, 128, 256} <= ico_sizes(ASSETS / "app_icon.ico"))

    def test_build_embeds_the_icon_and_ships_the_images(self):
        text = (ROOT / "tools" / "build_exe.py").read_text(encoding="utf-8")
        self.assertIn('"--icon"', text)
        self.assertIn("morv/ui/assets", text)

    def test_installer_uses_the_same_icon(self):
        text = (ROOT / "tools" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn(r"SetupIconFile=..\morv\ui\assets\app_icon.ico", text)

    def test_app_sets_the_window_icon_from_the_ico(self):
        text = (ROOT / "morv" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("setWindowIcon", text)
        self.assertIn("app_icon.ico", text)


if __name__ == "__main__":
    unittest.main()
