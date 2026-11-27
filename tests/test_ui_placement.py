"""Pins the frozen section ownership map to the shell, so a new section can't land unnoticed."""
import ast
import unittest
from pathlib import Path

from morv.ui import placement

SHELL = Path(__file__).resolve().parent.parent / "morv" / "ui" / "shell.py"


def _shell_tree() -> ast.Module:
    return ast.parse(SHELL.read_text(encoding="utf-8"))


def _nav_keys(tree: ast.Module) -> list[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "NAV" for t in node.targets):
            return [e.elts[0].value for e in node.value.elts]
    raise AssertionError("NAV not found in shell.py")


def _builder_owners(tree: ast.Module) -> dict[str, str]:
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Attribute) and t.attr == "_builders" for t in node.targets)):
            return {k.value: v.id for k, v in zip(node.value.keys, node.value.values, strict=True)}
    raise AssertionError("_builders not found in shell.py")


class PlacementMapTest(unittest.TestCase):
    def setUp(self):
        self.tree = _shell_tree()

    def test_sections_are_frozen_to_the_nav(self):
        # Adding or removing a top-level section changes this and fails here, forcing the
        # structural decision to the owner instead of landing silently in a feature PR.
        self.assertEqual(tuple(_nav_keys(self.tree)), placement.SECTIONS)

    def test_every_section_has_exactly_one_owner(self):
        self.assertEqual(set(placement.SECTION_OWNER), set(placement.SECTIONS))

    def test_section_owners_match_the_shell_page_builders(self):
        # home is built as DashboardPage in its own branch; the rest come from the _builders map.
        owners = _builder_owners(self.tree) | {"home": "DashboardPage"}
        self.assertEqual(owners, placement.SECTION_OWNER)


if __name__ == "__main__":
    unittest.main()
