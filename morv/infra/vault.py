"""Encrypted backup vault (.mtb file)."""
import base64
import binascii
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
_KDF = dict(n=2 ** 15, r=8, p=1)   # About 100-200ms on the shop machine; stored in the header to reopen later
_MAX_HEADER = 64 * 1024            # A real header is under 2KB; anything longer means a corrupt file
_GCM_TAG = 16                      # The shortest possible body is the GCM tag alone


class VaultError(RuntimeError):
    """Corrupt file, not a Morv Tires backup, or a wrong password or key."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError, TypeError) as e:
        raise VaultError("رأس الملف تالف") from e


def fingerprint(dek: bytes) -> str:
    return hashlib.sha256(b"morv_tires_dek_fingerprint_v1" + dek).hexdigest()[:32]


def _check_ring(ring) -> dict:
    try:
        kdf = ring["kdf"]
        ok = all(isinstance(kdf[k], int) and kdf[k] > 0 for k in ("n", "r", "p"))
        ok = ok and all(isinstance(ring[w][k], str) for w in ("pw", "rk") for k in ("salt", "nonce", "wrapped"))
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise VaultError("رأس الملف تالف")
    return ring


def _derive(secret: str, salt: bytes, kdf: dict) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, dklen=32,
                          n=kdf["n"], r=kdf["r"], p=kdf["p"], maxmem=256 * 1024 * 1024)


def _wrap(dek: bytes, secret: str, kdf: dict) -> dict:
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    kek = _derive(secret, salt, kdf)
    return {"salt": _b64(salt), "nonce": _b64(nonce), "wrapped": _b64(AESGCM(kek).encrypt(nonce, dek, b"dek"))}


def _unwrap(entry: dict, secret: str, kdf: dict) -> bytes:
    try:
        kek = _derive(secret, _unb64(entry["salt"]), kdf)
    except (ValueError, MemoryError) as e:      # scrypt parameters were tampered with
        raise VaultError("رأس الملف تالف") from e
    try:
        return AESGCM(kek).decrypt(_unb64(entry["nonce"]), _unb64(entry["wrapped"]), b"dek")
    except InvalidTag as e:
        raise VaultError("كلمة المرور أو مفتاح الاسترداد غير صحيح") from e
    except ValueError as e:                     # wrong nonce length
        raise VaultError("رأس الملف تالف") from e


# ---------- Keyring ----------

def new_keyring(password: str, recovery_key: str) -> tuple[bytes, dict]:
    dek = secrets.token_bytes(32)
    ring = {
        "kdf": dict(_KDF),
        "pw": _wrap(dek, password, _KDF),
        "rk": _wrap(dek, normalize_recovery_key(recovery_key), _KDF),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fp": fingerprint(dek),
    }
    return dek, ring


def with_fingerprint(ring: dict, dek: bytes) -> dict:
    out = dict(ring)
    out["fp"] = fingerprint(dek)
    return out


def matches(ring: dict, dek: bytes) -> bool | None:
    fp = ring.get("fp")
    if not fp:
        return None
    return secrets.compare_digest(fp, fingerprint(dek))


def rewrap_password(ring: dict, dek: bytes, new_password: str) -> dict:
    out = with_fingerprint(ring, dek)
    out["pw"] = _wrap(dek, new_password, ring["kdf"])
    return out


def rewrap_recovery(ring: dict, dek: bytes, new_recovery_key: str) -> dict:
    out = with_fingerprint(ring, dek)
    out["rk"] = _wrap(dek, normalize_recovery_key(new_recovery_key), ring["kdf"])
    return out


def unlock(ring: dict, secret: str) -> bytes:
    _check_ring(ring)
    try:
        return _unwrap(ring["pw"], secret, ring["kdf"])
    except VaultError:
        pass
    try:
        rk = normalize_recovery_key(secret)
    except ValueError as e:
        raise VaultError("كلمة المرور أو مفتاح الاسترداد غير صحيح") from e
    return _unwrap(ring["rk"], rk, ring["kdf"])


# ---------- Reading and writing the file ----------

def seal(plain_path, out_path, dek: bytes, ring: dict, meta: dict | None = None) -> dict:
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


def _read_header(f) -> tuple[dict, bytes]:
    if f.read(len(MAGIC)) != MAGIC:
        raise VaultError("الملف ليس نسخة احتياطية من Morv Tires")
    raw_len = f.read(4)
    if len(raw_len) != 4:
        raise VaultError("ملف النسخة مقطوع (ناقص)")
    (n,) = struct.unpack(">I", raw_len)
    if n == 0 or n > _MAX_HEADER:
        raise VaultError("رأس الملف تالف")
    hdr = f.read(n)
    if len(hdr) != n:
        raise VaultError("ملف النسخة مقطوع (ناقص)")
    try:
        header = json.loads(hdr.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise VaultError("رأس الملف تالف") from e
    if not isinstance(header, dict) or header.get("app_id") != APP_ID:
        raise VaultError("الملف ليس نسخة احتياطية من Morv Tires")
    _check_ring(header.get("keyring"))
    if not isinstance(header.get("body_nonce"), str) or not isinstance(header.get("plain_sha256"), str):
        raise VaultError("رأس الملف تالف")
    return header, hdr


def read_header(vault_path) -> dict:
    try:
        with open(vault_path, "rb") as f:
            return _read_header(f)[0]
    except OSError as e:
        raise VaultError(f"تعذّرت قراءة ملف النسخة: {e}") from e


def opens_with(vault_path, dek: bytes) -> bool:
    try:
        with open(vault_path, "rb") as f:
            header, hdr = _read_header(f)
            body = f.read()
        AESGCM(dek).decrypt(_unb64(header["body_nonce"]), body, hdr)
    except (OSError, VaultError, InvalidTag, ValueError):
        return False
    return True


def open_vault(vault_path, out_plain_path, *, dek: bytes | None = None, secret: str | None = None) -> dict:
    try:
        with open(vault_path, "rb") as f:
            header, hdr = _read_header(f)
            body = f.read()
    except OSError as e:
        raise VaultError(f"تعذّرت قراءة ملف النسخة: {e}") from e
    if len(body) < _GCM_TAG:
        raise VaultError("ملف النسخة مقطوع (ناقص)")
    if dek is None:
        if secret is None:
            raise VaultError("مطلوب كلمة المرور أو مفتاح الاسترداد")
        dek = unlock(header["keyring"], secret)
    try:
        data = AESGCM(dek).decrypt(_unb64(header["body_nonce"]), body, hdr)
    except InvalidTag as e:
        raise VaultError("النسخة تالفة أو عُبث بها، أو المفتاح لا يخصها") from e
    except ValueError as e:
        raise VaultError("رأس الملف تالف") from e
    if hashlib.sha256(data).hexdigest() != header["plain_sha256"]:
        raise VaultError("بصمة المحتوى لا تطابق الرأس")
    Path(out_plain_path).write_bytes(data)
    return header
