# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The calls from the GUI into the core that skip a service command can only get fewer.

The parity rule (PRD §7.11, FR-WORKFLOW-7, ADR-0100) says the application calls only the
Qt-free service commands of :mod:`tether.services` for an action. Most of the GUI was
written before those commands existed, so this test does not forbid a direct call into
the core. It lists each one that exists, and fails in both directions:

- a **new** direct import from ``src/tether/gui`` into a core package fails, so the list
  cannot grow; move the action onto a service command instead;
- an entry that is **no longer** in the code fails too, so a section issue that moves its
  calls onto commands must also remove them here, and the list only gets shorter.

A call is counted by its import: one entry for each ``(GUI file, core module, name)`` with
the number of import statements that bring it in, so a second function-level import of the
same name is growth as well. An import under ``if TYPE_CHECKING:`` is a type, not a call,
and is not counted. ``tether.gui`` itself, ``tether.services`` and the root ``tether``
package (its version) are not the core.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_NOT_CORE = ("tether.gui", "tether.services", "tether._version", "tether.__version__")

# Made by this module's scan on main at f8bec93 (#892). Only ever remove entries.
ALLOWED: dict[str, int] = {
    "gui/about.py: tether.idealize.driver.SIDECAR_ENV_VAR": 1,
    "gui/about.py: tether.idealize.driver.bundled_sidecar_python": 1,
    "gui/conditions.py: tether.io.filename.ConditionKey": 1,
    "gui/deeplasi_executor.py: tether.idealize.read_smd": 1,
    "gui/deeplasi_executor.py: tether.imaging.extract.MovieMetadata": 1,
    "gui/deeplasi_executor.py: tether.io.deeplasi.DeepLasiExport": 1,
    "gui/deeplasi_executor.py: tether.io.deeplasi.read_deeplasi_mat": 1,
    "gui/deeplasi_executor.py: tether.io.deeplasi.read_deeplasi_txt": 1,
    "gui/deeplasi_executor.py: tether.io.intake.AcquisitionFileSet": 1,
    "gui/deeplasi_executor.py: tether.io.movie.open_movie": 1,
    "gui/deeplasi_executor.py: tether.io.recover.RecoveredCoordinates": 1,
    "gui/deeplasi_executor.py: tether.io.recover.SmdCoordinateMatch": 1,
    "gui/deeplasi_executor.py: tether.io.recover.match_smd_to_coordinates": 1,
    "gui/deeplasi_executor.py: tether.io.recover.recover_coordinates": 1,
    "gui/deeplasi_executor.py: tether.io.tdat.Tdat": 1,
    "gui/deeplasi_executor.py: tether.io.tdat.read_tdat": 1,
    "gui/deeplasi_executor.py: tether.project.analysis_import.AnalysisOnlyImportSummary": 1,
    "gui/deeplasi_executor.py: tether.project.analysis_import.import_analysis_only_project": 1,
    "gui/deeplasi_executor.py: tether.project.reconstruct.ReconstructionSummary": 1,
    "gui/deeplasi_executor.py: tether.project.reconstruct.reconstruct_project": 1,
    "gui/deeplasi_wizard.py: tether.io.intake.AcquisitionFileSet": 1,
    "gui/deeplasi_wizard.py: tether.io.intake.DiscoveryResult": 1,
    "gui/deeplasi_wizard.py: tether.io.intake.MovieRefCheck": 1,
    "gui/deeplasi_wizard.py: tether.io.intake.discover_acquisitions": 1,
    "gui/histogram_dock.py: tether.analysis.histogram.DEFAULT_RANGE": 1,
    "gui/movie_panel.py: tether.io.movie.MovieReader": 1,
    "gui/shell.py: tether.analysis.histogram.apparent_e_histogram": 1,
    "gui/shell.py: tether.analysis.histogram.population_apparent_e_histogram": 1,
    "gui/shell.py: tether.analysis.overlap.DEFAULT_APERTURE_RADIUS": 1,
    "gui/shell.py: tether.analysis.overlap.OverlapInfo": 1,
    "gui/shell.py: tether.analysis.overlap.neighbor_report": 1,
    "gui/shell.py: tether.imaging.extract.read_molecules": 2,
    "gui/shell.py: tether.imaging.extract.read_patches": 1,
    "gui/shell.py: tether.imaging.extract.read_traces": 1,
    "gui/shell.py: tether.io.filename.parse_filename": 1,
    "gui/shell.py: tether.io.schema.MOLECULES_DTYPE": 1,
    "gui/shell.py: tether.io.schema.TABLE": 2,
    "gui/shell.py: tether.io.schema.create_project": 1,
    "gui/shell.py: tether.project.analysis_import.read_analysis_only_marker": 1,
    "gui/shell.py: tether.project.core.Project": 3,
    "gui/shell.py: tether.project.handoff.apply_reconcile": 1,
    "gui/shell.py: tether.project.handoff.hand_off_to_tmaven": 1,
    "gui/shell.py: tether.project.handoff.read_return_leg": 1,
    "gui/shell.py: tether.project.idealize.idealize_molecules": 1,
    "gui/shell.py: tether.project.labels.CurationLabel": 1,
    "gui/shell.py: tether.project.lock.DEFAULT_STALENESS_TIMEOUT_S": 1,
    "gui/shell.py: tether.project.lock.LockedError": 4,
    "gui/shell.py: tether.project.trace_layers.INTENSITY_QUANTITY_LAYERS": 1,
    "gui/trace_dock.py: tether.fret.efficiency.apparent_fret": 1,
}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _resolve(module: str | None, level: int, where: str) -> str:
    if not level:
        return module or ""
    parts = ["tether", *Path(where).parent.parts]
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    return ".".join([*base, module] if module else base)


def _is_core(module: str) -> bool:
    if not module.startswith("tether."):
        return False
    return not any(module == n or module.startswith(n + ".") for n in _NOT_CORE)


class _Scan(ast.NodeVisitor):
    def __init__(self, where: str) -> None:
        self.found: Counter[str] = Counter()
        self.where = where

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_core(alias.name):
                self.found[f"{self.where}: {alias.name}"] += 1

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolve(node.module, node.level, self.where)
        for alias in node.names:
            key = f"{module}.{alias.name}"
            # `from tether import project` names a core package; `from tether.io import x`
            # names something in one.
            if _is_core(key if module == "tether" else module) and _is_core(key):
                self.found[f"{self.where}: {key}"] += 1


def scan(root: Path = ROOT) -> Counter[str]:
    """Count each direct import from the GUI into the core."""
    src = root / "src" / "tether"
    total: Counter[str] = Counter()
    for path in sorted((src / "gui").rglob("*.py")):
        visitor = _Scan(path.relative_to(src).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        total.update(visitor.found)
    return total


def test_the_gui_makes_no_new_direct_call_into_the_core() -> None:
    grown = {k: v for k, v in scan().items() if v > ALLOWED.get(k, 0)}
    assert not grown, (
        "new direct imports from src/tether/gui into the core; run the action through a "
        f"service command in tether.services instead (ADR-0100): {grown}"
    )


def test_the_list_of_direct_calls_has_no_stale_entry() -> None:
    found = scan()
    shrunk = {k: (v, found.get(k, 0)) for k, v in ALLOWED.items() if found.get(k, 0) < v}
    assert not shrunk, (
        "these direct imports are gone or fewer (allowed, found); lower or remove them in "
        f"ALLOWED so the list cannot grow back: {shrunk}"
    )


def test_the_scan_counts_a_call_and_skips_a_type_only_import(tmp_path) -> None:
    gui = tmp_path / "src" / "tether" / "gui"
    gui.mkdir(parents=True)
    (gui / "panel.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "from tether import __version__\n"
        "from tether.gui.theme import x\n"
        "from tether.services import run\n"
        "from ..project.core import Project\n"
        "import tether.io.movie\n"
        "if TYPE_CHECKING:\n"
        "    from tether.analysis.histogram import Histogram1D\n"
        "else:\n"
        "    from tether.analysis.histogram import Histogram2D\n"
        "from tether import project\n"
        "def f():\n"
        "    from tether.project.core import Project\n",
        encoding="utf-8",
    )
    assert scan(tmp_path) == {
        "gui/panel.py: tether.project": 1,
        "gui/panel.py: tether.project.core.Project": 2,
        "gui/panel.py: tether.io.movie": 1,
        "gui/panel.py: tether.analysis.histogram.Histogram2D": 1,
    }
