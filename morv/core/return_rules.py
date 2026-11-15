"""قواعد مرتجع البيع النقية: حالة القطعة، وحساب القيمة المرتجعة، وحسابات القيد. بلا قاعدة بيانات."""

CONDITIONS = {"sound": "سليم — يعود للمخزون", "defective": "معيب — مخزن التالف"}
RETURNS_ACCOUNT = "4190"        # مردودات المبيعات
INVENTORY_ACCOUNT = "1410"      # السليم يعود للمخزون
DEFECT_ACCOUNT = "1430"         # المعيب ينتظر المورد خارج رصيد البيع
MIN_REASON = 3


class ReturnRuleError(ValueError):
    """قاعدة مرتجع مخالفة، برسالة تصلح للعرض."""


def check_condition(condition: str) -> str:
    """حالة القطعة من المسموح؛ ترجعها كما هي أو ترفض."""
    if condition not in CONDITIONS:
        raise ReturnRuleError("حالة القطعة إما سليمة أو معيبة")
    return condition


def check_reason(reason) -> str:
    """سبب المرتجع مطلوب ولا يقل عن ثلاثة أحرف."""
    text = (reason or "").strip()
    if len(text) < MIN_REASON:
        raise ReturnRuleError("سبب المرتجع مطلوب (3 أحرف على الأقل)")
    return text


def returnable(sold: int, already: int, qty: int) -> int:
    """الكمية المرتجعة لا تتجاوز ما بقي من المباع؛ ترجعها أو ترفض."""
    if type(qty) is not int or qty <= 0:
        raise ReturnRuleError("كمية المرتجع عدد صحيح موجب")
    if already + qty > sold:
        raise ReturnRuleError(f"المتاح للإرجاع {sold - already} فقط")
    return qty


def portion(total: int, done: int, qty: int, all_qty: int) -> int:
    """حصة qty وحدة من قيمة موزعة على all_qty، بفرق تراكمي فيكون مجموع الحصص = القيمة بالضبط."""
    return total * (done + qty) // all_qty - total * done // all_qty


def stock_account(condition: str) -> str:
    """حساب وجهة التكلفة المرتجعة حسب حالة القطعة."""
    return INVENTORY_ACCOUNT if condition == "sound" else DEFECT_ACCOUNT
