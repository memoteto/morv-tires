"""Shop identity, contact lines, tax ids and print preferences, stored in the settings table."""
import re
import shutil
from dataclasses import dataclass, fields
from pathlib import Path

from morv.core.money import to_latin
from morv.infra import paths, settings
from morv.infra.db.connection import transaction
from morv.infra.users import require

LOGO_FILE = "shop_logo.png"
PAPERS = ("a4", "a5", "r80", "r58")
_PHONE = re.compile(r"^01[0-9]{9}$")
_TEXT = ("name", "activity", "phone", "phone2", "whatsapp", "address", "hours", "website",
         "tax_id", "commercial_register", "thanks", "policy", "paper", "printer")
_FLAGS = ("tax_registered", "show_logo", "show_whatsapp", "show_hours", "show_policy")


@dataclass
class Shop:
    name: str = ""
    activity: str = ""               # one line under the shop name on printed documents
    phone: str = ""
    phone2: str = ""
    whatsapp: str = ""
    address: str = ""
    hours: str = ""
    website: str = ""
    tax_registered: bool = False     # gates the tax ids and the VAT line on printed documents
    tax_id: str = ""
    commercial_register: str = ""
    thanks: str = ""
    policy: str = ""
    show_logo: bool = True
    show_whatsapp: bool = True
    show_hours: bool = True
    show_policy: bool = True
    paper: str = "a4"
    printer: str = ""

    @property
    def logo_path(self) -> Path | None:
        p = paths.data_dir() / LOGO_FILE
        return p if p.exists() else None

    @property
    def initial(self) -> str:
        """Stands in for a missing logo image, as the approved design does."""
        return (self.name.strip()[:1] or "م")


class ShopError(ValueError):
    """Invalid shop data, with a message fit to show."""


def load(con) -> Shop:
    out = Shop()
    for name in _TEXT:
        setattr(out, name, settings.get(con, f"shop.{name}", getattr(out, name)))
    for name in _FLAGS:
        raw = settings.get(con, f"shop.{name}", "")
        if raw:
            setattr(out, name, raw == "1")
    return out


def validate(name: str, phone: str) -> tuple[str, str]:
    name = (name or "").strip()
    phone = to_latin((phone or "").strip()).replace(" ", "")
    if not name:
        raise ShopError("اسم المحل مطلوب")
    if not _PHONE.match(phone):
        raise ShopError("رقم الهاتف 11 رقمًا يبدأ بـ 01")
    return name, phone


def check(name: str, phone: str, address: str) -> tuple[str, str, str]:
    name, phone = validate(name, phone)
    address = (address or "").strip()
    if address and to_latin(address).isdigit():
        raise ShopError("العنوان لا يكون رقمًا فقط")
    return name, phone, address


def clean(data: Shop) -> Shop:
    """Trims every line, checks the optional numbers, and refuses tax ids without registration."""
    out = Shop(**{f.name: getattr(data, f.name) for f in fields(Shop)})
    for name in _TEXT:
        setattr(out, name, (getattr(out, name) or "").strip())
    out.name, out.phone, out.address = check(out.name, out.phone, out.address)
    for name, label in (("phone2", "الهاتف الثاني"), ("whatsapp", "رقم واتساب")):
        value = to_latin(getattr(out, name)).replace(" ", "")
        if value and not _PHONE.match(value):
            raise ShopError(f"{label} 11 رقمًا يبدأ بـ 01")
        setattr(out, name, value)
    out.tax_id = to_latin(out.tax_id)
    out.commercial_register = to_latin(out.commercial_register)
    if not out.tax_registered:
        out.tax_id = out.commercial_register = ""
    if out.paper not in PAPERS:
        out.paper = "a4"
    return out


def put(con, data: Shop) -> None:
    for name in _TEXT:
        settings.put(con, f"shop.{name}", getattr(data, name))
    for name in _FLAGS:
        settings.put(con, f"shop.{name}", "1" if getattr(data, name) else "0")


def save(con, by, data: Shop) -> Shop:
    require(con, by, "settings.edit", ShopError)
    data = clean(data)
    with transaction(con):
        put(con, data)
    return load(con)


def save_logo(src_path) -> Path:
    dest = paths.data_dir() / LOGO_FILE
    shutil.copyfile(src_path, dest)
    return dest


def remove_logo() -> None:
    (paths.data_dir() / LOGO_FILE).unlink(missing_ok=True)
