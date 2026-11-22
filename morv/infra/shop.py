"""Shop data (name, phone, address, logo, tax ids and the printed footer) in the settings table."""
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from morv.core.money import to_latin
from morv.infra import paths, settings
from morv.infra.db.connection import transaction
from morv.infra.users import require

LOGO_FILE = "shop_logo.png"
_PHONE = re.compile(r"^01[0-9]{9}$")


@dataclass
class Shop:
    name: str = ""
    phone: str = ""
    address: str = ""
    tax_id: str = ""                 # printed only once the shop is registered and fills it in
    commercial_register: str = ""
    invoice_footer: str = ""         # the shop's own words under every printed document

    @property
    def logo_path(self) -> Path | None:
        p = paths.data_dir() / LOGO_FILE
        return p if p.exists() else None


class ShopError(ValueError):
    """Invalid shop data, with a message fit to show."""


def load(con) -> Shop:
    return Shop(settings.get(con, "shop.name"), settings.get(con, "shop.phone"), settings.get(con, "shop.address"),
                settings.get(con, "shop.tax_id"), settings.get(con, "shop.commercial_register"),
                settings.get(con, "shop.invoice_footer"))


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


def put(con, name: str, phone: str, address: str, tax_id: str = "", commercial_register: str = "",
        invoice_footer: str = "") -> None:
    settings.put(con, "shop.name", name)
    settings.put(con, "shop.phone", phone)
    settings.put(con, "shop.address", address)
    settings.put(con, "shop.tax_id", tax_id)
    settings.put(con, "shop.commercial_register", commercial_register)
    settings.put(con, "shop.invoice_footer", invoice_footer)


def save(con, by, name: str, phone: str, address: str, tax_id: str = "", commercial_register: str = "",
         invoice_footer: str = "") -> Shop:
    require(con, by, "settings.edit", ShopError)
    values = check(name, phone, address)
    with transaction(con):
        put(con, *values, to_latin((tax_id or "").strip()), to_latin((commercial_register or "").strip()),
            (invoice_footer or "").strip())
    return load(con)


def save_logo(src_path) -> Path:
    dest = paths.data_dir() / LOGO_FILE
    shutil.copyfile(src_path, dest)
    return dest


def remove_logo() -> None:
    (paths.data_dir() / LOGO_FILE).unlink(missing_ok=True)
