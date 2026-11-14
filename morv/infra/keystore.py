"""حفظ مفتاح البيانات (DEK) على الجهاز حتى يعمل النسخ اليومي دون طلب كلمة المرور.

على Windows يُحمى المفتاح بخدمة DPAPI المدمجة: لا يفكه إلا نفس حساب Windows على نفس
الجهاز. على Linux (التطوير والاختبار) يُحفظ في ملف بصلاحيات المالك فقط، وهذا بديل أضعف
ويُكتب في السجل أنه بديل.

إن ضاع الملف (إعادة تثبيت Windows مثلًا) يطلب البرنامج كلمة مرور النسخ مرة واحدة
ويستخرج DEK من حلقة المفاتيح المحفوظة في الإعدادات، ثم يعيد حفظه هنا.
"""
import base64
import ctypes
import json
import os
import sys
from pathlib import Path

from morv.infra import paths

FILE_NAME = "device.key"
_ENTROPY = b"morv_tires_device_key_v1"


class KeystoreError(RuntimeError):
    """تعذّر حفظ مفتاح الجهاز أو قراءته."""


def key_path() -> Path:
    """مسار ملف مفتاح الجهاز داخل مجلد البيانات."""
    return paths.data_dir() / FILE_NAME


def _dpapi(data: bytes, protect: bool) -> bytes:
    """استدعاء CryptProtectData / CryptUnprotectData من Windows."""
    class Blob(ctypes.Structure):
        """بنية DATA_BLOB كما في واجهة Windows."""
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> Blob:
        """تحويل بايتات بايثون إلى DATA_BLOB."""
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    inp, ent, out = blob(data), blob(_ENTROPY), Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
        raise KeystoreError("فشل DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def save_dek(dek: bytes) -> str:
    """حفظ DEK على الجهاز. يعيد اسم الطريقة المستخدمة: 'dpapi' أو 'file'."""
    if sys.platform.startswith("win"):
        scheme, payload = "dpapi", _dpapi(dek, True)
    else:
        scheme, payload = "file", dek
    p = key_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"scheme": scheme, "dek": base64.b64encode(payload).decode("ascii")}),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)
    return scheme


def load_dek() -> bytes | None:
    """قراءة DEK من الجهاز، أو None إن لم يكن محفوظًا أو تعذّر فكه (حساب Windows آخر)."""
    p = key_path()
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
        payload = base64.b64decode(rec["dek"])
        if rec["scheme"] == "dpapi":
            return _dpapi(payload, False)
        return payload
    except (OSError, ValueError, KeyError, KeystoreError):
        return None


def forget_dek() -> None:
    """حذف مفتاح الجهاز (عند الاستعادة على جهاز آخر أو إعادة الإعداد)."""
    key_path().unlink(missing_ok=True)
