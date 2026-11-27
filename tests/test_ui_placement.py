"""Pins the frozen section ownership map to the shell, so a new section can't land unnoticed."""
import ast
import json
import unittest
from collections import Counter
from pathlib import Path

from morv.ui import placement

UI = Path(__file__).resolve().parent.parent / "morv" / "ui"
SHELL = UI / "shell.py"
LEGACY = Path(__file__).with_name("ui_legacy_action_sites.json")
BUTTON_FACTORIES = {
    "QPushButton", "QToolButton", "primary_button", "danger_button",
    "icon_button", "menu_button", "deferred_button",
}
ACTION_HOOKS = {
    "header_feature_action": ("extra_actions", "page_actions"),
    "record_feature_action": ("build_preview", "record_actions"),
}


def _called_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


class ActionSites(ast.NodeVisitor):
    def __init__(self, filename, source):
        self.filename = filename
        self.lines = source.splitlines()
        self.scope = []
        self.legacy_calls = Counter()
        self.problems = []
        self.aliases = {}
        self.visit(ast.parse(source))

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name in BUTTON_FACTORIES:
                self.aliases[alias.asname or alias.name] = alias.name

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_ClassDef
    visit_AsyncFunctionDef = visit_ClassDef

    def visit_Call(self, node):
        name = _called_name(node.func)
        if self.aliases.get(name, name) in BUTTON_FACTORIES:
            key = (self.filename, ".".join(self.scope), self.lines[node.lineno - 1].strip())
            self.legacy_calls[key] += 1
        if name == "_feature_button":
            self.problems.append(f"{self.filename}:{node.lineno}: use a public placement helper")
        if name in ACTION_HOOKS:
            hook, slot = ACTION_HOOKS[name]
            where = f"{self.filename}:{node.lineno}"
            if (len(self.scope) != 2 or self.scope[-1] != hook or not node.args
                    or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str)
                    or not isinstance(node.func, ast.Attribute)
                    or not isinstance(node.func.value, ast.Name) or node.func.value.id != "self"):
                self.problems.append(f"{where}: {name} requires a literal feature ID in {hook}")
            else:
                try:
                    placement.validate_list_action(
                        node.args[0].value, Path(self.filename).stem, self.scope[0], slot)
                except ValueError as exc:
                    self.problems.append(f"{where}: {exc}")
        self.generic_visit(node)


def _new_local_buttons(sites, allowed):
    return sites.legacy_calls - allowed


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


class FeaturePlacementTest(unittest.TestCase):
    def test_feature_domains_and_slots_are_known(self):
        self.assertEqual(set(placement.DOMAIN_OWNER.values()), set(placement.SECTIONS))
        for feature, slot in placement.FEATURE_SLOT.items():
            self.assertIn(feature.split(".")[0], placement.DOMAIN_OWNER)
            self.assertIn(slot, placement.SLOTS)
        for (module, page), section in placement.LIST_HOSTS.items():
            tree = ast.parse((UI / f"{module}.py").read_text(encoding="utf-8"))
            self.assertTrue(any(isinstance(n, ast.ClassDef) and n.name == page for n in tree.body))
            self.assertIn(section, placement.SECTIONS)
        for feature, host in placement.FEATURE_HOST.items():
            placement.validate_list_action(feature, *host, placement.FEATURE_SLOT[feature])

    def test_new_page_buttons_follow_the_central_action_path(self):
        allowed = Counter()
        for filename, scope, line, count in json.loads(LEGACY.read_text(encoding="utf-8")):
            allowed[filename, scope, line] += count
        problems = []
        for path in sorted(UI.glob("*.py")):
            if not path.stem.endswith(("_page", "_pages", "_tab")):
                continue
            sites = ActionSites(path.name, path.read_text(encoding="utf-8"))
            problems.extend(sites.problems)
            for (filename, scope, line), count in _new_local_buttons(sites, allowed).items():
                problems.append(f"{filename}:{scope}: {count} new local button(s): {line}")
        self.assertEqual(problems, [], "Use registered actions or shared controls:\n" + "\n".join(problems))

    def test_new_button_inside_an_old_method_is_not_grandfathered(self):
        source = 'class PurchasesListPage:\n    def build_preview(self):\n        btn = QPushButton("Old")\n'
        old = ActionSites("purchases_page.py", source)
        allowed = old.legacy_calls.copy()
        changed = ActionSites("purchases_page.py", source + '        btn = QPushButton("New")\n')
        self.assertFalse(_new_local_buttons(old, allowed))
        self.assertEqual(sum(_new_local_buttons(changed, allowed).values()), 1)
        duplicate = ActionSites("purchases_page.py", source + '        btn = QPushButton("Old")\n')
        self.assertEqual(sum(_new_local_buttons(duplicate, allowed).values()), 1)
        moved = ActionSites("purchases_page.py", source.replace("build_preview", "extra_actions"))
        self.assertEqual(sum(_new_local_buttons(moved, allowed).values()), 1)

    def test_wrong_section_or_slot_fails_and_approved_location_passes(self):
        source = ('class PurchasesListPage:\n    def build_preview(self):\n'
                  '        self.record_feature_action("purchase.acquisition_cost.create", text, run, icon)\n')
        self.assertEqual(ActionSites("purchases_page.py", source).problems, [])
        wrong = source.replace("PurchasesListPage", "ReportsPage")
        self.assertTrue(ActionSites("reports_page.py", wrong).problems)
        self.assertTrue(ActionSites("purchases_page.py", source.replace("build_preview", "extra_actions")).problems)
        wrong_slot = source.replace("record_feature_action", "header_feature_action").replace(
            "build_preview", "extra_actions")
        self.assertTrue(ActionSites("purchases_page.py", wrong_slot).problems)
        self.assertTrue(ActionSites("purchases_page.py", source.replace("purchase.acquisition_cost.create",
                                                                        "purchase.unknown")).problems)
        with self.assertRaises(ValueError):
            placement.validate_list_action("purchase.acquisition_cost.create", "reports_page",
                                           "ReportsPage", "record_actions")
        with self.assertRaises(ValueError):
            placement.validate_list_action("inventory.valuation.create", "inventory_page",
                                           "ItemsPage", "page_actions")


if __name__ == "__main__":
    unittest.main()
