"""Shop identity, contact lines, tax ids and print preferences, stored in the settings table."""
import re
import shutil
from dataclasses import dataclass, fields
from pathlib import Path

from morv.core.money import to_latin
from morv.infra import paths, settings
from morv.infra.db.connection import transaction
from morv.infra.users import audit, require

LOGO_FILE = "shop_logo.png"
PAPERS = ("a4", "a5", "r80", "r58")
_PHONE = re.compile(r"^01[0-9]{9}$")
_IDENTITY_TEXT = ("name", "activity", "phone", "phone2", "whatsapp", "address", "hours", "website",
                  "tax_id", "commercial_register")
_IDENTITY_FLAGS = ("tax_registered",)
_PRINT_TEXT = ("thanks", "policy", "paper", "printer")
_PRINT_FLAGS = ("show_logo", "show_whatsapp", "show_hours", "show_policy")
_TEXT = _IDENTITY_TEXT + _PRINT_TEXT
_FLAGS = _IDENTITY_FLAGS + _PRINT_FLAGS


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


def _trim(data: Shop, names) -> None:
    for name in names:
        setattr(data, name, (getattr(data, name) or "").strip())


def _clean_identity(out: Shop) -> None:
    _trim(out, _IDENTITY_TEXT)
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


def _clean_print(out: Shop) -> None:
    _trim(out, _PRINT_TEXT)
    if out.paper not in PAPERS:
        out.paper = "a4"


def clean(data: Shop) -> Shop:
    """Trims every line, checks the optional numbers, and refuses tax ids without registration."""
    out = Shop(**{f.name: getattr(data, f.name) for f in fields(Shop)})
    _clean_identity(out)
    _clean_print(out)
    return out


def _put_fields(con, data: Shop, text_names, flag_names) -> None:
    for name in text_names:
        settings.put(con, f"shop.{name}", getattr(data, name))
    for name in flag_names:
        settings.put(con, f"shop.{name}", "1" if getattr(data, name) else "0")


def put(con, data: Shop) -> None:
    _put_fields(con, data, _TEXT, _FLAGS)


def save(con, by, data: Shop) -> Shop:
    require(con, by, "settings.edit", ShopError)
    data = clean(data)
    with transaction(con):
        put(con, data)
    return load(con)


def _save_section(con, by, data: Shop, cleaner, text_names, flag_names, detail: str) -> Shop:
    with transaction(con):
        require(con, by, "settings.edit", ShopError)
        draft = Shop(**{name: getattr(data, name) for name in (*text_names, *flag_names)})
        cleaner(draft)
        _put_fields(con, draft, text_names, flag_names)
        audit(con, by, "shop.save", detail=detail)
        saved = load(con)
    return saved


def save_identity(con, by, data: Shop) -> Shop:
    return _save_section(con, by, data, _clean_identity, _IDENTITY_TEXT, _IDENTITY_FLAGS, "identity")


def save_print(con, by, data: Shop) -> Shop:
    return _save_section(con, by, data, _clean_print, _PRINT_TEXT, _PRINT_FLAGS, "printing")


def save_logo(src_path) -> Path:
    dest = paths.data_dir() / LOGO_FILE
    shutil.copyfile(src_path, dest)
    return dest


def remove_logo() -> None:
    (paths.data_dir() / LOGO_FILE).unlink(missing_ok=True)
