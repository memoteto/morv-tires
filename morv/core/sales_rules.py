"""Pure sale rules: discount allocation, account map, payment checks."""
from dataclasses import dataclass

from morv.core import money

REVENUE_ACCOUNT = {"tire": "4110", "battery": "4120", "service": "4130"}
PAYMENT_ACCOUNT = {"cash": "1110", "card": "1220", "transfer": "1210", "wallet": "1230", "credit": "1310",
                   "installment": "1320"}
MAX_PAYMENT_METHODS = 2


class SaleRuleError(ValueError):
    """Broken sale rule, with a message fit to show."""


@dataclass(frozen=True)
class Payment:
    method: str
    amount: int
    received: int | None = None
    reference: str | None = None


def allocate(bases: list[int], amount: int) -> list[int]:
    """Thin wrapper over the shared money.allocate: a discount may never exceed the bases' total."""
    if amount > sum(bases):
        raise SaleRuleError("الخصم أكبر من قيمة السطور")
    try:
        return money.allocate(bases, amount)
    except money.MoneyError as e:
        raise SaleRuleError("الخصم أكبر من قيمة السطور") from e


def check_payments(total: int, payments: list) -> None:
    if not payments:
        raise SaleRuleError("أضف طريقة دفع")
    if len(payments) > MAX_PAYMENT_METHODS:
        raise SaleRuleError("وسيلتان كحد أقصى في الفاتورة الواحدة")
    methods = [p.method for p in payments]
    if len(set(methods)) != len(methods):
        raise SaleRuleError("لا تتكرر الوسيلة في الفاتورة الواحدة")
    for p in payments:
        if p.method not in PAYMENT_ACCOUNT:
            raise SaleRuleError(f"وسيلة دفع غير معروفة: {p.method}")
        if type(p.amount) is not int or p.amount <= 0:
            raise SaleRuleError("مبلغ الدفعة عدد صحيح موجب بالقرش")
        if p.method == "cash":
            if type(p.received) is not int or p.received < p.amount:
                raise SaleRuleError("المبلغ المستلم نقدًا أقل من المطلوب")
        elif p.received is not None:
            raise SaleRuleError("المستلم يُسجَّل للنقد فقط")
    if sum(p.amount for p in payments) != total:
        raise SaleRuleError("مجموع الدفع لا يساوي إجمالي الفاتورة")
