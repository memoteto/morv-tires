"""تشغيل Morv Tires من الكود المصدري.

    python run.py           تشغيل عادي
    python run.py --smoke   فتح ثم إغلاق تلقائي (اختبار سريع)

وهو أيضًا الملف الذي يبني منه PyInstaller ملف MorvTires.exe.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from morv.app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
