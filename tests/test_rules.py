"""اختبارات آلية لقواعد CLAUDE.md، حتى لا تُكسر القواعد دون أن يلاحظ أحد.

كل قاعدة هنا تفحص الكود نفسه (نصًا أو شجرة AST) ولا تشغّله.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "morv"
ARABIC = re.compile(r"[؀-ۿ]")


def py_files(*dirs: Path) -> list[Path]:
    """كل ملفات بايثون داخل المجلدات المعطاة."""
    return sorted(f for d in dirs for f in d.rglob("*.py") if "__pycache__" not in f.parts)


def code_lines(path: Path):
    """أسطر الكود (رقم السطر، النص) بعد حذف التعليقات، حتى لا يُحاسب الشرح العربي."""
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        yield no, line.split("#", 1)[0]


class ArabicDocsTest(unittest.TestCase):
    """كل ملف وكلاس ودالة يُشرح منطقه بالعربية."""

    def test_docstrings_are_arabic(self):
        """يفحص الملفات في morv و tools و run.py. الدوال الخاصة مثل __init__ مستثناة
        إلا إذا كان فيها منطق (أكثر من 3 أسطر)."""
        missing = []
        files = py_files(PKG, ROOT / "tools") + [ROOT / "run.py"]
        for f in files:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            rel = f.relative_to(ROOT)
            is_empty_init = f.name == "__init__.py" and not tree.body
            if not is_empty_init and not ARABIC.search(ast.get_docstring(tree) or ""):
                missing.append(f"{rel}: الملف")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                dunder = node.name.startswith("__") and node.name.endswith("__")
                short = node.end_lineno - node.lineno < 3
                if dunder and short:
                    continue
                if not ARABIC.search(ast.get_docstring(node) or ""):
                    missing.append(f"{rel}:{node.lineno} {node.name}")
        self.assertEqual(missing, [], "ينقصها شرح عربي:\n" + "\n".join(missing))


class LayersTest(unittest.TestCase):
    """فصل الطبقات: core بلا Qt ولا SQL، و ui بلا SQL."""

    def imports(self, path: Path) -> set[str]:
        """أسماء الحزم التي يستوردها الملف (المستوى الأول فقط)."""
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_core_is_pure(self):
        """core لا يستورد Qt ولا sqlite3."""
        for f in py_files(PKG / "core"):
            bad = self.imports(f) & {"PySide6", "sqlite3"}
            self.assertFalse(bad, f"{f.name} يستورد {bad}")

    def test_ui_has_no_sql(self):
        """ui لا يستورد sqlite3 ولا يكتب أوامر SQL."""
        sql = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\b(FROM|INTO|SET|WHERE)\b")
        for f in py_files(PKG / "ui"):
            self.assertNotIn("sqlite3", self.imports(f), f.name)
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(sql.search(node.value), f"SQL داخل {f.name}")


class MoneyTest(unittest.TestCase):
    """المال أعداد صحيحة فقط."""

    def test_no_float_in_core(self):
        """لا float ولا أرقام عشرية في core."""
        for f in py_files(PKG / "core"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                self.assertFalse(isinstance(node, ast.Name) and node.id == "float",
                                 f"float في {f.name}:{getattr(node, 'lineno', '?')}")
                self.assertFalse(isinstance(node, ast.Constant) and type(node.value) is float,
                                 f"رقم عشري في {f.name}:{getattr(node, 'lineno', '?')}")

    def test_no_real_columns(self):
        """لا أعمدة REAL في أي ترقية."""
        for f in sorted((PKG / "infra" / "db" / "migrations").glob("*.sql")):
            text = "\n".join(line.split("--", 1)[0] for line in f.read_text(encoding="utf-8").splitlines())
            self.assertIsNone(re.search(r"\b(REAL|FLOAT|DOUBLE)\b", text, re.I), f.name)

    def test_no_double_spinbox(self):
        """QDoubleSpinBox ممنوع في البرنامج كله."""
        for f in py_files(PKG):
            self.assertNotIn("QDoubleSpinBox", f.read_text(encoding="utf-8"), f.name)


class UiRulesTest(unittest.TestCase):
    """قواعد الاتجاه والتنسيق والجداول."""

    def test_forbidden_calls(self):
        """أشياء ممنوعة في الواجهة: نافذة بلا إطار، وعناصر داخل صفوف الجداول، وقلب الاتجاه."""
        banned = ["FramelessWindowHint", "setIndexWidget", "setDirection("]
        for f in py_files(PKG):
            for no, line in code_lines(f):
                for word in banned:
                    self.assertNotIn(word, line, f"{f.name}:{no}")

    def test_align_right_only_absolute(self):
        """AlignRight مسموحة فقط مع AlignAbsolute (للطباعة)، وإلا تنقلب في RTL."""
        for f in py_files(PKG):
            for no, line in code_lines(f):
                if "AlignRight" in line:
                    self.assertIn("AlignAbsolute", line, f"{f.name}:{no}")

    def test_single_stylesheet_and_direction(self):
        """setStyleSheet و setLayoutDirection تُستدعيان في theme.py فقط."""
        for f in py_files(PKG):
            if f.name == "theme.py":
                continue
            for no, line in code_lines(f):
                self.assertNotIn("setStyleSheet(", line, f"{f.name}:{no}")
                self.assertNotIn("setLayoutDirection(", line, f"{f.name}:{no}")


if __name__ == "__main__":
    unittest.main()
