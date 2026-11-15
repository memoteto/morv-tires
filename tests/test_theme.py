"""Theme colour tests, without Qt."""
import re
import unittest


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def qss_text(t: dict) -> str:
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "morv" / "ui" / "theme.py").read_text(encoding="utf-8")
    body = src.split('return f"""', 1)[1].split('"""', 1)[0]
    return eval('f"""' + body + '"""', {"t": t})


class TooltipTest(unittest.TestCase):
    def test_tooltip_styled_and_readable(self):
        from morv.ui.tokens import DARK, LIGHT
        for name, t in (("light", {**LIGHT, "fs": 14}), ("dark", {**DARK, "fs": 14})):
            rule = re.search(r"QToolTip\s*\{([^}]*)\}", qss_text(t))
            self.assertIsNotNone(rule, f"لا قاعدة للتلميح في الوضع {name}")
            bg = re.search(r"background:\s*(#[0-9A-Fa-f]{6})", rule.group(1)).group(1)
            fg = re.search(r"(?<![-\w])color:\s*(#[0-9A-Fa-f]{6})", rule.group(1)).group(1)
            self.assertGreaterEqual(contrast(bg, fg), 4.5, f"تلميح غير مقروء في الوضع {name}")


class ObjectNamesTest(unittest.TestCase):
    # names used to find a widget, not to style it
    LOOKUP_ONLY = {"modal"}

    def test_every_object_name_has_a_rule(self):
        from pathlib import Path
        from morv.ui.tokens import LIGHT
        css = qss_text({**LIGHT, "fs": 14})
        ui = Path(__file__).resolve().parent.parent / "morv" / "ui"
        names = set()
        for f in ui.glob("*.py"):
            names |= set(re.findall(r'setObjectName\("(\w+)"\)', f.read_text(encoding="utf-8")))
            # names inside dicts such as {"error": "noticeError"}
            names |= set(re.findall(r'"(notice\w+|pill\w+|wizStep\w*)"', f.read_text(encoding="utf-8")))
        missing = sorted(n for n in names - self.LOOKUP_ONLY if f"#{n}" not in css)
        self.assertEqual(missing, [], f"أسماء بلا تنسيق: {missing}")


if __name__ == "__main__":
    unittest.main()
