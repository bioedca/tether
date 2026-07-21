# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Public ``@property`` accessors carry a docstring (PRD §9 M10 docs gate, issue #178).

Properties are what a caller actually *reads* off Tether's result types —
``Histogram2D.time_centers``, ``ReconcileReport.n_matched`` — and they were the one
systematically undocumented member kind in the package: 269 public accessors at 68.0%
coverage, against 96.1% for every other public callable. Without a docstring, a reader in
a REPL (or in a future generated API reference) gets a bare ``np.ndarray`` annotation and
no way to learn that ``time_centers`` is in **seconds** rather than frame indices.

The guard is deliberately **zero-tolerance** rather than a percentage floor. Issue #178
asks that the test "fails if a new undocumented public property is added", and a
threshold cannot do that once the backlog is cleared: at 269 accessors, deleting a single
docstring still leaves 99.6% coverage, which sails past a 95% bar. So the assertion is
that the undocumented set is *empty*, and the ≥95% acceptance criterion is asserted
separately as the weaker floor it is.

Presence is all this guards. It deliberately does **not** police docstring *shape*: the
accessors added for #178 are one-liners by that issue's constraint, but plenty of
pre-existing property docstrings are multi-paragraph and correct, and the wider
numpydoc-vs-Google convention is explicitly left to #179.

Stdlib only (:mod:`ast` over the source text, never an import), matching the scan pattern
in ``tests/test_marker_contract.py`` and ``tests/test_imports.py``. Nothing here imports
``tether``, so the optional GUI/torch stacks are irrelevant and this runs on the base
3-OS matrix.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tether"

# The decorators that make a method an attribute-like read. ``functools.cached_property``
# is included because it is indistinguishable from ``property`` at the call site.
_PROPERTY_DECORATORS = frozenset({"property", "cached_property"})

# The floor stated in issue #178's acceptance criteria. The real gate is
# ``test_no_public_property_is_undocumented`` below; this constant only pins the weaker
# published claim so it cannot quietly stop being true.
_MIN_COVERAGE_PCT = 95.0


def _is_property(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when ``node`` is decorated as a property.

    Compares the last dotted segment of the decorator, so ``@property``,
    ``@functools.cached_property`` and ``@cached_property`` all match, while a
    ``@foo.setter`` (whose final segment is ``setter``) correctly does not.
    """
    for dec in node.decorator_list:
        # Strip any call arguments: `@property` is a Name, `@x.setter` an Attribute.
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id in _PROPERTY_DECORATORS:
            return True
        if isinstance(target, ast.Attribute) and target.attr in _PROPERTY_DECORATORS:
            return True
    return False


def _public_properties() -> list[tuple[str, bool]]:
    """Every public property in ``src/tether`` as ``("path:line Class.name", documented)``.

    "Public" means neither the owning class nor the accessor is underscore-prefixed — a
    private helper's accessors are not part of the API surface this guard protects.
    """
    found: list[tuple[str, bool]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if cls.name.startswith("_"):
                continue
            for node in cls.body:
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if node.name.startswith("_") or not _is_property(node):
                    continue
                rel = path.relative_to(SRC.parents[1]).as_posix()
                label = f"{rel}:{node.lineno} {cls.name}.{node.name}"
                found.append((label, ast.get_docstring(node) is not None))
    return found


def test_the_scan_finds_the_property_surface() -> None:
    """Sanity: a broken path or decorator matcher would make every check below vacuous."""
    props = _public_properties()
    assert len(props) >= 250, (
        f"expected the accumulated public-property surface (269 at #178); found {len(props)}"
    )


def test_no_public_property_is_undocumented() -> None:
    """The real gate — every public property has a docstring.

    Zero-tolerance on purpose: this is what makes a *newly added* undocumented property
    fail, which a percentage floor cannot do once the backlog is cleared.
    """
    undocumented = [label for label, documented in _public_properties() if not documented]
    assert not undocumented, (
        f"these {len(undocumented)} public properties have no docstring — state what the "
        "value IS and its units, in one line (issue #178): " + "\n  ".join([""] + undocumented)
    )


def test_coverage_meets_the_published_floor() -> None:
    """The ≥95% figure issue #178 states as its acceptance criterion still holds."""
    props = _public_properties()
    documented = sum(1 for _, ok in props if ok)
    coverage = 100.0 * documented / len(props)
    assert coverage >= _MIN_COVERAGE_PCT, (
        f"public-property docstring coverage fell to {coverage:.1f}% "
        f"({documented}/{len(props)}), below the {_MIN_COVERAGE_PCT}% floor"
    )
