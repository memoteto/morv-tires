"""حسابات فاتورة البيع النقية بالقروش: الأسعار والإيراد وتقسيم التحصيل وسطور القيد. بلا قاعدة بيانات."""

REVENUE_ACCOUNTS = {"tire": "4110", "battery": "4120", "service": "4130"}
PAYMENT_ACCOUNTS = {"cash": "1110", "transfer": "1210", "card": "1220"}
CREDIT_ACCOUNT = "1310"
PRICE_COLUMNS = {"retail": "price_retail", "wholesale": "price_wholesale", "super": "price_super"}


class SaleCalcError(ValueError):
    """مدخلات حسابية مرفوضة، برسالة تصلح للعرض."""


def is_money(x) -> bool:
    """هل القيمة عدد صحيح (قروش) وليست bool."""
    return type(x) is int


def price_column(level: str | None) -> str:
    """عمود السعر لفئة العميل؛ بلا عميل أو فئة مجهولة = القطاعي."""
    return PRICE_COLUMNS.get(level or "retail", PRICE_COLUMNS["retail"])


def line_total(qty: int, unit_price: int) -> int:
    """إجمالي السطر = الكمية × سعر الوحدة، أعدادًا صحيحة موجبة."""
    if not is_money(qty) or qty <= 0:
        raise SaleCalcError("الكمية عدد صحيح موجب")
    if not is_money(unit_price) or unit_price <= 0:
        raise SaleCalcError("سعر الوحدة بالقرش عدد صحيح موجب")
    return qty * unit_price


def revenue_by_account(lines) -> dict:
    """مجموع الإيراد لكل حساب؛ lines = [(نوع الصنف, إجمالي السطر)]."""
    out: dict = {}
    for kind, total in lines:
        if kind not in REVENUE_ACCOUNTS:
            raise SaleCalcError("نوع صنف غير معروف")
        acc = REVENUE_ACCOUNTS[kind]
        out[acc] = out.get(acc, 0) + total
    return out


def split_payments(total: int, payments: dict) -> tuple:
    """يرجع (المحصَّل، الآجل). كل وسيلة مبلغ موجب، والمحصَّل لا يتجاوز الإجمالي، والباقي آجل."""
    paid = 0
    for method, amount in payments.items():
        if method not in PAYMENT_ACCOUNTS:
            raise SaleCalcError("وسيلة دفع غير معروفة")
        if not is_money(amount) or amount <= 0:
            raise SaleCalcError("مبلغ الدفعة بالقرش عدد صحيح موجب")
        paid += amount
    if paid > total:
        raise SaleCalcError("المحصَّل أكبر من إجمالي الفاتورة")
    return paid, total - paid


def entry_lines(payments: dict, credit: int, revenue: dict) -> list:
    """سطور قيد الفاتورة: مدين وسائل التحصيل والآجل، دائن الإيرادات. الإيراد يُسجَّل مرة واحدة."""
    lines = [(PAYMENT_ACCOUNTS[m], a, 0) for m, a in payments.items()]
    if credit:
        lines.append((CREDIT_ACCOUNT, credit, 0))
    lines += [(acc, 0, amount) for acc, amount in revenue.items()]
    return lines


def is_below_cost(total: int, cost_total: int) -> bool:
    """هل إجمالي السطر أقل من تكلفته FIFO؟ (تنبيه فقط، لا منع)."""
    return cost_total > 0 and total < cost_total


def credit_warning(limit: int, outstanding: int, new_credit: int) -> str | None:
    """تنبيه الآجل: حد 0 يعني «لا آجل»، وتجاوز الحد تنبيه فقط."""
    if new_credit <= 0:
        return None
    if limit == 0:
        return "العميل غير مصرّح له بالآجل (حد الآجل صفر)"
    if outstanding + new_credit > limit:
        return "الآجل يتجاوز حد العميل"
    return None
