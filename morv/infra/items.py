"""Items, their attributes and item-card field setup (database layer)."""
import sqlite3
from dataclasses import dataclass, field

from morv.core import tire_catalog, validate
from morv.core.text import norm_ar, scanner_fix
from morv.infra.db.connection import transaction
from morv.infra.users import audit, require

KINDS = {"tire": "إطار", "battery": "بطارية", "service": "خدمة"}
FIXED_KEYS = {"name", "code", "barcode", "brand", "model", "price_retail", "price_wholesale", "price_super",
              "min_qty", "notes"}


class ItemError(ValueError):
    """Item data refused, with a message fit to show."""


@dataclass
class Field:
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


# ---------- Field setup ----------

def fields(con, kind: str) -> list[Field]:
    rows = con.execute(
        "SELECT kind, key, label, example, ftype, options, show, required, print, filter, builtin, sort_order "
        "FROM item_fields WHERE kind IN ('common', ?) ORDER BY CASE kind WHEN 'common' THEN 0 ELSE 1 END, sort_order",
        (kind,)).fetchall()
    result = [Field(r[0], r[1], r[2], r[3] or "", r[4], (r[5] or "").split("|") if r[5] else [],
                    bool(r[6]), bool(r[7]), bool(r[8]), bool(r[9]), bool(r[10]), r[11]) for r in rows]
    for f in result:
        if (f.kind, f.key) == ("tire", "cat"):
            f.options = list(tire_catalog.CATEGORIES)
    return result


def tyre_catalog(con) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {size: {} for size in tire_catalog.SIZES}
    rows = con.execute(
        "SELECT s.value, c.value, count(*) FROM items i "
        "JOIN item_attrs s ON s.item_id=i.id AND s.field_key='size' "
        "LEFT JOIN item_attrs c ON c.item_id=i.id AND c.field_key='cat' "
        "WHERE i.kind='tire' GROUP BY s.value, c.value").fetchall()
    for raw, category, count in rows:
        try:
            size = validate.tire_size(raw)["size"]
        except validate.ValidationError:
            continue
        counts = result.setdefault(size, {})
        if category in tire_catalog.CATEGORIES:
            counts[category] = counts.get(category, 0) + count
    return result


# Shared fields that mean nothing for a service: no stock, no brand
SERVICE_HIDDEN = ("brand", "model", "min_qty", "origin", "loc", "war", "cond")


def visible_fields(con, kind: str) -> list[Field]:
    out = [f for f in fields(con, kind) if f.show]
    if kind == "service":
        out = [f for f in out if f.key not in SERVICE_HIDDEN]
    return out


def all_fields(con) -> list[Field]:
    out = []
    seen = set()
    for kind in ("common", *KINDS):
        for f in fields(con, kind):
            if (f.kind, f.key) not in seen:
                seen.add((f.kind, f.key))
                out.append(f)
    return out


def set_field(con, by, kind: str, key: str, *, show=None, required=None, print_=None, filter_=None) -> None:
    require(con, by, "items.manage", ItemError)
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
        sets.append("show=1")              # Required implies visible
    if show is False:
        sets.append("required=0")          # Hidden cannot be required
    if sets:
        with transaction(con):
            con.execute(f"UPDATE item_fields SET {', '.join(sets)} WHERE kind=? AND key=?", (*params, kind, key))
            audit(con, by, "items.fields", "item_field", f"{kind}.{key}", ", ".join(sets))


def add_custom_field(con, by, kind: str, label: str, ftype: str, options: list | None = None) -> str:
    require(con, by, "items.manage", ItemError)
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
    with transaction(con):
        con.execute("INSERT INTO item_fields (kind, key, label, example, ftype, options, show, required, sort_order) "
                    "VALUES (?,?,?,?,?,?,1,0,?)",
                    (kind, key, label, "", ftype, "|".join(o.strip() for o in options) if options else None, order))
        audit(con, by, "items.fields.add", "item_field", f"{kind}.{key}", label)
    return key


# ---------- Items ----------

def _clean(con, it: Item, previous_category: str = "") -> Item:
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
    # Lower tiers inherit the tier above when left empty, as in the design
    it.price_wholesale = it.price_wholesale or it.price_retail
    it.price_super = it.price_super or it.price_wholesale
    if min(it.price_retail, it.price_wholesale, it.price_super) <= 0:
        raise ItemError("الأسعار أكبر من صفر")
    if it.min_qty < 0:
        raise ItemError("حد الطلب لا يكون سالبًا")
    if it.barcode and len(it.barcode) < 4:
        raise ItemError("الباركود 4 خانات على الأقل")
    clean_attrs = {}
    for f in visible_fields(con, it.kind):
        if f.key in FIXED_KEYS:
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
            legacy = ((f.kind, f.key) == ("tire", "cat") and val == previous_category
                      and val in tire_catalog.LEGACY_CATEGORIES)
            if not legacy:
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
    con.execute("DELETE FROM item_attrs WHERE item_id=?", (item_id,))
    con.executemany("INSERT INTO item_attrs (item_id, field_key, value) VALUES (?,?,?)",
                    [(item_id, k, v) for k, v in attrs.items()])


def _unique_errors(con, it: Item) -> None:
    r = con.execute("SELECT name FROM items WHERE code=? COLLATE NOCASE AND id IS NOT ?", (it.code, it.id)).fetchone()
    if r:
        raise ItemError(f"الكود مستخدم للصنف: {r[0]}")
    if it.barcode:
        r = con.execute("SELECT name FROM items WHERE barcode=? AND id IS NOT ?", (it.barcode, it.id)).fetchone()
        if r:
            raise ItemError(f"الباركود مسجّل للصنف: {r[0]}")


def create(con, by, it: Item) -> Item:
    require(con, by, "items.manage", ItemError)
    _clean(con, it)
    _unique_errors(con, it)
    with transaction(con):
        cur = con.execute(
            "INSERT INTO items (kind, code, barcode, name, name_norm, brand, model, price_retail, price_wholesale, price_super, "
            "min_qty, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (it.kind, it.code, it.barcode or None, it.name, norm_ar(it.name), it.brand or None, it.model or None,
             it.price_retail, it.price_wholesale, it.price_super, it.min_qty, it.notes.strip() or None))
        it.id = cur.lastrowid
        _save_attrs(con, it.id, it.attrs)
        audit(con, by, "item.create", "item", it.id, f"{it.code} · {it.name}")
    return get(con, it.id)


def update(con, by, it: Item) -> Item:
    require(con, by, "items.manage", ItemError)
    with transaction(con):
        # Kind and legacy category must be read under the same write lock as the update.
        old = get(con, it.id)
        _clean(con, it, old.attrs.get("cat", "") if old.kind == "tire" else "")
        _unique_errors(con, it)
        if old.kind != it.kind and has_documents(con, it.id):
            raise ItemError("لا يمكن تغيير نوع صنف له حركات؛ أنشئ صنفًا جديدًا")
        if ((old.price_retail, old.price_wholesale, old.price_super) != (it.price_retail, it.price_wholesale, it.price_super)):
            require(con, by, "items.prices", ItemError)
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
    return get(con, it.id)


def set_active(con, by, item_id: int, active: bool) -> None:
    require(con, by, "items.manage", ItemError)
    with transaction(con):
        con.execute("UPDATE items SET active=? WHERE id=?", (int(active), item_id))
        audit(con, by, "item.activate" if active else "item.deactivate", "item", item_id)


def has_documents(con, item_id: int) -> bool:
    return con.execute("SELECT EXISTS(SELECT 1 FROM purchase_lines WHERE item_id=?) "
                       "OR EXISTS(SELECT 1 FROM stock_lots WHERE item_id=?) "
                       "OR EXISTS(SELECT 1 FROM sale_lines WHERE item_id=?)", (item_id,) * 3).fetchone()[0] == 1


def delete(con, by, item_id: int) -> None:
    require(con, by, "items.manage", ItemError)
    if has_documents(con, item_id):
        raise ItemError("للصنف حركات مسجّلة؛ أوقفه بدل حذفه")
    it = get(con, item_id)
    with transaction(con):
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
        audit(con, by, "item.delete", "item", item_id, f"{it.code} · {it.name}")


def get(con, item_id: int) -> Item:
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
    c = scanner_fix(validate.to_latin((code or "").strip()))
    r = con.execute("SELECT id FROM items WHERE active=1 AND (barcode=? OR code=? COLLATE NOCASE)", (c, c)).fetchone()
    return get(con, r[0]) if r else None


def next_seq(con, kind: str) -> int:
    return con.execute("SELECT count(*)+1 FROM items WHERE kind=?", (kind,)).fetchone()[0]


def search(con, query: str = "", kind: str | None = None, include_inactive: bool = False,
           limit: int = 300, offset: int = 0) -> list[tuple]:
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
    return con.execute("SELECT count(*) FROM items WHERE active=1").fetchone()[0]
