"""Master-data validation: mobile, national id, tyre size, item name and code."""
import re
from datetime import date

from morv.core.money import to_latin

_MOBILE = re.compile(r"^01[0-9]{9}$")
_NID = re.compile(r"^[23][0-9]{13}$")
# Metric, commercial, inch and flotation markings; keep construction and suffixes distinct.
_SIZE_METRIC = re.compile(r"^(\d{2,3})/(\d{2,3})(?:Z?R|B|-)(\d{2}(?:\.5)?)(?:C|LT)?$")
_SIZE_COMMERCIAL = re.compile(r"^(\d{3})R(\d{2}(?:\.5)?)(?:C|LT)?$")
_SIZE_INCH = re.compile(r"^(\d{1,2}(?:\.\d{2})?)[R-](\d{2}(?:\.5)?)(?:C|LT)?$")
_SIZE_COMPACT = re.compile(r"^([5-9]\d{2}|1[0-9]\d{2})([R-]\d{2}(?:\.5)?(?:C|LT)?)$")
_SIZE_FLOTATION = re.compile(r"^(\d{2})X(\d{1,2}\.\d{2})R(\d{2}(?:\.5)?)(?:LT)?$")

# Egyptian governorates in the national id, digits 8 and 9
_GOVERNORATES = {
    "01", "02", "03", "04", "11", "12", "13", "14", "15", "16", "17", "18", "19", "21", "22", "23",
    "24", "25", "26", "27", "28", "29", "31", "32", "33", "34", "35", "88",
}


class ValidationError(ValueError):
    """Invalid data, with a message fit to show."""


def mobile(text: str, required: bool = True) -> str:
    s = to_latin(text or "").replace(" ", "").replace("-", "")
    if not s:
        if required:
            raise ValidationError("رقم الموبايل مطلوب")
        return ""
    if not _MOBILE.match(s):
        raise ValidationError("رقم الموبايل 11 رقمًا يبدأ بـ 01")
    return s


def national_id(text: str) -> str:
    s = to_latin(text or "").replace(" ", "")
    if not s:
        return ""
    if not _NID.match(s):
        raise ValidationError("الرقم القومي 14 رقمًا يبدأ بـ 2 أو 3")
    century = 1900 if s[0] == "2" else 2000
    try:
        born = date(century + int(s[1:3]), int(s[3:5]), int(s[5:7]))
    except ValueError as e:
        raise ValidationError("الرقم القومي غير صحيح: الخانات من 2 إلى 7 يجب أن تكون سنة وشهر ويوم ميلاد "
                              "صحيحة — راجعه من البطاقة") from e
    if born > date.today():
        raise ValidationError("تاريخ الميلاد داخل الرقم القومي في المستقبل")
    if s[7:9] not in _GOVERNORATES:
        raise ValidationError("رمز المحافظة داخل الرقم القومي غير معروف")
    return s


def mask_national_id(nid: str) -> str:
    return f"**********{nid[-4:]}" if nid else ""


def tire_size(text: str) -> dict:
    s = to_latin(text or "").upper().replace(" ", "")
    s = s.replace("×", "X")
    compact = _SIZE_COMPACT.fullmatch(s)
    if compact:
        digits = compact.group(1)
        s = digits[:-2] + "." + digits[-2:] + compact.group(2)
    m = _SIZE_METRIC.fullmatch(s)
    if m:
        return {"size": s, "width": m.group(1), "aspect": m.group(2), "rim": m.group(3)}
    m = _SIZE_INCH.fullmatch(s) or _SIZE_COMMERCIAL.fullmatch(s)
    if m:
        return {"size": s, "width": m.group(1), "aspect": "", "rim": m.group(2)}
    m = _SIZE_FLOTATION.fullmatch(s)
    if m:
        return {"size": s, "width": m.group(2), "aspect": "", "rim": m.group(3)}
    raise ValidationError("اكتب المقاس مثل 205/55R16 أو 195R14C أو 7.50R16")


def suggest_item_name(kind: str, brand: str, model: str, attrs: dict) -> str:
    brand = (brand or "").strip()
    model = (model or "").strip()
    if kind == "tire":
        parts = ["إطار", brand, model, attrs.get("size", ""), (attrs.get("li", "") or "") + (attrs.get("si", "") or "")]
    elif kind == "battery":
        ah = attrs.get("ah", "")
        parts = ["بطارية", brand, f"{ah} أمبير" if ah else "", model]
    else:
        return model or brand
    return " ".join(p for p in parts if p).strip()


def suggest_item_code(kind: str, brand: str, attrs: dict, seq: int) -> str:
    b = re.sub(r"[^A-Z0-9]", "", (brand or "").upper())
    tail = lambda n: b[:n] if b else f"{seq:03d}"   # noqa: E731 - a short local expression
    if kind == "tire":
        size = re.sub(r"[^0-9]", "", attrs.get("size", "") or "")
        return f"TR-{size or '0'}-{tail(2)}"
    if kind == "battery":
        return f"BT-{attrs.get('ah', '') or '0'}-{tail(3)}"
    return f"SV-{seq:03d}"
