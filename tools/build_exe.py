"""بناء MorvTires.exe بأداة PyInstaller بوضع onedir (مجلد فيه الملف التنفيذي ومكتباته).

لماذا onedir وليس ملفًا واحدًا: الملف الواحد يفك نفسه في مجلد مؤقت عند كل تشغيل فيبطئ
البدء، وبرامج الحماية تشتبه فيه أكثر.

الاستخدام:  python tools/build_exe.py
الناتج:    dist/MorvTires/MorvTires.exe
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# وحدات Qt لا يستخدمها البرنامج؛ استبعادها يصغّر الحجم
EXCLUDES = ["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL",
            "PySide6.QtDBus", "PySide6.QtTest", "PySide6.QtConcurrent", "PySide6.QtSql",
            "PySide6.QtXml", "tkinter", "unittest", "pydoc"]

# ملفات تُحذف بعد البناء: OpenGL البرمجي (20 ميجابايت) لا يحتاجه برنامج ويدجتس عادي
DROP_AFTER = ["opengl32sw.dll"]


def main() -> int:
    """تشغيل PyInstaller ثم حذف الملفات غير اللازمة وطباعة الحجم النهائي."""
    import PyInstaller.__main__

    sep = ";" if sys.platform == "win32" else ":"
    migrations = ROOT / "morv" / "infra" / "db" / "migrations"
    args = [str(ROOT / "run.py"), "--name", "MorvTires", "--onedir", "--windowed", "--noconfirm",
            "--clean", "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            "--add-data", f"{migrations}{sep}morv/infra/db/migrations",
            "--add-data", f"{ROOT / 'morv' / 'ui' / 'fonts'}{sep}morv/ui/fonts"]
    for mod in EXCLUDES:
        args += ["--exclude-module", mod]
    PyInstaller.__main__.run(args)

    out = ROOT / "dist" / "MorvTires"
    for name in DROP_AFTER:
        for f in out.rglob(name):
            f.unlink()
            print(f"حُذف {f.name}")
    if not any(out.rglob("0001_foundation.sql")) or not any(out.rglob("IBMPlexSansArabic-Regular.ttf")):
        print("خطأ: ملفات الترقية أو الخطوط غير موجودة داخل البناء")
        return 1
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"تم البناء: {out} ({size / 1_048_576:.0f} MB)")
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
