"""قواعد الأمان: كلمات المرور، ومفتاح الاسترداد، والأدوار والصلاحيات.

هذه الطبقة حسابات فقط (بلا قاعدة بيانات ولا واجهة)، لذلك يمكن اختبارها بالكامل بلا Qt.

كلمات مرور الدخول تُحفظ ببصمة scrypt من المكتبة القياسية (لا مكتبات إضافية)، فلا يمكن
استرجاع كلمة المرور من البصمة حتى لمن يملك قاعدة البيانات.
"""
import hashlib
import hmac
import secrets

# ---------- الأدوار والصلاحيات ----------

ROLES = {"manager": "مدير", "cashier": "كاشير"}

# الصلاحيات المفعّلة حاليًا فقط؛ كل مرحلة تضيف صلاحيات شاشاتها عند بنائها.
PERMISSIONS = {
    "customers.manage": "إضافة وتعديل العملاء",
    "suppliers.manage": "إضافة وتعديل الموردين",
    "items.manage": "إضافة وتعديل الأصناف",
    "items.prices": "تعديل أسعار البيع",
    "items.cost_view": "رؤية التكلفة والهامش",
    "purchases.post": "ترحيل فواتير الشراء واستلام البضاعة",
    "sales.post": "ترحيل فواتير البيع",
    "sales.return": "ترحيل مرتجعات البيع",
    "stock.pick_lot": "اختيار دفعة DOT غير الأقدم عند الصرف",
    "users.manage": "المستخدمون والصلاحيات",
    "settings.edit": "الإعدادات العامة",
    "backup.restore": "الاستعادة من نسخة احتياطية",
}

# الصلاحيات الافتراضية لكل دور (قالب يُطبَّق عند إنشاء المستخدم ويمكن تعديله بعدها)
ROLE_DEFAULTS = {
    "manager": set(PERMISSIONS),
    "cashier": {"customers.manage", "sales.post"},
}


def role_permissions(role: str) -> set:
    """الصلاحيات الافتراضية لدور. المدير يملك كل شيء دائمًا."""
    if role not in ROLES:
        raise ValueError(f"دور غير معروف: {role}")
    return set(ROLE_DEFAULTS[role])


# ---------- كلمات مرور الدخول ----------

MIN_LOGIN_PASSWORD = 8
_SCRYPT = dict(n=2 ** 14, r=8, p=1)   # كلفة محسوبة لتكون أقل من 100ms على جهاز المحل


def password_strength(pw: str) -> int:
    """قوة كلمة المرور من 0 إلى 4: الطول 8، وفيها رقم، وفيها حرف، والطول 12."""
    pw = pw or ""
    return ((len(pw) >= 8) + any(c.isdigit() for c in pw)
            + any(c.isalpha() for c in pw) + (len(pw) >= 12))


def hash_password(pw: str) -> str:
    """بصمة كلمة مرور جديدة: scrypt مع ملح عشوائي، بصيغة نصية تُحفظ في القاعدة."""
    if len(pw or "") < MIN_LOGIN_PASSWORD:
        raise ValueError(f"كلمة المرور {MIN_LOGIN_PASSWORD} أحرف على الأقل")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pw.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${salt.hex()}${digest.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """هل كلمة المرور تطابق البصمة المحفوظة؟ المقارنة بزمن ثابت."""
    try:
        algo, n, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt((pw or "").encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=_SCRYPT["r"], p=_SCRYPT["p"], dklen=32)
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except (ValueError, AttributeError):
        return False


# ---------- كلمة مرور النسخ الاحتياطي ومفتاح الاسترداد ----------

MIN_BACKUP_PASSWORD = 12
MAX_LOGIN_FAILURES = 5
LOCK_MINUTES = 5

# أبجدية مفتاح الاسترداد: بلا 0/O/1/I حتى لا تلتبس عند الكتابة باليد
_RK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RK_GROUPS = 6
RK_GROUP_LEN = 4


def new_recovery_key() -> str:
    """مفتاح استرداد عشوائي من 24 رمزًا في 6 مجموعات: XXXX-XXXX-...، يظهر للمالك مرة واحدة."""
    chars = [secrets.choice(_RK_ALPHABET) for _ in range(RK_GROUPS * RK_GROUP_LEN)]
    return "-".join("".join(chars[i:i + RK_GROUP_LEN]) for i in range(0, len(chars), RK_GROUP_LEN))


def normalize_recovery_key(text: str) -> str:
    """توحيد ما كتبه المستخدم: حذف الفراغات والشرطات، وحروف كبيرة، وتصحيح 0→O و1→I.

    يعيد المفتاح بصيغته القياسية أو يرفع ValueError إن كان طوله خطأ.
    """
    raw = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    raw = raw.replace("0", "O").replace("1", "I")
    if len(raw) != RK_GROUPS * RK_GROUP_LEN or any(c not in _RK_ALPHABET for c in raw):
        raise ValueError("مفتاح الاسترداد 24 رمزًا من الحروف والأرقام")
    return "-".join(raw[i:i + RK_GROUP_LEN] for i in range(0, len(raw), RK_GROUP_LEN))


def check_backup_password(pw: str) -> str | None:
    """سبب رفض كلمة مرور النسخ، أو None إن كانت مقبولة: 12 حرفًا، وفيها حرف ورقم."""
    pw = pw or ""
    if len(pw) < MIN_BACKUP_PASSWORD:
        return f"كلمة مرور النسخ {MIN_BACKUP_PASSWORD} حرفًا على الأقل"
    if not any(c.isdigit() for c in pw) or not any(c.isalpha() for c in pw):
        return "يجب أن تحتوي على حروف وأرقام معًا"
    return None
