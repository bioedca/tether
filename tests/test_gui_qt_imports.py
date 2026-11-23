# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether.gui`` imports Qt one way, with a short list of stated exceptions (#339).

The convention, stated in the :mod:`tether.gui` package docstring: a module imports Qt
through ``pyqtgraph.Qt``. The startup path imports PySide6 itself, because pyqtgraph
loads NumPy and the splash must show first, and the napari movie panel imports
``qtpy``, because napari builds its own Qt objects through it.

These tests read the source with :mod:`ast` and import nothing, so they need no Qt and
run on every leg of the matrix. They see every import, at module level, inside a
function, and under ``if TYPE_CHECKING:``, so a convention break cannot hide in a lazy
import. Docstring cross-references (``[`QComboBox`][qtpy.QtWidgets.QComboBox]``, the
mkdocstrings form ADR-0093 adopted) are checked too, because
#339 found one that named a binding its module never imports.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GUI = Path(__file__).resolve().parents[1] / "src" / "tether" / "gui"

# The convention: the one Qt module path every other module imports from.
CONVENTION = "pyqtgraph.Qt"

# Every top-level Qt binding name. ``pyqtgraph`` itself is not here: ``import
# pyqtgraph as pg`` is the plotting library, not a Qt binding.
BINDINGS = frozenset({"PySide6", "PySide2", "PyQt6", "PyQt5", "qtpy"})

# The exceptions: module stem -> the one binding it may import besides the convention.
# Each must say why in its own docstring (see test_each_exception_states_its_reason).
EXCEPTIONS: dict[str, str] = {
    "app": "PySide6",
    "identity": "PySide6",
    "splash": "PySide6",
    "movie_panel": "qtpy",
}

# A mkdocstrings cross-reference: ``[`title`][target]``, or ``[`target`][]`` whose title is
# the target (docs/maintainers/docstring-style.md).
_XREF = re.compile(r"\[`([^`\]]+)`\]\[([^\]]*)\]")


def _modules() -> list[Path]:
    return sorted(GUI.glob("*.py"))


def _qt_imports(path: Path) -> list[tuple[int, str]]:
    """Every import in ``path`` whose top-level name is a Qt binding or ``pyqtgraph.Qt``.

    Returns ``(line, dotted module)`` pairs. ``from pyqtgraph import Qt`` counts as
    ``pyqtgraph.Qt``; ``import pyqtgraph`` (the plot library) does not count at all.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
            if node.module == "pyqtgraph" and any(a.name == "Qt" for a in node.names):
                names = [CONVENTION]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top in BINDINGS or name == CONVENTION or name.startswith(CONVENTION + "."):
                found.append((node.lineno, name))
    return found


def _binding(name: str) -> str:
    """``pyqtgraph.Qt`` for the convention, else the top-level binding name."""
    if name == CONVENTION or name.startswith(CONVENTION + "."):
        return CONVENTION
    return name.split(".")[0]


def _docstring(path: Path) -> str:
    """The module docstring with its whitespace collapsed, so line wrapping never matters."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return " ".join((ast.get_docstring(tree) or "").split())


def test_every_qt_import_follows_the_convention() -> None:
    """Outside the stated exceptions, every Qt import is from ``pyqtgraph.Qt``.

    An exception module may use the convention too, and its one named binding, but no
    third: the sidecar's PyQt5 or a stray PySide2 never belongs in ``tether.gui``.
    """
    breaks = []
    for path in _modules():
        allowed = {CONVENTION, EXCEPTIONS.get(path.stem, CONVENTION)}
        for line, name in _qt_imports(path):
            if _binding(name) not in allowed:
                breaks.append(f"{path.name}:{line}: {name}")
    assert not breaks, (
        f"these imports break the tether.gui Qt convention (import Qt from {CONVENTION}, "
        "or add the module to EXCEPTIONS with a reason in its docstring): "
        f"{breaks}"
    )


def test_every_exception_is_still_used() -> None:
    """No exception outlives its reason: each listed module imports its binding."""
    stale = [
        stem
        for stem, binding in EXCEPTIONS.items()
        if not any(_binding(n) == binding for _, n in _qt_imports(GUI / f"{stem}.py"))
    ]
    assert not stale, f"these EXCEPTIONS no longer import their binding; remove them: {stale}"


def test_each_exception_states_its_reason() -> None:
    """Each exception's module docstring names its binding and says it is not the convention.

    The test cannot judge a reason, but it can require one to be written where a reader
    of the module will find it: the docstring names the binding and contains
    ``not ``pyqtgraph.Qt````. The package docstring must also list the module, so the
    convention and its exceptions are stated in one place.
    """
    package_doc = _docstring(GUI / "__init__.py")
    missing = []
    for stem, binding in EXCEPTIONS.items():
        doc = _docstring(GUI / f"{stem}.py")
        if binding not in doc or f"not ``{CONVENTION}``" not in doc:
            missing.append(f"{stem}.py docstring")
        if f"tether.gui.{stem}" not in package_doc:
            missing.append(f"tether.gui docstring entry for {stem}")
    assert CONVENTION in package_doc, "the tether.gui docstring no longer states the convention"
    assert not missing, f"these places do not state the Qt-import exception: {missing}"


def _wrong_xrefs(name: str, text: str, allowed: set[str]) -> list[str]:
    """Each cross-reference in ``text`` that names a Qt binding outside ``allowed``.

    One optional leading ``~`` is stripped from a target before it is classified, so
    ``~qtpy.QtWidgets.QStackedWidget`` is read as ``qtpy`` and not skipped as an unknown
    name (#1113). Returns ``"name:line: target"`` entries.
    """
    wrong = []
    for match in _XREF.finditer(text):
        target = (match.group(2) or match.group(1)).removeprefix("~")
        if target.startswith(CONVENTION + "."):
            continue  # the convention is allowed everywhere
        if target.split(".")[0] not in BINDINGS:
            continue  # not a Qt binding (a pyqtgraph plot class, a tether name)
        if _binding(target) not in allowed:
            line = text.count("\n", 0, match.start()) + 1
            wrong.append(f"{name}:{line}: {target}")
    return wrong


def test_docstring_cross_references_name_an_imported_binding() -> None:
    """A docstring cross-reference to a Qt class names a binding the module uses.

    #339 found ``deeplasi_wizard_ui`` pointing at ``~qtpy.QtWidgets.QStackedWidget``
    while it imports only ``pyqtgraph.Qt``. A reference may name the convention, or the
    exception binding of that module; any other binding is a stale name.
    """
    wrong = []
    for path in _modules():
        allowed = {CONVENTION, EXCEPTIONS.get(path.stem, CONVENTION)}
        wrong += _wrong_xrefs(path.name, path.read_text(encoding="utf-8"), allowed)
    assert not wrong, f"these cross-references name a Qt binding the module does not use: {wrong}"


def test_cross_reference_with_a_leading_tilde_is_still_checked() -> None:
    """A target written ``[~qtpy...]`` is classified as ``qtpy``, not skipped (#1113).

    A module that imports only the convention fails on it; the ``qtpy`` exception module
    and a ``~``-prefixed convention target still pass.
    """
    text = '"""See [`QStackedWidget`][~qtpy.QtWidgets.QStackedWidget]."""\n'
    assert _wrong_xrefs("m.py", text, {CONVENTION}) == ["m.py:1: qtpy.QtWidgets.QStackedWidget"]
    assert _wrong_xrefs("movie_panel.py", text, {CONVENTION, "qtpy"}) == []
    convention = '"""See [`QWidget`][~pyqtgraph.Qt.QtWidgets.QWidget]."""\n'
    assert _wrong_xrefs("m.py", convention, {CONVENTION}) == []
