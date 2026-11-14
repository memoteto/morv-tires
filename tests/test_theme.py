"""اختبارات ألوان الثيم (لا تحتاج Qt؛ تفحص نص ملف التنسيق والألوان فقط)."""
import re
import unittest


def luminance(hex_color: str) -> float:
    """السطوع النسبي للون حسب معيار WCAG."""
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    """نسبة التباين بين لونين (من 1 إلى 21)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def qss_text(t: dict) -> str:
    """نص ملف التنسيق دون استيراد Qt: نفس دالة qss من theme.py تُنفَّذ على قالبها."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "morv" / "ui" / "theme.py").read_text(encoding="utf-8")
    body = src.split('return f"""', 1)[1].split('"""', 1)[0]
    return eval('f"""' + body + '"""', {"t": t})


class TooltipTest(unittest.TestCase):
    """خطأ سابق: التلميح ظهر مربعًا أبيض فارغًا في الوضع الداكن (نص فاتح على خلفية Windows البيضاء)."""

    def test_tooltip_styled_and_readable(self):
        """ملف التنسيق يحدد للتلميح خلفية ولون نص، والتباين بينهما 4.5:1 على الأقل في الوضعين."""
        from morv.ui.tokens import DARK, LIGHT
        for name, t in (("light", LIGHT), ("dark", DARK)):
            rule = re.search(r"QToolTip\s*\{([^}]*)\}", qss_text(t))
            self.assertIsNotNone(rule, f"لا قاعدة للتلميح في الوضع {name}")
            bg = re.search(r"background:\s*(#[0-9A-Fa-f]{6})", rule.group(1)).group(1)
            fg = re.search(r"(?<![-\w])color:\s*(#[0-9A-Fa-f]{6})", rule.group(1)).group(1)
            self.assertGreaterEqual(contrast(bg, fg), 4.5, f"تلميح غير مقروء في الوضع {name}")


if __name__ == "__main__":
    unittest.main()
