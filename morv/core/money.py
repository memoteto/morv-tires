"""المبالغ المالية بالقرش.

القاعدة: كل مبلغ في البرنامج عدد صحيح (int) بالقرش، والجنيه = 100 قرش.
لا يُستخدم float أبدًا، لأن الكسور العشرية في الحاسب غير دقيقة
(مثلًا 0.1 + 0.2 لا تساوي 0.3 بالضبط)، وهذا يُفسد الحسابات بمرور الوقت.

الإدخال يقبل الأرقام اللاتينية والعربية (١٢٣)، والإخراج دائمًا بأرقام لاتينية.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# جدول تحويل الأرقام العربية والفارسية وفواصلها إلى مقابلها اللاتيني
_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬",
    "01234567890123456789.,",
)


class MoneyError(ValueError):
    """خطأ في مبلغ مُدخل: فارغ، أو ليس رقمًا، أو فيه أكثر من خانتين عشريتين."""


def to_latin(text: str) -> str:
    """تحويل الأرقام العربية (١٢٣) والفاصلة العشرية العربية (٫) إلى لاتينية."""
    return str(text).translate(_DIGITS)


def parse_money(text) -> int:
    """تحويل نص مبلغ إلى قروش.

    أمثلة: '1,234.5' أو '١٬٢٣٤٫٥' ← 123450 قرشًا.
    يرفض: النص الفارغ، وغير الرقمي، وأكثر من خانتين عشريتين،
    ويرفض الأعداد الصحيحة المباشرة لأنها غامضة (جنيه أم قرش؟).
    """
    if isinstance(text, int):
        raise MoneyError("مرّر المبلغ نصًا؛ العدد الصحيح غامض (جنيه أم قرش؟)")
    s = to_latin(text).strip().replace(",", "").replace(" ", "")
    if not s:
        raise MoneyError("المبلغ فارغ")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise MoneyError(f"ليس رقمًا: {text!r}") from e
    if d != d.quantize(Decimal("0.01")):
        raise MoneyError(f"أكثر من خانتين عشريتين: {text!r}")
    return int(d * 100)


def fmt(minor: int) -> str:
    """عرض قروش كمبلغ مقروء: 123450 ← '1,234.50' و -500 ← '-5.00'."""
    if not isinstance(minor, int):
        raise MoneyError("fmt تقبل قروشًا (int) فقط")
    sign = "-" if minor < 0 else ""
    pounds, piasters = divmod(abs(minor), 100)
    return f"{sign}{pounds:,}.{piasters:02d}"


def percent_of(minor: int, rate: str, rounding=ROUND_HALF_UP) -> int:
    """حساب نسبة مئوية من مبلغ وتقريبها لأقرب قرش.

    rate نص مثل '15' أو '12.5' (وليس float).
    طريقة التقريب معامل منفصل لأنها قرار محاسبي يمكن تغييره.
    """
    r = Decimal(to_latin(rate))
    if r < 0:
        raise MoneyError("النسبة سالبة")
    return int((Decimal(minor) * r / 100).quantize(Decimal(1), rounding=rounding))
