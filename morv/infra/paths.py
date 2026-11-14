"""أماكن ملفات البرنامج على الجهاز.

قاعدة المالك: البيانات في مجلد المستخدم المحلي، وممنوع وضعها في مجلد متزامن
(Google Drive وغيره) أو مجلد شبكة، لأن المزامنة قد تُفسد ملف SQLite أثناء الكتابة.
"""
import os
import sys
from pathlib import Path

from morv import APP_NAME

DB_FILE = "morv_tires.db"


def data_dir() -> Path:
    """مجلد بيانات البرنامج. متغير البيئة MORV_DATA_DIR يغيّره (للاختبارات فقط)."""
    override = os.environ.get("MORV_DATA_DIR")
    if override:
        d = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        d = base / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """مسار قاعدة البيانات الرئيسية."""
    return data_dir() / DB_FILE


def backups_dir() -> Path:
    """مجلد النسخ الاحتياطية المحلية."""
    d = data_dir() / "backups"
    d.mkdir(exist_ok=True)
    return d


def logs_dir() -> Path:
    """مجلد سجلات التشغيل والأخطاء."""
    d = data_dir() / "logs"
    d.mkdir(exist_ok=True)
    return d


_SYNC_MARKERS = ("google drive", "googledrive", "onedrive", "dropbox", "icloud", "my drive")


def looks_synced_or_network(path: Path) -> bool:
    """فحص تقريبي: هل المسار داخل مجلد مزامنة أو مجلد شبكة؟ (يُستخدم للتحذير فقط)"""
    p = str(path).lower()
    return p.startswith("\\\\") or any(m in p for m in _SYNC_MARKERS)
