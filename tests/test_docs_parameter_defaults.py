# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every default printed on the parameter reference page matches the code.

``docs/reference/parameters.md`` re-homes the user-facing analysis parameters out of
the unpublished spec, and CONTRIBUTING's accuracy bar makes a *wrong* documented
default worse than an absent one: a scientist reads the page, believes the number,
and reports it in a methods section. This module is the durable guard against that —
if a constant moves in ``src/`` and the page is not updated, the test fails.

The defaults are read straight out of the source with :mod:`ast` rather than by
importing ``tether``, so the guard runs on the plain 3-OS ``test`` matrix without
pulling SciPy/h5py/matplotlib in and without executing module-import side effects.

A registry entry reads its live value from a module constant
(:func:`module_constant`), a dataclass field (:func:`dataclass_field`) or a
function-signature default (:func:`function_default`) — the last because much of what
the page prints (the per-mode detector thresholds, ``intensity_quantity``, the
population functions' boolean switches) is a plain keyword default with no constant
behind it.

The page side is parsed from its Markdown tables: for each registered entry, *every*
row whose **Parameter** cell carries the entry's backticked tokens must state the live
value in its **Default** cell. A parameter documented twice (``min_window_frames``
gates both the leakage and the gamma estimator) therefore has to agree with itself as
well as with the code. A second pass walks the rows rather than the entries, so on a
row that prints several values no drifted default can hide behind a sibling's.

Both passes originally looked only at the *set* of literals in a Default cell, which is
blind on the two rows that key their values to a **mode name** rather than to a
position (``detection_threshold``, ``min_separation``). Swapping those labels leaves
every literal present and distinct, so the guard stayed green while the page printed
the wrong per-mode number. A registry entry may therefore carry mode labels, and a
labelled value must appear next to *its own* label — see :class:`Claim`.

A default spelled as a constant (``bins=DEFAULT_NBINS``) is two links, page -> constant
and constant -> signature, and the registry pins only the first. The second is
:data:`_CONSTANT_BACKED_SURFACES`, and that table is itself checked against the page: the
**Set via** column is read, every keyword a constant-naming row spells is resolved in the
source, and each one defaulted to a name must be in the table (#442). The table, in turn,
compares the constant each default's name is *bound* to -- followed through imports to the
module that assigns it -- not merely the name it is spelled with (#513). Only the keywords a
cell *spells* are reached: a surface written bare (``train_classifier(…)``) or with only some
of its keywords spelled leaves its other constant-backed defaults unbound by this module.
"""

from __future__ import annotations

import ast
import functools
import re
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "reference" / "parameters.md"
SRC = ROOT / "src"

# A Markdown table body row: at least the six documented columns. Header/separator
# rows are filtered out by the separator pattern below.
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
_BACKTICKED_RE = re.compile(r"`([^`]+)`")
# A module-level constant as the page spells one: SCREAMING_CASE. Distinguishes the
# constants a Parameter cell names from the keyword arguments beside them.
_CONSTANT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _module_source(dotted: str) -> ast.Module:
    """Parse ``tether.x.y`` from disk (no import, so no third-party dependency)."""
    path = SRC / Path(*dotted.split(".")).with_suffix(".py")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal(node: ast.AST) -> object:
    return ast.literal_eval(node)


def module_constant(dotted: str, name: str) -> object:
    """The module-level value of ``name`` in module ``dotted``."""
    for stmt in _module_source(dotted).body:
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == name:
                assert stmt.value is not None
                return _literal(stmt.value)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _literal(stmt.value)
    raise AssertionError(f"{dotted} has no module-level constant {name!r}")


def function_default(dotted: str, func: str, param: str) -> object:
    """The default of the parameter ``param`` on ``func`` in module ``dotted``.

    Function-signature defaults are the other half of what the page prints. The
    per-mode detector thresholds, ``intensity_quantity``, the population
    functions' boolean switches and the cross-correlation lag arguments are plain
    keyword defaults rather than module constants, so :func:`module_constant`
    cannot reach them and they would otherwise drift unguarded. A default that is
    itself a name (``bins=DEFAULT_NBINS``) is rejected: that value belongs to a
    :func:`module_constant` entry, so no registry line can silently pin a name
    instead of a value.
    """
    for stmt in _module_source(dotted).body:
        if not (isinstance(stmt, ast.FunctionDef) and stmt.name == func):
            continue
        args = stmt.args
        positional = args.posonlyargs + args.args
        defaulted = positional[len(positional) - len(args.defaults) :]
        pairs: list[tuple[str, ast.expr | None]] = [
            (arg.arg, default) for arg, default in zip(defaulted, args.defaults, strict=True)
        ]
        pairs += [
            (arg.arg, default)
            for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True)
        ]
        for name, node in pairs:
            if name != param:
                continue
            if node is None:
                raise AssertionError(f"{dotted}.{func} parameter {param!r} has no default")
            if isinstance(node, ast.Name):
                raise AssertionError(
                    f"{dotted}.{func} defaults {param!r} to the constant {node.id} — "
                    f"pin it with module_constant() instead"
                )
            return _literal(node)
        raise AssertionError(f"{dotted}.{func} has no defaulted parameter {param!r}")
    raise AssertionError(f"{dotted} has no module-level function {func!r}")


def _signature(fn: ast.FunctionDef) -> dict[str, ast.expr | None]:
    """Every named parameter of ``fn`` mapped to its default node (``None`` if it has none).

    ``*args`` / ``**kwargs`` are not named parameters and are left out, so a keyword the
    page spells that is only swallowed by ``**kwargs`` does not count as existing.
    """
    args = fn.args
    positional = args.posonlyargs + args.args
    padded: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    out = dict(zip((a.arg for a in positional), padded + list(args.defaults), strict=True))
    out.update(zip((a.arg for a in args.kwonlyargs), args.kw_defaults, strict=True))
    return out


def _function_def(dotted: str, qualname: str) -> ast.FunctionDef:
    """The ``def`` for ``qualname`` in module ``dotted``: ``func`` or ``Class.method``."""
    owner, _, name = qualname.rpartition(".")
    body: list[ast.stmt] = _module_source(dotted).body
    if owner:
        cls = next((s for s in body if isinstance(s, ast.ClassDef) and s.name == owner), None)
        if cls is None:
            raise AssertionError(f"{dotted} has no class {owner!r}")
        body = cls.body
    for stmt in body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == name:
            return stmt
    kind = "method" if owner else "module-level function"
    raise AssertionError(f"{dotted} has no {kind} {qualname!r}")


def _name_default(where: str, param: str, node: ast.expr | None) -> str:
    """``node``'s constant name, or a failure saying the signature has left the constant."""
    if not isinstance(node, ast.Name):
        raise AssertionError(
            f"{where} no longer defaults {param!r} to a named constant "
            f"(it is {ast.unparse(node) if node is not None else 'absent'}) — the page "
            f"prints the constant's value for this surface, so the two have parted"
        )
    return node.id


def function_default_name(dotted: str, func: str, param: str) -> str:
    """The **name** ``param``'s default is spelled with on ``func`` in module ``dotted``.

    The mirror image of :func:`function_default`, which rejects a name default and sends
    you to :func:`module_constant` instead. That refusal is right -- the printed value
    belongs to the constant's own entry, and pinning a name as if it were a value would be
    a lie -- but it leaves one link unguarded: the constant can sit still while the
    *signature* drifts off it. Nothing else in this module reads a signature whose default
    is a name, so nothing else would notice.

    ``func`` may be dotted, ``Class.method``, because the page documents a method surface
    (``DwellTimeAnalysis.histogram(nbins=…)``) exactly as it documents a function.

    Read through :func:`_keyword_parameters`, the view :func:`_resolve` uses: every row
    of :data:`_CONSTANT_BACKED_SURFACES` is a ``kw=…`` claim, so a parameter moved before
    ``/`` has left the surface the page documents even though its default still names
    the constant.
    """
    signature = _keyword_parameters(_function_def(dotted, func))
    if param not in signature:
        raise AssertionError(
            f"{dotted}.{func} has no parameter {param!r} a caller can pass by keyword"
        )
    return _name_default(f"{dotted}.{func}", param, signature[param])


def dataclass_field(dotted: str, class_name: str, field: str) -> object:
    """The default of ``field`` on the dataclass ``class_name`` in module ``dotted``."""
    for stmt in _module_source(dotted).body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == class_name:
            for item in stmt.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id == field
                    and item.value is not None
                ):
                    return _literal(item.value)
            raise AssertionError(f"{dotted}.{class_name} has no field {field!r}")
    raise AssertionError(f"{dotted} has no class {class_name!r}")


def _callee(node: ast.expr | None) -> str | None:
    """``f`` for a call ``f(…)`` or ``m.f(…)``; ``None`` for anything else."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _field_default(value: ast.expr | None) -> ast.expr | None:
    """A field's default node, looking through ``field(default=…)``.

    ``field(default_factory=…)`` has no default *value* to name, so the call itself is
    returned -- never the factory, which is a bare name (``list``) and would otherwise read
    as a constant default.
    """
    if _callee(value) != "field":
        return value
    assert isinstance(value, ast.Call)
    keywords = {kw.arg: kw.value for kw in value.keywords}
    return keywords.get("default", value if "default_factory" in keywords else None)


def _dataclass_fields(cls: ast.ClassDef) -> dict[str, ast.expr | None]:
    """The annotated fields of a class body mapped to their default nodes."""
    return {
        item.target.id: _field_default(item.value)
        for item in cls.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    }


def dataclass_field_name(dotted: str, class_name: str, field: str) -> str:
    """The **name** the default of dataclass field ``field`` is spelled with.

    :func:`dataclass_field` with the ``literal_eval`` replaced by an ``ast.Name`` check --
    the dataclass counterpart of :func:`function_default_name`, for a field such as
    ``SidecarSupervision.probe_timeout`` that the page documents through its constant.
    Read through :func:`_constructor_keywords`, as :func:`_resolve` reads a dataclass
    token, so a field the constructor no longer takes fails here too.
    """
    for stmt in _module_source(dotted).body:
        if isinstance(stmt, ast.ClassDef) and stmt.name == class_name:
            fields = _constructor_keywords(stmt)
            if field not in fields:
                raise AssertionError(
                    f"{dotted}.{class_name} has no field {field!r} its constructor accepts"
                )
            return _name_default(f"{dotted}.{class_name}", field, fields[field])
    raise AssertionError(f"{dotted} has no class {class_name!r}")


def _is_dataclass(cls: ast.ClassDef) -> bool:
    """Is ``cls`` decorated ``@dataclass`` / ``@dataclass(...)`` / ``@dataclasses.dataclass``?"""
    for decorator in cls.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (isinstance(target, ast.Name) and target.id == "dataclass") or (
            isinstance(target, ast.Attribute) and target.attr == "dataclass"
        ):
            return True
    return False


def surface_default_name(dotted: str, qualname: str, param: str) -> str:
    """The constant name a documented surface's default is spelled with, any resolver.

    A bare ``qualname`` naming a dataclass in ``dotted`` is read as a field
    (:func:`dataclass_field_name`); anything else -- a module-level function or a
    ``Class.method`` -- as a signature (:func:`function_default_name`).
    """
    if "." not in qualname and any(
        isinstance(s, ast.ClassDef) and s.name == qualname and _is_dataclass(s)
        for s in _module_source(dotted).body
    ):
        return dataclass_field_name(dotted, qualname, param)
    return function_default_name(dotted, qualname, param)


# --- Where a constant name is bound -------------------------------------------
# A default is spelled with a *name*, and a name is only as good as what it is bound to.
# ``tether.project.photobleach`` imports ``PB_PRIOR_A``; were that import to become
# ``from tether.fret.photobleach import PB_PRIOR_MU as PB_PRIOR_A``, the signature would
# still read ``a=PB_PRIOR_A`` and default to the wrong value (#513). So the name is followed
# through the module-level imports that bind it to the module that assigns it, and the
# table is compared with what it reaches.


def _parsed(dotted: str) -> ast.Module | str:
    """``dotted`` parsed from under ``SRC`` (a module or a package), or why it cannot be.

    Where ``x.py`` and ``x/__init__.py`` both exist, which one an import reaches is not
    modelled here; the collision is reported instead, so it can only fail.
    """
    base = SRC / Path(*dotted.split("."))
    paths = [p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()]
    if len(paths) > 1:
        return "both a module and a package"
    if not paths:
        return "outside src/"
    return ast.parse(paths[0].read_text(encoding="utf-8"), filename=str(paths[0]))


def _binds(node: ast.AST) -> set[str]:
    """The names ``node`` binds, rebinds or unbinds, in whatever scope, or ``{"*"}`` for a
    star import, which may bind any name.

    Every form Python has: assignment and ``del`` targets, imports, ``def`` and ``class``,
    ``except … as``, the captures of a ``match`` pattern, and a ``global`` or ``nonlocal``
    declaration (through which a function rebinds a module name).
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return {node.id}
    if isinstance(node, ast.alias):
        return {"*" if node.name == "*" else node.asname or node.name.split(".")[0]}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest}
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return set(node.names)
    return set()


def _binding_sites(tree: ast.Module, name: str) -> list[ast.AST]:
    """Every node anywhere in ``tree`` that binds ``name`` or may (:func:`_binds`).

    Deliberately blind to scope: a function-local of the same name counts too. That can only
    make the trace fail, never let it pass, and it means no scoping rule has to be right.
    """
    return [node for node in ast.walk(tree) if {name, "*"} & _binds(node)]


def _absolute(dotted: str, stmt: ast.ImportFrom) -> str | None:
    """The module a ``from … import`` in module ``dotted`` reads, relative levels resolved,
    or ``None`` for a relative import that climbs above the top-level package."""
    if not stmt.level:
        return stmt.module or ""
    parts = dotted.split(".")
    is_package = parts[-1] == "__init__" or (SRC / Path(*parts) / "__init__.py").is_file()
    # A package's ``__init__`` resolves ``.`` against the package itself, a module against
    # the package that holds it. (A name that is both is refused by :func:`_parsed`.)
    package = [p for p in parts if p != "__init__"] if is_package else parts[:-1]
    if stmt.level > len(package):
        return None  # Python: "attempted relative import beyond top-level package"
    base = package[: len(package) - (stmt.level - 1)]
    return ".".join(base + ([stmt.module] if stmt.module else []))


def _package_name(dotted: str) -> str:
    """``pkg.mod`` for ``pkg/mod.py`` and ``pkg`` for ``pkg/__init__.py``, as imports spell it."""
    return dotted.removesuffix(".__init__")


class _Bound(NamedTuple):
    """Where a default's name is bound, the imports followed to get there, and -- unless the
    trace ended on the one plain ``NAME = …`` :func:`module_constant` reads -- why not."""

    module: str
    name: str
    via: tuple[str, ...]
    problem: str | None


def bound_constant(dotted: str, qualname: str, name: str) -> _Bound:
    """Follow the default ``name`` of the surface ``qualname`` in ``dotted`` to its assignment.

    In each module along the way the name must have exactly **one** binding site
    (:func:`_binding_sites`), and it must be a top-level statement: a ``from X import A as B``,
    followed into ``X`` (relative imports resolved), or the plain ``NAME = …`` that ends the
    trace. In the surface's own module that statement must also come before the surface,
    since a ``def`` evaluates its defaults when it runs. Anything else -- a second binding of
    any kind (a shadow, a reassignment, a conditional or class-body rebinding, a ``match``
    capture, a duplicate alias, a star import), a binding nested in another statement, a
    module outside ``src/`` or one that is also a package, or a relative import above the
    top-level package -- ends the trace with the reason in ``problem``, so it can only
    fail. Python's own resolution is never modelled, only ruled out.
    """
    tree = _module_source(dotted)
    owner = qualname.split(".")[0]
    before: int | None = next(
        s.lineno
        for s in tree.body
        if isinstance(s, (ast.FunctionDef, ast.ClassDef)) and s.name == owner
    )
    via: list[str] = []
    while True:
        module = _parsed(dotted)
        if isinstance(module, str):
            return _Bound(dotted, name, tuple(via), module)
        sites = _binding_sites(module, name)
        if len(sites) != 1:
            return _Bound(dotted, name, tuple(via), f"bound {len(sites)} times there")
        stmt = next((s for s in module.body if any(n is sites[0] for n in ast.walk(s))), None)
        if stmt is None or (before is not None and stmt.lineno >= before):
            return _Bound(dotted, name, tuple(via), "bound after the surface that reads it")
        before = None  # an imported module has run to the end before the import returns
        if isinstance(stmt, ast.ImportFrom) and sites[0] in stmt.names:
            alias = sites[0]
            assert isinstance(alias, ast.alias)
            via.append(f"{dotted}: {ast.unparse(ast.ImportFrom(stmt.module, [alias], stmt.level))}")
            target = _absolute(dotted, stmt)
            if target is None:
                return _Bound(
                    dotted, name, tuple(via), "a relative import beyond the top-level package"
                )
            dotted, name = target, alias.name
            if len(via) > 50:
                return _Bound(dotted, name, tuple(via), "an import cycle")
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [getattr(stmt, "target", None)]
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and sites[0] in targets:
            return _Bound(_package_name(dotted), name, tuple(via), None)
        return _Bound(dotted, name, tuple(via), f"bound by `{ast.unparse(stmt)[:60]}`")


@functools.cache
def _assignments(src: Path) -> dict[str, tuple[str, ...]]:
    """Every name bound under ``src`` other than by import, mapped to the module of each
    site binding it -- once per site, so a module that binds a name twice lists itself twice."""
    out: dict[str, tuple[str, ...]] = {}
    for path in sorted(src.rglob("*.py")):
        dotted = _package_name(".".join(path.relative_to(src).with_suffix("").parts))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.alias):
                for name in _binds(node):
                    out[name] = (*out.get(name, ()), dotted)
    return out


def check_constant_backed(dotted: str, qualname: str, param: str, constant: str) -> None:
    """One :data:`_CONSTANT_BACKED_SURFACES` row: the default reaches ``constant``'s assignment.

    Spelling the name is not enough (:func:`bound_constant`). The constant's home is the one
    site under ``src/`` that binds it other than by import, so a second one -- a local shadow
    beside the import, a reassignment in its own module -- fails as ambiguous rather than
    letting either one vouch for the page.
    """
    spelled = surface_default_name(dotted, qualname, param)
    assert spelled == constant, (
        f"{dotted}.{qualname} defaults {param!r} to something other than {constant} — "
        f"docs/reference/parameters.md prints {constant}'s value for this surface"
    )
    homes = _assignments(SRC).get(constant, ())
    assert len(homes) == 1, (
        f"{constant} is bound {len(homes)} times under src/ other than by import "
        f"({list(homes)}), so it has no single home for a default to be traced to"
    )
    bound = bound_constant(dotted, qualname, spelled)
    assert (bound.module, bound.name, bound.problem) == (homes[0], constant, None), (
        f"{dotted}.{qualname} defaults {param!r} to {spelled}, which is bound to "
        f"{bound.module}.{bound.name}"
        + (f" through {list(bound.via)}" if bound.via else "")
        + (f" ({bound.problem})" if bound.problem else "")
        + f", not to {homes[0]}.{constant} — the name is right and the value it carries is not"
    )


def _fmt(value: object) -> str:
    """Render a live default the way the page writes it in a Default cell."""
    if isinstance(value, str):
        return value
    return repr(value)


def _states(text: str, value: object) -> bool:
    """Does the backticked Default-cell literal ``text`` state ``value``?

    Compared by *value* and type, not by spelling, because prose is allowed to
    write a number the way a reader would: the page says ``1e-6`` and
    ``("pdf", "svg", "png")`` where ``repr`` would give ``1e-06`` and
    ``('pdf', 'svg', 'png')``. Requiring the parsed type to match exactly keeps the
    guard honest anyway — ``1`` does not satisfy ``True``, and ``0`` does not
    satisfy ``0.0``. Bare enum words (``wavelet``, ``cpu``, ``scott``) are not
    Python literals, so a live ``str`` is compared as text.
    """
    if isinstance(value, str):
        return text == value
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return False
    return type(parsed) is type(value) and bool(parsed == value)


# --- Labelled Default cells ---------------------------------------------------
# Two rows print several values keyed by a *mode name* rather than by position:
#
#   detection_threshold | `None` (per-mode: `0.5` intensity, `0.98` bandpass)
#   min_separation      | `None` (per-mode: `8.0` wavelet, `3.0` intensity and bandpass)
#
# Every check below this point used to look only at the *set* of literals in the
# cell, so swapping the two labels -- writing `0.98` intensity and `0.5` bandpass --
# left both literals present and distinct and every assertion green, while the page
# told a reader the wrong per-mode number. A label is not decoration on these rows;
# it is half of the claim.

#: A backticked literal and the run of prose up to the next literal. That span is the
#: literal's label text: the cell is read left to right, and a label written after a
#: value belongs to it until the next value starts. Bounded by construction, so a
#: label cannot be satisfied by a word sitting next to some other number.
_LITERAL_SPAN_RE = re.compile(r"`([^`]+)`([^`]*)")

#: Word characters only -- `intensity,` and `bandpass)` must match the bare labels.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _labelled_literals(cell: str) -> list[tuple[str, frozenset[str]]]:
    """Each backticked literal in ``cell`` with the label words that follow it."""
    return [
        (literal, frozenset(_WORD_RE.findall(span)))
        for literal, span in _LITERAL_SPAN_RE.findall(cell)
    ]


class Claim(NamedTuple):
    """One documented default: the row it lives on, its live value, and its labels.

    ``labels`` is empty for the ordinary single-value row, where the value alone is
    the whole claim and any literal in the cell may satisfy it -- the behaviour every
    row had before mode labels existed. When labels are present the literal must
    *also* carry all of them, which is what makes a swap fail.
    """

    tokens: tuple[str, ...]
    value: object
    labels: frozenset[str] = frozenset()

    def satisfied_by(self, cell: str) -> bool:
        """Does ``cell`` state this claim's value under *all* of its labels?"""
        return any(
            _states(literal, self.value) and self.labels <= words
            for literal, words in _labelled_literals(cell)
        )


# --- The registry -------------------------------------------------------------
# (backticked tokens the Parameter cell must carry, live value[, mode labels]). Some
# names are documented on more than one row (``learning_rate`` is both a ranker and a
# deep hyper-parameter), so an entry may pin a second token to identify its row. The
# value is read from the source, so a code change the page does not follow fails
# here. The optional third element is the mode label(s) the value must be printed
# under; omit it on a row that prints one value.

_Entry = tuple[tuple[str, ...], object] | tuple[tuple[str, ...], object, tuple[str, ...]]


def _registry() -> list[_Entry]:
    opt = ("tether.project.extract", "ExtractOptions")
    entries: list[_Entry] = [
        # Detection and extraction (ExtractOptions is the pinned CLI contract).
        ((name,), dataclass_field(*opt, name))
        for name in (
            "donor_side",
            "detection_mode",
            "detection_block",
            "prealign",
            "prealign_upsample",
            "prealign_low_sigma",
            "prealign_high_sigma",
            "pair_tol",
            "rms_gate",
            "window",
            "coloc_distance",
            "disk_radius",
            "ring_inner",
            "ring_outer",
            "bg_window",
        )
    ]
    detect = "tether.imaging.detect"
    entries += [
        # Per-mode detector defaults. ``ExtractOptions`` stores ``None`` for both and
        # the mode's own function supplies the number the page prints, so these are
        # reachable only as function-signature defaults. Each is labelled with the mode
        # it belongs to, because on these two rows the label carries as much of the
        # claim as the number does.
        #
        # ``min_separation`` is read from all three functions even though intensity and
        # bandpass currently agree: the two entries MERGE into one labelled claim only
        # while their live values are equal, so changing one of them alone splits them
        # again and the page is asked for a literal it does not print.
        (
            ("detection_threshold",),
            function_default(detect, "detect_spots_intensity", "threshold"),
            ("intensity",),
        ),
        (
            ("detection_threshold",),
            function_default(detect, "detect_spots_bandpass", "threshold"),
            ("bandpass",),
        ),
        (
            ("min_separation",),
            function_default(detect, "detect_spots", "min_separation"),
            ("wavelet",),
        ),
        (
            ("min_separation",),
            function_default(detect, "detect_spots_intensity", "min_separation"),
            ("intensity",),
        ),
        (
            ("min_separation",),
            function_default(detect, "detect_spots_bandpass", "min_separation"),
            ("bandpass",),
        ),
        # Batch over-gate policy (the row names no constant, only its module).
        (("tether.project.batch",), module_constant("tether.project.batch", "POLICY_WARN")),
        # Photobleaching priors.
        (("PB_PRIOR_A",), module_constant("tether.fret.photobleach", "PB_PRIOR_A")),
        (("PB_PRIOR_B",), module_constant("tether.fret.photobleach", "PB_PRIOR_B")),
        (("PB_PRIOR_BETA",), module_constant("tether.fret.photobleach", "PB_PRIOR_BETA")),
        (("PB_PRIOR_MU",), module_constant("tether.fret.photobleach", "PB_PRIOR_MU")),
        (
            ("intensity_quantity", "tether.project.photobleach"),
            function_default(
                "tether.project.photobleach", "compute_photobleach", "intensity_quantity"
            ),
        ),
        # Corrections.
        (
            ("intensity_quantity", "tether.project.leakage"),
            function_default(
                "tether.project.leakage", "compute_leakage_alpha", "intensity_quantity"
            ),
        ),
        (
            ("intensity_quantity", "tether.project.gamma"),
            function_default("tether.project.gamma", "compute_gamma", "intensity_quantity"),
        ),
        (
            ("alpha_override",),
            function_default("tether.project.correct", "compute_corrected_fret", "alpha_override"),
        ),
        (
            ("gamma_override",),
            function_default("tether.project.correct", "compute_corrected_fret", "gamma_override"),
        ),
        (
            ("apparent_e_only",),
            function_default("tether.project.correct", "compute_corrected_fret", "apparent_e_only"),
        ),
        (("LEAKAGE_CEILING",), module_constant("tether.fret.leakage", "LEAKAGE_CEILING")),
        (
            ("DEFAULT_MIN_WINDOW_FRAMES",),
            module_constant("tether.fret.leakage", "DEFAULT_MIN_WINDOW_FRAMES"),
        ),
        (
            ("DEFAULT_MIN_QUALIFYING_TRACES",),
            module_constant("tether.fret.leakage", "DEFAULT_MIN_QUALIFYING_TRACES"),
        ),
        (("GAMMA_CEILING",), module_constant("tether.fret.gamma", "GAMMA_CEILING")),
        (
            ("DEFAULT_GAMMA_HALF_WINDOW",),
            module_constant("tether.fret.gamma", "DEFAULT_GAMMA_HALF_WINDOW"),
        ),
        # Idealization.
        (("MODEL_TYPE_DEFAULT",), module_constant("tether.project.idealize", "MODEL_TYPE_DEFAULT")),
        (
            ("NSTATES_GRID_DEFAULT",),
            module_constant("tether.project.idealize", "NSTATES_GRID_DEFAULT"),
        ),
        (
            # Canonical in ``driver`` and re-exported by ``supervisor`` (#222), so the
            # page's ``tether.idealize.supervisor`` path stays true while the value has
            # one home. ``module_constant`` reads the *source*, so it has to be pointed
            # at the module that assigns the literal, not one that imports the name.
            ("DEFAULT_SIDECAR_TIMEOUT",),
            module_constant("tether.idealize.driver", "DEFAULT_SIDECAR_TIMEOUT"),
        ),
        (
            ("DEFAULT_MAX_RESTARTS",),
            module_constant("tether.idealize.supervisor", "DEFAULT_MAX_RESTARTS"),
        ),
        (
            ("DEFAULT_PROBE_TIMEOUT",),
            module_constant("tether.idealize.supervisor", "DEFAULT_PROBE_TIMEOUT"),
        ),
        (
            ("nstates", "tether.project.idealize"),
            function_default("tether.project.idealize", "idealize_molecules", "nstates"),
        ),
        (
            ("nrestarts",),
            function_default("tether.project.idealize", "idealize_molecules", "nrestarts"),
        ),
        (
            ("intensity_quantity", "tether.project.idealize"),
            function_default("tether.project.idealize", "idealize_molecules", "intensity_quantity"),
        ),
        (
            ("defer_if_unavailable",),
            dataclass_field(
                "tether.idealize.supervisor", "SidecarSupervision", "defer_if_unavailable"
            ),
        ),
        # Analysis — science tunables.
        (
            ("intensity_quantity", "tether.analysis"),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "intensity_quantity",
            ),
        ),
        (
            # ``include_first`` is one printed default governing two surfaces: the dwell
            # survival fit and the kinetics rate estimator. Same tokens and same live
            # value, so ``_claims`` merges them into the single ``False`` the page prints
            # -- and the merge is by value, so the day one of them changes they split
            # again and the row is asked for a literal it does not carry.
            ("include_first",),
            function_default("tether.analysis.dwell", "population_dwell_times", "include_first"),
        ),
        (
            ("include_first",),
            function_default("tether.analysis.kinetics", "pooled_exit_rates", "include_first"),
        ),
        (
            # The extraction core the other two delegate to. The row names it, and it is
            # public (``tether.analysis.state_dwells``), so a reader can call it directly
            # and its default is as documented as theirs.
            ("include_first",),
            function_default("tether.analysis.dwell", "state_dwells", "include_first"),
        ),
        (
            # Disambiguated on its module: ``state`` is a short, reusable word and a bare
            # entry would bind any future row that happens to backtick it.
            ("state", "tether.analysis.dwell"),
            function_default("tether.analysis.dwell", "population_dwell_times", "state"),
        ),
        (
            ("per_molecule_equal_weight",),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "per_molecule_equal_weight",
            ),
        ),
        (
            ("density",),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "density",
            ),
        ),
        (
            ("density",),
            function_default(
                "tether.analysis.histogram",
                "population_time_signal_histogram2d",
                "density",
            ),
        ),
        (
            ("include_rejected", "tether.analysis"),
            function_default(
                "tether.analysis.histogram",
                "population_apparent_e_histogram",
                "include_rejected",
            ),
        ),
        (
            ("include_stale",),
            function_default("tether.analysis.dwell", "population_dwell_times", "include_stale"),
        ),
        (
            ("model", "tether.analysis.dwell"),
            function_default("tether.analysis.dwell", "population_dwell_times", "model"),
        ),
        (
            ("DEFAULT_BOOTSTRAP_RESAMPLES",),
            module_constant("tether.analysis.histogram", "DEFAULT_BOOTSTRAP_RESAMPLES"),
        ),
        (("DEFAULT_CI_LEVEL",), module_constant("tether.analysis.histogram", "DEFAULT_CI_LEVEL")),
        (("DEFAULT_SEED",), module_constant("tether.analysis.histogram", "DEFAULT_SEED")),
        (
            ("DEFAULT_HMM_MAX_ITER",),
            module_constant("tether.analysis.kinetics", "DEFAULT_HMM_MAX_ITER"),
        ),
        (("DEFAULT_HMM_TOL",), module_constant("tether.analysis.kinetics", "DEFAULT_HMM_TOL")),
        (
            ("nstates", "tether.analysis.kinetics"),
            function_default("tether.analysis.kinetics", "fit_gaussian_hmm", "nstates"),
        ),
        (
            ("DEFAULT_HMM_VAR_FLOOR",),
            module_constant("tether.analysis.kinetics", "DEFAULT_HMM_VAR_FLOOR"),
        ),
        (("DEFAULT_DWELL_DT",), module_constant("tether.analysis.dwell", "DEFAULT_DWELL_DT")),
        (("DEFAULT_TIME_DT",), module_constant("tether.analysis.histogram", "DEFAULT_TIME_DT")),
        (
            ("DEFAULT_CLOUD_TIME_DT",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_TIME_DT"),
        ),
        (
            ("DEFAULT_DWELL_CI_LEVEL",),
            module_constant("tether.analysis.dwell", "DEFAULT_DWELL_CI_LEVEL"),
        ),
        # Analysis — rendering defaults.
        (("DEFAULT_NBINS",), module_constant("tether.analysis.histogram", "DEFAULT_NBINS")),
        (("DEFAULT_RANGE",), module_constant("tether.analysis.histogram", "DEFAULT_RANGE")),
        (
            # The A1 overlay grid. Both entry points default it to the constant itself,
            # which ``function_default`` refuses on purpose, so the constant is the only
            # honest way to pin the printed value here. Note what that does NOT cover: it
            # pins the CONSTANT, not the two signatures, so on its own it would let
            # ``model_gaussian_overlay(n_points=501)`` drift off a still-1001 constant with
            # the page none the wiser. ``_CONSTANT_BACKED_SURFACES`` below closes that.
            ("DEFAULT_OVERLAY_POINTS",),
            module_constant("tether.analysis.histogram", "DEFAULT_OVERLAY_POINTS"),
        ),
        (("DEFAULT_TIME_BINS",), module_constant("tether.analysis.histogram", "DEFAULT_TIME_BINS")),
        (
            ("DEFAULT_SIGNAL_BINS",),
            module_constant("tether.analysis.histogram", "DEFAULT_SIGNAL_BINS"),
        ),
        (
            ("DEFAULT_SIGNAL_RANGE",),
            module_constant("tether.analysis.histogram", "DEFAULT_SIGNAL_RANGE"),
        ),
        (
            ("DEFAULT_SYNC_PREFRAME",),
            module_constant("tether.analysis.histogram", "DEFAULT_SYNC_PREFRAME"),
        ),
        (
            ("from_state",),
            function_default(
                "tether.analysis.histogram", "transition_sync_histogram2d", "from_state"
            ),
        ),
        (
            ("to_state",),
            function_default(
                "tether.analysis.histogram", "transition_sync_histogram2d", "to_state"
            ),
        ),
        (
            ("single_dwell",),
            function_default(
                "tether.analysis.histogram", "transition_sync_histogram2d", "single_dwell"
            ),
        ),
        (("DEFAULT_TDP_NSKIP",), module_constant("tether.analysis.tdp", "DEFAULT_TDP_NSKIP")),
        (
            ("DEFAULT_TDP_SIGNAL_BINS",),
            module_constant("tether.analysis.tdp", "DEFAULT_TDP_SIGNAL_BINS"),
        ),
        (
            ("DEFAULT_TDP_SIGNAL_RANGE",),
            module_constant("tether.analysis.tdp", "DEFAULT_TDP_SIGNAL_RANGE"),
        ),
        (("DEFAULT_DWELL_NBINS",), module_constant("tether.analysis.dwell", "DEFAULT_DWELL_NBINS")),
        (
            ("DEFAULT_TPROB_NBINS",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_NBINS"),
        ),
        (
            ("DEFAULT_TPROB_RANGE",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_RANGE"),
        ),
        (
            ("DEFAULT_TPROB_KDE_BANDWIDTH",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_KDE_BANDWIDTH"),
        ),
        (
            ("DEFAULT_TPROB_KDE_POINTS",),
            module_constant("tether.analysis.transition_prob", "DEFAULT_TPROB_KDE_POINTS"),
        ),
        (
            # The transition-probability KDE switch, bound on BOTH signatures. Two
            # entries that merge while the pair agrees, by the same value-keyed merge
            # ``min_separation`` gets in ``_claims`` -- but only the merge is shared, and
            # the difference is the point: ``min_separation``'s bare tokens are safe
            # because they resolve to exactly one row, which is what a bare ``kde`` would
            # not do. The single/population defaults drifting apart is precisely what
            # this is here to catch. Disambiguated on its module because the raw-FRET
            # cloud has a ``kde`` of its own further down the same table, and token
            # matching is a SUBSET test applied to every match: a bare ``("kde",)`` entry
            # binds *both* rows and requires each to print its value. While the two agree
            # on ``True`` that is invisible. The moment they legitimately diverge it is
            # worse than invisible -- the two claims no longer merge, and each then demands
            # satisfaction on both rows, so a page that correctly prints ``True`` on one
            # and ``False`` on the other FAILS. The divergence becomes unrepresentable,
            # which is a guard telling you to write something untrue.
            ("kde", "tether.analysis.transition_prob"),
            function_default("tether.analysis.transition_prob", "transition_prob_histogram", "kde"),
        ),
        (
            ("kde", "tether.analysis.transition_prob"),
            function_default(
                "tether.analysis.transition_prob", "population_transition_prob_histogram", "kde"
            ),
        ),
        (
            ("DEFAULT_STATE_NUMBER_LOW",),
            module_constant("tether.analysis.state_number", "DEFAULT_STATE_NUMBER_LOW"),
        ),
        (
            ("states_high",),
            function_default(
                "tether.analysis.state_number", "population_state_number", "states_high"
            ),
        ),
        (
            ("max_lag",),
            function_default("tether.analysis.crosscorr", "cross_correlation", "max_lag"),
        ),
        (
            ("normalize",),
            function_default("tether.analysis.crosscorr", "cross_correlation", "normalize"),
        ),
        (
            ("DEFAULT_CLOUD_SIGNAL_BINS",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_SIGNAL_BINS"),
        ),
        (
            ("DEFAULT_CLOUD_TIME_BINS",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_TIME_BINS"),
        ),
        (
            ("DEFAULT_CLOUD_SIGNAL_RANGE",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_SIGNAL_RANGE"),
        ),
        (
            ("DEFAULT_CLOUD_HDR_COVERAGES",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_HDR_COVERAGES"),
        ),
        (
            ("DEFAULT_CLOUD_BW_METHOD",),
            module_constant("tether.analysis.cloud", "DEFAULT_CLOUD_BW_METHOD"),
        ),
        (
            ("time_range",),
            function_default("tether.analysis.cloud", "raw_fret_cloud", "time_range"),
        ),
        (
            # Disambiguated for the same reason as the transition-probability pair above:
            # each ``kde`` must bind its own row and only its own.
            ("kde", "tether.analysis.cloud"),
            function_default("tether.analysis.cloud", "raw_fret_cloud", "kde"),
        ),
        (("DEFAULT_ELBOW_K_MAX",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_K_MAX")),
        (
            ("DEFAULT_ELBOW_RESTARTS",),
            module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_RESTARTS"),
        ),
        (("DEFAULT_ELBOW_SEED",), module_constant("tether.analysis.cloud", "DEFAULT_ELBOW_SEED")),
        (("k_min",), function_default("tether.analysis.cloud", "k_rmse_elbow", "k_min")),
        (
            ("in_grid_only",),
            function_default(
                "tether.analysis.cloud",
                "population_fret_cloud_state_number_elbow",
                "in_grid_only",
            ),
        ),
        (
            ("DEFAULT_ALPHA_FACTOR",),
            module_constant("tether.analysis.cloud", "DEFAULT_ALPHA_FACTOR"),
        ),
        (
            ("in_grid_only",),
            function_default(
                "tether.analysis.cloud", "population_fret_cloud_alpha_shape", "in_grid_only"
            ),
        ),
        (
            ("DEFAULT_ANTICORR_WINDOW",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_WINDOW"),
        ),
        (
            ("DEFAULT_ANTICORR_STEP",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_STEP"),
        ),
        (
            ("DEFAULT_ANTICORR_MIN_MAGNITUDE",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_MIN_MAGNITUDE"),
        ),
        (
            ("DEFAULT_ANTICORR_MIN_WINDOWS",),
            module_constant("tether.analysis.anticorrelation", "DEFAULT_ANTICORR_MIN_WINDOWS"),
        ),
        (
            ("DEFAULT_EXPORT_DPI",),
            module_constant("tether.analysis.plot_export", "DEFAULT_EXPORT_DPI"),
        ),
        (("DEFAULT_FIGSIZE",), module_constant("tether.analysis.plot_export", "DEFAULT_FIGSIZE")),
        (
            ("DEFAULT_PLOT_FORMATS",),
            module_constant("tether.analysis.plot_export", "DEFAULT_PLOT_FORMATS"),
        ),
        # Curation ranker.
        (
            ("intensity_quantity", "tether.project.features"),
            function_default("tether.project.features", "compute_features", "intensity_quantity"),
        ),
        (
            ("include_rejected", "tether.project.features"),
            function_default("tether.project.features", "compute_features", "include_rejected"),
        ),
        (("DEFAULT_SEED_WEIGHT",), module_constant("tether.ml.weighting", "DEFAULT_SEED_WEIGHT")),
        (("DEFAULT_DRIFT_ALPHA",), module_constant("tether.ml.drift", "DEFAULT_DRIFT_ALPHA")),
        (
            ("DEFAULT_SHIP_BAR_PTS",),
            module_constant("tether.ml.prequential", "DEFAULT_SHIP_BAR_PTS"),
        ),
        (
            ("learning_rate", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "learning_rate"),
        ),
        (
            ("max_iter", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "max_iter"),
        ),
        (
            ("max_leaf_nodes", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "max_leaf_nodes"),
        ),
        (
            ("min_samples_leaf", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "min_samples_leaf"),
        ),
        (
            ("l2_regularization", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "l2_regularization"),
        ),
        (
            ("early_stopping", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "early_stopping"),
        ),
        (
            ("random_state", "RankerHyperparams"),
            dataclass_field("tether.ml.gbranker", "RankerHyperparams", "random_state"),
        ),
        # Optional deep add-on.
        (
            ("intensity_quantity", "tether.project.deep_dataset"),
            function_default(
                "tether.project.deep_dataset", "build_deep_dataset", "intensity_quantity"
            ),
        ),
        (
            ("DEFAULT_DEEP_CHANNELS",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_DEEP_CHANNELS"),
        ),
        (
            ("DEFAULT_WINDOW_LENGTH",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_WINDOW_LENGTH"),
        ),
        (
            ("DEFAULT_NORMALIZATION",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_NORMALIZATION"),
        ),
        (
            ("DEFAULT_VAL_FRACTION",),
            module_constant("tether.ml.deep.dataset", "DEFAULT_VAL_FRACTION"),
        ),
        (("DEFAULT_SPLIT_SEED",), module_constant("tether.ml.deep.dataset", "DEFAULT_SPLIT_SEED")),
        (
            ("stratify",),
            function_default("tether.ml.deep.dataset", "train_val_split", "stratify"),
        ),
        (("DEFAULT_EPOCHS",), module_constant("tether.ml.deep.model", "DEFAULT_EPOCHS")),
        (("DEFAULT_BATCH_SIZE",), module_constant("tether.ml.deep.model", "DEFAULT_BATCH_SIZE")),
        (
            ("DEFAULT_LEARNING_RATE",),
            module_constant("tether.ml.deep.model", "DEFAULT_LEARNING_RATE"),
        ),
        (
            ("DEFAULT_NUM_CONV_LAYERS",),
            module_constant("tether.ml.deep.model", "DEFAULT_NUM_CONV_LAYERS"),
        ),
        (
            ("DEFAULT_CONV_CHANNELS",),
            module_constant("tether.ml.deep.model", "DEFAULT_CONV_CHANNELS"),
        ),
        (("DEFAULT_KERNEL_SIZE",), module_constant("tether.ml.deep.model", "DEFAULT_KERNEL_SIZE")),
        (("DEFAULT_LSTM_HIDDEN",), module_constant("tether.ml.deep.model", "DEFAULT_LSTM_HIDDEN")),
        (
            ("DEFAULT_BIDIRECTIONAL",),
            module_constant("tether.ml.deep.model", "DEFAULT_BIDIRECTIONAL"),
        ),
        (("DEFAULT_DROPOUT",), module_constant("tether.ml.deep.model", "DEFAULT_DROPOUT")),
        (
            ("DEFAULT_FINE_TUNE_EPOCHS",),
            module_constant("tether.ml.deep.model", "DEFAULT_FINE_TUNE_EPOCHS"),
        ),
        (
            ("DEFAULT_FINE_TUNE_LEARNING_RATE",),
            module_constant("tether.ml.deep.model", "DEFAULT_FINE_TUNE_LEARNING_RATE"),
        ),
        (("DEFAULT_FREEZE_CONV",), module_constant("tether.ml.deep.model", "DEFAULT_FREEZE_CONV")),
        (("DEFAULT_DEVICE",), module_constant("tether.ml.deep.model", "DEFAULT_DEVICE")),
    ]
    return entries


def _claims(entries: list[_Entry]) -> list[Claim]:
    """Registry entries as :class:`Claim`s, merging the ones the page prints once.

    Two entries with the same tokens and the same live value are **one** documented
    claim, not two -- ``min_separation`` reads its default from both the intensity and
    the bandpass detector and the page prints a single ``3.0`` for the pair. Merging
    unions their labels, so that one literal is required to carry *both* mode names,
    which is the acceptance criterion for a value shared by several labels.

    The merge is by live value, so it is not a way of excusing drift: if the two
    functions stop agreeing, the entries no longer merge and the page is asked for a
    literal it does not print. This is the same collapse the row-walking pass already
    performed inline, lifted here so both passes see one consistent claim list.
    """
    merged: dict[tuple[tuple[str, ...], str, str], Claim] = {}
    for entry in entries:
        tokens, value = entry[0], entry[1]
        labels = frozenset(entry[2]) if len(entry) > 2 else frozenset()
        key = (tokens, type(value).__name__, repr(value))
        previous = merged.get(key)
        merged[key] = Claim(tokens, value, labels | (previous.labels if previous else frozenset()))
    return list(merged.values())


REGISTRY = _claims(_registry())


def _rows() -> list[list[str]]:
    """Every Markdown table body row of the page, as a list of stripped cells."""
    out: list[list[str]] = []
    for line in PAGE.read_text(encoding="utf-8").split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|") or _SEPARATOR_RE.match(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= 2:
            out.append(cells)
    return out


ROWS = _rows()


def test_page_exists_and_has_tables() -> None:
    assert PAGE.is_file(), f"{PAGE} is missing"
    assert len(ROWS) > 50, "the parameter page lost its tables"


def test_page_is_listed_in_the_site_nav() -> None:
    """`mkdocs build --strict` fails on a page that is in docs/ but not in nav."""
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "reference/parameters.md" in mkdocs


def test_page_does_not_link_the_unpublished_spec() -> None:
    """The whole point of the page is that PRD.md is excluded from the site."""
    text = PAGE.read_text(encoding="utf-8")
    assert "PRD.md" not in text


@pytest.mark.parametrize(
    "claim",
    REGISTRY,
    ids=[
        claim.tokens[0] + ("-" + "-".join(sorted(claim.labels)) if claim.labels else "")
        for claim in REGISTRY
    ],
)
def test_documented_default_matches_the_code(claim: Claim) -> None:
    name = claim.tokens[0]
    matches = [row for row in ROWS if set(claim.tokens) <= set(_BACKTICKED_RE.findall(row[0]))]
    assert matches, f"no Parameter cell on the page mentions `{name}`"
    for row in matches:
        literals = _BACKTICKED_RE.findall(row[1])
        assert literals, f"the Default cell for `{name}` has no backticked literal"
        assert claim.satisfied_by(row[1]), (
            f"docs/reference/parameters.md documents {literals} for `{name}`"
            + (f" under {sorted(claim.labels)}" if claim.labels else "")
            + f", but the code says {_fmt(claim.value)!r}"
            + (
                " — the value must be printed next to its own mode label, so a label swap "
                "fails here even when every number is still on the row"
                if claim.labels
                else ""
            )
        )


def test_a_multi_value_row_states_each_registered_default_separately() -> None:
    """A changed default may not hide behind a sibling's value on the same row.

    ``test_documented_default_matches_the_code`` accepts *any* backticked literal
    in the Default cell, so on a row that prints several values a drifted constant
    passes as soon as its new value collides with a neighbour's: flip
    ``DEFAULT_CONV_CHANNELS`` from ``32`` to ``5`` and the ``5`` already printed
    for ``kernel_size`` absorbs it. This walks the row instead of the entry and
    makes every registered default claim its **own** literal.

    Two registry lines that carry the same tokens *and* the same value are one
    documented claim, not two — ``min_separation`` prints a single ``3.0`` for both
    the intensity and the bandpass detector — so they collapse in :func:`_claims`
    before the walk. A row that still prints fewer literals than it has entries has
    collapsed values the registry cannot tell apart (``dt`` / ``time_dt`` print one
    ``1.0`` for three separately-named constants); it keeps the per-entry check only.

    A labelled claim additionally has to take a literal that carries its labels, so on
    the two mode-keyed rows this pass fails a swap as well — it does not merely count.
    """
    for row in ROWS:
        parameter_tokens = set(_BACKTICKED_RE.findall(row[0]))
        entries = [claim for claim in REGISTRY if set(claim.tokens) <= parameter_tokens]
        cell = row[1] if len(row) > 1 else ""
        literals = _BACKTICKED_RE.findall(cell)
        if len(entries) < 2 or len(literals) < len(entries):
            continue
        unclaimed = _labelled_literals(cell)
        for claim in entries:
            for i, (text, words) in enumerate(unclaimed):
                if _states(text, claim.value) and claim.labels <= words:
                    del unclaimed[i]
                    break
            else:
                raise AssertionError(
                    f"the row for `{claim.tokens[0]}` prints {literals} but has no literal left "
                    f"for its live value {_fmt(claim.value)!r}"
                    + (f" under {sorted(claim.labels)}" if claim.labels else "")
                    + " — a default on this row has drifted and is hiding behind a sibling's "
                    "value, or its mode label has been swapped"
                )


def test_every_constant_the_page_prints_a_value_for_is_registered() -> None:
    """The registry may not lag the page.

    ``test_documented_default_matches_the_code`` only checks what ``REGISTRY``
    names, so a row added to the page with an unregistered constant would be
    unguarded and could drift away from the code unnoticed. Every SCREAMING_CASE
    constant named in a **Parameter** cell of a six-column parameter table is
    therefore required to have an entry. The three-column "Tolerances and gates"
    table is exempt on purpose: it deliberately does not restate values, so it has
    no Default cell to check.
    """
    registered = {token for claim in REGISTRY for token in claim.tokens}
    documented: set[str] = set()
    for row in ROWS:
        if len(row) != 6:
            continue
        documented |= {t for t in _BACKTICKED_RE.findall(row[0]) if _CONSTANT_RE.match(t)}
    missing = sorted(documented - registered)
    assert not missing, (
        "docs/reference/parameters.md prints a default for these constants but "
        f"_registry() does not pin them to the code: {missing}"
    )


#: The surfaces the page documents whose default is a *constant*, not a literal:
#: ``(module, qualified name, parameter, the constant it must still be spelled with)``.
#: The qualified name is a module-level function, a ``Class.method``, or a dataclass whose
#: field the parameter is (:func:`surface_default_name` tells them apart).
#:
#: Every literal-defaulted surface is bound by a ``function_default`` registry entry, which
#: reads the literal out of the signature. These cannot be: their defaults are names, and
#: ``function_default`` refuses a name on purpose. The registry therefore pins their
#: printed value to the *constant* and the signature to nothing, so
#: ``model_gaussian_overlay(n_points=500)`` with ``DEFAULT_OVERLAY_POINTS`` left at ``1001``
#: would pass every registry check while the page tells the reader ``1001``.
#:
#: Hand-written, and checked against the page rather than trusted:
#: :func:`test_every_spelled_keyword_with_a_constant_default_is_in_the_table` derives every
#: ``(surface, keyword)`` pair a constant-naming row's **Set via** cell spells and requires
#: each one defaulted to a name to be here -- the keywords a cell *spells*, and no more: a
#: surface written bare (``population_transition_density(…)``) contributes nothing, so its
#: constant-backed defaults are not bound here. The table may hold more than the page names
#: (the ``w0`` seam below); it may not hold less. The *module* is not derived, because the
#: row names the module its constant lives in, which is not always the function's
#: (``compute_photobleach`` is in ``tether.project.photobleach``, ``PB_PRIOR_A`` in
#: ``tether.fret.photobleach``) -- so it is written here, and verified by the resolvers.
_CONSTANT_BACKED_SURFACES = [
    ("tether.analysis.histogram", "model_gaussian_overlay", "n_points", "DEFAULT_OVERLAY_POINTS"),
    (
        "tether.analysis.histogram",
        "population_model_gaussian_overlay",
        "n_points",
        "DEFAULT_OVERLAY_POINTS",
    ),
    # EVERY public ``w0`` surface, not merely the three the row names as entry points.
    # The row's claim is that the same keyword carries the same default across the whole
    # weighting/ranking seam, which is a claim about all of them; and the printed ``0.3``
    # comes from the constant, so any one signature could drift off it while the page
    # stayed green. Enumerated rather than sampled on purpose -- three successive review
    # passes each found one more surface the previous list had missed.
    ("tether.project.gbranking", "train_ranker", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.weighting", "recompute_label_weights", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.deep_dataset", "build_deep_dataset", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.gbranking", "weighted_training_set", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.gbranking", "score_molecules", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.gbranking", "ranker_ranking", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.gbranking", "ranker_precision_at_k", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.project.active", "next_recommendation", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.ml.weighting", "seed_weight", "w0", "DEFAULT_SEED_WEIGHT"),
    ("tether.ml.weighting", "effective_weights", "w0", "DEFAULT_SEED_WEIGHT"),
    # Every remaining (surface, keyword) pair a constant-naming row's **Set via** cell
    # spells whose signature default is a name (#442). ``idealize_molecules.timeout`` is
    # also pinned by ``tests/test_idealize_timeout_default.py`` (#222); it is re-bound here
    # so the page-derived coverage check below needs no second file.
    ("tether.analysis.cloud", "k_rmse_elbow", "k_max", "DEFAULT_ELBOW_K_MAX"),
    ("tether.analysis.cloud", "k_rmse_elbow", "restarts", "DEFAULT_ELBOW_RESTARTS"),
    ("tether.analysis.cloud", "k_rmse_elbow", "seed", "DEFAULT_ELBOW_SEED"),
    ("tether.analysis.cloud", "raw_fret_cloud", "time_dt", "DEFAULT_CLOUD_TIME_DT"),
    ("tether.analysis.dwell", "DwellTimeAnalysis.histogram", "nbins", "DEFAULT_DWELL_NBINS"),
    ("tether.analysis.dwell", "population_dwell_times", "ci_level", "DEFAULT_DWELL_CI_LEVEL"),
    ("tether.analysis.dwell", "population_dwell_times", "dt", "DEFAULT_DWELL_DT"),
    ("tether.analysis.histogram", "apparent_e_histogram", "bins", "DEFAULT_NBINS"),
    ("tether.analysis.histogram", "apparent_e_histogram", "value_range", "DEFAULT_RANGE"),
    ("tether.analysis.histogram", "bootstrap_histogram_ci", "ci_level", "DEFAULT_CI_LEVEL"),
    (
        "tether.analysis.histogram",
        "bootstrap_histogram_ci",
        "n_resamples",
        "DEFAULT_BOOTSTRAP_RESAMPLES",
    ),
    ("tether.analysis.histogram", "bootstrap_histogram_ci", "seed", "DEFAULT_SEED"),
    ("tether.analysis.histogram", "time_signal_histogram2d", "time_dt", "DEFAULT_TIME_DT"),
    (
        "tether.analysis.histogram",
        "transition_sync_histogram2d",
        "sync_preframe",
        "DEFAULT_SYNC_PREFRAME",
    ),
    ("tether.analysis.kinetics", "fit_gaussian_hmm", "n_iter", "DEFAULT_HMM_MAX_ITER"),
    ("tether.analysis.kinetics", "fit_gaussian_hmm", "tol", "DEFAULT_HMM_TOL"),
    ("tether.analysis.kinetics", "fit_gaussian_hmm", "var_floor", "DEFAULT_HMM_VAR_FLOOR"),
    ("tether.analysis.plot_export", "export_figure", "dpi", "DEFAULT_EXPORT_DPI"),
    ("tether.analysis.plot_export", "export_figure", "formats", "DEFAULT_PLOT_FORMATS"),
    (
        "tether.analysis.state_number",
        "population_state_number",
        "states_low",
        "DEFAULT_STATE_NUMBER_LOW",
    ),
    ("tether.fret.photobleach", "detect_photobleach", "a", "PB_PRIOR_A"),
    ("tether.fret.photobleach", "detect_photobleach", "b", "PB_PRIOR_B"),
    ("tether.fret.photobleach", "detect_photobleach", "beta", "PB_PRIOR_BETA"),
    ("tether.fret.photobleach", "detect_photobleach", "mu", "PB_PRIOR_MU"),
    ("tether.idealize.supervisor", "SidecarSupervision", "probe_timeout", "DEFAULT_PROBE_TIMEOUT"),
    ("tether.ml.deep.dataset", "assemble_dataset", "channels", "DEFAULT_DEEP_CHANNELS"),
    ("tether.ml.deep.dataset", "assemble_dataset", "normalization", "DEFAULT_NORMALIZATION"),
    ("tether.ml.deep.dataset", "assemble_dataset", "window_length", "DEFAULT_WINDOW_LENGTH"),
    ("tether.ml.deep.dataset", "train_val_split", "seed", "DEFAULT_SPLIT_SEED"),
    ("tether.ml.deep.dataset", "train_val_split", "val_fraction", "DEFAULT_VAL_FRACTION"),
    ("tether.ml.deep.model", "fine_tune", "seed", "DEFAULT_SPLIT_SEED"),
    ("tether.ml.deep.model", "train_classifier", "device", "DEFAULT_DEVICE"),
    ("tether.ml.deep.model", "train_classifier", "seed", "DEFAULT_SPLIT_SEED"),
    ("tether.project.deep_dataset", "build_deep_dataset", "channels", "DEFAULT_DEEP_CHANNELS"),
    ("tether.project.drift", "cross_condition_drift", "alpha", "DEFAULT_DRIFT_ALPHA"),
    ("tether.project.gamma", "compute_gamma", "ceiling", "GAMMA_CEILING"),
    ("tether.project.gamma", "compute_gamma", "half_window", "DEFAULT_GAMMA_HALF_WINDOW"),
    (
        "tether.project.gamma",
        "compute_gamma",
        "min_qualifying_traces",
        "DEFAULT_MIN_QUALIFYING_TRACES",
    ),
    ("tether.project.gamma", "compute_gamma", "min_window_frames", "DEFAULT_MIN_WINDOW_FRAMES"),
    ("tether.project.idealize", "idealize_molecules", "model_type", "MODEL_TYPE_DEFAULT"),
    ("tether.project.idealize", "idealize_molecules", "nstates_grid", "NSTATES_GRID_DEFAULT"),
    ("tether.project.idealize", "idealize_molecules", "timeout", "DEFAULT_SIDECAR_TIMEOUT"),
    ("tether.project.leakage", "compute_leakage_alpha", "ceiling", "LEAKAGE_CEILING"),
    (
        "tether.project.leakage",
        "compute_leakage_alpha",
        "min_qualifying_traces",
        "DEFAULT_MIN_QUALIFYING_TRACES",
    ),
    (
        "tether.project.leakage",
        "compute_leakage_alpha",
        "min_window_frames",
        "DEFAULT_MIN_WINDOW_FRAMES",
    ),
    ("tether.project.photobleach", "compute_photobleach", "a", "PB_PRIOR_A"),
    ("tether.project.photobleach", "compute_photobleach", "b", "PB_PRIOR_B"),
    ("tether.project.photobleach", "compute_photobleach", "beta", "PB_PRIOR_BETA"),
    ("tether.project.photobleach", "compute_photobleach", "mu", "PB_PRIOR_MU"),
    (
        "tether.project.prequential",
        "ranker_prequential_uplift",
        "ship_bar_pts",
        "DEFAULT_SHIP_BAR_PTS",
    ),
]


def test_the_w0_surface_list_is_exhaustive() -> None:
    """No public ``w0`` keyword may exist outside :data:`_CONSTANT_BACKED_SURFACES`.

    The list above is enumerated, and an enumeration rots: three review passes each found
    a surface the previous list had missed, and the page now claims the default holds
    across the *whole* seam. So the enumeration is checked rather than trusted -- add a
    ``w0=`` keyword anywhere under ``src/`` and this fails until the table admits it.
    """
    covered = {(mod, func) for mod, func, param, _ in _CONSTANT_BACKED_SURFACES if param == "w0"}
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        dotted = ".".join(path.relative_to(SRC).with_suffix("").parts)
        for stmt in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
            if not isinstance(stmt, ast.FunctionDef) or stmt.name.startswith("_"):
                continue
            args = stmt.args
            named = args.posonlyargs + args.args + args.kwonlyargs
            if any(a.arg == "w0" for a in named):
                found.add((dotted, stmt.name))
    assert found == covered, (
        "the public w0 surfaces and _CONSTANT_BACKED_SURFACES have diverged — "
        f"unbound: {sorted(found - covered)}; stale entries: {sorted(covered - found)}"
    )


@pytest.mark.parametrize(
    ("dotted", "func", "param", "constant"),
    _CONSTANT_BACKED_SURFACES,
    ids=[f"{f}.{p}" for _, f, p, _ in _CONSTANT_BACKED_SURFACES],
)
def test_a_documented_constant_backed_default_still_names_its_constant(
    dotted: str, func: str, param: str, constant: str
) -> None:
    """A signature the page documents may not drift off the constant it prints.

    Nor onto another constant under the same name: :func:`check_constant_backed` follows
    the name to the module that assigns it (#513).
    """
    check_constant_backed(dotted, func, param, constant)


# --- The table, checked against the page ----------------------------------------
# ``_CONSTANT_BACKED_SURFACES`` is enumerated by hand, and an enumeration rots. The page's
# **Set via** column is what names function surfaces, so it is read here: every backticked
# ``NAME(kw=…)`` token on a row whose **Parameter** cell names a SCREAMING_CASE constant
# yields one ``(surface, keyword)`` pair per keyword it spells, and each pair lands in
# exactly one of three dispositions --
#
#   1. the keyword's default is a *name*      -> the pair must be in the table;
#   2. it is a literal, or there is no default -> nothing to bind here (a literal default
#      is ``function_default``'s business);
#   3. ``NAME`` resolves to no public definition, or to more than one, or the call as
#      written would not accept the keyword, or the default is neither a literal nor a
#      name (``mode=ParticleDetectionMode.WAVELET``)  -> the check fails, naming the token.
#
# A backticked token carrying a parenthesis that is not one well-formed call of a surface
# (``apparent-e-histogram(bins=…)``, ``apparent_e_histogram(bins=…))``) is disposition 3
# too: it names no callable, and skipping it would leave the row green on the strength of
# its other tokens (#513).
#
# There is no fourth. Nothing can declare a surface out of scope, because an exemption
# list would let the whole check be discharged by binding a few surfaces and exempting the
# rest. A token that spells no keyword (``DeepTraceDataset.split(…)``) yields no pair, and
# a row naming no constant is not walked: the predicate is the keywords a cell *spells*.
# That is a limit of what this guard covers, not a claim that the rest is safe: a default
# the cell leaves unspelled (``train_classifier(…)``, ``population_transition_density(…)``)
# can drift off its constant without any check here noticing.

#: A **Set via** token naming a callable surface: ``NAME(…)``, where ``NAME`` may be dotted
#: (``DwellTimeAnalysis.histogram``). CLI flags and prose never match.
_SURFACE_TOKEN_RE = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\((.*)\)$")
#: A keyword a surface token spells: ``kw=…``. ``==`` is a comparison, not a keyword.
_KEYWORD_RE = re.compile(r"([A-Za-z_]\w*)\s*=(?!=)")


_Modules = tuple[tuple[str, ast.Module], ...]


@functools.cache
def _source_modules() -> _Modules:
    """Every module under ``src/``, parsed once, as ``(dotted name, tree)``."""
    return tuple(
        (
            ".".join(path.relative_to(SRC).with_suffix("").parts),
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )
        for path in sorted(SRC.rglob("*.py"))
    )


def _is_public(dotted: str) -> bool:
    """No component of ``dotted`` is private (``__init__`` is the package, not private)."""
    return not any(part.startswith("_") and part != "__init__" for part in dotted.split("."))


def _keyword_parameters(fn: ast.FunctionDef) -> dict[str, ast.expr | None]:
    """The parameters a caller can pass **by keyword**, with their default nodes.

    :func:`_signature` minus the positional-only ones: a documented ``kw=…`` call on a
    parameter declared before ``/`` is a ``TypeError`` in Python, so it must not resolve.
    """
    positional_only = {a.arg for a in fn.args.posonlyargs}
    return {k: v for k, v in _signature(fn).items() if k not in positional_only}


def _is_annotation(node: ast.expr, name: str) -> bool:
    """Is the annotation ``node`` ``name``, ``typing.name`` or ``name[…]`` (or a string of it)?"""
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("[", 1)[0].rsplit(".", 1)[-1].strip() == name
    return (isinstance(node, ast.Name) and node.id == name) or (
        isinstance(node, ast.Attribute) and node.attr == name
    )


def _constructor_keywords(cls: ast.ClassDef) -> dict[str, ast.expr | None]:
    """The keywords a call ``Cls(…)`` of the dataclass ``cls`` accepts, as Python decides.

    An ``__init__`` written in the class body wins (``@dataclass`` does not overwrite it).
    Otherwise the generated one takes every annotated field except a ``ClassVar``, the
    ``KW_ONLY`` marker and a ``field(init=False)`` -- and nothing at all under
    ``@dataclass(init=False)``. Fields inherited from a base class are not followed, so a
    page token naming one fails disposition 3 rather than passing unchecked.
    """
    for item in cls.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            return {k: v for k, v in _keyword_parameters(item).items() if k != "self"}
    for decorator in cls.decorator_list:
        if isinstance(decorator, ast.Call) and any(
            kw.arg == "init" and isinstance(kw.value, ast.Constant) and kw.value.value is False
            for kw in decorator.keywords
        ):
            return {}
    accepted: dict[str, ast.expr | None] = {}
    for item in cls.body:
        if not (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)):
            continue
        if _is_annotation(item.annotation, "ClassVar") or _is_annotation(
            item.annotation, "KW_ONLY"
        ):
            continue
        if (
            isinstance(item.value, ast.Call)
            and _callee(item.value) == "field"
            and any(
                kw.arg == "init" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                for kw in item.value.keywords
            )
        ):
            continue
        accepted[item.target.id] = _field_default(item.value)
    return accepted


def _resolve(name: str, modules: _Modules) -> list[tuple[str, dict[str, ast.expr | None]]]:
    """Every **public** ``(module, keyword parameters)`` the surface ``name`` resolves to.

    Three resolvers: a module-level function ``name``; a dataclass ``name``, read as its
    constructor; and, for a dotted ``name``, the method ``Class.method``. Each answers what
    Python would accept as a keyword on that call (:func:`_keyword_parameters`,
    :func:`_constructor_keywords`).

    Only public definitions count -- no private module or name -- because the page
    documents calls a reader makes, and a private same-named helper
    (``tether.ml.deep._torch_model.fine_tune``) would otherwise keep a token resolving
    after the documented one was removed. Every public hit is returned, never the first:
    the caller requires exactly one, so a second public definition of the same name fails
    loudly instead of being picked quietly. The *module* is still not read off the page,
    whose **Parameter** cell names where the constant lives, which is not always where the
    function does (``compute_photobleach`` / ``PB_PRIOR_A``).
    """
    owner, _, leaf = name.rpartition(".")
    if "." in owner or any(part.startswith("_") for part in name.split(".")):
        return []
    hits: list[tuple[str, dict[str, ast.expr | None]]] = []
    for dotted, tree in modules:
        if not _is_public(dotted):
            continue
        for stmt in tree.body:
            if owner:
                if isinstance(stmt, ast.ClassDef) and stmt.name == owner:
                    hits += [
                        (dotted, _keyword_parameters(item))
                        for item in stmt.body
                        if isinstance(item, ast.FunctionDef) and item.name == leaf
                    ]
            elif isinstance(stmt, ast.FunctionDef) and stmt.name == leaf:
                hits.append((dotted, _keyword_parameters(stmt)))
            elif isinstance(stmt, ast.ClassDef) and stmt.name == leaf and _is_dataclass(stmt):
                hits.append((dotted, _constructor_keywords(stmt)))
    return hits


class _Derived(NamedTuple):
    """What the page's **Set via** cells require of :data:`_CONSTANT_BACKED_SURFACES`.

    ``required`` maps each disposition-1 ``(module, qualified name, parameter)`` to the
    constants named by each row that spelled it, one set per row and never merged: two
    rows spelling the same pair make two claims, and a union would let one row's constant
    vouch for the other's. ``unresolved`` holds one message per disposition-3 token or
    keyword.
    """

    required: dict[tuple[str, str, str], tuple[frozenset[str], ...]]
    unresolved: list[str]


def _is_call(token: str, name: str) -> bool:
    """Does ``token``, its ``…`` read as ``...``, compile as one call of ``name`` itself --
    parentheses balanced, no keyword repeated, and not a call of what that call returns?"""
    source = token.replace("…", "...")
    try:
        body = ast.parse(source, mode="eval").body
        compile(source, "<Set via>", "eval")  # the parser lets a repeated keyword through
    except SyntaxError:
        return False
    return isinstance(body, ast.Call) and ast.unparse(body.func) == name


def _is_literal(node: ast.expr) -> bool:
    """Does ``ast.literal_eval`` accept ``node``?"""
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def _derive(rows: list[list[str]], modules: _Modules | None = None) -> _Derived:
    """Walk the constant-naming six-column rows and sort every spelled keyword.

    ``modules`` defaults to the real source tree; a test passes parsed snippets instead.
    """
    modules = _source_modules() if modules is None else modules
    required: dict[tuple[str, str, str], tuple[frozenset[str], ...]] = {}
    unresolved: list[str] = []
    for row in rows:
        if len(row) != 6:
            continue
        constants = frozenset(t for t in _BACKTICKED_RE.findall(row[0]) if _CONSTANT_RE.match(t))
        if not constants:
            continue
        for token in _BACKTICKED_RE.findall(row[4]):
            match = _SURFACE_TOKEN_RE.match(token)
            if match is None or not _is_call(token, match.group(1)):
                if "(" in token or ")" in token:
                    unresolved.append(
                        f"`{token}` carries a parenthesis but is not a surface call: NAME(…) "
                        "needs NAME to be a Python identifier, optionally dotted, called once "
                        "with both parentheses and no keyword repeated"
                    )
                continue
            name, inner = match.groups()
            hits = _resolve(name, modules)
            if len(hits) != 1:
                unresolved.append(
                    f"`{token}` names {name!r}, which resolves to "
                    + (
                        "no public module-level function, dataclass or Class.method under src/"
                        if not hits
                        else f"{len(hits)} public definitions ({sorted(d for d, _ in hits)}), "
                        "so the page does not say which one it documents"
                    )
                )
                continue
            ((dotted, params),) = hits
            for keyword in _KEYWORD_RE.findall(inner):
                if keyword not in params:
                    unresolved.append(
                        f"`{token}` spells {keyword}=, which {dotted}.{name} does not accept "
                        "by keyword"
                    )
                elif isinstance(params[keyword], ast.Name):
                    key = (dotted, name, keyword)
                    required[key] = (*required.get(key, ()), constants)
                elif (default := params[keyword]) is not None and not _is_literal(default):
                    unresolved.append(
                        f"`{token}` spells {keyword}=, whose default on {dotted}.{name} is "
                        f"{ast.unparse(default)}: neither a literal nor a constant's name, so "
                        "nothing here can check it against the page"
                    )
    return _Derived(required, unresolved)


def _foreign(
    required: dict[tuple[str, str, str], tuple[frozenset[str], ...]],
    table: dict[tuple[str, str, str], str],
) -> list[str]:
    """Each derived pair whose table constant some row spelling it does not name.

    Checked row by row: every row that spells the pair must name the table's constant.
    """
    return sorted(
        f"{'.'.join(key)} -> {table[key]} (row names {sorted(constants)})"
        for key, rows in required.items()
        if key in table
        for constants in rows
        if table[key] not in constants
    )


def test_every_surface_a_set_via_cell_names_resolves() -> None:
    """Disposition 3: a documented surface or keyword that the code lacks fails outright.

    Rename a surface in a **Set via** cell, or have the code drop the keyword the cell
    spells, and the page is describing a call no reader can make. That is not a pair the
    table can skip -- it is the page and the code having parted -- so it fails here. So
    does a call-shaped token naming no surface, and a spelled keyword whose default
    neither check can read.
    """
    unresolved = _derive(ROWS).unresolved
    assert not unresolved, (
        "docs/reference/parameters.md names surfaces the code does not have:\n"
        + "\n".join(unresolved)
    )


def test_every_spelled_keyword_with_a_constant_default_is_in_the_table() -> None:
    """Disposition 1: every keyword a **Set via** cell spells, defaulted to a name, is bound.

    One-directional -- what the page derives must be a subset of the table, which may hold
    more (the ``w0`` seam surfaces the page claims in words rather than by name). A table
    entry for a derived pair must also name a constant that the deriving row documents, so
    for that pair the binding runs page -> constant -> signature by name.
    """
    required = _derive(ROWS).required
    assert required, (
        "no (surface, keyword) pair was derived from any Set via cell -- the page's table "
        "layout has changed and this check would otherwise pass on nothing"
    )
    table = {
        (dotted, func, param): constant
        for dotted, func, param, constant in _CONSTANT_BACKED_SURFACES
    }
    unbound = sorted(".".join(key) for key in required if key not in table)
    assert not unbound, (
        "docs/reference/parameters.md documents these constant-defaulted surfaces, but "
        f"_CONSTANT_BACKED_SURFACES does not bind them to their constant: {unbound}"
    )
    foreign = _foreign(required, table)
    assert not foreign, (
        "table entries bind a page-named surface to a constant its own row does not name: "
        f"{foreign}"
    )


def test_the_derivation_sorts_each_keyword_into_exactly_one_disposition() -> None:
    """The mechanism itself, on rows built here: a skip would make the guard vacuous.

    One constant-naming row exercising all three dispositions plus the keyword-less token,
    and the same cell on a row naming no constant, which is not walked at all.
    """
    cell = (
        "`apparent_e_histogram(bins=…)` / `raw_fret_cloud(kde=…)` / "
        "`DeepTraceDataset.split(…)` / `apparent_e_histogram_x(bins=…)` / "
        "`apparent_e_histogram(no_such_keyword=…)` / `tether extract --window`"
    )
    derived = _derive([["`bins`<br>`DEFAULT_NBINS`", "`151`", "", "", cell, "**no**"]])
    assert derived.required == {
        ("tether.analysis.histogram", "apparent_e_histogram", "bins"): (
            frozenset({"DEFAULT_NBINS"}),
        )
    }
    assert len(derived.unresolved) == 2
    assert "apparent_e_histogram_x" in derived.unresolved[0]
    assert "no_such_keyword" in derived.unresolved[1]

    unwalked = _derive([["`bins`", "`151`", "", "", cell, "**no**"]])
    assert unwalked == _Derived({}, [])


def _snippets(*modules: tuple[str, str]) -> _Modules:
    """Parse ``(dotted name, source)`` pairs into the shape :func:`_derive` reads."""
    return tuple((dotted, ast.parse(source)) for dotted, source in modules)


def _one_row(cell: str) -> list[list[str]]:
    return [["`x`<br>`DEFAULT_X`", "`1`", "", "", cell, "**no**"]]


def test_a_positional_only_parameter_is_not_a_keyword() -> None:
    """``f(bins=…)`` on a ``bins`` declared before ``/`` is a ``TypeError``, so it fails."""
    modules = _snippets(
        ("tether.m", "def f(values, bins=DEFAULT_X, /, *, value_range=DEFAULT_X): ...")
    )
    derived = _derive(_one_row("`f(bins=…, value_range=…)`"), modules)
    assert derived.required == {("tether.m", "f", "value_range"): (frozenset({"DEFAULT_X"}),)}
    assert len(derived.unresolved) == 1 and "bins=" in derived.unresolved[0]


@pytest.mark.parametrize(
    ("source", "accepted"),
    [
        ("@dataclass(frozen=True)\nclass S:\n    t: float = DEFAULT_X\n", True),
        ("@dataclass(frozen=True, init=False)\nclass S:\n    t: float = DEFAULT_X\n", False),
        ("@dataclass\nclass S:\n    t: float = field(default=DEFAULT_X, init=False)\n", False),
        ("@dataclass\nclass S:\n    t: ClassVar[float] = DEFAULT_X\n", False),
        ("@dataclass\nclass S:\n    t: float = DEFAULT_X\n    def __init__(self): ...\n", False),
        ("@dataclass\nclass S:\n    t: float = field(default=DEFAULT_X)\n", True),
    ],
    ids=["generated", "init-false", "field-init-false", "classvar", "explicit-init", "field"],
)
def test_a_dataclass_token_resolves_against_its_constructor(source: str, accepted: bool) -> None:
    """A field the constructor does not take is not a keyword of ``S(…)``."""
    derived = _derive(_one_row("`S(t=…)`"), _snippets(("tether.m", source)))
    if accepted:
        assert derived == _Derived({("tether.m", "S", "t"): (frozenset({"DEFAULT_X"}),)}, [])
    else:
        assert derived.required == {} and len(derived.unresolved) == 1


def test_a_private_namesake_cannot_stand_in_for_a_removed_surface() -> None:
    """The documented ``fine_tune`` gone, a private one left: the token must not resolve.

    And two *public* definitions of one name are ambiguous, so they fail too rather than
    one being picked.
    """
    private = ("tether.ml.deep._torch_model", "def fine_tune(model, seed=None): ...")
    public = ("tether.ml.deep.model", "def fine_tune(model, seed=DEFAULT_X): ...")
    cell = "`fine_tune(seed=…)`"
    assert _derive(_one_row(cell), _snippets(private, public)) == _Derived(
        {("tether.ml.deep.model", "fine_tune", "seed"): (frozenset({"DEFAULT_X"}),)}, []
    )
    gone = _derive(_one_row(cell), _snippets(private))
    assert gone.required == {} and "no public" in gone.unresolved[0]
    twin = ("tether.ml.other", "def fine_tune(model, seed=DEFAULT_X): ...")
    ambiguous = _derive(_one_row(cell), _snippets(public, twin))
    assert ambiguous.required == {} and "2 public definitions" in ambiguous.unresolved[0]


def test_two_rows_spelling_one_pair_are_checked_row_by_row() -> None:
    """A row naming the wrong constant fails even when another row names the right one."""
    modules = _snippets(("tether.m", "def f(values, bins=DEFAULT_A): ..."))
    rows = [
        ["`bins`<br>`DEFAULT_A`", "`1`", "", "", "`f(bins=…)`", "**no**"],
        ["`pre`<br>`DEFAULT_B`", "`2`", "", "", "`f(bins=…)`", "**no**"],
    ]
    derived = _derive(rows, modules)
    key = ("tether.m", "f", "bins")
    assert derived.required == {key: (frozenset({"DEFAULT_A"}), frozenset({"DEFAULT_B"}))}
    assert _foreign(derived.required, {key: "DEFAULT_A"}) == [
        "tether.m.f.bins -> DEFAULT_A (row names ['DEFAULT_B'])"
    ]
    assert _foreign({key: derived.required[key][:1]}, {key: "DEFAULT_A"}) == []


@pytest.mark.parametrize(
    ("source", "qualname"),
    [
        ("DEFAULT_X = 1\ndef f(values, w0=DEFAULT_X, /): ...\n", "f"),
        (
            "DEFAULT_X = 1\n@dataclass(frozen=True, init=False)\nclass S:\n"
            "    w0: float = DEFAULT_X\n",
            "S",
        ),
    ],
    ids=["positional-only", "dataclass-init-false"],
)
def test_a_table_binding_needs_the_keyword_to_be_passable(
    source: str, qualname: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A table row is a ``kw=…`` claim, so a default the call cannot take by keyword fails.

    Even though the default still names the constant: the hand-enumerated ``w0`` rows are
    bound only here, never through :func:`_derive`.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    with pytest.raises(AssertionError, match="w0"):
        surface_default_name("pkg.mod", qualname, "w0")


_PRIORS = "PB_PRIOR_A = 1.0\nPB_PRIOR_MU = 0.0\n"


@pytest.mark.parametrize(
    ("modules", "failure"),
    [
        ({"proj": "from pkg.fret import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n"}, None),
        (
            {
                "relay": "from pkg.fret import PB_PRIOR_A\n",
                "proj": "from pkg.relay import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
            },
            None,
        ),
        (
            {
                "relay/__init__": "from ..fret import PB_PRIOR_A\n",
                "proj": "from pkg.relay import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
            },
            None,
        ),
        (
            {"proj": "from pkg.fret import PB_PRIOR_MU as PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n"},
            "from pkg.fret import PB_PRIOR_MU as PB_PRIOR_A",
        ),
        (
            {"proj": "from .fret import PB_PRIOR_MU as PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n"},
            "from .fret import PB_PRIOR_MU as PB_PRIOR_A",
        ),
        (
            {
                "proj": "from pkg.fret import PB_PRIOR_A\nPB_PRIOR_A = 2.0\n"
                "def f(a=PB_PRIOR_A): ...\n"
            },
            "no single home",
        ),
        (
            {
                "fret": _PRIORS + "PB_PRIOR_A = 0.0\n",
                "proj": "from pkg.fret import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
            },
            "bound 2 times under src/",
        ),
        (
            {"proj": "def f(a=PB_PRIOR_A): ...\nfrom pkg.fret import PB_PRIOR_A\n"},
            "bound to pkg.proj.PB_PRIOR_A",
        ),
        (
            {
                "proj": "from pkg.fret import PB_PRIOR_A\nif True:\n"
                "    from pkg.fret import PB_PRIOR_MU as PB_PRIOR_A\n"
                "def f(a=PB_PRIOR_A): ...\n"
            },
            "bound 2 times there",
        ),
        (
            {
                "other": "PB_PRIOR_X = 2.0\n",
                "proj": "from pkg.fret import PB_PRIOR_A\nfrom pkg.other import *\n"
                "def f(a=PB_PRIOR_A): ...\n",
            },
            "bound 2 times there",
        ),
        (
            {
                "proj": "from pkg.fret import PB_PRIOR_A\nclass C:\n    PB_PRIOR_A = 2.0\n"
                "    def f(self, a=PB_PRIOR_A): ...\n"
            },
            "bound 2 times under src/",
        ),
        (
            {
                "proj": "from pkg.fret import PB_PRIOR_A\nmatch {}:\n"
                "    case {'a': PB_PRIOR_A}: ...\ndef f(a=PB_PRIOR_A): ...\n"
            },
            "bound 2 times under src/",
        ),
        (
            {
                "proj": "from pkg.fret import PB_PRIOR_A, PB_PRIOR_MU as PB_PRIOR_A\n"
                "def f(a=PB_PRIOR_A): ...\n"
            },
            "bound 2 times there",
        ),
        (
            {
                "fret": "PB_PRIOR_MU = 0.0\n",
                "consts/__init__": "PB_PRIOR_A = 1.0\n",
                "proj": "from pkg.consts import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
            },
            None,
        ),
        (
            {
                "fret": "PB_PRIOR_MU = 0.0\n",
                "consts": "PB_PRIOR_A = 1.0\n",
                "consts/__init__": "PB_PRIOR_X = 2.0\n",
                "proj": "from pkg.consts import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
            },
            "both a module and a package",
        ),
        (
            {
                "fret": "PB_PRIOR_MU = 0.0\n",
                "../top": "PB_PRIOR_A = 1.0\n",
                "proj": "from ..top import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
            },
            "beyond the top-level package",
        ),
        (
            {
                "proj": "from pkg.fret import PB_PRIOR_A\ndef f(a=PB_PRIOR_A): ...\n",
                "proj/__init__": "from pkg.fret import PB_PRIOR_A\n",
            },
            "both a module and a package",
        ),
    ],
    ids=[
        "import",
        "re-export",
        "package-relative-re-export",
        "alias",
        "relative-alias",
        "shadow",
        "reassigned-at-home",
        "import-after-def",
        "compound-rebinding",
        "star-import",
        "class",
        "match-capture",
        "duplicate-alias",
        "package-init-home",
        "package-shadows-module",
        "relative-beyond-top",
        "surface-shadowed-by-package",
    ],
)
def test_a_name_default_is_traced_to_the_constant_it_is_bound_to(
    modules: dict[str, str],
    failure: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#513: ``a=PB_PRIOR_A`` passes only if ``PB_PRIOR_A`` is bound to that constant.

    An alias, a shadow, a reassignment, a conditional rebinding, a star import, or an import
    the ``def`` runs before all leave the spelled name intact and the value open to change,
    so each fails and names what it found. So do a module that is also a package, on either
    end of the trace, and a relative import Python would refuse (above the top-level
    package). Each key is a path under ``pkg/``; ``../top`` sits beside it.
    """
    for name, source in {"fret": _PRIORS, **modules}.items():
        path = tmp_path / "pkg" / f"{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    qualname = "C.f" if "class C" in modules["proj"] else "f"
    if failure is None:
        check_constant_backed("pkg.proj", qualname, "a", "PB_PRIOR_A")
    else:
        with pytest.raises(AssertionError, match=re.escape(failure)):
            check_constant_backed("pkg.proj", qualname, "a", "PB_PRIOR_A")


def test_a_call_shaped_token_that_names_no_surface_fails() -> None:
    """#513: ``apparent-e-histogram(bins=…)`` names no callable, so it may not be skipped.

    Nor may a token whose parentheses do not balance, whichever way.

    CLI flags and prose carry no parenthesis and are still not walked.
    """
    malformed = [
        "`apparent-e-histogram(bins=…)`",
        "`apparent_e_histogram(bins=…`",
        "`apparent_e_histogram(bins=…))`",
        "`apparent_e_histogram((bins=…)`",
        "`apparent_e_histogram(bins=…)(…)`",
        "`apparent_e_histogram(bins=…, bins=…)`",
    ]
    cell = " / ".join([*malformed, "`tether extract --window`", "`--window`"])
    derived = _derive([["`bins`<br>`DEFAULT_NBINS`", "`151`", "", "", cell, "**no**"]])
    assert derived.required == {}
    assert len(derived.unresolved) == len(malformed)
    for token, message in zip(malformed, derived.unresolved, strict=True):
        assert token in message


def test_a_default_that_is_neither_a_literal_nor_a_name_fails() -> None:
    """#513: ``mode=ParticleDetectionMode.WAVELET`` is read by neither check, so it fails."""
    derived = _derive([["`x`<br>`DEFAULT_X`", "`1`", "", "", "`detect_spots_by_mode(mode=…)`", ""]])
    assert derived.required == {}
    assert len(derived.unresolved) == 1
    assert "`detect_spots_by_mode(mode=…)`" in derived.unresolved[0]
    assert "ParticleDetectionMode.WAVELET" in derived.unresolved[0]

    source = "def f(a=float('nan'), b=-1, c=None, d=(1, 2.0), e=DEFAULT_X): ..."
    snippet = _derive(_one_row("`f(a=…, b=…, c=…, d=…, e=…)`"), _snippets(("tether.m", source)))
    assert snippet.required == {("tether.m", "f", "e"): (frozenset({"DEFAULT_X"}),)}
    assert len(snippet.unresolved) == 1 and "float('nan')" in snippet.unresolved[0]


def test_every_row_states_whether_it_is_recorded_in_the_project() -> None:
    """No parameter row may leave the 'Recorded in `.tether`' cell empty."""
    for row in ROWS:
        if len(row) != 6:
            continue  # the two 3-column explanatory tables
        assert row[5], f"empty 'Recorded in .tether' cell in row: {row[0]}"


# --- The label binding itself -------------------------------------------------
# The passes above assert what the page currently says. These assert that the guard
# would NOTICE if it changed -- which is the whole of #221, since every literal stayed
# present and distinct through the swap that motivated it.

#: The two mode-keyed cells as the page writes them today.
_THRESHOLD_CELL = "`None` (per-mode: `0.5` intensity, `0.98` bandpass)"
_SEPARATION_CELL = "`None` (per-mode: `8.0` wavelet, `3.0` intensity and bandpass)"


@pytest.mark.parametrize(
    ("parameter", "modes"),
    [
        ("detection_threshold", ("intensity", "bandpass")),
        ("min_separation", ("wavelet", "intensity", "bandpass")),
    ],
)
def test_each_mode_label_lands_in_exactly_one_literals_span(
    parameter: str, modes: tuple[str, ...]
) -> None:
    """The premise: the cell parses into labelled clauses, one owner per mode.

    Deliberately structural rather than a byte-for-byte pin on the cell. The claims
    above are all *subset* tests, so a rewording that dropped a mode word entirely
    would make them pass vacuously -- the binding would quietly become the
    set-of-literals check it replaced, and nothing would say so. This is the check
    that notices, while still leaving the prose free to be rewritten.

    A mode owned by two spans is the same defect from the other side: it would let a
    value satisfy a label that also sits next to a different value.
    """
    cell = next(
        row[1] for row in ROWS if parameter in _BACKTICKED_RE.findall(row[0]) and len(row) > 1
    )
    spans = _labelled_literals(cell)
    for mode in modes:
        owners = [literal for literal, words in spans if mode in words]
        assert len(owners) == 1, (
            f"the `{parameter}` Default cell must name the `{mode}` mode next to exactly one "
            f"value for the label binding to mean anything; it is claimed by {owners} in "
            f"{cell!r}"
        )


def test_a_label_swap_fails_even_though_every_literal_survives() -> None:
    """#221's acceptance criterion, and the exact hole it was filed for.

    Swapping the two mode labels leaves `0.5` and `0.98` both present and both
    distinct, so the set-of-literals checks that predate this stay green while the
    page tells a reader to threshold `bandpass` at `0.5`.
    """
    swapped = "`None` (per-mode: `0.98` intensity, `0.5` bandpass)"
    # The literals are untouched by the swap -- which is why counting them cannot help.
    assert set(_BACKTICKED_RE.findall(swapped)) == set(_BACKTICKED_RE.findall(_THRESHOLD_CELL))

    intensity = Claim(("detection_threshold",), 0.5, frozenset({"intensity"}))
    bandpass = Claim(("detection_threshold",), 0.98, frozenset({"bandpass"}))
    assert intensity.satisfied_by(_THRESHOLD_CELL)
    assert bandpass.satisfied_by(_THRESHOLD_CELL)
    assert not intensity.satisfied_by(swapped)
    assert not bandpass.satisfied_by(swapped)


def test_one_literal_may_carry_several_labels() -> None:
    """A value shared by two modes is one literal that must answer for both.

    ``min_separation`` prints a single ``3.0`` for intensity and bandpass. The merged
    claim demands both words in that literal's span, so dropping either from the page
    fails -- while the ``8.0`` sitting beside it cannot satisfy either.
    """
    shared = Claim(("min_separation",), 3.0, frozenset({"intensity", "bandpass"}))
    assert shared.satisfied_by(_SEPARATION_CELL)
    assert not shared.satisfied_by("`None` (per-mode: `8.0` wavelet, `3.0` intensity)")
    assert not shared.satisfied_by("`None` (per-mode: `3.0` intensity, `8.0` bandpass)")

    wavelet = Claim(("min_separation",), 8.0, frozenset({"wavelet"}))
    assert wavelet.satisfied_by(_SEPARATION_CELL)
    assert not wavelet.satisfied_by(
        "`None` (per-mode: `3.0` wavelet, `8.0` intensity and bandpass)"
    )


def test_a_label_is_bounded_by_the_next_literal() -> None:
    """A label may not be satisfied by a word belonging to a different value.

    Without the bound, "the cell contains `0.5` and contains 'intensity'" is true of
    every arrangement of those tokens, which is precisely the check that failed to
    catch the swap. The span ends at the next backtick, so a label written *before* a
    value belongs to the value it follows, not the one it precedes.
    """
    assert _labelled_literals(_THRESHOLD_CELL) == [
        ("None", frozenset({"per-mode"})),
        ("0.5", frozenset({"intensity"})),
        ("0.98", frozenset({"bandpass"})),
    ]


def test_an_unlabelled_claim_is_satisfied_by_any_literal_in_the_cell() -> None:
    """Every ordinary row keeps the behaviour it had before labels existed.

    The empty label set is a subset of every span, so an unlabelled claim never
    consults the prose -- the binding is opt-in per registry entry, and the ~200 rows
    that print one value are unaffected.
    """
    plain = Claim(("window",), 21)
    assert plain.satisfied_by("`21`")
    assert plain.satisfied_by("`None` (per-mode: `21` whatever, `3.0` else)")
    assert not plain.satisfied_by("`22`")


def test_entries_sharing_tokens_and_value_merge_their_labels() -> None:
    """The merge is by live value, so it cannot excuse two defaults drifting apart.

    While intensity and bandpass agree, they are one claim carrying both labels. Give
    them different values and they split into two claims that each demand their own
    labelled literal -- so a code change to one of them alone is still caught.
    """
    tokens = ("min_separation",)
    agreeing = _claims([(tokens, 3.0, ("intensity",)), (tokens, 3.0, ("bandpass",))])
    assert len(agreeing) == 1
    assert agreeing[0].labels == frozenset({"intensity", "bandpass"})

    drifted = _claims([(tokens, 3.0, ("intensity",)), (tokens, 4.0, ("bandpass",))])
    assert len(drifted) == 2
    assert not any(claim.satisfied_by(_SEPARATION_CELL) for claim in drifted if claim.value == 4.0)


def test_the_two_mode_keyed_rows_are_registered_with_labels() -> None:
    """The binding is worthless if the registry stops using it.

    Pins the mode/value pairs #221 names, read from the live registry rather than
    restated, so this fails if an entry loses its label or its source function.
    """
    labelled = {(claim.tokens[0], claim.value): claim.labels for claim in REGISTRY if claim.labels}
    assert labelled == {
        ("detection_threshold", 0.5): frozenset({"intensity"}),
        ("detection_threshold", 0.98): frozenset({"bandpass"}),
        ("min_separation", 8.0): frozenset({"wavelet"}),
        ("min_separation", 3.0): frozenset({"intensity", "bandpass"}),
    }
