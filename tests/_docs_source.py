# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-reading helpers shared by the docs guards (#749).

The docs guards compare what a page prints with what ``src/tether`` holds, and they read
the source with :mod:`ast` rather than by importing ``tether``: no third-party dependency,
no module-import side effects, and the docs are checked against the source beside them.
Two guards used to carry their own copy of the same reader, and the copies drifted apart
-- one returned the *first* binding of a module constant and the other the *last* -- so
the readers live here, once.

It is a plain helper module (not a ``test_*`` file, so pytest never collects it) and,
under pytest's default ``prepend`` import mode with no ``tests/__init__.py``, is importable
as a top-level module on every OS in the CI matrix, like ``tests/_analysis_store.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path


def module_constant(src: Path, dotted: str, name: str) -> object:
    """The module-level value of ``name`` in module ``dotted`` under ``src``.

    The **last** top-level binding is the one returned, because it is the one the runtime
    uses: a module that binds ``X = 5.0`` and then ``X = 8.0`` is read as ``8.0``.
    Returning the first would pin a page to a value the code never uses. An annotation
    with no value (``X: float``) binds nothing at runtime, so the assignment before it
    stays the effective one.
    """
    path = src / Path(*dotted.split(".")).with_suffix(".py")
    bound: ast.expr | None = None
    for stmt in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name and stmt.value:
                bound = stmt.value
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    bound = stmt.value
    if bound is None:
        raise AssertionError(f"{dotted} has no module-level constant {name!r}")
    return ast.literal_eval(bound)


# --- Calls in a Python code fence, checked against the source signature ------------


def module_tree(src: Path, dotted: str) -> ast.Module:
    """``tether.x.y`` (a module or a package) parsed from under ``src``."""
    path = src / Path(*dotted.split("."))
    path = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
    return ast.parse(path.read_text(encoding="utf-8"))


def top_level(tree: ast.Module, name: str) -> ast.AST | None:
    """The top-level ``def`` or ``class`` called ``name`` in ``tree``, if there is one."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
    return None


def check_call(
    call: ast.Call, func: ast.FunctionDef, *, bound: bool, spreads: dict[str, list[str]]
) -> list[str]:
    """Why ``call`` would fail against ``func``'s signature (an empty list when it would not).

    ``bound`` drops the first parameter (``self`` or ``cls``). ``spreads`` maps a name the
    fence binds to ``dict(k=...)`` to its keys, so ``f(**name)`` is checked key by key.
    """
    args = func.args
    positional = [a.arg for a in args.posonlyargs + args.args][1 if bound else 0 :]
    keyword_ok = set(positional[len(args.posonlyargs) :]) | {a.arg for a in args.kwonlyargs}
    problems = []
    if args.vararg is None and len(call.args) > len(positional):
        problems.append(f"{len(call.args)} positional arguments, at most {len(positional)}")
    given = set(positional[: len(call.args)])
    names = []
    for kw in call.keywords:
        if kw.arg is not None:
            names.append(kw.arg)
        elif isinstance(kw.value, ast.Name) and kw.value.id in spreads:
            names += spreads[kw.value.id]
        else:
            problems.append("a ** argument whose keys the page does not spell out")
    for arg in names:
        if arg not in keyword_ok and args.kwarg is None:
            problems.append(f"no parameter {arg!r}")
        given.add(arg)
    n_pos_defaults = len(args.defaults)
    required = positional[: len(positional) - n_pos_defaults] + [
        a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is None
    ]
    problems += [f"missing {r!r}" for r in required if r not in given]
    return problems


def dict_literals(tree: ast.Module) -> dict[str, list[str]]:
    """``name = dict(k=...)`` assignments, so ``f(**name)`` can be checked key by key."""
    found = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "dict"
            and not node.value.args
        ):
            found[node.targets[0].id] = [kw.arg for kw in node.value.keywords]
    return found


def tether_calls(
    src: Path, tree: ast.Module
) -> list[tuple[str, ast.Call, ast.FunctionDef | None, bool]]:
    """Every call in ``tree`` of a name the fence imports from ``tether``.

    Returns ``(label, call, def, bound)``: ``f(...)`` for an imported function and
    ``Cls.meth(...)`` for a method of an imported class. ``def`` is ``None`` when the
    imported name has no such top-level ``def`` -- a class called directly, say -- so the
    caller decides what that means rather than this reader skipping it.
    """
    imported = {
        alias.asname or alias.name: node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tether")
        for alias in node.names
    }
    found: list[tuple[str, ast.Call, ast.FunctionDef | None, bool]] = []
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        if isinstance(call.func, ast.Name) and call.func.id in imported:
            target = top_level(module_tree(src, imported[call.func.id]), call.func.id)
            label, bound = call.func.id, False
        elif (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in imported
        ):
            owner = top_level(module_tree(src, imported[call.func.value.id]), call.func.value.id)
            target = (
                top_level(ast.Module(body=owner.body, type_ignores=[]), call.func.attr)
                if isinstance(owner, ast.ClassDef)
                else None
            )
            label, bound = f"{call.func.value.id}.{call.func.attr}", True
        else:
            continue
        found.append((label, call, target if isinstance(target, ast.FunctionDef) else None, bound))
    return found
