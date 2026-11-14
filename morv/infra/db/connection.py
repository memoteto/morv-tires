"""فتح قاعدة البيانات بأمان.

حمايتان قبل أي كتابة:
1) رفض ملف البرنامج القديم بالاسم (tire_shop_live.db).
2) رفض أي قاعدة موجودة لا تحمل علامة البرنامج app_id = 'morv_tires'.
   يُفحص ذلك بفتح الملف للقراءة فقط، فلا يُلمس الملف الغريب أبدًا.
"""
import sqlite3
from pathlib import Path

from morv import APP_ID

FORBIDDEN_NAMES = {"tire_shop_live.db"}


class UnsafeDatabase(RuntimeError):
    """محاولة فتح قاعدة ليست تابعة للبرنامج — تُرفض دائمًا."""


def _apply_pragmas(con: sqlite3.Connection) -> None:
    """إعدادات SQLite المعتمدة بعد التجربة.

    WAL: القراءة لا تنتظر الكتابة.
    synchronous=FULL: لا تضيع آخر فاتورة محفوظة إذا انقطعت الكهرباء (لا نفترض وجود UPS).
    foreign_keys=ON: يمنع ربط مستند بشيء غير موجود.
    """
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-16000")   # حوالي 16 ميجابايت ذاكرة مؤقتة
    con.execute("PRAGMA busy_timeout=3000")


def read_app_id(path: Path):
    """قراءة علامة البرنامج من ملف موجود دون تعديله. يعيد None إن لم توجد."""
    ro = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        row = ro.execute("SELECT v FROM app_meta WHERE k='app_id'").fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        ro.close()


def _is_empty(path: Path) -> bool:
    """هل الملف قاعدة SQLite فارغة تمامًا (بلا جداول)؟ يُفتح للقراءة فقط."""
    ro = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return ro.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    except sqlite3.DatabaseError:
        return False      # ملف تالف أو ليس SQLite: لا نعتبره فارغًا ولا نلمسه
    finally:
        ro.close()


def open_db(path, create: bool = False) -> sqlite3.Connection:
    """فتح قاعدة البرنامج.

    create=True يسمح بإنشاء ملف جديد فارغ (الترقيات تبني جداوله بعد ذلك).
    القاعدة الموجودة تُفتح فقط إذا كانت تحمل علامة البرنامج.
    المعاملات تُدار يدويًا (BEGIN/COMMIT) لذلك isolation_level=None.
    """
    p = Path(path)
    if p.name.lower() in FORBIDDEN_NAMES:
        raise UnsafeDatabase(f"رفض فتح قاعدة البرنامج القديم: {p}")
    exists = p.exists() and p.stat().st_size > 0
    if not exists and not create:
        raise FileNotFoundError(p)
    # ملف موجود بلا أي جداول = قاعدة جديدة فشل إنشاؤها سابقًا؛ يُسمح بإكمالها عند create فقط
    if exists and read_app_id(p) != APP_ID and not (create and _is_empty(p)):
        raise UnsafeDatabase(f"هذه ليست قاعدة Morv Tires، تم الرفض: {p}")
    con = sqlite3.connect(p, isolation_level=None)
    _apply_pragmas(con)
    return con
