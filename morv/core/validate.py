"""قواعد التحقق من البيانات الأساسية: الموبايل المصري، والرقم القومي، ومقاس الإطار، وتوليد اسم وكود الصنف.

كلها دوال نقية بلا قاعدة بيانات ولا واجهة، لذلك تُختبر بالكامل بلا Qt.
"""
import re
from datetime import date

from morv.core.money import to_latin

_MOBILE = re.compile(r"^01[0-9]{9}$")
_NID = re.compile(r"^[23][0-9]{13}$")
# مقاس متري مثل 205/55R16 أو 205/55ZR16، ومقاس بوصة مثل 7.50R16
_SIZE_METRIC = re.compile(r"^(\d{3})/(\d{2})Z?R(\d{2}(?:\.5)?)$")
_SIZE_INCH = re.compile(r"^(\d{1,2}\.\d{2})R(\d{2}(?:\.5)?)$")

# محافظات مصر في الرقم القومي (الخانتان 8 و9)
_GOVERNORATES = {
    "01", "02", "03", "04", "11", "12", "13", "14", "15", "16", "17", "18", "19", "21", "22", "23",
    "24", "25", "26", "27", "28", "29", "31", "32", "33", "34", "35", "88",
}


class ValidationError(ValueError):
    """بيانات غير صالحة، برسالة تصلح للعرض."""


def mobile(text: str, required: bool = True) -> str:
    """موبايل مصري: 11 رقمًا يبدأ بـ 01. يقبل الأرقام العربية والفراغات ويعيده لاتينيًا نظيفًا."""
    s = to_latin(text or "").replace(" ", "").replace("-", "")
    if not s:
        if required:
            raise ValidationError("رقم الموبايل مطلوب")
        return ""
    if not _MOBILE.match(s):
        raise ValidationError("رقم الموبايل 11 رقمًا يبدأ بـ 01")
    return s


def national_id(text: str) -> str:
    """الرقم القومي المصري (اختياري): 14 رقمًا، يبدأ بقرن 2 أو 3، وفيه تاريخ ميلاد صحيح ورمز محافظة معروف.

    يعيد الرقم نظيفًا أو نصًا فارغًا إن لم يُدخل. لا يوجد معادلة تحقق رسمية منشورة للخانة الأخيرة،
    فلا نتحقق منها.
    """
    s = to_latin(text or "").replace(" ", "")
    if not s:
        return ""
    if not _NID.match(s):
        raise ValidationError("الرقم القومي 14 رقمًا يبدأ بـ 2 أو 3")
    century = 1900 if s[0] == "2" else 2000
    try:
        born = date(century + int(s[1:3]), int(s[3:5]), int(s[5:7]))
    except ValueError as e:
        raise ValidationError("تاريخ الميلاد داخل الرقم القومي غير صحيح") from e
    if born > date.today():
        raise ValidationError("تاريخ الميلاد داخل الرقم القومي في المستقبل")
    if s[7:9] not in _GOVERNORATES:
        raise ValidationError("رمز المحافظة داخل الرقم القومي غير معروف")
    return s


def mask_national_id(nid: str) -> str:
    """عرض الرقم القومي مخفيًا إلا آخر 4 أرقام."""
    return f"**********{nid[-4:]}" if nid else ""


def tire_size(text: str) -> dict:
    """تحليل مقاس الإطار إلى (width, aspect, rim) بعد توحيده بحروف كبيرة بلا فراغات.

    مثال: '205/55r16' → {'size': '205/55R16', 'width': '205', 'aspect': '55', 'rim': '16'}.
    """
    s = to_latin(text or "").upper().replace(" ", "")
    m = _SIZE_METRIC.match(s)
    if m:
        return {"size": s, "width": m.group(1), "aspect": m.group(2), "rim": m.group(3)}
    m = _SIZE_INCH.match(s)
    if m:
        return {"size": s, "width": m.group(1), "aspect": "", "rim": m.group(2)}
    raise ValidationError("اكتب المقاس مثل 205/55R16 أو 7.50R16")


def suggest_item_name(kind: str, brand: str, model: str, attrs: dict) -> str:
    """اسم الصنف كما يظهر في الفاتورة، مولّدًا من البيانات (يمكن للمستخدم تعديله)."""
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
    """كود داخلي مقترح: TR-2055516-MI للإطار، BT-70-ACD للبطارية، SV-003 للخدمة."""
    b = to_latin(brand or "").upper()
    b = re.sub(r"[^A-Z0-9]", "", b)
    if kind == "tire":
        size = re.sub(r"[^0-9]", "", attrs.get("size", "") or "")
        return f"TR-{size or '0'}-{b[:2] or 'XX'}"
    if kind == "battery":
        return f"BT-{attrs.get('ah', '') or '0'}-{b[:3] or 'XXX'}"
    return f"SV-{seq:03d}"
