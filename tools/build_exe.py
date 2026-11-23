"""Build MorvTires.exe with PyInstaller in onedir mode."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Qt modules the app never uses; excluding them shrinks the build
EXCLUDES = ["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL",
            "PySide6.QtDBus", "PySide6.QtTest", "PySide6.QtConcurrent", "PySide6.QtSql",
            "PySide6.QtXml", "tkinter", "unittest", "pydoc"]

# Deleted after the build: software OpenGL, 20 MB a plain widgets app never needs
DROP_AFTER = ["opengl32sw.dll"]


def main() -> int:
    import PyInstaller.__main__

    sep = ";" if sys.platform == "win32" else ":"
    migrations = ROOT / "morv" / "infra" / "db" / "migrations"
    assets = ROOT / "morv" / "ui" / "assets"
    args = [str(ROOT / "run.py"), "--name", "MorvTires", "--onedir", "--windowed", "--noconfirm",
            "--clean", "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            "--add-data", f"{migrations}{sep}morv/infra/db/migrations",
            "--icon", str(assets / "app_icon.ico"),
            "--add-data", f"{assets}{sep}morv/ui/assets",
            "--add-data", f"{ROOT / 'morv' / 'ui' / 'fonts'}{sep}morv/ui/fonts"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    PyInstaller.__main__.run(args)

    out = ROOT / "dist" / "MorvTires"
    for name in DROP_AFTER:
        for f in out.rglob(name):
            f.unlink()
            print(f"حُذف {f.name}")
    needed = ("0001_foundation.sql", "IBMPlexSansArabic-Regular.ttf", "sidebar_logo.png", "app_icon.ico")
    if not all(any(out.rglob(name)) for name in needed):
        print("خطأ: ملفات الترقية أو الخطوط أو الشعار غير موجودة داخل البناء")
        return 1
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"تم البناء: {out} ({size / 1_048_576:.0f} MB)")
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
