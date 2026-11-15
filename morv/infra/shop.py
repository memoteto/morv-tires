"""بيانات المحل (الاسم والهاتف والعنوان والشعار) في جدول الإعدادات.

تُستخدم في ترويسة الفواتير والإيصالات لاحقًا، ولذلك تُفحص هنا مرة واحدة: الهاتف 11 رقمًا يبدأ بـ 01.
"""
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from morv.core.money import to_latin
from morv.infra import paths, settings
from morv.infra.db.connection import transaction

LOGO_FILE = "shop_logo.png"
_PHONE = re.compile(r"^01[0-9]{9}$")


@dataclass
class Shop:
    """بيانات المحل كما تُعرض وتُطبع."""
    name: str = ""
    phone: str = ""
    address: str = ""

    @property
    def logo_path(self) -> Path | None:
        """مسار الشعار إن وُجد."""
        p = paths.data_dir() / LOGO_FILE
        return p if p.exists() else None


class ShopError(ValueError):
    """بيانات محل غير صالحة، برسالة تصلح للعرض."""


def load(con) -> Shop:
    """قراءة بيانات المحل."""
    return Shop(settings.get(con, "shop.name"), settings.get(con, "shop.phone"), settings.get(con, "shop.address"))


def validate(name: str, phone: str) -> tuple[str, str]:
    """فحص الاسم والهاتف، ويعيدهما منظّفين (الأرقام العربية تتحول إلى لاتينية)."""
    name = (name or "").strip()
    phone = to_latin((phone or "").strip()).replace(" ", "")
    if not name:
        raise ShopError("اسم المحل مطلوب")
    if not _PHONE.match(phone):
        raise ShopError("رقم الهاتف 11 رقمًا يبدأ بـ 01")
    return name, phone


def check(name: str, phone: str, address: str) -> tuple[str, str, str]:
    """فحص بيانات المحل كلها قبل أي كتابة. العنوان اختياري لكنه لا يكون رقمًا وحده."""
    name, phone = validate(name, phone)
    address = (address or "").strip()
    if address and to_latin(address).isdigit():
        raise ShopError("العنوان لا يكون رقمًا فقط")
    return name, phone, address


def put(con, name: str, phone: str, address: str) -> None:
    """كتابة بيانات محل مفحوصة (داخل معاملة المتصل)."""
    settings.put(con, "shop.name", name)
    settings.put(con, "shop.phone", phone)
    settings.put(con, "shop.address", address)


def save(con, name: str, phone: str, address: str) -> Shop:
    """حفظ بيانات المحل بعد فحصها، الثلاثة معًا في معاملة واحدة."""
    values = check(name, phone, address)
    with transaction(con):
        put(con, *values)
    return load(con)


def save_logo(src_path) -> Path:
    """نسخ ملف الشعار المختار إلى مجلد البيانات (PNG) حتى لا يعتمد البرنامج على مكانه الأصلي."""
    dest = paths.data_dir() / LOGO_FILE
    shutil.copyfile(src_path, dest)
    return dest


def remove_logo() -> None:
    """حذف الشعار."""
    (paths.data_dir() / LOGO_FILE).unlink(missing_ok=True)
