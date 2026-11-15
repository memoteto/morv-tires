"""المبالغ المالية بالقرش.

القاعدة: كل مبلغ في البرنامج عدد صحيح (int) بالقرش، والجنيه = 100 قرش.
لا يُستخدم float أبدًا، لأن الكسور العشرية في الحاسب غير دقيقة
(مثلًا 0.1 + 0.2 لا تساوي 0.3 بالضبط)، وهذا يُفسد الحسابات بمرور الوقت.

الإدخال يقبل الأرقام اللاتينية والعربية (١٢٣)، والإخراج دائمًا بأرقام لاتينية.
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# جدول تحويل الأرقام العربية والفارسية وفواصلها إلى مقابلها اللاتيني
_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬",
    "01234567890123456789.,",
)


# شكل المبلغ المقبول: إشارة سالبة اختيارية، ثم جزء صحيح (أرقام متصلة أو مجموعات آلاف
# مفصولة بفاصلة كل ثلاث خانات بالضبط)، ثم كسر اختياري بعد النقطة. [0-9] وليس \d
# لأن \d تقبل أرقام لغات أخرى لا نعرف كيف نحسبها.
_AMOUNT = re.compile(r"^(?P<sign>-)?(?P<int>[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)?(?:\.(?P<frac>[0-9]*))?$")


class MoneyError(ValueError):
    """خطأ في مبلغ مُدخل: فارغ، أو ليس رقمًا، أو فيه أكثر من خانتين عشريتين."""


def to_latin(text: str) -> str:
    """تحويل الأرقام العربية (١٢٣) والفاصلة العشرية العربية (٫) إلى لاتينية."""
    return str(text).translate(_DIGITS)


def parse_money(text) -> int:
    """تحويل نص مبلغ إلى قروش، بقبول صارم حتى لا يُفهم مبلغ على غير ما قصده كاتبه.

    المقبول: '1234' و'1234.5' و'1,234.50' و'١٬٢٣٤٫٥٠' و'.5' ← قروش صحيحة.
    المرفوض برسالة (MoneyError):
    - الفاصلة في غير موضع الآلاف مثل '1,50' (قد يقصد بها كاتبها 1.50 فتُقرأ 150 جنيهًا).
    - المسافات داخل الرقم مثل '1 2'، والصيغة العلمية '1e5'، و'Infinity' و'NaN'.
    - النص الفارغ، وأكثر من خانتين عشريتين.
    - العدد الصحيح المباشر (int) لأنه غامض: جنيه أم قرش؟
    """
    if isinstance(text, int):
        raise MoneyError("مرّر المبلغ نصًا؛ العدد الصحيح غامض (جنيه أم قرش؟)")
    s = to_latin(text).strip()
    if not s:
        raise MoneyError("المبلغ فارغ")
    m = _AMOUNT.match(s)
    if not m or not (m.group("int") or m.group("frac")):
        if "," in s and s.replace(",", "") and _AMOUNT.match(s.replace(",", "")):
            raise MoneyError(f"الفاصلة لفصل الآلاف فقط مثل 1,234.50 — وللكسور استخدم النقطة: {text!r}")
        raise MoneyError(f"ليس مبلغًا صحيحًا: {text!r}")
    frac = m.group("frac") or ""
    if len(frac) > 2:
        raise MoneyError(f"أكثر من خانتين عشريتين: {text!r}")
    pounds = int((m.group("int") or "0").replace(",", ""))
    minor = pounds * 100 + int(frac.ljust(2, "0") or "0")
    return -minor if m.group("sign") else minor


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
    try:
        r = Decimal(to_latin(rate).strip())
    except InvalidOperation as e:
        raise MoneyError(f"نسبة غير صحيحة: {rate!r}") from e
    if not r.is_finite():
        raise MoneyError(f"نسبة غير صحيحة: {rate!r}")
    if r < 0:
        raise MoneyError("النسبة سالبة")
    return int((Decimal(minor) * r / 100).quantize(Decimal(1), rounding=rounding))
