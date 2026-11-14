"""صندوق النسخة الاحتياطية المشفّرة (ملف .mtb = Morv Tires Backup).

الفكرة بقفلين ومفتاح واحد:
- مفتاح البيانات (DEK): 32 بايت عشوائية هو الذي يشفّر ملف قاعدة البيانات بـ AES-256-GCM.
  GCM يكشف أي تلاعب أو تلف في الملف عند الفتح.
- يُحفظ DEK داخل كل ملف نسخة مرتين، مغلّفًا:
  (أ) بمفتاح مشتق من كلمة مرور النسخ (scrypt بطيء عمدًا ضد التخمين).
  (ب) بمفتاح مشتق من مفتاح الاسترداد (24 رمزًا عشوائيًا).
  فأي نسخة تُفتح على أي جهاز بكلمة المرور أو بمفتاح الاسترداد، ولا تحتاج الجهاز القديم.

شكل الملف: MAGIC ثم طول الرأس (4 بايت) ثم الرأس JSON ثم جسم مشفّر واحد.
الرأس يدخل في حساب GCM كبيانات مصادقة (AAD) فلا يمكن تعديله دون كشف.

«حلقة المفاتيح» (keyring): الجزء من الرأس الذي يحمل التغليفين، تُحفظ في الإعدادات
وتُعاد كتابتها في كل نسخة جديدة. تغيير كلمة المرور = تغليف جديد لنفس DEK.
"""
import base64
import hashlib
import json
import os
import secrets
import struct
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from morv import APP_ID
from morv.core.security import normalize_recovery_key

MAGIC = b"MORVBK1\n"
EXT = ".mtb"
_KDF = dict(n=2 ** 15, r=8, p=1)   # نحو 100-200ms على جهاز المحل؛ يُحفظ في الرأس لفتحه لاحقًا


class VaultError(RuntimeError):
    """ملف تالف، أو ليس نسخة Morv Tires، أو كلمة مرور/مفتاح خطأ."""


def _b64(b: bytes) -> str:
    """بايتات إلى نص للرأس."""
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    """نص الرأس إلى بايتات."""
    return base64.b64decode(s)


def _derive(secret: str, salt: bytes, kdf: dict) -> bytes:
    """مفتاح تغليف 32 بايت من سر نصي (كلمة مرور أو مفتاح استرداد) بـ scrypt."""
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, dklen=32,
                          n=kdf["n"], r=kdf["r"], p=kdf["p"], maxmem=256 * 1024 * 1024)


def _wrap(dek: bytes, secret: str, kdf: dict) -> dict:
    """تغليف DEK بسر: ملح جديد + nonce جديد + النص المشفر."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    kek = _derive(secret, salt, kdf)
    return {"salt": _b64(salt), "nonce": _b64(nonce), "wrapped": _b64(AESGCM(kek).encrypt(nonce, dek, b"dek"))}


def _unwrap(entry: dict, secret: str, kdf: dict) -> bytes:
    """فك تغليف DEK بسر. يرفع VaultError إن كان السر خطأ."""
    kek = _derive(secret, _unb64(entry["salt"]), kdf)
    try:
        return AESGCM(kek).decrypt(_unb64(entry["nonce"]), _unb64(entry["wrapped"]), b"dek")
    except InvalidTag as e:
        raise VaultError("كلمة المرور أو مفتاح الاسترداد غير صحيح") from e


# ---------- حلقة المفاتيح ----------

def new_keyring(password: str, recovery_key: str) -> tuple[bytes, dict]:
    """إنشاء DEK جديد وتغليفه بكلمة المرور وبمفتاح الاسترداد. يعيد (DEK، حلقة المفاتيح)."""
    dek = secrets.token_bytes(32)
    ring = {
        "kdf": dict(_KDF),
        "pw": _wrap(dek, password, _KDF),
        "rk": _wrap(dek, normalize_recovery_key(recovery_key), _KDF),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return dek, ring


def rewrap_password(ring: dict, dek: bytes, new_password: str) -> dict:
    """حلقة جديدة بنفس DEK وكلمة مرور جديدة (مفتاح الاسترداد كما هو)."""
    out = dict(ring)
    out["pw"] = _wrap(dek, new_password, ring["kdf"])
    return out


def rewrap_recovery(ring: dict, dek: bytes, new_recovery_key: str) -> dict:
    """حلقة جديدة بنفس DEK ومفتاح استرداد جديد (كلمة المرور كما هي)."""
    out = dict(ring)
    out["rk"] = _wrap(dek, normalize_recovery_key(new_recovery_key), ring["kdf"])
    return out


def unlock(ring: dict, secret: str) -> bytes:
    """استخراج DEK من حلقة بكلمة المرور أو بمفتاح الاسترداد (يجرّب الاثنين)."""
    try:
        return _unwrap(ring["pw"], secret, ring["kdf"])
    except VaultError:
        pass
    try:
        rk = normalize_recovery_key(secret)
    except ValueError as e:
        raise VaultError("كلمة المرور أو مفتاح الاسترداد غير صحيح") from e
    return _unwrap(ring["rk"], rk, ring["kdf"])


# ---------- كتابة الملف وقراءته ----------

def seal(plain_path, out_path, dek: bytes, ring: dict, meta: dict | None = None) -> dict:
    """تشفير ملف قاعدة بيانات إلى ملف .mtb. يعيد الرأس المكتوب.

    يُكتب أولًا إلى .part ثم يُعاد تسميته، فلا يبقى ملف ناقص باسم نهائي لو انقطعت الكهرباء.
    """
    src = Path(plain_path)
    out = Path(out_path)
    data = src.read_bytes()
    header = {
        "app_id": APP_ID,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plain_size": len(data),
        "plain_sha256": hashlib.sha256(data).hexdigest(),
        "keyring": ring,
        "body_nonce": _b64(secrets.token_bytes(12)),
    }
    header.update(meta or {})
    hdr = json.dumps(header, ensure_ascii=False, sort_keys=True).encode("utf-8")
    body = AESGCM(dek).encrypt(_unb64(header["body_nonce"]), data, hdr)
    part = out.with_suffix(out.suffix + ".part")
    with open(part, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack(">I", len(hdr)))
        f.write(hdr)
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    part.replace(out)
    return header


def read_header(vault_path) -> dict:
    """قراءة رأس ملف .mtb دون فك التشفير (للعرض في القائمة والتحقق أنه ملفنا)."""
    p = Path(vault_path)
    with open(p, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise VaultError("الملف ليس نسخة احتياطية من Morv Tires")
        (n,) = struct.unpack(">I", f.read(4))
        try:
            header = json.loads(f.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise VaultError("رأس الملف تالف") from e
    if header.get("app_id") != APP_ID:
        raise VaultError("الملف ليس نسخة احتياطية من Morv Tires")
    return header


def open_vault(vault_path, out_plain_path, *, dek: bytes | None = None, secret: str | None = None) -> dict:
    """فك ملف .mtb إلى ملف قاعدة بيانات عادي. يعيد الرأس.

    يُفتح إما بـ DEK مباشرة (مفتاح الجهاز للعمليات التلقائية) أو بسر (كلمة المرور أو
    مفتاح الاسترداد). أي تلف أو تلاعب في الملف يرفع VaultError قبل كتابة أي شيء.
    """
    p = Path(vault_path)
    with open(p, "rb") as f:
        if f.read(len(MAGIC)) != MAGIC:
            raise VaultError("الملف ليس نسخة احتياطية من Morv Tires")
        (n,) = struct.unpack(">I", f.read(4))
        hdr = f.read(n)
        body = f.read()
    try:
        header = json.loads(hdr.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise VaultError("رأس الملف تالف") from e
    if header.get("app_id") != APP_ID:
        raise VaultError("الملف ليس نسخة احتياطية من Morv Tires")
    if dek is None:
        if secret is None:
            raise VaultError("مطلوب كلمة المرور أو مفتاح الاسترداد")
        dek = unlock(header["keyring"], secret)
    try:
        data = AESGCM(dek).decrypt(_unb64(header["body_nonce"]), body, hdr)
    except InvalidTag as e:
        raise VaultError("النسخة تالفة أو عُبث بها، أو المفتاح لا يخصها") from e
    if hashlib.sha256(data).hexdigest() != header["plain_sha256"]:
        raise VaultError("بصمة المحتوى لا تطابق الرأس")
    Path(out_plain_path).write_bytes(data)
    return header
