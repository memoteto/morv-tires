"""Suppliers (database layer): unique mobile, deactivate instead of delete."""
import sqlite3
from dataclasses import dataclass

from morv.core import validate
from morv.core.text import norm_ar
from morv.infra.db.connection import transaction
from morv.infra.users import audit, require


SUPPLIES = {"tires": "إطارات", "batteries": "بطاريات", "both": "إطارات وبطاريات", "other": "أخرى"}
PAY_METHODS = {"cash": "نقدي", "transfer": "تحويل بنكي", "wallet": "InstaPay / محفظة"}
VAT_STATUSES = {"unknown": "غير محدد", "unregistered": "غير مسجل", "registered": "مسجل"}


class SupplierError(ValueError):
    """Supplier data refused, with a message fit to show."""


@dataclass
class Supplier:
    id: int | None
    name: str
    mobile: str
    phone2: str = ""
    contact_person: str = ""
    address: str = ""
    tax_id: str = ""
    notes: str = ""
    active: bool = True
    payment_days: int = 0
    credit_limit: int = 0
    supplies: str = "both"
    pay_method: str = "cash"
    commercial_reg: str = ""
    bank_info: str = ""
    vat_status: str = "unknown"
    vat_prices_inclusive: bool = False

    @property
    def vat_mode(self) -> str:
        if self.vat_status == "registered":
            return "inclusive" if self.vat_prices_inclusive else "exclusive"
        return "none" if self.vat_status == "unregistered" else "unknown"


def _clean(s: Supplier) -> Supplier:
    s.name = (s.name or "").strip()
    if not s.name:
        raise SupplierError("اسم المورد مطلوب")
    try:
        s.mobile = validate.mobile(s.mobile)
        s.phone2 = validate.mobile(s.phone2, required=False)
    except validate.ValidationError as e:
        raise SupplierError(str(e)) from e
    if s.supplies not in SUPPLIES or s.pay_method not in PAY_METHODS:
        raise SupplierError("قيمة غير معروفة في «يورّد لنا» أو طريقة السداد")
    if s.vat_status not in VAT_STATUSES or type(s.vat_prices_inclusive) is not bool:
        raise SupplierError("حالة التسجيل أو طريقة أسعار الضريبة غير صحيحة")
    if s.payment_days < 0 or s.credit_limit < 0:
        raise SupplierError("مدة السداد وحد الائتمان لا يكونان سالبين")
    return s


def _duplicate(con, mobile: str, excluding: int | None) -> str | None:
    r = con.execute("SELECT name FROM suppliers WHERE mobile=? AND id IS NOT ?", (mobile, excluding)).fetchone()
    return r[0] if r else None


def create(con, by, s: Supplier) -> Supplier:
    require(con, by, "suppliers.manage", SupplierError)
    _clean(s)
    dup = _duplicate(con, s.mobile, None)
    if dup:
        raise SupplierError(f"يوجد مورد مسجّل بنفس الرقم: {dup}")
    with transaction(con):
        cur = con.execute(
            "INSERT INTO suppliers (name, name_norm, mobile, phone2, contact_person, address, tax_id, notes, payment_days, "
            "credit_limit, supplies, pay_method, commercial_reg, bank_info, vat_status, vat_prices_inclusive) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.name, norm_ar(s.name), s.mobile, s.phone2 or None, s.contact_person.strip() or None,
             s.address.strip() or None, s.tax_id.strip() or None, s.notes.strip() or None, s.payment_days,
             s.credit_limit, s.supplies, s.pay_method, s.commercial_reg.strip() or None, s.bank_info.strip() or None,
             s.vat_status, int(s.vat_prices_inclusive)))
        s.id = cur.lastrowid
        audit(con, by, "supplier.create", "supplier", s.id, f"{s.name} · {s.mobile}")
    return get(con, s.id)


def update(con, by, s: Supplier) -> Supplier:
    require(con, by, "suppliers.manage", SupplierError)
    _clean(s)
    dup = _duplicate(con, s.mobile, s.id)
    if dup:
        raise SupplierError(f"يوجد مورد مسجّل بنفس الرقم: {dup}")
    with transaction(con):
        con.execute(
            "UPDATE suppliers SET name=?, name_norm=?, mobile=?, phone2=?, contact_person=?, address=?, tax_id=?, notes=?, "
            "payment_days=?, credit_limit=?, supplies=?, pay_method=?, commercial_reg=?, bank_info=?, "
            "vat_status=?, vat_prices_inclusive=?, "
            "updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE id=?",
            (s.name, norm_ar(s.name), s.mobile, s.phone2 or None, s.contact_person.strip() or None,
             s.address.strip() or None, s.tax_id.strip() or None, s.notes.strip() or None, s.payment_days,
             s.credit_limit, s.supplies, s.pay_method, s.commercial_reg.strip() or None, s.bank_info.strip() or None,
             s.vat_status, int(s.vat_prices_inclusive), s.id))
        audit(con, by, "supplier.update", "supplier", s.id, s.name)
    return get(con, s.id)


def set_active(con, by, supplier_id: int, active: bool) -> None:
    require(con, by, "suppliers.manage", SupplierError)
    with transaction(con):
        con.execute("UPDATE suppliers SET active=? WHERE id=?", (int(active), supplier_id))
        audit(con, by, "supplier.activate" if active else "supplier.deactivate", "supplier", supplier_id)


def has_documents(con, supplier_id: int) -> bool:
    return con.execute(
        "SELECT EXISTS(SELECT 1 FROM purchase_invoices WHERE supplier_id=?) "
        "OR EXISTS(SELECT 1 FROM treasury_movements WHERE supplier_id=?)", (supplier_id, supplier_id)).fetchone()[0] == 1


def delete(con, by, supplier_id: int) -> None:
    require(con, by, "suppliers.manage", SupplierError)
    if has_documents(con, supplier_id):
        raise SupplierError("للمورد مستندات مسجّلة؛ أوقفه بدل حذفه")
    s = get(con, supplier_id)
    with transaction(con):
        con.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
        audit(con, by, "supplier.delete", "supplier", supplier_id, f"{s.name} · {s.mobile}")


def get(con, supplier_id: int) -> Supplier:
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    con.row_factory = None
    if not r:
        raise SupplierError("المورد غير موجود")
    return Supplier(r["id"], r["name"], r["mobile"], r["phone2"] or "", r["contact_person"] or "",
                    r["address"] or "", r["tax_id"] or "", r["notes"] or "", bool(r["active"]),
                    r["payment_days"], r["credit_limit"], r["supplies"], r["pay_method"],
                    r["commercial_reg"] or "", r["bank_info"] or "", r["vat_status"], bool(r["vat_prices_inclusive"]))


def search(con, query: str = "", include_inactive: bool = False, limit: int = 300, offset: int = 0) -> list[tuple]:
    q = (query or "").strip()
    where, params = [], []
    if not include_inactive:
        where.append("active=1")
    if q:
        digits = "".join(ch for ch in validate.to_latin(q) if ch.isdigit())
        where.append("(name_norm LIKE ? OR (? <> '' AND mobile LIKE ?))")
        params += [f"%{norm_ar(q)}%", digits, f"%{digits}%"]
    sql = ("SELECT id, name, mobile, contact_person, active, payment_days, supplies, address FROM suppliers" +
           (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY name_norm LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def count(con) -> int:
    return con.execute("SELECT count(*) FROM suppliers WHERE active=1").fetchone()[0]
