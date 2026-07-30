# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The idealization timeout has one source of truth (#222).

Before this guard, ``1800.0`` was written as five independent literals — on
:func:`tether.idealize.run_vbfret`, on ``idealize_molecules`` and ``reidealize`` in
:mod:`tether.project.idealize`, and on the two :class:`~tether.project.core.Project`
wrappers — beside a sixth in :data:`tether.idealize.supervisor.DEFAULT_SIDECAR_TIMEOUT`,
which is the one the parameter reference page documents and
``tests/test_docs_parameter_defaults.py`` pins. Nothing tied them together, so changing
the documented constant alone would have left the page right about one advertised
surface and silently wrong about the other four.

Three checks, because each is satisfiable while the defect returns by another route:

* **AST, per surface** — the *spelling* of each default is the name
  ``DEFAULT_SIDECAR_TIMEOUT``, not a number. A value check cannot see the difference
  between a name and a literal that currently happens to agree, which is precisely the
  state this issue describes.
* **AST, per module** — ``1800`` appears across these modules exactly once, in the
  canonical assignment. This catches what an enumeration cannot: a *new* surface, or a
  call site, that quietly writes the number again.
* **Runtime** — every surface's live default is the same object as the canonical
  constant, so a name that resolved to some *other* module's constant would still fail.

The AST halves parse the source rather than importing, mirroring
``tests/test_docs_parameter_defaults.py``: reading the text is what makes "no literal
here" checkable at all, and it keeps those checks meaningful even where ``tether``
itself will not import. ``tether`` is therefore imported inside the runtime tests only.

The canonical home is :mod:`tether.idealize.driver` rather than the supervisor that
first declared it, because the supervisor imports the driver and the reverse would be a
cycle. ``supervisor.DEFAULT_SIDECAR_TIMEOUT`` remains a supported import path.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

CANONICAL_MODULE = "tether.idealize.driver"
CONSTANT = "DEFAULT_SIDECAR_TIMEOUT"

#: Every public surface that forwards a ``timeout`` to the sidecar, as
#: ``(module, qualified function, parameter)``. A method is spelled ``Class.method``.
TIMEOUT_SURFACES = [
    ("tether.idealize.driver", "run_vbfret", "timeout"),
    ("tether.project.idealize", "idealize_molecules", "timeout"),
    ("tether.project.idealize", "reidealize", "timeout"),
    ("tether.project.core", "Project.idealize", "timeout"),
    ("tether.project.core", "Project.reidealize", "timeout"),
]


def _module_source(dotted: str) -> ast.Module:
    path = SRC / Path(*dotted.split(".")).with_suffix(".py")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(dotted: str, qualified: str) -> ast.FunctionDef:
    """The ``def`` node for ``qualified``, which may be ``Class.method``."""
    *owner, name = qualified.split(".")
    body: list[ast.stmt] = _module_source(dotted).body
    for part in owner:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef) and stmt.name == part:
                body = stmt.body
                break
        else:  # pragma: no cover - a typo in the table, not a product state
            raise AssertionError(f"{dotted} has no class {part!r}")
    for stmt in body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == name:
            return stmt
    raise AssertionError(f"{dotted} has no function {qualified!r}")


def _default_node(func: ast.FunctionDef, param: str) -> ast.expr:
    """The default expression for ``param``, positional or keyword-only."""
    positional = func.args.posonlyargs + func.args.args
    defaulted = positional[len(positional) - len(func.args.defaults) :]
    pairs: list[tuple[str, ast.expr | None]] = [
        (arg.arg, default) for arg, default in zip(defaulted, func.args.defaults, strict=True)
    ]
    pairs += list(
        zip(
            (arg.arg for arg in func.args.kwonlyargs),
            func.args.kw_defaults,
            strict=True,
        )
    )
    for name, node in pairs:
        if name == param:
            if node is None:
                raise AssertionError(f"{func.name} parameter {param!r} has no default")
            return node
    raise AssertionError(f"{func.name} has no defaulted parameter {param!r}")


def _live(dotted: str, qualified: str) -> object:
    """Import ``dotted`` and resolve ``qualified``, which may be ``Class.method``."""
    module = __import__(dotted, fromlist=["_"])
    target: object = module
    for part in qualified.split("."):
        target = getattr(target, part)
    return target


@pytest.mark.parametrize(("module", "func", "param"), TIMEOUT_SURFACES)
def test_the_default_is_spelled_as_the_constant(module: str, func: str, param: str) -> None:
    """No surface repeats the number; each names the shared constant."""
    node = _default_node(_function(module, func), param)
    assert isinstance(node, ast.Name), (
        f"{module}.{func} defaults {param!r} to {ast.dump(node)} — it must name "
        f"{CONSTANT} so the value has one source of truth (#222)"
    )
    assert node.id == CONSTANT


@pytest.mark.parametrize("module", sorted({module for module, _, _ in TIMEOUT_SURFACES}))
def test_the_only_timeout_literal_is_the_canonical_assignment(module: str) -> None:
    """``1800`` appears in these modules exactly once, as the constant's own value.

    The parametrized checks above pin the surfaces this issue enumerated. This one is
    the sweep behind them: a *new* surface that quietly repeats the number, or a stray
    ``timeout=1800.0`` left in a call site, fails here even though no listed default
    changed. That is the failure mode #222 describes, and enumerating known surfaces
    cannot catch it.
    """
    tree = _module_source(module)
    literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in (1800, 1800.0)
    ]
    expected = 1 if module == CANONICAL_MODULE else 0
    assert len(literals) == expected, (
        f"{module} spells the idealization timeout as a literal on "
        f"line(s) {[node.lineno for node in literals]} — use {CONSTANT} (#222)"
    )


@pytest.mark.parametrize(("module", "func", "param"), TIMEOUT_SURFACES)
def test_the_live_default_is_the_canonical_constant(module: str, func: str, param: str) -> None:
    """The name each surface resolves at import time is the canonical object."""
    canonical = _live(CANONICAL_MODULE, CONSTANT)
    default = inspect.signature(_live(module, func)).parameters[param].default
    assert default is canonical


def test_the_supervisor_import_path_still_works() -> None:
    """``supervisor.DEFAULT_SIDECAR_TIMEOUT`` is the documented path and stays supported.

    ``docs/reference/parameters.md`` and ``docs/stability.md`` both name the supervisor,
    and ``tests/test_docs_parameter_defaults.py`` pins the documented value. The move to
    the driver is a re-home, not a removal.
    """
    from tether.idealize import supervisor

    assert supervisor.DEFAULT_SIDECAR_TIMEOUT is _live(CANONICAL_MODULE, CONSTANT)
    assert CONSTANT in supervisor.__all__


def test_the_canonical_value_is_unchanged() -> None:
    """The re-home moves the declaration, never the number.

    ``docs/stability.md`` calls a silently changed default a breaking change, so the
    value is pinned here as well as through the documentation guard.
    """
    assert _live(CANONICAL_MODULE, CONSTANT) == pytest.approx(1800.0)


def test_none_still_means_wait_indefinitely(tmp_path: Path) -> None:
    """``None`` is a real value on this parameter, not "use the default".

    The constant is the default, so ``None`` must keep reaching ``subprocess.run``
    untouched — the caller asked to wait forever. A refactor that resolved ``None`` to
    the constant would silently start killing long cold fits.
    """
    from tether.idealize import driver

    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(cmd, 0.0)

    # Swap the module reference rather than the stdlib function, so nothing outside
    # run_vbfret's own lookup is affected. TimeoutExpired has to come along: the
    # function catches it by attribute off this same name.
    shim = SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(driver, "subprocess", shim)
        patch.setattr(driver, "resolve_sidecar_python", lambda _: "python")
        # Any existing file: run_vbfret only stats the path before the faked run.
        with pytest.raises(driver.SidecarError):
            driver.run_vbfret(Path(__file__), timeout=None, model_out=tmp_path / "model.hdf5")
    assert seen["timeout"] is None
