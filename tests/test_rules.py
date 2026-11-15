"""Automated tests for the CLAUDE.md rules, so they cannot break unnoticed."""
import ast
import io
import tokenize
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "morv"
ARABIC = re.compile(r"[\u0600-\u06FF]")


def py_files(*dirs: Path) -> list[Path]:
    return sorted(f for d in dirs for f in d.rglob("*.py") if "__pycache__" not in f.parts)


def code_lines(path: Path):
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        yield no, line.split("#", 1)[0]


class DocstringTest(unittest.TestCase):
    MAX_FIRST_LINE = 100     # a docstring's first line may not exceed this

    def test_every_module_has_a_short_docstring(self):
        for f in self.files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            if f.name == "__init__.py" and not tree.body:
                continue
            doc = ast.get_docstring(tree)
            rel = f.relative_to(ROOT)
            self.assertTrue(doc and doc.strip(), f"{rel}: the module has no docstring")
            self.assertLessEqual(len(doc.strip().splitlines()[0]), self.MAX_FIRST_LINE,
                                 f"{rel}: the first docstring line is too long")

    def test_no_docstring_first_line_is_too_long(self):
        long = []
        for f in self.files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                doc = ast.get_docstring(node)
                if doc and len(doc.strip().splitlines()[0]) > self.MAX_FIRST_LINE:
                    long.append(f"{f.relative_to(ROOT)}:{node.lineno} {node.name}")
        self.assertEqual(long, [], "docstring first line too long:\n" + "\n".join(long))

    def test_explanations_are_not_arabic(self):
        """Arabic belongs in the program's own messages, not in docstrings or comments."""
        found = []
        for f in self.files() + py_files(ROOT / "tests"):
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                doc = ast.get_docstring(node)
                if doc and ARABIC.search(doc):
                    name = getattr(node, "name", "module")
                    found.append(f"{f.relative_to(ROOT)}: docstring of {name}")
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.COMMENT and ARABIC.search(tok.string):
                    found.append(f"{f.relative_to(ROOT)}:{tok.start[0]}: comment")
        self.assertEqual(found, [], "Arabic explanation in code:\n" + "\n".join(found))

    def files(self) -> list[Path]:
        return py_files(PKG, ROOT / "tools") + [ROOT / "run.py", ROOT / "run_tests.py"]


class LayersTest(unittest.TestCase):
    def imports(self, path: Path) -> set[str]:
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_core_is_pure(self):
        for f in py_files(PKG / "core"):
            bad = self.imports(f) & {"PySide6", "sqlite3"}
            self.assertFalse(bad, f"{f.name} يستورد {bad}")

    def test_ui_has_no_sql(self):
        sql = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\b(FROM|INTO|SET|WHERE)\b")
        for f in py_files(PKG / "ui"):
            self.assertNotIn("sqlite3", self.imports(f), f.name)
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(sql.search(node.value), f"SQL داخل {f.name}")


class MoneyTest(unittest.TestCase):
    def test_no_float_in_core(self):
        for f in py_files(PKG / "core"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                self.assertFalse(isinstance(node, ast.Name) and node.id == "float",
                                 f"float في {f.name}:{getattr(node, 'lineno', '?')}")
                self.assertFalse(isinstance(node, ast.Constant) and type(node.value) is float,
                                 f"رقم عشري في {f.name}:{getattr(node, 'lineno', '?')}")

    def test_no_real_columns(self):
        for f in sorted((PKG / "infra" / "db" / "migrations").glob("*.sql")):
            text = "\n".join(line.split("--", 1)[0] for line in f.read_text(encoding="utf-8").splitlines())
            self.assertIsNone(re.search(r"\b(REAL|FLOAT|DOUBLE)\b", text, re.I), f.name)

    def test_no_double_spinbox(self):
        for f in py_files(PKG):
            self.assertNotIn("QDoubleSpinBox", f.read_text(encoding="utf-8"), f.name)


class UiRulesTest(unittest.TestCase):
    def test_forbidden_calls(self):
        banned = ["FramelessWindowHint", "setIndexWidget", "setDirection(", "QMessageBox"]
        for f in py_files(PKG):
            for no, line in code_lines(f):
                for word in banned:
                    if word == "FramelessWindowHint" and f.name == "modal.py":
                        continue   # only the shared modal is frameless, with its own draggable header
                    self.assertNotIn(word, line, f"{f.name}:{no}")

    def test_align_right_only_absolute(self):
        for f in py_files(PKG):
            for no, line in code_lines(f):
                if "AlignRight" in line:
                    self.assertIn("AlignAbsolute", line, f"{f.name}:{no}")

    def test_single_stylesheet_and_direction(self):
        for f in py_files(PKG):
            if f.name == "theme.py":
                continue
            for no, line in code_lines(f):
                self.assertNotIn("setStyleSheet(", line, f"{f.name}:{no}")
                self.assertNotIn("setLayoutDirection(", line, f"{f.name}:{no}")


if __name__ == "__main__":
    unittest.main()
