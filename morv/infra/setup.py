"""الإعداد الأول للبرنامج: بيانات المحل، حساب المدير، وتشفير النسخ — معًا أو لا شيء.

لماذا ملف مستقل: الإعداد كان ثلاث خطوات منفصلة تكتب كل واحدة منها وحدها، فإذا فشلت الثالثة
بقي مدير بلا تشفير، وعدّ البرنامج نفسه مُعدًّا لأن فيه مستخدمًا. الآن:

- علامة صريحة في الإعدادات (setup.completed_at) هي التي تقول إن الإعداد اكتمل، لا عدد المستخدمين.
- كل ما يُكتب في القاعدة يُكتب في معاملة واحدة تنتهي بالعلامة نفسها.
- الشيء الوحيد خارج القاعدة (مفتاح الجهاز) يُحفظ ويُتحقق منه قبل المعاملة؛ فإن فشل لم يُكتب
  شيء، وإن فشلت المعاملة بعده فالمحاولة التالية تكتب مفتاحًا جديدًا فوقه.
- قاعدة قديمة انقطع إعدادها وفيها مدير: المحاولة التالية تعيد استعمال صفه ولا تنشئ مديرًا ثانيًا.

النسخة الاحتياطية الأولى ليست جزءًا من الإعداد: تؤخذ بعده، وفشلها يظهر في تنبيه النسخ ولا
يلغي إعدادًا مكتملًا.
"""
import sqlite3
from datetime import datetime

from morv.core import security
from morv.infra import backup, settings, shop, users
from morv.infra.db.connection import transaction

COMPLETE_KEY = "setup.completed_at"


class SetupError(RuntimeError):
    """تعذّر إكمال الإعداد الأول، برسالة تصلح للعرض."""


def is_complete(con) -> bool:
    """هل اكتمل الإعداد الأول؟ (العلامة الصريحة فقط.)"""
    return bool(settings.get(con, COMPLETE_KEY, ""))


def run_first_setup(con, *, shop_name: str, shop_phone: str, shop_address: str, admin_username: str,
                    admin_name: str, admin_password: str, backup_password: str, recovery_key: str,
                    usb_dir: str = "") -> users.User:
    """تنفيذ الإعداد الأول كله، ويعيد حساب المدير.

    كل المدخلات تُفحص قبل أي كتابة، فترفع ShopError أو AuthError أو SetupError برسالة للمستخدم.
    فشل حفظ مفتاح الجهاز يرفع BackupError ولا يُكتب شيء في القاعدة.
    """
    if is_complete(con):
        raise SetupError("البرنامج مُعدّ من قبل")
    name, phone, address = shop.check(shop_name, shop_phone, shop_address)
    username, pw_hash = users.check_new_user(admin_username, admin_name, admin_password, "manager")
    reason = security.check_backup_password(backup_password)
    if reason:
        raise SetupError(reason)
    try:
        security.normalize_recovery_key(recovery_key)
    except ValueError as e:
        raise SetupError(f"مفتاح الاسترداد غير صالح: {e}") from e
    ring, scheme = backup.prepare_keyring(backup_password, recovery_key)
    try:
        with transaction(con):
            shop.put(con, name, phone, address)
            uid = users.put_first_manager(con, username, admin_name.strip(), pw_hash)
            backup.store_keyring(con, ring, scheme)
            if usb_dir.strip():
                settings.put(con, "backup.usb_dir", usb_dir.strip())
            admin = users.get_user(con, uid)
            users.audit(con, admin, "setup.done", None, None, "اكتمل الإعداد الأول")
            settings.put(con, COMPLETE_KEY, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except sqlite3.Error as e:
        raise SetupError(f"تعذّر حفظ الإعداد ولم يُكتب منه شيء: {e}") from e
    return admin
