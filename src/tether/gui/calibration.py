# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shell's Calibration menu: make a channel map once, use it for later movies (#353).

A bead-slide video or one bead image gives the donor<->acceptor registration of a
setup. [`ChannelMapActions`][] makes that map with the ``map`` service command
([`tether.services.maps`][]), which saves it as a ``.npz`` map file, and keeps the
file as the session's channel map. A later movie is then extracted with the ``extract``
service command and ``map_path`` set to that file, so the extraction reloads the saved
map instead of fitting its own (``tether extract --map``). A map file from an earlier
session is chosen with **Choose channel map…**.

Every action runs a service command and nothing else (ADR-0100): the command line
(``tether map create``, ``tether extract --map``) gives the same result for the same
input. The commands run on the calling thread, as the shell's other actions do until
the background-task pattern of #343 lands. The mapping page with the spots, the pairs
and the residual overlay is #917.

The ``*_dialog`` methods only ask for paths; the headless methods they call are what
the ``pytest-qt`` tests drive.
"""

from __future__ import annotations

from collections.abc import Callable
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.services import (
    CommandFailed,
    ExtractRequest,
    ExtractResult,
    MapCreateRequest,
    MapCreateResult,
)
from tether.services import run as run_service

if TYPE_CHECKING:
    from tether.project.extract import ExtractionSummary

__all__ = ["ChannelMapActions"]

_BEAD_FILTER = "Bead image or video (*.tif *.tiff);;All files (*)"
_MAP_FILTER = "Channel map (*.npz)"
_MOVIE_FILTER = "Movie (*.tif *.tiff);;All files (*)"
_TETHER_FILTER = "Tether project (*.tether)"


class ChannelMapActions:
    """Make, choose and apply the session's channel map file.

    ``status`` shows one line in the window's status bar; ``open_project`` opens a
    ``.tether`` in the shell (the shell's ``load_project``); ``window`` parents the
    file dialogs.
    """

    def __init__(
        self,
        *,
        status: Callable[[str], None],
        open_project: Callable[[Path], object],
        window: Any = None,
    ) -> None:
        self._status = status
        self._open_project = open_project
        self._window = window
        self._map_path: Path | None = None

    @property
    def map_path(self) -> Path | None:
        """The session's channel map file, or ``None`` when none is made or chosen."""
        return self._map_path

    def make_map(
        self, source: str | PathLike[str], output: str | PathLike[str]
    ) -> MapCreateResult | None:
        """Make a map file from a bead file and keep it as the session's map.

        Returns the command's result, or ``None`` when it failed; the status bar says
        which. An existing file at ``output`` is replaced, by a complete map only (the
        save dialog asks first). A map over the RMS gate is still kept, and the status
        line says it is low-confidence.
        """
        request = MapCreateRequest(source=str(source), output=str(output), overwrite=True)
        try:
            result: MapCreateResult = run_service(request)
        except CommandFailed as exc:
            self._status(f"Channel map not made: {exc}")
            return None
        self._map_path = result.path
        note = " (low confidence: over the RMS gate)" if result.low_confidence else ""
        self._status(
            f"Channel map {result.path.name}: RMS {result.rms_residual:.3f} px over "
            f"{result.n_pairs} bead pairs{note}"
        )
        return result

    def choose_map(self, path: str | PathLike[str]) -> Path | None:
        """Keep an existing ``.npz`` map file as the session's map.

        The file is read when a movie is extracted with it, and a file that is not a
        usable map is refused there with the reason.
        """
        path = Path(path)
        if path.suffix.lower() != ".npz" or not path.is_file():
            self._status(f"Channel map not chosen: {path.name} is not a .npz map file")
            return None
        self._map_path = path
        self._status(f"Channel map: {path.name}")
        return path

    def extract_with_map(
        self, movie: str | PathLike[str], output: str | PathLike[str]
    ) -> ExtractResult | None:
        """Extract ``movie`` into a new ``output`` project with the session's map.

        The extraction reloads the saved map file (``map_path``), so the movie is split
        and registered as the map says. On success the new project is opened in the
        shell; when the shell cannot open it now (an idealization is running, say), the
        status line says the project is saved but not opened. An existing ``output`` is
        refused, never replaced.

        The status line carries the extraction's warnings: a map over its RMS gate
        (the molecules are tagged ``low-confidence-registration``) and halves that look
        swapped (the advisory donor-side check, ADR-0070). The blocking over-gate dialog
        of PRD §7.1 is #351.
        """
        if self._map_path is None:
            self._status("Extract: make or choose a channel map first")
            return None
        request = ExtractRequest(movie=str(movie), output=str(output), map_path=str(self._map_path))
        try:
            result: ExtractResult = run_service(request)
        except CommandFailed as exc:
            self._status(f"Extraction failed: {exc}")
            return None
        summary = result.summary
        opened = self._open_project(summary.output_path)
        where = summary.output_path.name
        if opened is None:  # the shell refused or failed the open; the movie is saved
            where += " (saved, not opened: use File > Open project once the shell is free)"
        self._status(
            f"Extracted {summary.n_molecules} molecule(s) with channel map "
            f"{self._map_path.name} -> {where}{_extraction_warnings(summary)}"
        )
        return result

    # --- dialogs ----------------------------------------------------------------

    def make_map_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick a bead file and a map file name, then make the map."""
        from pyqtgraph.Qt import QtWidgets

        source, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "Bead image or video", "", _BEAD_FILTER
        )
        if not source:
            self._status("Channel map cancelled")
            return
        output, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "Save channel map", str(Path(source).with_suffix(".npz")), _MAP_FILTER
        )
        if not output:
            self._status("Channel map cancelled")
            return
        if not output.lower().endswith(".npz"):
            output += ".npz"
        self.make_map(source, output)

    def choose_map_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick an existing map file."""
        from pyqtgraph.Qt import QtWidgets

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "Choose channel map", "", _MAP_FILTER
        )
        if not path:
            self._status("Choose channel map cancelled")
            return
        self.choose_map(path)

    def extract_with_map_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        """Menu entry: pick a movie and a new project name, then extract with the map."""
        from pyqtgraph.Qt import QtWidgets

        if self._map_path is None:
            self._status("Extract: make or choose a channel map first")
            return
        movie, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "Movie to extract", "", _MOVIE_FILTER
        )
        if not movie:
            self._status("Extract cancelled")
            return
        output, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "New project", str(Path(movie).with_suffix(".tether")), _TETHER_FILTER
        )
        if not output:
            self._status("Extract cancelled")
            return
        if not output.lower().endswith(".tether"):
            output += ".tether"
        self.extract_with_map(movie, output)


def _extraction_warnings(summary: ExtractionSummary) -> str:
    """Return the status-line warnings of one extraction, ``""`` when it has none."""
    notes = []
    if summary.low_confidence_registration:
        gate = "its" if summary.rms_gate is None else f"the {summary.rms_gate:g} px"
        notes.append(
            f"low confidence: RMS {summary.rms_residual:.3f} px is over {gate} gate, "
            "molecules tagged 'low-confidence-registration'"
        )
    check = summary.donor_side_check
    if check is not None and check.swapped:
        notes.append(
            f"donor and acceptor halves look swapped ({check.n_swapped} of "
            f"{check.n_events} bleach events); check the channel order of the map"
        )
    return "".join(f"; warning: {note}" for note in notes)
