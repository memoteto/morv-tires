"""محرك النسخ الاحتياطي واختبار الاستعادة.

النسخة تُؤخذ بواجهة SQLite الرسمية للنسخ (backup API)، فتكون سليمة حتى لو كان البرنامج
يكتب في نفس اللحظة. بعدها تُفحص سلامتها، ثم تُعطى اسمها النهائي، ثم تُسجّل في backup_runs.
النسخة التي لا تنجح في الفحص لا تُعتبر نسخة أبدًا.

اختبار الاستعادة يفتح نسخة في مجلد مؤقت معزول ويتأكد أنها تعمل، دون لمس قاعدة التشغيل.
التشفير ومفتاح الاسترداد والاستعادة الفعلية تأتي مع شاشة النسخ في المرحلة 1.
"""
import hashlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from morv import APP_ID
from morv.infra import paths

KINDS = ("manual", "daily", "pre_migration", "safety")


class BackupError(RuntimeError):
    """فشل إنشاء نسخة أو فحصها."""


@dataclass
class BackupResult:
    """نتيجة عملية نسخ أو اختبار استعادة."""
    ok: bool
    path: Path | None
    size_bytes: int
    sha256: str
    detail: str


def _now() -> str:
    """الوقت الحالي بصيغة ثابتة للسجلات."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256(path: Path) -> str:
    """بصمة الملف، تُستخدم لاكتشاف أي تلف أو تغيير لاحق في النسخة."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_backup(path) -> tuple:
    """فحص نسخة دون تعديلها: سليمة؟ تابعة للبرنامج؟ ما رقم ترقيتها؟

    يعيد (True, وصف) أو (False, سبب الفشل).
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False, "الملف غير موجود أو فارغ"
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            check = con.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                return False, f"فحص السلامة فشل: {check}"
            row = con.execute("SELECT v FROM app_meta WHERE k='app_id'").fetchone()
            if not row or row[0] != APP_ID:
                return False, "ليست نسخة من قاعدة Morv Tires"
            version = con.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            return True, f"سليمة · ترقية {version} · {tables} جدول"
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        return False, f"الملف تالف أو ليس قاعدة SQLite: {e}"


def _record(con, action, kind, started, result: BackupResult) -> None:
    """تسجيل العملية في backup_runs إن كان الجدول موجودًا (قبل أول ترقية لا يوجد)."""
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_runs'").fetchone()
    if not has:
        return
    con.execute(
        "INSERT INTO backup_runs (action, kind, started_at, finished_at, file_name, size_bytes, sha256, status, detail) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (action, kind, started, _now(), result.path.name if result.path else None,
         result.size_bytes, result.sha256 or None, "ok" if result.ok else "failed", result.detail))


def create_backup(con: sqlite3.Connection, kind: str = "manual", dest_dir=None) -> BackupResult:
    """أخذ نسخة احتياطية مفحوصة من قاعدة التشغيل.

    الخطوات: نسخ إلى ملف مؤقت ← فحص السلامة ← بصمة ← الاسم النهائي ← تسجيل.
    عند أي فشل يُحذف الملف المؤقت ويُسجّل الفشل ويُرفع BackupError.
    """
    if kind not in KINDS:
        raise BackupError(f"نوع نسخة غير معروف: {kind}")
    dest = Path(dest_dir) if dest_dir else paths.backups_dir()
    dest.mkdir(parents=True, exist_ok=True)
    started = _now()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    final = dest / f"morv_tires_{stamp}_{kind}.db"
    part = final.with_suffix(".db.part")
    try:
        target = sqlite3.connect(part)
        try:
            con.backup(target)
        finally:
            target.close()
        ok, detail = verify_backup(part)
        if not ok:
            raise BackupError(detail)
        digest = _sha256(part)
        part.replace(final)
        result = BackupResult(True, final, final.stat().st_size, digest, detail)
    except (sqlite3.Error, OSError, BackupError) as e:
        part.unlink(missing_ok=True)
        result = BackupResult(False, None, 0, "", str(e))
        _record(con, "backup", kind, started, result)
        raise BackupError(f"فشلت النسخة الاحتياطية: {e}") from e
    _record(con, "backup", kind, started, result)
    return result


def restore_test(con: sqlite3.Connection, backup_path, expected_sha256: str | None = None) -> BackupResult:
    """اختبار أن نسخة تصلح للاستعادة، دون لمس قاعدة التشغيل.

    تُنسخ النسخة إلى مجلد مؤقت، وتُقارن بصمتها بالمسجلة (إن وُجدت)، وتُفحص، ثم يُحذف المجلد.
    النتيجة تُسجّل في backup_runs كاختبار استعادة.
    """
    started = _now()
    src = Path(backup_path)
    tmp = Path(tempfile.mkdtemp(prefix="morv_restore_test_"))
    try:
        copy = tmp / src.name
        shutil.copy2(src, copy)
        digest = _sha256(copy)
        if expected_sha256 and digest != expected_sha256:
            result = BackupResult(False, src, copy.stat().st_size, digest, "البصمة لا تطابق المسجّلة: النسخة تغيّرت أو تلفت")
        else:
            ok, detail = verify_backup(copy)
            result = BackupResult(ok, src, copy.stat().st_size, digest, detail)
    except OSError as e:
        result = BackupResult(False, src, 0, "", f"تعذّر نسخ الملف للاختبار: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _record(con, "restore_test", "manual", started, result)
    return result
