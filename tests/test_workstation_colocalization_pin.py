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


def _pinned_names(tree: ast.AST) -> set[str]:
    """The names that every assignment to them binds to a donor-anchored ``ExtractOptions``."""
    bound: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    ok = _names_the_rule(node.value)
                    bound[target.id] = bound.get(target.id, True) and ok
    return {name for name, ok in bound.items() if ok}


def _extract_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "extract_movie":
                calls.append(node)
    return calls


def unpinned_calls(source: str) -> list[int]:
    """The line of each ``extract_movie`` call in *source* that does not name the rule."""
    tree = ast.parse(source)
    pinned = _pinned_names(tree)
    lines = []
    for call in _extract_calls(tree):
        options = next((kw.value for kw in call.keywords if kw.arg == "options"), None)
        if isinstance(options, ast.Name) and options.id in pinned:
            continue
        if _names_the_rule(options):
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
    ],
)
def test_the_check_accepts_a_call_that_names_the_rule(source: str) -> None:
    assert unpinned_calls(source) == []
