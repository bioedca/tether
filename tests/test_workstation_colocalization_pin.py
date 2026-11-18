# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The workstation callers of ``extract_movie`` name the donor-anchored rule (#1231).

The speed history (#723) and oracle (a) on more acquisitions (#715) run only on the
workstation, so CI never extracts with them. This test reads their source instead. Both compare
a run with numbers made under the donor-anchored rule: the earlier speed records, and the
ADR-0022 limits. Since #1189 made partner-in-both the default (ADR-0127), a call that names no
rule measures fewer molecules. That moved the 2026-11-18 speed record about three times and the
lab-08 recall below 0.95, with no change of code speed or of detection.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Every workstation module that extracts a movie and compares the result with earlier numbers.
CALLERS = ("scripts/workstation/perf_stage.py", "tests/test_deeplasi_agreement.py")

RULE = "donor-anchored"


def _names_the_rule(node: ast.expr | None) -> bool:
    """True when *node* is ``ExtractOptions(colocalization="donor-anchored", ...)``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if called != "ExtractOptions":
        return False
    return any(
        kw.arg == "colocalization" and isinstance(kw.value, ast.Constant) and kw.value.value == RULE
        for kw in node.keywords
    )


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_nodes(scope: ast.AST):
    """The nodes of *scope* that belong to it and not to a function nested inside it."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, _SCOPES):
            todo.extend(ast.iter_child_nodes(node))


def _binding(scope: ast.AST, name: str) -> bool | None:
    """How *scope* binds *name*: True if only to the rule, False if otherwise, None if not."""
    if isinstance(scope, _SCOPES):
        args = scope.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
        if any(a is not None and a.arg == name for a in params):
            return False  # a parameter is whatever the caller passes
    nodes = list(_own_nodes(scope))
    plain = {id(t) for a in nodes if isinstance(a, ast.Assign) for t in a.targets}
    found: bool | None = None
    for node in nodes:
        if isinstance(node, _SCOPES) and getattr(node, "name", None) == name:
            return False
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                found = (found is not False) and _names_the_rule(node.value)
        elif (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, ast.Store)
            and id(node) not in plain
        ):
            return False  # a for target, with/as, walrus or augmented assignment
    return found


def _extract_calls(tree: ast.AST) -> list[tuple[ast.Call, list[ast.AST]]]:
    """Each ``extract_movie`` call with its enclosing scopes, the innermost last."""
    calls: list[tuple[ast.Call, list[ast.AST]]] = []

    def visit(node: ast.AST, scopes: list[ast.AST]) -> None:
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "extract_movie":
                calls.append((node, scopes))
        inner = [*scopes, node] if isinstance(node, _SCOPES) else scopes
        for child in ast.iter_child_nodes(node):
            visit(child, inner)

    visit(tree, [tree])
    return calls


def unpinned_calls(source: str) -> list[int]:
    """The line of each ``extract_movie`` call in *source* that does not name the rule.

    A name passed as ``options`` counts when the nearest scope that binds it, the way Python
    looks it up, binds it only to a donor-anchored ``ExtractOptions``. A parameter, a loop
    target or any other binding counts as not pinned.
    """
    lines = []
    for call, scopes in _extract_calls(ast.parse(source)):
        options = next((kw.value for kw in call.keywords if kw.arg == "options"), None)
        if _names_the_rule(options):
            continue
        if isinstance(options, ast.Name):
            binding = next(
                (b for b in (_binding(s, options.id) for s in reversed(scopes)) if b is not None),
                None,
            )
            if binding:
                continue
        lines.append(call.lineno)
    return lines


@pytest.mark.parametrize("path", CALLERS)
def test_every_workstation_extraction_names_the_donor_anchored_rule(path: str) -> None:
    source = (ROOT / path).read_text(encoding="utf-8")
    assert _extract_calls(ast.parse(source)), f"{path} no longer calls extract_movie"
    bad = unpinned_calls(source)
    assert not bad, (
        f"{path}: extract_movie at line(s) {bad} does not pass "
        f'options=ExtractOptions(colocalization="{RULE}") (#1231, ADR-0127)'
    )


@pytest.mark.parametrize(
    "source",
    [
        "extract_movie(m, o)",
        "extract_movie(m, o, options=ExtractOptions())",
        "extract_movie(m, o, options=ExtractOptions(colocalization='partner-in-both'))",
        "opts = ExtractOptions(colocalization='donor-anchored')\n"
        "opts = ExtractOptions()\n"
        "extract_movie(m, o, options=opts)",
        "def unrelated():\n"
        "    options = ExtractOptions(colocalization='donor-anchored')\n"
        "def run(options=None):\n"
        "    extract_movie(m, o, options=options)",
        "options = ExtractOptions(colocalization='donor-anchored')\n"
        "def run():\n"
        "    options = ExtractOptions()\n"
        "    extract_movie(m, o, options=options)",
        "for options in (None,):\n    extract_movie(m, o, options=options)",
    ],
)
def test_the_check_finds_a_call_that_does_not_name_the_rule(source: str) -> None:
    assert unpinned_calls(source)


@pytest.mark.parametrize(
    "source",
    [
        "extract_movie(m, o, options=ExtractOptions(colocalization='donor-anchored'))",
        "opts = ExtractOptions(colocalization='donor-anchored')\nextract_movie(m, o, options=opts)",
        "x.extract_movie(m, o, options=x.ExtractOptions(colocalization='donor-anchored'))",
        "def stage():\n"
        "    options = ExtractOptions(colocalization='donor-anchored')\n"
        "    def extract():\n"
        "        return extract_movie(m, o, options=options)\n"
        "    return extract",
    ],
)
def test_the_check_accepts_a_call_that_names_the_rule(source: str) -> None:
    assert unpinned_calls(source) == []
