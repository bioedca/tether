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
* **AST, per module** — no ``timeout`` in these modules is written as a number: not a
  parameter default, not a call keyword. This catches what an enumeration cannot: a *new*
  surface, or a call site, that quietly writes a number instead of naming a constant. It
  keys on the *context* (#324), so an unrelated ``1800`` is not part of the contract,
  while a ``timeout=900.0`` is — a second number is a second source of truth too.
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


def _is_number(node: ast.expr | None) -> bool:
    """``node`` is a numeric literal, sign included (``1800.0``, ``-1``), never a bool."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    )


def _timeout_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Every place ``tree`` writes a number where a ``timeout`` is expected.

    Two contexts, the two #222 is about: a parameter *named* ``timeout`` whose default is
    a numeric literal, on any ``def``, ``async def`` or ``lambda``; and a call keyword
    ``timeout=<numeric literal>``. Each hit is ``(line, description)``. The value is
    never consulted, so neither an unrelated ``1800`` elsewhere nor a ``timeout`` that
    names a constant — this one or ``DEFAULT_OPEN_CHECK_TIMEOUT`` — is reported.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            args = node.args
            positional = args.posonlyargs + args.args
            defaulted = positional[len(positional) - len(args.defaults) :]
            pairs = list(zip(defaulted, args.defaults, strict=True))
            pairs += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
            owner = getattr(node, "name", "<lambda>")
            for arg, default in pairs:
                if arg.arg == "timeout" and _is_number(default):
                    assert default is not None  # narrowed by _is_number
                    hits.append(
                        (default.lineno, f"{owner}() defaults timeout to {ast.unparse(default)}")
                    )
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "timeout" and _is_number(keyword.value):
                    hits.append(
                        (
                            keyword.value.lineno,
                            f"a call passes timeout={ast.unparse(keyword.value)}",
                        )
                    )
    return sorted(hits)


@pytest.mark.parametrize("module", sorted({module for module, _, _ in TIMEOUT_SURFACES}))
def test_no_timeout_is_written_as_a_number(module: str) -> None:
    """No ``timeout`` default or ``timeout=`` keyword in these modules is a number.

    The parametrized checks above pin the surfaces this issue enumerated. This one is
    the sweep behind them: a *new* surface that quietly repeats the number, or a stray
    ``timeout=1800.0`` left in a call site, fails here even though no listed default
    changed. That is the failure mode #222 describes, and enumerating known surfaces
    cannot catch it.

    It reads the context rather than the value (#324): the canonical assignment and any
    unrelated ``1800`` are not timeouts in this sense and pass, while a timeout written
    as *any* number fails, because a second number is a second source of truth.
    """
    hits = _timeout_literals(_module_source(module))
    assert not hits, (
        f"{module} writes a timeout as a number — "
        + "; ".join(f"line {line}: {what}" for line, what in hits)
        + f". Name a module-level constant instead: {CONSTANT} if this is the "
        "idealization timeout (#222), or a constant of its own, as "
        "DEFAULT_OPEN_CHECK_TIMEOUT is for the open-check"
    )


def test_the_sweep_reads_context_not_the_number() -> None:
    """The sweep's own contract, on source written for the purpose (#324).

    The modules it guards pass it today, so they cannot show that it would *fail* on a
    regression, nor that it ignores an unrelated ``1800``. These snippets do both.
    """
    unrelated = "MAX_FRAMES = 1800\nwindow = [0] * 1800\nlimit = max(1800.0, 2)\n"
    named = (
        "def run(*, timeout=DEFAULT_SIDECAR_TIMEOUT): ...\n"
        "def check(timeout=DEFAULT_OPEN_CHECK_TIMEOUT): ...\n"
        "run(timeout=timeout)\n"
        "check(timeout=None)\n"
        "def lock(timeout_s=30.0): ...\n"
    )
    assert _timeout_literals(ast.parse(unrelated)) == []
    assert _timeout_literals(ast.parse(named)) == []

    surface = "def fit(x, *, timeout: float | None = 1800.0): ...\n"
    call = "fit(x, timeout=1800.0)\n"
    other = "def probe(timeout=900.0): ...\nprobe(timeout=-1)\n"
    assert _timeout_literals(ast.parse(surface)) == [(1, "fit() defaults timeout to 1800.0")]
    assert _timeout_literals(ast.parse(call)) == [(1, "a call passes timeout=1800.0")]
    assert _timeout_literals(ast.parse(other)) == [
        (1, "probe() defaults timeout to 900.0"),
        (2, "a call passes timeout=-1"),
    ]


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
