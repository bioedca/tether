# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The README and two site pages say where each feature runs today (#667).

The README said extraction, curation, corrections, idealization and analysis were "all in the
application today", and three pages described "an embedded napari movie panel" that the shell
never embeds. ``mkdocs build --strict`` cannot see a false claim, so this module binds the
claims that a code fact can decide.

**The fact is what ``src/tether/gui`` imports**, read with :mod:`ast` and so without Qt: a
feature the application does not import is not in the application. It is a proxy, not proof,
and it is checked in both directions. A feature listed "in the application today" whose module
the GUI does not import fails here, and so does a planned feature whose module the GUI now
imports. The second direction is the "restore the claim" step: the PR that brings a feature
to the application moves its README entry in the same change.

Every README entry is a bullet that opens with a bold label. Each label is in
:data:`BINDINGS` or in :data:`UNBOUND`, so a new entry cannot skip the decision. Unbound
labels are claims no import can decide; ``UNBOUND`` says why for each one.

Dependency-free and stdlib-only, so it runs on the base 3-OS ``test`` matrix.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
README = _REPO / "README.md"
LANDING = _REPO / "docs" / "index.md"
PACKAGING = _REPO / "docs" / "packaging.md"
GUI = _REPO / "src" / "tether" / "gui"
PROJECT_CORE = _REPO / "src" / "tether" / "project" / "core.py"

IN_APP = "In the application today"
CLI = "From the command line today"
PYTHON = "From Python today; planned for the application"
NOT_YET = "Not in Tether yet"
GROUPS = (IN_APP, CLI, PYTHON, NOT_YET)

#: label -> (module prefixes the GUI imports once the feature is in the application).
#: ``None`` binds a fact that is not an import; see :func:`_in_application`.
BINDINGS: dict[str, tuple[str, ...] | None] = {
    "Open and browse": ("tether.gui.trace_dock",),
    "Curation": ("tether.project.labels",),
    "Neighbor view": ("tether.gui.overlap_dock",),
    "One-trace idealization": ("tether.project.idealize",),
    "Standalone-tMAVEN hand-off": ("tether.project.handoff",),
    "Condition validation": ("tether.gui.conditions",),
    "Deep-LASI import": ("tether.gui.deeplasi_wizard_ui",),
    "Population histogram": ("tether.gui.histogram_dock",),
    "Native extraction": ("tether.project.extract",),
    "Batch pipeline": ("tether.project.batch",),
    "Corrections": ("tether.project.correct",),
    "Quality ranker": ("tether.ml", "tether.project.gbranking", "tether.project.ranking"),
    "Analysis plots": ("tether.analysis.tdp", "tether.analysis.dwell", "tether.analysis.cloud"),
    "Exports": ("tether.project.export", "tether.analysis.plot_export"),
    "Movie panel": ("tether.gui.movie_panel",),
    "Trace ⇄ movie round trip": ("tether.gui.roundtrip",),
    "Category keys": None,
}

#: Labels no import decides, each with the reason.
UNBOUND: dict[str, str] = {
    "Whole-project idealization": (
        "the GUI already imports idealize_molecules for the one-trace fit, so only a menu "
        "action (#625) would tell the two apart"
    ),
}

#: The planned-panel sentence on the two site pages, which the panel's PR removes.
PLANNED_PANEL = "movie panel, which reaches the application"


def _is_type_checking(test: ast.expr) -> bool:
    """``TYPE_CHECKING`` or ``typing.TYPE_CHECKING``, the guard whose body never runs."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


def _runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node under ``node`` except the body of an ``if TYPE_CHECKING:`` block.

    Imports there are for the type checker only and do not run, so they say nothing about what
    the application loads. The ``else`` branch of such a block does run, so it is kept.
    """
    if isinstance(node, ast.If) and _is_type_checking(node.test):
        children: list[ast.AST] = list(node.orelse)
    else:
        children = list(ast.iter_child_nodes(node))
    for child in children:
        yield child
        yield from _runtime_nodes(child)


def _imports_in(source: str, own: str) -> set[str]:
    """Every ``tether`` module ``source`` imports at run time, other than ``own`` itself."""
    found: set[str] = set()
    for node in _runtime_nodes(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            parts = ["tether", "gui"][: 3 - node.level] if node.level else []
            base = ".".join(parts + ([node.module] if node.module else []))
            names = [base] + [f"{base}.{alias.name}" for alias in node.names]
        else:
            continue
        found.update(name for name in names if name.startswith("tether") and name != own)
    return found


def _gui_imports() -> set[str]:
    """Every ``tether`` module a ``src/tether/gui`` file imports, other than the file itself."""
    found: set[str] = set()
    for path in sorted(GUI.glob("*.py")):
        found |= _imports_in(path.read_text(encoding="utf-8"), f"tether.gui.{path.stem}")
    return found


def _project_methods() -> set[str]:
    tree = ast.parse(PROJECT_CORE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Project":
            return {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
    raise AssertionError("src/tether/project/core.py no longer defines Project")


def _in_application(label: str, imports: set[str]) -> bool:
    prefixes = BINDINGS[label]
    if prefixes is None:  # "Category keys": the keys persist once Project.set_category exists.
        return "set_category" in _project_methods()
    return any(name == p or name.startswith(p + ".") for p in prefixes for name in imports)


def _groups(text: str) -> dict[str, list[str]]:
    """The README's ``### <group>`` sections under ``## What it does``, as bullet labels."""
    section = text.split("## What it does\n", 1)[1].split("\n## ", 1)[0]
    groups: dict[str, list[str]] = {}
    for chunk in re.split(r"^### ", section, flags=re.MULTILINE)[1:]:
        heading, _, body = chunk.partition("\n")
        labels = re.findall(r"^- \*\*(.+?)\.?\*\*", body, flags=re.MULTILINE)
        groups[heading.strip()] = [label.rstrip(".") for label in labels]
    return groups


def _flat(text: str) -> str:
    """Prose with emphasis, link targets and line breaks removed, for phrase checks."""
    text = re.sub(r"\]\([^)]*\)", "]", text).replace("[", "").replace("]", "")
    return re.sub(r"\s+", " ", text.replace("**", "").replace("*", "")).lower()


def test_the_readme_keeps_its_four_groups() -> None:
    assert tuple(_groups(README.read_text(encoding="utf-8"))) == GROUPS


def test_every_readme_entry_is_bound_or_says_why_not() -> None:
    labels = [
        label for group in _groups(README.read_text(encoding="utf-8")).values() for label in group
    ]
    unknown = sorted(set(labels) - set(BINDINGS) - set(UNBOUND))
    assert not unknown, f"add each new README entry to BINDINGS or UNBOUND: {unknown}"
    assert len(labels) == len(set(labels)), "a README entry is listed twice"
    missing = sorted((set(BINDINGS) | set(UNBOUND)) - set(labels))
    assert not missing, f"bound README entries are gone; update this test with them: {missing}"


@pytest.mark.parametrize("group", GROUPS)
def test_each_readme_entry_is_in_the_group_the_code_supports(group: str) -> None:
    imports = _gui_imports()
    wrong = []
    for label in _groups(README.read_text(encoding="utf-8"))[group]:
        if label in UNBOUND:
            continue
        in_app = _in_application(label, imports)
        if in_app != (group == IN_APP):
            wrong.append(label)
    assert not wrong, (
        f"under '{group}', these entries disagree with what src/tether/gui imports. Move each "
        f"one to the group where it runs today (#667): {wrong}"
    )


@pytest.mark.parametrize("page", [README, LANDING, PACKAGING], ids=lambda p: p.name)
def test_the_movie_panel_is_described_as_it_is(page: Path) -> None:
    text = page.read_text(encoding="utf-8")
    if page == README:  # The planned entry names the panel; the rest of the page must not.
        text = re.sub(rf"^### {NOT_YET}\n.*?(?=^#)", "", text, flags=re.MULTILINE | re.DOTALL)
    flat = _flat(text)
    if _in_application("Movie panel", _gui_imports()):
        assert _flat(PLANNED_PANEL) not in flat, f"{page.name}: the panel is in; restore the claim"
    else:
        assert "embedded napari movie panel" not in flat, (
            f"{page.name} names an embedded napari movie panel, which the shell does not embed "
            "(#356)"
        )


def test_the_guards_see_the_wording_they_replaced() -> None:
    """An absence check is worthless unless presence is shown, so the retired wording is pinned."""
    old = "a PySide6 shell with an embedded **napari** movie\n  panel and a fast trace dock"
    assert "embedded napari movie panel" in _flat(old)
    old_list = "## What it does\n\n### In the application today\n\n- **Movie panel** — embedded\n"
    assert _groups(old_list)[IN_APP] == ["Movie panel"]
    assert not _in_application("Movie panel", _gui_imports())
    assert _in_application("Population histogram", _gui_imports())


def test_a_type_only_import_is_not_a_gui_import() -> None:
    """Only imports that run count: a ``TYPE_CHECKING`` body is for the type checker (#687)."""
    source = (
        "import typing\n"
        "from typing import TYPE_CHECKING\n"
        "from tether.project import labels\n"
        "if TYPE_CHECKING:\n"
        "    from tether.project.batch import run\n"
        "    import tether.gui.movie_panel\n"
        "if typing.TYPE_CHECKING:\n"
        "    from ..ml import ranker\n"
        "else:\n"
        "    from . import histogram_dock\n"
        "    if TYPE_CHECKING:\n"
        "        from tether.project import handoff\n"
        "def later():\n"
        "    if TYPE_CHECKING:\n"
        "        import tether.project.extract\n"
        "    import tether.project.correct\n"
    )
    found = _imports_in(source, "tether.gui.example")
    assert {"tether.project.labels", "tether.gui.histogram_dock", "tether.project.correct"} <= found
    type_only = {
        "tether.project.batch",
        "tether.project.batch.run",
        "tether.gui.movie_panel",
        "tether.ml",
        "tether.ml.ranker",
        "tether.project.extract",
        "tether.project.handoff",
    }
    assert not found & type_only
