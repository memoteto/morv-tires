"""محرك النسخ الاحتياطي: نسخ مشفّرة، واحتفاظ، وفلاشة، واختبار استعادة، واستعادة فعلية.

مسار النسخة:
  قاعدة التشغيل ─(SQLite backup API)→ ملف مؤقت ─(فحص سلامة)→ ─(تشفير vault)→ ملف .mtb نهائي
النسخة التي لا تنجح في الفحص لا تُعتبر نسخة أبدًا، وكل عملية تُسجَّل في backup_runs بنتيجتها.

قبل إعداد التشفير (أثناء معالج التشغيل الأول أو نسخة ما قبل الترقية على قاعدة قديمة)
تُكتب نسخة غير مشفّرة بامتداد .db؛ وبعد الإعداد كل النسخ .mtb.

الجدولة (تُستدعى من التطبيق عند البدء والإغلاق):
- نسخة يومية واحدة؛ الاحتفاظ بآخر 14 يومية، ونسخة لكل شهر لمدة 12 شهرًا.
- اختبار استعادة تلقائي كل 7 أيام على أحدث نسخة.
- تنبيه إن فشلت آخر نسخة، أو مرّ يومان بلا نسخة، أو 7 أيام بلا نسخة على الفلاشة.
"""
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from morv import APP_ID
from morv.core.text import ltr
from morv.infra import keystore, paths, settings, vault

KINDS = ("manual", "daily", "pre_migration", "safety")
KIND_LABELS = {"manual": "يدوية", "daily": "يومية", "pre_migration": "قبل تحديث البرنامج", "safety": "أمان قبل الاستعادة"}
KEEP_DAILY = 14
KEEP_MONTHLY = 12
KEEP_OTHER = 5
RESTORE_TEST_DAYS = 7
STALE_DAYS = 2
USB_STALE_DAYS = 7
_NAME = re.compile(r"^morv_tires_(\d{8})_(\d{6})_\d+_([a-z_]+)\.(db|mtb)$")
_TS = "%Y-%m-%d %H:%M:%S"


class BackupError(RuntimeError):
    """فشل إنشاء نسخة أو فحصها أو استعادتها."""


@dataclass
class BackupResult:
    """نتيجة عملية نسخ أو اختبار أو استعادة."""
    ok: bool
    path: Path | None
    size_bytes: int
    sha256: str
    detail: str


@dataclass
class BackupInfo:
    """وصف ملف نسخة في القائمة."""
    path: Path
    at: datetime
    kind: str
    encrypted: bool
    size_bytes: int


def _now_s() -> str:
    """الوقت الحالي بصيغة السجلات."""
    return datetime.now().strftime(_TS)


def _sha256(path: Path) -> str:
    """بصمة ملف، لاكتشاف أي تغيير لاحق في النسخة."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_db_file(path) -> tuple:
    """فحص ملف قاعدة بيانات (غير مشفّر) دون تعديله: سليم؟ تابع للبرنامج؟ ما رقم ترقيته؟"""
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


def _record(con, action, kind, started, result: BackupResult, username=None) -> None:
    """تسجيل العملية في backup_runs إن كان الجدول موجودًا (قبل أول ترقية لا يوجد)."""
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_runs'").fetchone()
    if not has:
        return
    cols = {r[1] for r in con.execute("PRAGMA table_info(backup_runs)")}
    if "username" in cols:
        con.execute(
            "INSERT INTO backup_runs (action, kind, started_at, finished_at, file_name, size_bytes, sha256, status, detail, username) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (action, kind, started, _now_s(), result.path.name if result.path else None,
             result.size_bytes, result.sha256 or None, "ok" if result.ok else "failed", result.detail, username))
    else:
        # قاعدة لم تصل بعد إلى الترقية 0002 (نسخة ما قبل الترقية)
        con.execute(
            "INSERT INTO backup_runs (action, kind, started_at, finished_at, file_name, size_bytes, sha256, status, detail) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (action, kind, started, _now_s(), result.path.name if result.path else None,
             result.size_bytes, result.sha256 or None, "ok" if result.ok else "failed", result.detail))


# ---------- التشفير: الإعداد والمفاتيح ----------

def is_encryption_ready(con) -> bool:
    """هل أُعدّت حلقة المفاتيح (أي أن التشفير مفعّل)؟"""
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
    return bool(has) and bool(settings.get(con, "backup.keyring", ""))


def _keyring(con) -> dict:
    """حلقة المفاتيح من الإعدادات."""
    raw = settings.get(con, "backup.keyring", "")
    if not raw:
        raise BackupError("التشفير غير مُعدّ بعد")
    return json.loads(raw)


def setup_encryption(con, password: str, recovery_key: str) -> None:
    """إعداد التشفير أول مرة: DEK جديد مغلّف بكلمة المرور وبمفتاح الاسترداد، ومحفوظ على الجهاز."""
    dek, ring = vault.new_keyring(password, recovery_key)
    settings.put(con, "backup.keyring", json.dumps(ring))
    settings.put(con, "backup.keystore_scheme", keystore.save_dek(dek))


def device_dek(con) -> bytes | None:
    """مفتاح البيانات من الجهاز، أو None إن كان مفقودًا (يُطلب حينها كلمة المرور مرة واحدة)."""
    return keystore.load_dek()


def relink_device(con, secret: str) -> None:
    """إعادة حفظ DEK على هذا الجهاز بكلمة المرور أو مفتاح الاسترداد (بعد إعادة تثبيت Windows مثلًا)."""
    dek = vault.unlock(_keyring(con), secret)
    settings.put(con, "backup.keystore_scheme", keystore.save_dek(dek))


def change_backup_password(con, current_secret: str, new_password: str) -> None:
    """تغيير كلمة مرور النسخ. النسخ القديمة تبقى تُفتح بالقديمة أو بمفتاح الاسترداد."""
    ring = _keyring(con)
    dek = vault.unlock(ring, current_secret)
    settings.put(con, "backup.keyring", json.dumps(vault.rewrap_password(ring, dek, new_password)))
    keystore.save_dek(dek)


def regenerate_recovery_key(con, current_secret: str, new_recovery_key: str) -> None:
    """مفتاح استرداد جديد (المدير فقط). القديم يبقى يفتح النسخ المأخوذة قبل التغيير فقط."""
    ring = _keyring(con)
    dek = vault.unlock(ring, current_secret)
    settings.put(con, "backup.keyring", json.dumps(vault.rewrap_recovery(ring, dek, new_recovery_key)))


# ---------- إنشاء النسخ ----------

def _snapshot(con, dest: Path) -> None:
    """نسخة سليمة من قاعدة التشغيل إلى ملف، حتى لو كان البرنامج يكتب في نفس اللحظة."""
    target = sqlite3.connect(dest)
    try:
        con.backup(target)
    finally:
        target.close()


def create_backup(con: sqlite3.Connection, kind: str = "manual", dest_dir=None, username: str = None) -> BackupResult:
    """أخذ نسخة احتياطية مفحوصة؛ مشفّرة إن كان التشفير مُعدًّا وإلا عادية.

    عند أي فشل يُحذف المؤقت ويُسجَّل الفشل ويُرفع BackupError.
    """
    if kind not in KINDS:
        raise BackupError(f"نوع نسخة غير معروف: {kind}")
    dest = Path(dest_dir) if dest_dir else paths.backups_dir()
    dest.mkdir(parents=True, exist_ok=True)
    started = _now_s()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    encrypted = is_encryption_ready(con)
    final = dest / f"morv_tires_{stamp}_{kind}{vault.EXT if encrypted else '.db'}"
    tmp = dest / f"{final.name}.snapshot"
    try:
        _snapshot(con, tmp)
        ok, detail = verify_db_file(tmp)
        if not ok:
            raise BackupError(detail)
        if encrypted:
            dek = device_dek(con)
            if dek is None:
                raise BackupError("مفتاح الجهاز مفقود؛ أعد ربط الجهاز بكلمة مرور النسخ")
            vault.seal(tmp, final, dek, _keyring(con), {"kind": kind, "db_detail": detail})
            tmp.unlink(missing_ok=True)
            detail += " · مشفّرة"
        else:
            tmp.replace(final)
        result = BackupResult(True, final, final.stat().st_size, _sha256(final), detail)
    except (sqlite3.Error, OSError, BackupError, vault.VaultError) as e:
        tmp.unlink(missing_ok=True)
        result = BackupResult(False, None, 0, "", str(e))
        _record(con, "backup", kind, started, result, username)
        raise BackupError(f"فشلت النسخة الاحتياطية: {e}") from e
    _record(con, "backup", kind, started, result, username)
    if kind != "safety":
        settings.put(con, "backup.last_ok_at", _now_s())
    return result


# ---------- القائمة والاحتفاظ ----------

def list_backups(dest_dir=None) -> list[BackupInfo]:
    """ملفات النسخ في مجلد، الأحدث أولًا، بقراءة الاسم فقط (سريع ولا يفتح الملفات)."""
    dest = Path(dest_dir) if dest_dir else paths.backups_dir()
    out = []
    for p in dest.iterdir() if dest.exists() else []:
        m = _NAME.match(p.name)
        if not m:
            continue
        at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        out.append(BackupInfo(p, at, m.group(3), m.group(4) == "mtb", p.stat().st_size))
    out.sort(key=lambda b: b.at, reverse=True)
    return out


def prune(dest_dir=None) -> list[Path]:
    """حذف النسخ القديمة حسب سياسة الاحتفاظ، ويعيد ما حُذف.

    اليومية: آخر 14، ثم آخر نسخة من كل شهر لـ 12 شهرًا. الأنواع الأخرى: آخر 5 من كل نوع.
    """
    backups = list_backups(dest_dir)
    keep = set()
    daily = [b for b in backups if b.kind == "daily"]
    keep.update(b.path for b in daily[:KEEP_DAILY])
    months = {}
    for b in daily:                       # الأحدث أولًا، فأول ظهور لشهر هو آخر نسخة فيه
        months.setdefault(b.at.strftime("%Y-%m"), b)
    for key in sorted(months, reverse=True)[:KEEP_MONTHLY]:
        keep.add(months[key].path)
    for kind in KINDS:
        if kind != "daily":
            keep.update([b.path for b in backups if b.kind == kind][:KEEP_OTHER])
    removed = []
    for b in backups:
        if b.path not in keep:
            b.path.unlink(missing_ok=True)
            removed.append(b.path)
    return removed


# ---------- الفلاشة ----------

def usb_dir(con) -> Path | None:
    """مجلد الفلاشة المحفوظ في الإعدادات، أو None."""
    v = settings.get(con, "backup.usb_dir", "")
    return Path(v) if v else None


def same_drive_as_data(folder) -> bool:
    """هل المجلد على نفس قرص مجلد البيانات؟ (فلاشة على نفس القرص لا تحمي من تعطّل الجهاز)."""
    try:
        a = Path(folder).resolve()
        b = paths.data_dir().resolve()
    except OSError:
        return False
    if a.drive or b.drive:                 # Windows: مقارنة حرف القرص
        return a.drive.upper() == b.drive.upper()
    while not a.exists() and a.parent != a:   # المجلد قد لا يكون أُنشئ بعد؛ نصعد لأقرب موجود
        a = a.parent
    try:                                   # Linux: مقارنة الجهاز
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def usb_available(con) -> bool:
    """هل الفلاشة موصولة الآن (مجلدها موجود)؟"""
    d = usb_dir(con)
    return bool(d) and d.exists()


def copy_to_usb(con, username: str = None) -> BackupResult:
    """نسخ أحدث نسخة محلية إلى الفلاشة مع التحقق بالبصمة بعد النسخ."""
    started = _now_s()
    d = usb_dir(con)
    latest = next((b for b in list_backups() if b.kind in ("daily", "manual")), None)
    try:
        if not d:
            raise BackupError("لم يُحدَّد مجلد الفلاشة في الإعدادات")
        if not d.exists():
            raise BackupError("الفلاشة غير موصولة")
        if latest is None:
            raise BackupError("لا توجد نسخة محلية بعد")
        if not latest.encrypted:
            raise BackupError("لا تُنقل نسخة غير مشفّرة إلى الفلاشة")
        target = d / latest.path.name
        shutil.copy2(latest.path, target)
        if _sha256(target) != _sha256(latest.path):
            target.unlink(missing_ok=True)
            raise BackupError("النسخ إلى الفلاشة لم يكتمل بسلامة")
        result = BackupResult(True, target, target.stat().st_size, _sha256(target), "نُسخت إلى الفلاشة")
    except (OSError, BackupError) as e:
        result = BackupResult(False, latest.path if latest else None, 0, "", str(e))
        _record(con, "usb_copy", "manual", started, result, username)
        raise BackupError(str(e)) from e
    _record(con, "usb_copy", "manual", started, result, username)
    settings.put(con, "backup.last_usb_at", _now_s())
    return result


# ---------- اختبار الاستعادة والاستعادة الفعلية ----------

def restore_test(con, backup_path, expected_sha256: str | None = None, *, secret: str | None = None,
                 username: str = None) -> BackupResult:
    """اختبار أن نسخة تُفتح وتصلح للاستعادة، في مجلد مؤقت معزول دون لمس قاعدة التشغيل.

    إن أُعطيت البصمة المسجّلة تُقارن أولًا. المشفّرة تُفتح بمفتاح الجهاز، أو بسر
    (كلمة المرور/مفتاح الاسترداد) إن أُعطي.
    """
    started = _now_s()
    src = Path(backup_path)
    tmp = Path(tempfile.mkdtemp(prefix="morv_restore_test_"))
    try:
        if expected_sha256 and _sha256(src) != expected_sha256:
            raise BackupError("البصمة لا تطابق المسجّلة: النسخة تغيّرت أو تلفت")
        plain = tmp / "test.db"
        if src.suffix == vault.EXT:
            dek = None if secret else device_dek(con)
            if dek is None and not secret:
                raise BackupError("مفتاح الجهاز مفقود؛ أدخل كلمة مرور النسخ للاختبار")
            vault.open_vault(src, plain, dek=dek, secret=secret)
        else:
            shutil.copy2(src, plain)
        ok, detail = verify_db_file(plain)
        result = BackupResult(ok, src, src.stat().st_size, _sha256(src), detail)
    except (OSError, BackupError, vault.VaultError) as e:
        result = BackupResult(False, src, 0, "", str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _record(con, "restore_test", "manual", started, result, username)
    if result.ok:
        settings.put(con, "backup.last_test_at", _now_s())
    return result


def restore_file(db_path, backup_path, secret: str | None = None, dek: bytes | None = None) -> Path:
    """استبدال ملف قاعدة التشغيل بنسخة، بعد إغلاق كل الاتصالات بها. يعيد مسار نسخة الأمان.

    الترتيب: فك النسخة في مؤقت وفحصها ← نسخة أمان من الملف الحالي (مشفّرة بنفس المفتاح إن وُجد)
    ← استبدال الملف وحذف ملفات WAL. أي فشل قبل الاستبدال يترك قاعدة التشغيل كما هي.
    """
    db = Path(db_path)
    src = Path(backup_path)
    tmp = Path(tempfile.mkdtemp(prefix="morv_restore_"))
    try:
        plain = tmp / "restore.db"
        if src.suffix == vault.EXT:
            header = vault.open_vault(src, plain, dek=dek, secret=secret)
            if dek is None:
                dek = vault.unlock(header["keyring"], secret)
        else:
            shutil.copy2(src, plain)
        ok, detail = verify_db_file(plain)
        if not ok:
            raise BackupError(detail)
        safety = None
        # قاعدة بلا مستخدمين (جهاز جديد أنشأها للتو) لا تستحق نسخة أمان
        if db.exists() and _has_users(db):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            sdir = db.parent / "backups"
            sdir.mkdir(exist_ok=True)
            if dek is not None and src.suffix == vault.EXT:
                safety = sdir / f"morv_tires_{stamp}_safety{vault.EXT}"
                vault.seal(db, safety, dek, vault.read_header(src)["keyring"], {"kind": "safety"})
            else:
                safety = sdir / f"morv_tires_{stamp}_safety.db"
                shutil.copy2(db, safety)
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        shutil.copy2(plain, db)
        return safety
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _has_users(db_path) -> bool:
    """هل في القاعدة مستخدمون؟ تُقرأ للقراءة فقط؛ قاعدة بلا جدول users تُعامل كفارغة."""
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT count(*) FROM users").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


# ---------- الجدولة والصحة ----------

def _parse(ts: str) -> datetime | None:
    """نص وقت من الإعدادات إلى datetime، أو None."""
    return datetime.strptime(ts, _TS) if ts else None


def run_scheduled(con, username: str = None) -> list[str]:
    """ما يُشغَّل تلقائيًا عند البدء والإغلاق: نسخة يومية، تنظيف، واختبار استعادة أسبوعي.

    يعيد قائمة بما حدث للسجل. لا يرفع استثناءات؛ الفشل يُسجَّل ويظهر في التنبيه.
    """
    done = []
    if not is_encryption_ready(con):
        return ["التشفير غير مُعدّ؛ لا نسخ تلقائي"]
    today = datetime.now().date()
    last = _parse(settings.get(con, "backup.last_daily_at", ""))
    if last is None or last.date() < today:
        try:
            create_backup(con, "daily", username=username)
            settings.put(con, "backup.last_daily_at", _now_s())
            done.append("نسخة يومية")
            for p in prune():
                done.append(f"حُذفت نسخة قديمة {p.name}")
        except BackupError as e:
            done.append(f"فشلت النسخة اليومية: {e}")
    last_test = _parse(settings.get(con, "backup.last_test_at", ""))
    if last_test is None or (datetime.now() - last_test).days >= RESTORE_TEST_DAYS:
        latest = next((b for b in list_backups() if b.kind in ("daily", "manual")), None)
        if latest:
            r = restore_test(con, latest.path, username=username)
            done.append("اختبار استعادة: " + ("نجح" if r.ok else f"فشل: {r.detail}"))
    return done


def health(con) -> tuple[str, str]:
    """حالة النسخ للتنبيه في الشريط العلوي: ('ok'|'warn'|'danger', نص).

    danger: آخر عملية نسخ فشلت، أو لا نسخة منذ يومين. warn: 7 أيام بلا فلاشة أو مفتاح الجهاز مفقود.
    """
    if not is_encryption_ready(con):
        return "danger", "النسخ الاحتياطي غير مُعدّ"
    last_run = con.execute("SELECT status, detail FROM backup_runs WHERE action='backup' AND kind IN ('daily','manual') "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    if last_run and last_run[0] == "failed":
        return "danger", f"فشلت آخر نسخة احتياطية: {last_run[1]}"
    last_ok = _parse(settings.get(con, "backup.last_ok_at", ""))
    if last_ok is None:
        return "danger", "لم تُؤخذ نسخة احتياطية بعد"
    if datetime.now() - last_ok > timedelta(days=STALE_DAYS):
        return "danger", f"آخر نسخة احتياطية منذ {ltr(str((datetime.now() - last_ok).days))} أيام"
    if device_dek(con) is None:
        return "warn", "مفتاح الجهاز مفقود؛ أعد ربط الجهاز من الإعدادات"
    if usb_dir(con):
        last_usb = _parse(settings.get(con, "backup.last_usb_at", ""))
        if last_usb is None or datetime.now() - last_usb > timedelta(days=USB_STALE_DAYS):
            return "warn", "لم تُنقل نسخة إلى الفلاشة منذ أكثر من أسبوع"
    return "ok", f"آخر نسخة {ltr(last_ok.strftime('%Y-%m-%d %H:%M'))}"
