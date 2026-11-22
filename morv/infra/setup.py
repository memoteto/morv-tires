"""First-run setup: shop, manager account and backup encryption, all or nothing."""
import sqlite3
from datetime import datetime

from morv.core import security
from morv.infra import backup, settings, shop, users
from morv.infra.db.connection import transaction

COMPLETE_KEY = "setup.completed_at"


class SetupError(RuntimeError):
    """First-run setup could not finish, with a message fit to show."""


def is_complete(con) -> bool:
    return bool(settings.get(con, COMPLETE_KEY, ""))


def run_first_setup(con, *, shop_name: str, shop_phone: str, shop_address: str, admin_username: str,
                    admin_name: str, admin_password: str, backup_password: str, recovery_key: str,
                    usb_dir: str = "") -> users.User:
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
            shop.put(con, shop.Shop(name=name, phone=phone, address=address))
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
