"""WhatsApp reminder text: the approved template, its variables and the wa.me link. No database."""
from datetime import date

CUSTOMER = "{اسم_العميل}"
CONTRACT = "{رقم_العقد}"
AMOUNT = "{المبلغ}"
DUE_DATE = "{تاريخ_الاستحقاق}"
LATE_DAYS = "{أيام_التأخير}"
SHOP = "{اسم_المحل}"
VARIABLES = (CUSTOMER, CONTRACT, AMOUNT, DUE_DATE, LATE_DAYS, SHOP)

LATE_TEMPLATE = (
    "السلام عليكم أ/ {اسم_العميل}،\n"
    "نذكّركم بأقساط متأخرة على عقد {رقم_العقد} بإجمالي {المبلغ} ج.م، "
    "أقدمها مستحق بتاريخ {تاريخ_الاستحقاق} (منذ {أيام_التأخير} يومًا).\n"
    "يسعدنا السداد في المحل في أقرب وقت.\n"
    "شكرًا لكم — {اسم_المحل}"
)


class ReminderError(ValueError):
    """Reminder refused, with a message fit to show."""


def late_days(due_date: str, today: str | None = None) -> int:
    """Days past the due date, never negative: an instalment not yet due is late by nothing."""
    now = date.fromisoformat(today) if today else date.today()
    return max((now - date.fromisoformat(due_date)).days, 0)


def render(template: str, values: dict) -> str:
    text = template
    for name in VARIABLES:
        text = text.replace(name, str(values.get(name, "")))
    return text


def wa_link(mobile: str, text: str) -> str:
    """wa.me needs the number in full international form; Egyptian 01xxxxxxxxx becomes 201xxxxxxxxx."""
    digits = "".join(c for c in (mobile or "") if c.isdigit())
    if not digits:
        raise ReminderError("لا يوجد رقم موبايل لهذا العميل")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "20" + digits[1:]
    return f"https://wa.me/{digits}?text={_escape(text)}"


def _escape(text: str) -> str:
    out = []
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in "-._~"):
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    return "".join(out)
