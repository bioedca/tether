# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Apertures and background page of the Prepare section (#922; PRD §7.1 FR-EXTRACT).

[`ApertureSection`][] is a view of the extraction page
([`ExtractionSection`][tether.gui.extraction.ExtractionSection]). It reads the movie, the
registration source and the options of that page, and **Review apertures** runs
[`review_movie`][tether.services.apertures.review_movie] on a background worker, so the window
stays usable. When it finishes the page shows:

* the mean frame of the movie with the signal aperture (the PSF disk, solid) and the background
  ring (its inner and outer circle, dotted) of every detected donor spot, at the donor position
  and at the acceptor position the registration maps it to;
* a mark on every spot at an edge (orange: the run skips it) and on every molecule whose
  aperture overlaps a neighbour's (magenta), with the count of each. The rules are the present
  guard rules, see [`tether.services.apertures`][];
* the list of the spots. Selecting molecules shows their preview traces, the traces the full run
  gives them; the preview writes nothing to a project.

A change of an option, the movie or the registration on the extraction page runs the review
again, once a review has been shown. **Save report…** writes the JSON and the PNG report with
[`write_report`][tether.services.apertures.write_report], the function ``tether apertures``
calls, so the two write the same files.

The module imports no Qt at import time; the class imports it when it is built.
"""

from __future__ import annotations

from collections.abc import Callable
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.services import apertures as aperture_service

if TYPE_CHECKING:
    import numpy as np

    from tether.gui.tasks import TaskOutcome
    from tether.project.extract import ExtractOptions
    from tether.services.apertures import ApertureReview

__all__ = ["ApertureSection", "ReviewInputs"]

#: ``(movie, options, registration source arguments)``: what a review runs on.
ReviewInputs = tuple[str, "ExtractOptions", dict[str, Any]]

#: The interval of the main-thread poll of a running review, in ms.
_POLL_MS = 50
#: How long the page waits after a change before it reviews again, in ms.
_DEBOUNCE_MS = 300
_STATUS_WORDS = {
    "ok": "",
    "overlap": "overlaps a neighbour",
    "at-edge": "at an edge: skipped by the run",
    "no-partner": "no acceptor partner: skipped by the run",
}


class ApertureSection:
    """The Apertures and background page: the apertures, the marks and the preview traces.

    ``status`` shows one line in the window's status bar. ``inputs`` returns the movie, the
    options and the registration source arguments to review, or ``None`` after the caller has
    shown why they are refused. ``window`` parents the file dialog. Create this on the main
    thread, after the ``QApplication``. The page is [`widget`][].
    """

    def __init__(
        self,
        *,
        status: Callable[[str], None],
        inputs: Callable[[], ReviewInputs | None],
        window: Any = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore

        from tether.gui.task_service import TaskService

        self._status = status
        self._inputs = inputs
        self._window = window
        self._tasks = TaskService(poll_ms=_POLL_MS)
        self._review: ApertureReview | None = None
        self._frame: np.ndarray | None = None
        self._again = False
        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._review_again)
        self._build()

    # --- widgets ------------------------------------------------------------------

    def _build(self) -> None:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets

        page = self._widget = QtWidgets.QWidget()
        page.setAccessibleName("Apertures and background")
        layout = QtWidgets.QVBoxLayout(page)
        intro = QtWidgets.QLabel(
            "Review the signal aperture and the background ring of every spot before the run, "
            "with the movie and the options of this page. Orange: at an edge, the run skips it. "
            "Magenta: the aperture overlaps a neighbour. Select molecules to see their traces."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        buttons = QtWidgets.QHBoxLayout()
        self._review_button = QtWidgets.QPushButton("&Review apertures")
        self._review_button.clicked.connect(self.review)
        self._save_button = QtWidgets.QPushButton("&Save report…")
        self._save_button.clicked.connect(self._save_dialog)
        buttons.addWidget(self._review_button)
        buttons.addWidget(self._save_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._counts_label = QtWidgets.QLabel("Review the apertures to see the spots.")
        self._counts_label.setWordWrap(True)
        self._counts_label.setAccessibleName("Aperture counts")
        layout.addWidget(self._counts_label)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)
        self._image_plot = pg.PlotWidget()
        self._image_plot.setAccessibleName("Apertures on the frame")
        self._image_plot.invertY(True)
        self._image_plot.setAspectLocked(True)
        self._image_item = pg.ImageItem(axisOrder="row-major")
        self._image_plot.addItem(self._image_item)
        self._rings: list[Any] = []
        for _ in ("disk", "ring inner", "ring outer"):
            item = pg.ScatterPlotItem(pxMode=False, symbol="o", brush=None)
            self._image_plot.addItem(item)
            self._rings.append(item)
        splitter.addWidget(self._image_plot)

        lower = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._spot_list = QtWidgets.QListWidget()
        self._spot_list.setAccessibleName("Spots")
        self._spot_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._spot_list.itemSelectionChanged.connect(self._show_traces)
        lower.addWidget(self._spot_list)
        self._trace_plot = pg.PlotWidget()
        self._trace_plot.setAccessibleName("Preview traces")
        self._trace_plot.setLabel("bottom", "frame")
        self._trace_plot.setLabel("left", "intensity")
        lower.addWidget(self._trace_plot)
        splitter.addWidget(lower)

    # --- the state a test reads ---------------------------------------------------

    @property
    def widget(self) -> Any:
        """The page: the ``QWidget`` the extraction page shows as its second view."""
        return self._widget

    @property
    def counts_label(self) -> Any:
        """The label with the counts, or why the last review did not finish."""
        return self._counts_label

    @property
    def spot_list(self) -> Any:
        """The ``QListWidget`` of the spots; a molecule's item holds its row as user data."""
        return self._spot_list

    @property
    def last_review(self) -> ApertureReview | None:
        """The review on show, or ``None``."""
        return self._review

    @property
    def busy(self) -> bool:
        """Whether a review runs, its outcome not yet applied, or one is due."""
        return self._tasks.busy or self._timer.isActive() or self._again

    @property
    def n_drawn(self) -> int:
        """How many apertures the frame shows: two per spot, donor and acceptor."""
        return len(self._rings[0].data)

    @property
    def trace_names(self) -> list[str]:
        """The names of the trace curves on show, one per channel of each selected molecule."""
        return [item.name() for item in self._trace_plot.getPlotItem().listDataItems()]

    def selected_molecules(self) -> list[int]:
        """Return the rows of the selected molecules, in ascending order."""
        rows = {item.data(_ROLE) for item in self._spot_list.selectedItems()}
        return sorted(row for row in rows if row is not None)

    def select_molecules(self, molecules: list[int]) -> None:
        """Select the items of ``molecules`` (and no other)."""
        wanted = set(molecules)
        self._spot_list.blockSignals(True)
        for index in range(self._spot_list.count()):
            item = self._spot_list.item(index)
            item.setSelected(item.data(_ROLE) in wanted)
        self._spot_list.blockSignals(False)
        self._show_traces()

    # --- the review ---------------------------------------------------------------

    def review(self) -> bool:
        """Start the review of the extraction page's movie; return whether it started."""
        if self._tasks.busy:
            self._again = True
            return False
        inputs = self._inputs()
        if inputs is None:
            self._clear(
                "Not reviewed: the message under the options says why the inputs are refused."
            )
            return False
        movie, options, kwargs = inputs
        self._status(f"Reviewing the apertures of {Path(movie).name}…")
        task = self._tasks.start(
            _review_task(movie, options, kwargs), context=movie, on_finished=self._finish
        )
        return task is not None

    def inputs_changed(self) -> None:
        """Review again shortly, if a review is on show or runs: an input changed."""
        if self._review is not None or self._tasks.busy:
            self._timer.start()

    def _review_again(self) -> None:
        self.review()

    def _finish(self, outcome: TaskOutcome) -> None:
        from tether.gui.tasks import TaskState

        if outcome.state is not TaskState.DONE:
            self._clear(f"Review failed: {outcome.error}")
            self._status(f"Aperture review failed: {outcome.error}")
        else:
            self._review, self._frame = outcome.value
            self._show(self._review, self._frame)
            self._status(f"Apertures of {Path(outcome.context).name}: {self._review.summary()}")
        if self._again:  # an input changed while it ran: what is on show is stale
            self._again = False
            self.review()

    def _clear(self, text: str) -> None:
        self._review = self._frame = None
        self._counts_label.setText(text)
        self._image_item.clear()
        for item in self._rings:
            item.clear()
        self._spot_list.clear()
        self._trace_plot.clear()

    def _show(self, review: ApertureReview, frame: np.ndarray) -> None:
        import numpy as np
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets

        selected = set(self.selected_molecules())
        self._counts_label.setText(review.summary())
        low, high = np.percentile(frame, [1, 99.5]) if frame.size else (0.0, 1.0)
        self._image_item.setImage(frame, levels=(float(low), float(max(high, low + 1e-9))))
        points = [xy for spot in review.spots for xy in (spot.donor_xy, spot.acceptor_xy)]
        pens = [
            pg.mkPen(aperture_service.STATUS_COLORS[spot.status])
            for spot in review.spots
            for _ in range(2)
        ]
        dotted = [pg.mkPen(pen.color(), style=QtCore.Qt.PenStyle.DotLine) for pen in pens]
        x = [p[0] for p in points]
        y = [p[1] for p in points]
        radii = (review.disk_radius, review.ring_inner, review.ring_outer)
        for item, radius, pen in zip(self._rings, radii, (pens, dotted, dotted), strict=True):
            item.setData(x=x, y=y, size=2 * radius, pen=pen, brush=None)
        self._spot_list.blockSignals(True)
        self._spot_list.clear()
        for spot in review.spots:
            name = f"spot {spot.spot}"
            if spot.molecule is not None:
                name = f"molecule {spot.molecule} (spot {spot.spot})"
            words = _STATUS_WORDS[spot.status]
            item = QtWidgets.QListWidgetItem(f"{name}: {words}" if words else name)
            item.setData(_ROLE, spot.molecule)
            if spot.molecule is None:  # the run gives it no trace
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsSelectable)
            self._spot_list.addItem(item)
            item.setSelected(spot.molecule in selected)
        self._spot_list.blockSignals(False)
        self._show_traces()

    def _show_traces(self) -> None:
        self._trace_plot.clear()
        if self._review is None:
            return
        from tether.gui import theme

        active = theme.current()
        self._trace_plot.addLegend()
        for molecule in self.selected_molecules():
            trace = aperture_service.preview_trace(self._review, molecule)
            for channel in ("donor", "acceptor"):
                pen = active.trace(channel)
                self._trace_plot.plot(trace[channel], pen=pen, name=f"{molecule} {channel}")

    # --- the report ---------------------------------------------------------------

    def save_report(
        self, json_path: str | PathLike[str], png_path: str | PathLike[str] | None = None
    ) -> list[Path]:
        """Write the report of the review on show, with the selected molecules' traces.

        Raises ``ValueError`` when no review is on show.
        """
        if self._review is None or self._frame is None:
            raise ValueError("review the apertures first")
        written = aperture_service.write_report(
            self._review, self._frame, json_path, png_path, molecules=self.selected_molecules()
        )
        self._status("Aperture report written: " + ", ".join(p.name for p in written))
        return written

    def _save_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        if self._review is None:
            self._status("Save report unavailable: review the apertures first")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "Aperture report", "apertures.json", "Aperture report (*.json)"
        )
        if not path:
            return
        json_path = Path(path).with_suffix(".json")
        self.save_report(json_path, json_path.with_suffix(".png"))

    # --- lifecycle ----------------------------------------------------------------

    def close(self) -> None:
        """Forget a running review and stop the worker."""
        self._timer.stop()
        self._again = False
        self._tasks.abandon()
        self._tasks.shutdown()


#: The item data role that holds a molecule's row (``Qt.ItemDataRole.UserRole``).
_ROLE = 0x0100


def _review_task(movie: str, options: ExtractOptions, kwargs: dict[str, Any]) -> Callable[[], Any]:
    """Return the review as one call, for the background worker."""
    return lambda: aperture_service.review_movie(movie, options, **kwargs)
