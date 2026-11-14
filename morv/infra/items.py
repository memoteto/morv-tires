"""الأصناف وخصائصها وإعداد حقول بطاقة الصنف (طبقة قاعدة البيانات).

الصنف له أعمدة ثابتة يعتمد عليها البيع (النوع، الكود، الباركود، الاسم، الأسعار الثلاثة)
وخصائص حسب نوعه (المقاس، الأمبير...) تُحفظ في item_attrs بمفتاح الحقل.
الحقول الظاهرة والإلزامية تُقرأ من item_fields التي يضبطها المدير من الإعدادات.
"""
import sqlite3
from dataclasses import dataclass, field

from morv.core import validate
from morv.core.text import norm_ar, scanner_fix
from morv.infra.users import audit

KINDS = {"tire": "إطار", "battery": "بطارية", "service": "خدمة"}
FIXED_KEYS = {"name", "code", "barcode", "brand", "model", "price_retail", "price_wholesale", "price_super",
              "min_qty", "notes"}


class ItemError(ValueError):
    """بيانات صنف مرفوضة، برسالة تصلح للعرض."""


@dataclass
class Field:
    """حقل في بطاقة الصنف كما يضبطه المدير."""
    kind: str
    key: str
    label: str
    example: str
    ftype: str
    options: list
    show: bool
    required: bool
    print: bool
    filter: bool
    builtin: bool
    sort_order: int


@dataclass
class Item:
    """صنف كامل."""
    id: int | None
    kind: str
    code: str
    name: str
    barcode: str = ""
    brand: str = ""
    model: str = ""
    price_retail: int = 0
    price_wholesale: int = 0
    price_super: int = 0
    min_qty: int = 0
    notes: str = ""
    active: bool = True
    attrs: dict = field(default_factory=dict)


# ---------- إعداد الحقول ----------

def fields(con, kind: str) -> list[Field]:
    """حقول نوع معين (المشتركة ثم الخاصة) بترتيب العرض."""
    rows = con.execute(
        "SELECT kind, key, label, example, ftype, options, show, required, print, filter, builtin, sort_order "
        "FROM item_fields WHERE kind IN ('common', ?) ORDER BY CASE kind WHEN 'common' THEN 0 ELSE 1 END, sort_order",
        (kind,)).fetchall()
    return [Field(r[0], r[1], r[2], r[3] or "", r[4], (r[5] or "").split("|") if r[5] else [],
                  bool(r[6]), bool(r[7]), bool(r[8]), bool(r[9]), bool(r[10]), r[11]) for r in rows]


def all_fields(con) -> list[Field]:
    """كل الحقول لكل الأنواع (لشاشة الإعداد)."""
    out = []
    seen = set()
    for kind in ("common", *KINDS):
        for f in fields(con, kind):
            if (f.kind, f.key) not in seen:
                seen.add((f.kind, f.key))
                out.append(f)
    return out


def set_field(con, by, kind: str, key: str, *, show=None, required=None, print_=None, filter_=None) -> None:
    """تعديل إعدادات حقل. الحقول المقفلة (builtin) تبقى ظاهرة وإلزامية دائمًا."""
    r = con.execute("SELECT builtin, show FROM item_fields WHERE kind=? AND key=?", (kind, key)).fetchone()
    if not r:
        raise ItemError("حقل غير معروف")
    if r[0] and (show is False or required is False):
        raise ItemError("هذا الحقل أساسي يعتمد عليه البرنامج ولا يمكن إخفاؤه")
    sets, params = [], []
    for col, val in (("show", show), ("required", required), ("print", print_), ("filter", filter_)):
        if val is not None:
            sets.append(f"{col}=?")
            params.append(int(val))
    if required:
        sets.append("show=1")              # الإلزامي ظاهر بالضرورة
    if show is False:
        sets.append("required=0")          # المخفي لا يكون إلزاميًا
    if sets:
        con.execute(f"UPDATE item_fields SET {', '.join(sets)} WHERE kind=? AND key=?", (*params, kind, key))
        audit(con, by, "items.fields", "item_field", f"{kind}.{key}", ", ".join(sets))


def add_custom_field(con, by, kind: str, label: str, ftype: str, options: list | None = None) -> str:
    """إضافة حقل مخصص (نص أو رقم أو قائمة). يعيد مفتاحه."""
    if kind not in ("common", *KINDS) or ftype not in ("text", "num", "sel", "bool"):
        raise ItemError("نوع الحقل غير معروف")
    label = (label or "").strip()
    if not label:
        raise ItemError("اسم الحقل مطلوب")
    if ftype == "sel" and not options:
        raise ItemError("قائمة الاختيارات مطلوبة")
    n = con.execute("SELECT count(*) FROM item_fields WHERE key LIKE 'custom_%'").fetchone()[0] + 1
    key = f"custom_{n}"
    order = con.execute("SELECT COALESCE(max(sort_order),0)+1 FROM item_fields WHERE kind=?", (kind,)).fetchone()[0]
    con.execute("INSERT INTO item_fields (kind, key, label, example, ftype, options, show, required, sort_order) "
                "VALUES (?,?,?,?,?,?,1,0,?)",
                (kind, key, label, "", ftype, "|".join(o.strip() for o in options) if options else None, order))
    audit(con, by, "items.fields.add", "item_field", f"{kind}.{key}", label)
    return key


# ---------- الأصناف ----------

def _clean(con, it: Item) -> Item:
    """فحص الصنف حسب الحقول المفعّلة والإلزامية لنوعه، وتنظيف قيمه."""
    if it.kind not in KINDS:
        raise ItemError("نوع الصنف غير معروف")
    it.name = (it.name or "").strip()
    it.code = validate.to_latin((it.code or "").strip()).upper()
    it.barcode = scanner_fix(validate.to_latin((it.barcode or "").strip()))
    it.brand = (it.brand or "").strip()
    it.model = (it.model or "").strip()
    if not it.name:
        raise ItemError("اسم الصنف مطلوب")
    if not it.code:
        raise ItemError("الكود الداخلي مطلوب")
    if it.price_retail <= 0:
        raise ItemError("سعر القطاعي مطلوب وأكبر من صفر")
    # الأسعار الأدنى تأخذ ما فوقها إن تُركت فارغة (كما في التصميم)
    it.price_wholesale = it.price_wholesale or it.price_retail
    it.price_super = it.price_super or it.price_wholesale
    if min(it.price_retail, it.price_wholesale, it.price_super) <= 0:
        raise ItemError("الأسعار أكبر من صفر")
    if it.min_qty < 0:
        raise ItemError("حد الطلب لا يكون سالبًا")
    if it.barcode and len(it.barcode) < 4:
        raise ItemError("الباركود 4 خانات على الأقل")
    clean_attrs = {}
    for f in fields(con, it.kind):
        if f.key in FIXED_KEYS or not f.show:
            continue
        raw = it.attrs.get(f.key, "")
        val = str(raw).strip() if raw is not None else ""
        if f.ftype == "size" and val:
            try:
                val = validate.tire_size(val)["size"]
            except validate.ValidationError as e:
                raise ItemError(str(e)) from e
        if f.ftype == "num" and val:
            val = validate.to_latin(val).replace(",", "")
            try:
                float(val)
            except ValueError as e:
                raise ItemError(f"«{f.label}» يجب أن يكون رقمًا") from e
        if f.ftype == "sel" and val and val not in f.options:
            raise ItemError(f"قيمة «{f.label}» ليست من الاختيارات")
        if f.ftype == "bool":
            val = "1" if val in ("1", "true", "True", "نعم") else ""
        if f.required and not val:
            raise ItemError(f"«{f.label}» مطلوب")
        if val:
            clean_attrs[f.key] = val
    if it.kind != "service":
        brand_field = next((f for f in fields(con, it.kind) if f.key == "brand"), None)
        if brand_field and brand_field.required and not it.brand:
            raise ItemError("الماركة مطلوبة")
    it.attrs = clean_attrs
    return it


def _save_attrs(con, item_id: int, attrs: dict) -> None:
    """استبدال خصائص الصنف."""
    con.execute("DELETE FROM item_attrs WHERE item_id=?", (item_id,))
    con.executemany("INSERT INTO item_attrs (item_id, field_key, value) VALUES (?,?,?)",
                    [(item_id, k, v) for k, v in attrs.items()])


def _unique_errors(con, it: Item) -> None:
    """رسائل واضحة لتكرار الكود أو الباركود بدل خطأ SQLite العام."""
    r = con.execute("SELECT name FROM items WHERE code=? COLLATE NOCASE AND id IS NOT ?", (it.code, it.id)).fetchone()
    if r:
        raise ItemError(f"الكود مستخدم للصنف: {r[0]}")
    if it.barcode:
        r = con.execute("SELECT name FROM items WHERE barcode=? AND id IS NOT ?", (it.barcode, it.id)).fetchone()
        if r:
            raise ItemError(f"الباركود مسجّل للصنف: {r[0]}")


def create(con, by, it: Item) -> Item:
    """إنشاء صنف مع خصائصه."""
    _clean(con, it)
    _unique_errors(con, it)
    con.execute("BEGIN")
    try:
        cur = con.execute(
            "INSERT INTO items (kind, code, barcode, name, name_norm, brand, model, price_retail, price_wholesale, price_super, "
            "min_qty, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (it.kind, it.code, it.barcode or None, it.name, norm_ar(it.name), it.brand or None, it.model or None,
             it.price_retail, it.price_wholesale, it.price_super, it.min_qty, it.notes.strip() or None))
        it.id = cur.lastrowid
        _save_attrs(con, it.id, it.attrs)
        audit(con, by, "item.create", "item", it.id, f"{it.code} · {it.name}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return get(con, it.id)


def update(con, by, it: Item) -> Item:
    """تعديل صنف. تغيير الأسعار يُسجَّل بقيمه في المراجعة."""
    _clean(con, it)
    _unique_errors(con, it)
    old = get(con, it.id)
    con.execute("BEGIN")
    try:
        con.execute(
            "UPDATE items SET kind=?, code=?, barcode=?, name=?, name_norm=?, brand=?, model=?, price_retail=?, price_wholesale=?, "
            "price_super=?, min_qty=?, notes=?, updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime') WHERE id=?",
            (it.kind, it.code, it.barcode or None, it.name, norm_ar(it.name), it.brand or None, it.model or None,
             it.price_retail, it.price_wholesale, it.price_super, it.min_qty, it.notes.strip() or None, it.id))
        _save_attrs(con, it.id, it.attrs)
        detail = f"{it.code}"
        if (old.price_retail, old.price_wholesale, old.price_super) != (it.price_retail, it.price_wholesale, it.price_super):
            detail += (f" · الأسعار {old.price_retail}/{old.price_wholesale}/{old.price_super} → "
                       f"{it.price_retail}/{it.price_wholesale}/{it.price_super}")
        audit(con, by, "item.update", "item", it.id, detail)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return get(con, it.id)


def set_active(con, by, item_id: int, active: bool) -> None:
    """إيقاف صنف أو تفعيله (الموقوف لا يظهر في البيع)."""
    con.execute("UPDATE items SET active=? WHERE id=?", (int(active), item_id))
    audit(con, by, "item.activate" if active else "item.deactivate", "item", item_id)


def has_documents(con, item_id: int) -> bool:
    """هل للصنف حركات؟ يُوسَّع في المرحلة 3."""
    return False


def delete(con, by, item_id: int) -> None:
    """حذف فعلي لصنف بلا حركات."""
    if has_documents(con, item_id):
        raise ItemError("للصنف حركات مسجّلة؛ أوقفه بدل حذفه")
    it = get(con, item_id)
    con.execute("DELETE FROM items WHERE id=?", (item_id,))
    audit(con, by, "item.delete", "item", item_id, f"{it.code} · {it.name}")


def get(con, item_id: int) -> Item:
    """صنف برقمه مع خصائصه."""
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    con.row_factory = None
    if not r:
        raise ItemError("الصنف غير موجود")
    attrs = dict(con.execute("SELECT field_key, value FROM item_attrs WHERE item_id=?", (item_id,)).fetchall())
    return Item(r["id"], r["kind"], r["code"], r["name"], r["barcode"] or "", r["brand"] or "", r["model"] or "",
                r["price_retail"], r["price_wholesale"], r["price_super"], r["min_qty"], r["notes"] or "",
                bool(r["active"]), attrs)


def by_barcode(con, code: str) -> Item | None:
    """صنف بالباركود بعد تصحيح الماسح، أو بالكود الداخلي."""
    c = scanner_fix(validate.to_latin((code or "").strip()))
    r = con.execute("SELECT id FROM items WHERE active=1 AND (barcode=? OR code=? COLLATE NOCASE)", (c, c)).fetchone()
    return get(con, r[0]) if r else None


def next_seq(con, kind: str) -> int:
    """الرقم التسلسلي التالي لكود مقترح من نوع معين."""
    return con.execute("SELECT count(*)+1 FROM items WHERE kind=?", (kind,)).fetchone()[0]


def search(con, query: str = "", kind: str | None = None, include_inactive: bool = False,
           limit: int = 300, offset: int = 0) -> list[tuple]:
    """بحث القائمة: (id, kind, code, name, spec, price_retail, price_wholesale, price_super, min_qty, active).

    spec: المقاس للإطار، والأمبير للبطارية. البحث يطابق الاسم الموحّد أو الكود أو الباركود أو المقاس.
    """
    q = (query or "").strip()
    where, params = [], []
    if not include_inactive:
        where.append("i.active=1")
    if kind:
        where.append("i.kind=?")
        params.append(kind)
    if q:
        lat = validate.to_latin(q).upper().replace(" ", "")
        where.append("(i.name_norm LIKE ? OR i.code LIKE ? OR i.barcode LIKE ? OR EXISTS "
                     "(SELECT 1 FROM item_attrs a WHERE a.item_id=i.id AND a.field_key IN ('size','ah') AND a.value LIKE ?))")
        params += [f"%{norm_ar(q)}%", f"%{lat}%", f"%{lat}%", f"%{lat}%"]
    sql = ("SELECT i.id, i.kind, i.code, i.name, "
           "COALESCE((SELECT value FROM item_attrs a WHERE a.item_id=i.id AND a.field_key='size'), "
           "(SELECT value || ' أمبير' FROM item_attrs a WHERE a.item_id=i.id AND a.field_key='ah'), ''), "
           "i.price_retail, i.price_wholesale, i.price_super, i.min_qty, i.active FROM items i" +
           (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY i.kind, i.name_norm LIMIT ? OFFSET ?")
    return con.execute(sql, (*params, limit, offset)).fetchall()


def count(con) -> int:
    """عدد الأصناف النشطة."""
    return con.execute("SELECT count(*) FROM items WHERE active=1").fetchone()[0]
