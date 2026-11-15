"""العملاء ومركباتهم (طبقة قاعدة البيانات).

قواعد ثابتة:
- الموبايل فريد؛ عند التكرار تُرفض العملية وتُذكر رسالة الخطأ اسم العميل الموجود.
- لا حذف لعميل له مستندات لاحقًا؛ الإيقاف هو الطريق. الحذف الفعلي مسموح ما دام بلا مستندات
  (في هذه المرحلة لا مستندات بعد، والفحص يُوسَّع مع كل جدول يشير إلى العميل).
- البحث بالاسم العربي الموحّد (norm_ar) أو الموبايل أو رقم اللوحة.
"""
import sqlite3
from dataclasses import dataclass, field

from morv.core import validate
from morv.core.text import norm_ar
from morv.infra.db.connection import transaction
from morv.infra.users import audit

TYPES = {"individual": "فرد", "workshop": "ورشة / مركز صيانة", "company": "شركة / أسطول", "trader": "تاجر"}
LEVELS = {"retail": "قطاعي", "wholesale": "جملة", "super": "جملة الجملة"}


class CustomerError(ValueError):
    """بيانات عميل مرفوضة، برسالة تصلح للعرض."""


@dataclass
class Vehicle:
    """مركبة عميل."""
    make: str = ""
    model: str = ""
    year: int | None = None
    plate: str = ""
    tire_front: str = ""
    tire_rear: str = ""
    battery: str = ""
    odometer: int | None = None
    id: int | None = None

    @property
    def title(self) -> str:
        """وصف قصير: الماركة والموديل والسنة."""
        return " ".join(str(p) for p in (self.make, self.model, self.year) if p)


@dataclass
class Customer:
    """عميل كامل البيانات."""
    id: int | None
    name: str
    mobile: str
    mobile2: str = ""
    national_id: str = ""
    customer_type: str = "individual"
    price_level: str = "retail"
    credit_limit: int = 0
    credit_days: int = 0
    address: str = ""
    work_place: str = ""
    tax_id: str = ""
    notes: str = ""
    whatsapp_ok: bool = False
    active: bool = True
    vehicles: list = field(default_factory=list)


def _rows(con, sql, params=()):
    """تنفيذ استعلام وإرجاع صفوف بأسماء الأعمدة."""
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.row_factory = None


def _from_row(r) -> Customer:
    """صف إلى Customer (بلا مركبات)."""
    return Customer(r["id"], r["name"], r["mobile"], r["mobile2"] or "", r["national_id"] or "",
                    r["customer_type"], r["price_level"], r["credit_limit"], r["credit_days"],
                    r["address"] or "", r["work_place"] or "", r["tax_id"] or "", r["notes"] or "",
                    bool(r["whatsapp_ok"]), bool(r["active"]))


def _clean(c: Customer) -> Customer:
    """فحص البيانات وتنظيفها قبل الحفظ."""
    c.name = (c.name or "").strip()
    if not c.name:
        raise CustomerError("اسم العميل مطلوب")
    try:
        c.mobile = validate.mobile(c.mobile)
        c.mobile2 = validate.mobile(c.mobile2, required=False)
        c.national_id = validate.national_id(c.national_id)
    except validate.ValidationError as e:
        raise CustomerError(str(e)) from e
    if c.customer_type not in TYPES or c.price_level not in LEVELS:
        raise CustomerError("نوع العميل أو فئة السعر غير معروفة")
    if c.credit_limit < 0 or c.credit_days < 0:
        raise CustomerError("حد الآجل ومدته لا يكونان سالبين")
    return c


def _duplicate(con, mobile: str, excluding: int | None) -> str | None:
    """اسم العميل الذي يحمل نفس الموبايل، أو None."""
    r = con.execute("SELECT name FROM customers WHERE mobile=? AND id IS NOT ?", (mobile, excluding)).fetchone()
    return r[0] if r else None


def _save_vehicles(con, customer_id: int, vehicles: list) -> None:
    """استبدال مركبات العميل بالقائمة المعطاة (قائمة صغيرة؛ الاستبدال أبسط من المزامنة)."""
    con.execute("DELETE FROM customer_vehicles WHERE customer_id=?", (customer_id,))
    for v in vehicles:
        if not any((v.make, v.model, v.plate)):
            continue
        plate = (v.plate or "").strip()
        con.execute(
            "INSERT INTO customer_vehicles (customer_id, make, model, year, plate, plate_norm, tire_front, tire_rear, battery, odometer) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (customer_id, v.make.strip() or None, v.model.strip() or None, v.year, plate or None,
             norm_ar(plate).replace(" ", "") or None, v.tire_front.strip().upper() or None,
             v.tire_rear.strip().upper() or None, v.battery.strip() or None, v.odometer))


def create(con, by, c: Customer) -> Customer:
    """إنشاء عميل مع مركباته في معاملة واحدة."""
    _clean(c)
    dup = _duplicate(con, c.mobile, None)
    if dup:
        raise CustomerError(f"يوجد عميل مسجّل بنفس الرقم: {dup}")
    with transaction(con):
        cur = con.execute(
            "INSERT INTO customers (name, name_norm, mobile, mobile2, national_id, customer_type, price_level, credit_limit, "
            "credit_days, address, work_place, tax_id, notes, whatsapp_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c.name, norm_ar(c.name), c.mobile, c.mobile2 or None, c.national_id or None, c.customer_type, c.price_level,
             c.credit_limit, c.credit_days, c.address.strip() or None, c.work_place.strip() or None,
             c.tax_id.strip() or None, c.notes.strip() or None, int(c.whatsapp_ok)))
        c.id = cur.lastrowid
        _save_vehicles(con, c.id, c.vehicles)
        audit(con, by, "customer.create", "customer", c.id, f"{c.name} · {c.mobile}")
    return get(con, c.id)


def update(con, by, c: Customer) -> Customer:
    """تعديل عميل ومركباته. تغيير فئة السعر يُسجَّل في المراجعة لأنه يؤثر في أسعار البيع."""
    _clean(c)
    old = get(con, c.id)
    dup = _duplicate(con, c.mobile, c.id)
    if dup:
        raise CustomerError(f"يوجد عميل مسجّل بنفس الرقم: {dup}")
    with transaction(con):
        con.execute(
            "UPDATE customers SET name=?, name_norm=?, mobile=?, mobile2=?, national_id=?, customer_type=?, price_level=?, "
            "credit_limit=?, credit_days=?, address=?, work_place=?, tax_id=?, notes=?, whatsapp_ok=?, "
            "updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE id=?",
            (c.name, norm_ar(c.name), c.mobile, c.mobile2 or None, c.national_id or None, c.customer_type, c.price_level,
             c.credit_limit, c.credit_days, c.address.strip() or None, c.work_place.strip() or None,
             c.tax_id.strip() or None, c.notes.strip() or None, int(c.whatsapp_ok), c.id))
        _save_vehicles(con, c.id, c.vehicles)
        detail = f"{c.name}"
        if old.price_level != c.price_level:
            detail += f" · فئة السعر {LEVELS[old.price_level]} → {LEVELS[c.price_level]}"
        if old.credit_limit != c.credit_limit:
            detail += f" · حد الآجل {old.credit_limit} → {c.credit_limit}"
        audit(con, by, "customer.update", "customer", c.id, detail)
    return get(con, c.id)


def set_active(con, by, customer_id: int, active: bool) -> None:
    """إيقاف عميل أو تفعيله (الموقوف لا يظهر في البيع)."""
    with transaction(con):
        con.execute("UPDATE customers SET active=? WHERE id=?", (int(active), customer_id))
        audit(con, by, "customer.activate" if active else "customer.deactivate", "customer", customer_id)


def has_documents(con, customer_id: int) -> bool:
    """هل للعميل مستندات تمنع حذفه؟ يُوسَّع مع كل جدول جديد يشير إلى العملاء."""
    return False


def delete(con, by, customer_id: int) -> None:
    """حذف فعلي، مسموح فقط لعميل بلا مستندات."""
    if has_documents(con, customer_id):
        raise CustomerError("للعميل مستندات مسجّلة؛ أوقفه بدل حذفه")
    c = get(con, customer_id)
    with transaction(con):
        con.execute("DELETE FROM customers WHERE id=?", (customer_id,))
        audit(con, by, "customer.delete", "customer", customer_id, f"{c.name} · {c.mobile}")


def get(con, customer_id: int) -> Customer:
    """عميل برقمه مع مركباته."""
    rows = _rows(con, "SELECT * FROM customers WHERE id=?", (customer_id,))
    if not rows:
        raise CustomerError("العميل غير موجود")
    c = _from_row(rows[0])
    c.vehicles = [Vehicle(v["make"] or "", v["model"] or "", v["year"], v["plate"] or "", v["tire_front"] or "",
                          v["tire_rear"] or "", v["battery"] or "", v["odometer"], v["id"])
                  for v in _rows(con, "SELECT * FROM customer_vehicles WHERE customer_id=? ORDER BY id", (customer_id,))]
    return c


def search(con, query: str = "", level: str | None = None, include_inactive: bool = False,
           limit: int = 300, offset: int = 0) -> list[tuple]:
    """بحث القائمة: (id, name, mobile, price_level, customer_type, first_vehicle, active).

    النص يُطابق الاسم الموحّد أو الموبايل أو رقم اللوحة. النتائج بترتيب الاسم، صفحة صفحة.
    """
    q = (query or "").strip()
    where, params = [], []
    if not include_inactive:
        where.append("c.active=1")
    if level:
        where.append("c.price_level=?")
        params.append(level)
    if q:
        nq = f"%{norm_ar(q)}%"
        digits = "".join(ch for ch in validate.to_latin(q) if ch.isdigit())
        where.append("(c.name_norm LIKE ? OR (? <> '' AND c.mobile LIKE ?) OR EXISTS "
                     "(SELECT 1 FROM customer_vehicles v WHERE v.customer_id=c.id AND v.plate_norm LIKE ?))")
        params += [nq, digits, f"%{digits}%", f"%{norm_ar(q).replace(' ', '')}%"]
    sql = ("SELECT c.id, c.name, c.mobile, c.price_level, c.customer_type, "
           "(SELECT make || ' ' || COALESCE(model,'') FROM customer_vehicles v WHERE v.customer_id=c.id ORDER BY v.id LIMIT 1), "
           "c.active FROM customers c" + (" WHERE " + " AND ".join(where) if where else "") +
           " ORDER BY c.name_norm LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def count(con) -> int:
    """عدد العملاء النشطين."""
    return con.execute("SELECT count(*) FROM customers WHERE active=1").fetchone()[0]
