"""Backup engine: encrypted copies, retention, USB, restore test and real restore."""
import hashlib
import json
import os
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
from morv.infra.db import migrator
from morv.infra.db.connection import transaction
from morv.infra.logs import log

KINDS = ("manual", "daily", "pre_migration", "safety")
KIND_LABELS = {"manual": "يدوية", "daily": "يومية", "pre_migration": "قبل تحديث البرنامج", "safety": "أمان قبل الاستعادة"}
KEEP_DAILY = 14
KEEP_MONTHLY = 12
KEEP_OTHER = 5
PROOF_CANDIDATES = 3      # How many old backups to try when proving the device key against an unmarked keyring
_RELINK_HINT = "أعد ربط الجهاز بكلمة مرور النسخ من الإعدادات ← النسخ الاحتياطي"
_BUSY = "قاعدة البيانات مفتوحة في نافذة أو برنامج آخر؛ أغلقه ثم أعد المحاولة"
RESTORE_TEST_DAYS = 7
STALE_DAYS = 2
OFF_DEVICE_STALE_DAYS = 7      # RPO for the copy that survives losing the machine
OFF_DEVICE_LOST_DAYS = 14      # past this the shop counts as having no off-device copy at all
_NAME = re.compile(r"^morv_tires_(\d{8})_(\d{6})_\d+_([a-z_]+)\.(db|mtb)$")
_TEMP_NAME = re.compile(r"^morv_tires_\d{8}_\d{6}_\d+_[a-z_]+\.(?:db|mtb)\.(?:snapshot(?:-wal|-shm)?|part)$")
_in_flight: set[Path] = set()      # Claimed temp paths; one process holds the data folder, so the rest are dead
_TS = "%Y-%m-%d %H:%M:%S"


class BackupError(RuntimeError):
    """Making, checking or restoring a backup failed."""


@dataclass
class BackupResult:
    ok: bool
    path: Path | None
    size_bytes: int
    sha256: str
    detail: str


@dataclass
class BackupInfo:
    path: Path
    at: datetime
    kind: str
    encrypted: bool
    size_bytes: int


def _now_s() -> str:
    return datetime.now().strftime(_TS)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_db_file(path) -> tuple:
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
            broken = con.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                where = "، ".join(sorted({str(r[0]) for r in broken})[:3])
                return False, f"علاقات مكسورة في {ltr(str(len(broken)))} سطرًا ({where})"
            try:
                migrator.pending(con)   # Refuses a file from a newer build, or one whose migrations were edited
            except migrator.MigrationError as e:
                return False, str(e)
            version = con.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            return True, f"سليمة · ترقية {version} · {tables} جدول"
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        return False, f"الملف تالف أو ليس قاعدة SQLite: {e}"


def _record(con, action, kind, started, result: BackupResult, username=None) -> None:
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
        # A database still below migration 0002, from a pre-upgrade backup
        con.execute(
            "INSERT INTO backup_runs (action, kind, started_at, finished_at, file_name, size_bytes, sha256, status, detail) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (action, kind, started, _now_s(), result.path.name if result.path else None,
             result.size_bytes, result.sha256 or None, "ok" if result.ok else "failed", result.detail))


# ---------- Encryption: setup and keys ----------

def is_encryption_ready(con) -> bool:
    has = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
    return bool(has) and bool(settings.get(con, "backup.keyring", ""))


def _keyring(con) -> dict:
    raw = settings.get(con, "backup.keyring", "")
    if not raw:
        raise BackupError("التشفير غير مُعدّ بعد")
    return json.loads(raw)


def _save_device_key(dek: bytes) -> str:
    try:
        scheme = keystore.save_dek(dek)
    except (OSError, keystore.KeystoreError) as e:
        raise BackupError(f"تعذّر حفظ مفتاح الجهاز: {e}") from e
    if keystore.load_dek() != dek:
        raise BackupError("حُفظ مفتاح الجهاز لكن قراءته لم تطابق ما كُتب")
    return scheme


def prepare_keyring(password: str, recovery_key: str) -> tuple[dict, str]:
    dek, ring = vault.new_keyring(password, recovery_key)
    return ring, _save_device_key(dek)


def store_keyring(con, ring: dict, scheme: str) -> None:
    settings.put(con, "backup.keyring", json.dumps(ring))
    settings.put(con, "backup.keystore_scheme", scheme)


def setup_encryption(con, password: str, recovery_key: str) -> None:
    ring, scheme = prepare_keyring(password, recovery_key)
    with transaction(con):
        store_keyring(con, ring, scheme)


def device_dek(con) -> bytes | None:
    return keystore.load_dek()


def device_key_state(con) -> str:
    dek = keystore.load_dek()
    if dek is None:
        return "missing"
    m = vault.matches(_keyring(con), dek)
    return "unverified" if m is None else ("ok" if m else "mismatch")


def _prove_legacy_key(con, ring: dict, dek: bytes) -> bool:
    tried = 0
    for b in list_backups():
        if not b.encrypted or tried >= PROOF_CANDIDATES:
            continue
        try:
            their = vault.read_header(b.path)["keyring"]
        except vault.VaultError:
            continue
        if their["pw"]["wrapped"] != ring["pw"]["wrapped"] and their["rk"]["wrapped"] != ring["rk"]["wrapped"]:
            continue
        tried += 1
        if vault.opens_with(b.path, dek):
            settings.put(con, "backup.keyring", json.dumps(vault.with_fingerprint(ring, dek)))
            return True
    return False


def _checked_device_dek(con) -> bytes:
    dek = device_dek(con)
    if dek is None:
        raise BackupError(f"مفتاح الجهاز مفقود. {_RELINK_HINT}")
    ring = _keyring(con)
    ok = vault.matches(ring, dek)
    if ok is None and not _prove_legacy_key(con, ring, dek):
        raise BackupError(f"تعذّر التأكد أن مفتاح الجهاز يخص هذه البيانات. {_RELINK_HINT}")
    if ok is False:
        raise BackupError(f"مفتاح الجهاز لا يخص هذه البيانات (ربما بعد استعادة نسخة من إعداد آخر). {_RELINK_HINT}")
    return dek


def relink_device(con, secret: str) -> None:
    ring = _keyring(con)
    dek = vault.unlock(ring, secret)
    scheme = _save_device_key(dek)
    with transaction(con):
        store_keyring(con, vault.with_fingerprint(ring, dek), scheme)


def change_backup_password(con, current_secret: str, new_password: str) -> None:
    ring = _keyring(con)
    dek = vault.unlock(ring, current_secret)
    scheme = _save_device_key(dek)
    with transaction(con):
        store_keyring(con, vault.rewrap_password(ring, dek, new_password), scheme)


def regenerate_recovery_key(con, current_secret: str, new_recovery_key: str) -> None:
    ring = _keyring(con)
    dek = vault.unlock(ring, current_secret)
    settings.put(con, "backup.keyring", json.dumps(vault.rewrap_recovery(ring, dek, new_recovery_key)))


# ---------- Making backups ----------

def _snapshot(con, dest: Path) -> None:
    target = sqlite3.connect(dest)
    try:
        con.backup(target)
    finally:
        target.close()


def create_backup(con: sqlite3.Connection, kind: str = "manual", dest_dir=None, username: str | None = None,
                  plain: bool = False) -> BackupResult:
    if kind not in KINDS:
        raise BackupError(f"نوع نسخة غير معروف: {kind}")
    dest = Path(dest_dir) if dest_dir else paths.backups_dir()
    dest.mkdir(parents=True, exist_ok=True)
    started = _now_s()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    encrypted = is_encryption_ready(con) and not plain
    final = dest / f"morv_tires_{stamp}_{kind}{vault.EXT if encrypted else '.db'}"
    tmp = dest / f"{final.name}.snapshot"
    mine = {tmp.resolve(), vault.part_path(final).resolve()}
    _in_flight.update(mine)                 # Claimed before the files exist, so a sweep can never see them unclaimed
    try:
        _snapshot(con, tmp)
        ok, detail = verify_db_file(tmp)
        if not ok:
            raise BackupError(detail)
        if encrypted:
            dek = _checked_device_dek(con)
            vault.seal(tmp, final, dek, _keyring(con), {"kind": kind, "db_detail": detail})
            detail += " · مشفّرة"
        else:
            tmp.replace(final)
        result = BackupResult(True, final, final.stat().st_size, _sha256(final), detail)
    except (sqlite3.Error, OSError, BackupError, vault.VaultError) as e:
        result = BackupResult(False, None, 0, "", str(e))
        _record(con, "backup", kind, started, result, username)
        raise BackupError(f"فشلت النسخة الاحتياطية: {e}") from e
    finally:
        _discard(tmp)                       # Snapshot plus its -wal/-shm, which hold plain database pages
        _in_flight.difference_update(mine)
    _record(con, "backup", kind, started, result, username)
    if kind != "safety":
        settings.put(con, "backup.last_ok_at", _now_s())
    return result


# ---------- Listing and retention ----------

def list_backups(dest_dir=None) -> list[BackupInfo]:
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


def sweep_leftovers(dest_dir=None) -> list[Path]:
    """Removes abandoned snapshot and part files; a claimed one is spared whatever its age."""
    dest = Path(dest_dir) if dest_dir else paths.backups_dir()
    removed = []
    for p in sorted(dest.iterdir()) if dest.exists() else []:
        if not _TEMP_NAME.match(p.name) or p.resolve() in _in_flight:
            continue
        try:
            p.unlink()
        except OSError:                     # Still open, as a live writer keeps it on Windows: try again next run
            continue
        removed.append(p)
    return removed


def prune(dest_dir=None) -> list[Path]:
    backups = list_backups(dest_dir)
    keep = set()
    daily = [b for b in backups if b.kind == "daily"]
    keep.update(b.path for b in daily[:KEEP_DAILY])
    months = {}
    for b in daily:                       # Newest first, so a month's first appearance is its last backup
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


# ---------- USB drive ----------

def usb_dir(con) -> Path | None:
    v = settings.get(con, "backup.usb_dir", "")
    return Path(v) if v else None


def same_drive_as_data(folder) -> bool:
    try:
        a = Path(folder).resolve()
        b = paths.data_dir().resolve()
    except OSError:
        return False
    if a.drive or b.drive:                 # Windows: compare the drive letter
        return a.drive.upper() == b.drive.upper()
    while not a.exists() and a.parent != a:   # The folder may not exist yet, so walk up to the nearest one that does
        a = a.parent
    try:                                   # Linux: compare the device
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def usb_available(con) -> bool:
    d = usb_dir(con)
    return bool(d) and d.exists()


def off_device_state(con) -> tuple[str, str]:
    """No off-device copy is a standing gap, not a silent default: losing the disk would lose everything."""
    folder = usb_dir(con)
    if not folder:
        return "warn", "لا توجد نسخة خارج الجهاز؛ حدّد مجلد فلاشة أو قرص خارجي من الإعدادات"
    last = _parse(settings.get(con, "backup.last_usb_at", ""))
    if last is None:
        return "danger", "لم تُنقل أي نسخة خارج الجهاز بعد"
    days = (datetime.now() - last).days
    if days >= OFF_DEVICE_LOST_DAYS:
        return "danger", f"آخر نسخة خارج الجهاز منذ {ltr(str(days))} يومًا"
    if days >= OFF_DEVICE_STALE_DAYS:
        return "warn", f"آخر نسخة خارج الجهاز منذ {ltr(str(days))} يومًا"
    if same_drive_as_data(folder):
        return "warn", "مجلد النسخ الخارجي على قرص الجهاز نفسه، فيضيع معه"
    return "ok", ""


def copy_to_usb(con, username: str | None = None) -> BackupResult:
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


# ---------- Restore test and real restore ----------

def restore_test(con, backup_path, expected_sha256: str | None = None, *, secret: str | None = None,
                 username: str | None = None) -> BackupResult:
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


def _sidecars(db: Path) -> list[Path]:
    return [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]


def _discard(path: Path) -> None:
    for f in (path, vault.part_path(path), *_sidecars(path)):   # A killed restore leaves plaintext in the .part
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync(path: Path) -> None:
    with open(path, "rb+") as f:
        os.fsync(f.fileno())


def _fsync_dir(folder: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _settle_live_db(db: Path) -> bool:
    try:
        con = sqlite3.connect(db, timeout=2)
        try:
            busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        if "locked" in str(e) or "busy" in str(e):
            raise BackupError(_BUSY) from e
        return False
    except sqlite3.DatabaseError:
        return False                      # Not a readable database: keep it as is, then replace it
    wal = _sidecars(db)[0]
    if busy or (wal.exists() and wal.stat().st_size > 0):
        raise BackupError(_BUSY)
    return True


def _keep_damaged(db: Path, stamp: str) -> None:
    sdir = db.parent / "backups"
    sdir.mkdir(exist_ok=True)
    for f in (db, *_sidecars(db)):
        if f.exists():
            shutil.copy2(f, sdir / f"damaged_{stamp}_{f.name}")


def restore_file(db_path, backup_path, secret: str | None = None, dek: bytes | None = None) -> Path | None:
    db = Path(db_path)
    src = Path(backup_path)
    encrypted = src.suffix == vault.EXT
    staged = db.with_name(db.name + ".restore-new")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safety = None
    try:
        _discard(staged)                                   # Leftovers of an interrupted attempt
        ring = None
        if encrypted:
            header = vault.open_vault(src, staged, dek=dek, secret=secret)
            ring = header["keyring"]
            if dek is None:
                dek = vault.unlock(ring, secret)
        else:
            shutil.copyfile(src, staged)
        ok, detail = verify_db_file(staged)
        if not ok:
            raise BackupError(detail)
        for f in _sidecars(staged):
            f.unlink(missing_ok=True)
        _fsync(staged)
        if db.exists():
            if not _settle_live_db(db):
                _keep_damaged(db, stamp)
            elif _has_users(db):                           # A database with no users is a new device: no safety copy needed
                sdir = db.parent / "backups"
                sdir.mkdir(exist_ok=True)
                if encrypted:
                    safety = sdir / f"morv_tires_{stamp}_safety{vault.EXT}"
                    vault.seal(db, safety, dek, ring, {"kind": "safety"})
                else:
                    safety = sdir / f"morv_tires_{stamp}_safety.db"
                    shutil.copy2(db, safety)
            for f in _sidecars(db):                        # Empty after the merge, or kept beside the corrupt one
                f.unlink(missing_ok=True)
        os.replace(staged, db)
    except BaseException as e:
        _discard(staged)
        if isinstance(e, (OSError, sqlite3.Error)):
            raise BackupError(f"تعذّرت الاستعادة: {e}") from e
        raise
    _fsync_dir(db.parent)
    if encrypted:
        try:
            _save_device_key(dek)
        except BackupError as e:
            log.warning("استُعيدت البيانات لكن تعذّر حفظ مفتاح الجهاز: %s", e)
    return safety


def _has_users(db_path) -> bool:
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT count(*) FROM users").fetchone()[0] > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


# ---------- Schedule and health ----------

def _parse(ts: str) -> datetime | None:
    return datetime.strptime(ts, _TS) if ts else None


def _test_target(con) -> tuple[BackupInfo | None, str]:
    """Prefers the off-device copy: that is the one a shop restores from after losing the machine."""
    folder = usb_dir(con)
    if folder and folder.exists():
        off = next((b for b in list_backups(folder) if b.kind in ("daily", "manual")), None)
        if off:
            return off, "الفلاشة"
    return next((b for b in list_backups() if b.kind in ("daily", "manual")), None), "الجهاز"


def run_scheduled(con, username: str | None = None) -> list[str]:
    done = []
    stray = sweep_leftovers()
    if stray:
        done.append(f"تنظيف ملفات مؤقتة من عملية متوقفة: {ltr(str(len(stray)))}")
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
    if usb_available(con) and settings.get(con, "backup.last_usb_at", "")[:10] != today.isoformat():
        try:
            copy_to_usb(con, username)
            done.append("نسخة على الفلاشة")
        except BackupError as e:
            done.append(f"فشل النسخ إلى الفلاشة: {e}")
    last_test = _parse(settings.get(con, "backup.last_test_at", ""))
    if last_test is None or (datetime.now() - last_test).days >= RESTORE_TEST_DAYS:
        latest, where = _test_target(con)
        if latest:
            r = restore_test(con, latest.path, username=username)
            done.append(f"اختبار استعادة من {where}: " + ("نجح" if r.ok else f"فشل: {r.detail}"))
    return done


def health(con) -> tuple[str, str]:
    if not is_encryption_ready(con):
        return "danger", "النسخ الاحتياطي غير مُعدّ"
    key_state = device_key_state(con)
    if key_state == "mismatch":
        return "danger", "مفتاح الجهاز لا يخص هذه البيانات؛ أعد ربط الجهاز من الإعدادات"
    last_run = con.execute("SELECT status, detail FROM backup_runs WHERE action='backup' AND kind IN ('daily','manual') "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    if last_run and last_run[0] == "failed":
        return "danger", f"فشلت آخر نسخة احتياطية: {last_run[1]}"
    last_ok = _parse(settings.get(con, "backup.last_ok_at", ""))
    if last_ok is None:
        return "danger", "لم تُؤخذ نسخة احتياطية بعد"
    if datetime.now() - last_ok > timedelta(days=STALE_DAYS):
        return "danger", f"آخر نسخة احتياطية منذ {ltr(str((datetime.now() - last_ok).days))} أيام"
    off_state, off_text = off_device_state(con)
    if off_state == "danger":
        return "danger", off_text
    if key_state == "missing":
        return "warn", "مفتاح الجهاز مفقود؛ أعد ربط الجهاز من الإعدادات"
    if off_state == "warn":
        return "warn", off_text
    return "ok", f"آخر نسخة {ltr(last_ok.strftime('%Y-%m-%d %H:%M'))}"
