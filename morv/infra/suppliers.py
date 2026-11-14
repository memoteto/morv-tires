"""الموردون (طبقة قاعدة البيانات). نفس قواعد العملاء: موبايل فريد، إيقاف بدل الحذف، بحث موحّد."""
import sqlite3
from dataclasses import dataclass

from morv.core import validate
from morv.core.text import norm_ar
from morv.infra.users import audit


class SupplierError(ValueError):
    """بيانات مورد مرفوضة، برسالة تصلح للعرض."""


@dataclass
class Supplier:
    """مورد."""
    id: int | None
    name: str
    mobile: str
    phone2: str = ""
    contact_person: str = ""
    address: str = ""
    tax_id: str = ""
    notes: str = ""
    active: bool = True


def _clean(s: Supplier) -> Supplier:
    """فحص وتنظيف قبل الحفظ."""
    s.name = (s.name or "").strip()
    if not s.name:
        raise SupplierError("اسم المورد مطلوب")
    try:
        s.mobile = validate.mobile(s.mobile)
        s.phone2 = validate.mobile(s.phone2, required=False)
    except validate.ValidationError as e:
        raise SupplierError(str(e)) from e
    return s


def _duplicate(con, mobile: str, excluding: int | None) -> str | None:
    """اسم المورد صاحب نفس الموبايل، أو None."""
    r = con.execute("SELECT name FROM suppliers WHERE mobile=? AND id IS NOT ?", (mobile, excluding)).fetchone()
    return r[0] if r else None


def create(con, by, s: Supplier) -> Supplier:
    """إنشاء مورد."""
    _clean(s)
    dup = _duplicate(con, s.mobile, None)
    if dup:
        raise SupplierError(f"يوجد مورد مسجّل بنفس الرقم: {dup}")
    cur = con.execute(
        "INSERT INTO suppliers (name, name_norm, mobile, phone2, contact_person, address, tax_id, notes) VALUES (?,?,?,?,?,?,?,?)",
        (s.name, norm_ar(s.name), s.mobile, s.phone2 or None, s.contact_person.strip() or None,
         s.address.strip() or None, s.tax_id.strip() or None, s.notes.strip() or None))
    s.id = cur.lastrowid
    audit(con, by, "supplier.create", "supplier", s.id, f"{s.name} · {s.mobile}")
    return get(con, s.id)


def update(con, by, s: Supplier) -> Supplier:
    """تعديل مورد."""
    _clean(s)
    dup = _duplicate(con, s.mobile, s.id)
    if dup:
        raise SupplierError(f"يوجد مورد مسجّل بنفس الرقم: {dup}")
    con.execute(
        "UPDATE suppliers SET name=?, name_norm=?, mobile=?, phone2=?, contact_person=?, address=?, tax_id=?, notes=?, "
        "updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE id=?",
        (s.name, norm_ar(s.name), s.mobile, s.phone2 or None, s.contact_person.strip() or None,
         s.address.strip() or None, s.tax_id.strip() or None, s.notes.strip() or None, s.id))
    audit(con, by, "supplier.update", "supplier", s.id, s.name)
    return get(con, s.id)


def set_active(con, by, supplier_id: int, active: bool) -> None:
    """إيقاف أو تفعيل."""
    con.execute("UPDATE suppliers SET active=? WHERE id=?", (int(active), supplier_id))
    audit(con, by, "supplier.activate" if active else "supplier.deactivate", "supplier", supplier_id)


def has_documents(con, supplier_id: int) -> bool:
    """هل للمورد فواتير شراء؟ يُوسَّع في المرحلة 3."""
    return False


def delete(con, by, supplier_id: int) -> None:
    """حذف فعلي لمورد بلا مستندات."""
    if has_documents(con, supplier_id):
        raise SupplierError("للمورد مستندات مسجّلة؛ أوقفه بدل حذفه")
    s = get(con, supplier_id)
    con.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
    audit(con, by, "supplier.delete", "supplier", supplier_id, f"{s.name} · {s.mobile}")


def get(con, supplier_id: int) -> Supplier:
    """مورد برقمه."""
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    con.row_factory = None
    if not r:
        raise SupplierError("المورد غير موجود")
    return Supplier(r["id"], r["name"], r["mobile"], r["phone2"] or "", r["contact_person"] or "",
                    r["address"] or "", r["tax_id"] or "", r["notes"] or "", bool(r["active"]))


def search(con, query: str = "", include_inactive: bool = False, limit: int = 300, offset: int = 0) -> list[tuple]:
    """بحث القائمة: (id, name, mobile, contact_person, active)."""
    q = (query or "").strip()
    where, params = [], []
    if not include_inactive:
        where.append("active=1")
    if q:
        digits = "".join(ch for ch in validate.to_latin(q) if ch.isdigit())
        where.append("(name_norm LIKE ? OR (? <> '' AND mobile LIKE ?))")
        params += [f"%{norm_ar(q)}%", digits, f"%{digits}%"]
    sql = ("SELECT id, name, mobile, contact_person, active FROM suppliers" +
           (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY name_norm LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def count(con) -> int:
    """عدد الموردين النشطين."""
    return con.execute("SELECT count(*) FROM suppliers WHERE active=1").fetchone()[0]
