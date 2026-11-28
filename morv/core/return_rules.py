"""Pure sales-return rules: item condition, returned value, ledger accounts."""

CONDITIONS = {"sound": "سليم — يعود للمخزون", "defective": "معيب — مخزن التالف"}
RETURNS_ACCOUNT = "4190"        # sales returns
INVENTORY_ACCOUNT = "1410"      # sound goes back to stock
DEFECT_ACCOUNT = "1430"         # defective waits for the supplier, outside sellable stock
MIN_REASON = 3


class ReturnRuleError(ValueError):
    """Broken return rule, with a message fit to show."""


def check_condition(condition: str) -> str:
    if condition not in CONDITIONS:
        raise ReturnRuleError("حالة القطعة إما سليمة أو معيبة")
    return condition


def check_reason(reason) -> str:
    text = (reason or "").strip()
    if len(text) < MIN_REASON:
        raise ReturnRuleError("سبب المرتجع مطلوب (3 أحرف على الأقل)")
    return text


def returnable(sold: int, already: int, qty: int) -> int:
    if type(qty) is not int or qty <= 0:
        raise ReturnRuleError("كمية المرتجع عدد صحيح موجب")
    if already + qty > sold:
        raise ReturnRuleError(f"المتاح للإرجاع {sold - already} فقط")
    return qty


def portion(total: int, done: int, qty: int, all_qty: int) -> int:
    return total * (done + qty) // all_qty - total * done // all_qty


def tax_portion(total: int, tax: int, done: int, qty: int, all_qty: int) -> int:
    """Allocate tax by cumulative gross, never exceeding the gross of any partial return."""
    if not total:
        return 0
    before = total * done // all_qty
    after = total * (done + qty) // all_qty
    return tax * after // total - tax * before // total


def stock_account(condition: str) -> str:
    return INVENTORY_ACCOUNT if condition == "sound" else DEFECT_ACCOUNT
