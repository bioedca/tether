# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The extraction page of the Prepare section: movie, options, stage preview, spot overlays (#350).

[`ExtractionSection`][] is the central page of the Prepare section of the shell. The user
chooses one movie and its registration source (a native fit, a saved channel map file or a
Deep-LASI ``.tmap``), sets the extraction options, and then:

* **Preview** runs every stage of the headless stage preview
  ([`iter_extraction_stages`][tether.project.preview.iter_extraction_stages], #348) on a
  background worker. It reads the movie through the extraction reader, so any movie that
  **Extract…** reads previews too, and a movie it refuses gives the same message (#1415);
  the movie on show stays open until the page clears the preview, previews another movie
  or closes. The stage table shows the outcome of each stage, and the reason when a
  stage fails. The movie viewer then shows the detected spots and the control-point
  residuals, each as a [`MovieOverlay`][tether.gui.movie_panel.MovieOverlay] on the
  [`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel]; there is no new overlay type.
* **Extract…** runs the ``extract`` service command into a **new** ``.tether`` project, the
  same command as ``tether extract`` (ADR-0100). The page then shows the summary and the
  result of the advisory donor-side check (#646, ADR-0070): a warning with the evidence and
  the remedy when the halves look swapped, and a note otherwise. The check never changes the
  data and never blocks. Publishing into the open project is #352.
* The **Apertures and background** tab
  ([`ApertureSection`][tether.gui.apertures.ApertureSection], #922) draws the signal aperture
  and the background ring of every spot, marks the spots at an edge and the overlapping ones,
  and shows the preview traces of the molecules the user selects, before the run.

The options form has one row for each field of
[`ExtractionFields`][tether.services.profile.ExtractionFields], the fields of an extraction
profile (#349): every field of [`ExtractOptions`][tether.project.extract.ExtractOptions], with
the channel layout as its four profile fields. A value is checked by the same
[`ExtractionError`][tether.project.extract.ExtractionError] path as the command line, and the
error shows under the form; it is never a raw traceback.

The page calls the core only through [`tether.services`][] (ADR-0100): the ``extract``
command for the run, and the read-only helpers of [`tether.services.preview`][] for the form,
the preview, the stage rows, the spot views and the donor-side note.

The module imports no Qt at import time; the class imports it when it is built.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.services import preview as preview_service

if TYPE_CHECKING:
    from tether.gui.movie_panel import MovieOverlay
    from tether.gui.tasks import TaskOutcome
    from tether.project.extract import ExtractOptions
    from tether.project.preview import ExtractionPreview
    from tether.services import ExtractResult
    from tether.services.preview import PreviewMovie
    from tether.services.profile import ExtractionProfile

__all__ = [
    "REGISTRATION_SOURCES",
    "VIEW_NAMES",
    "ExtractionSection",
    "movie_filter",
    "preview_overlays",
]

#: The registration sources the page offers: a native fit, a map file, a ``.tmap``.
REGISTRATION_SOURCES: tuple[str, ...] = ("native", "map-file", "tmap")
_SOURCE_LABELS = {
    "native": "Native fit from the movie's own spots",
    "map-file": "Channel map file (.npz)",
    "tmap": "Deep-LASI .tmap",
}
_SOURCE_FILTERS = {"map-file": "Channel map (*.npz)", "tmap": "Deep-LASI map (*.tmap)"}
_TETHER_FILTER = "Tether project (*.tether)"
#: The interval of the main-thread poll of a running preview or extraction, in ms.
_POLL_MS = 50
#: The names of the overlay views of the movie viewer, by the key of ``spot_views``.
VIEW_NAMES = {
    preview_service.SPOTS: "Detected spots (green: donor, red: acceptor)",
    preview_service.RESIDUALS: (
        "Control-point residuals (green: donor mapped by the fit, red: acceptor found)"
    ),
}


def movie_filter() -> str:
    """Return the movie file filter of the page: every container extraction reads (#1415).

    The patterns are the file suffixes of the readers in
    [`EXTRACTED_CONTAINERS`][tether.io.movie.EXTRACTED_CONTAINERS]
    ([`movie_file_suffixes`][tether.services.movies.movie_file_suffixes]), so a container
    that joins that list joins the filter; "All files" follows.
    """
    from tether.services.movies import movie_file_suffixes

    patterns = " ".join(f"*{suffix}" for suffix in movie_file_suffixes())
    return f"Movie ({patterns});;All files (*)"


def preview_overlays(
    preview: ExtractionPreview, frame_shape: tuple[int, int], *, aperture_radius: float
) -> dict[str, MovieOverlay]:
    """Return one [`MovieOverlay`][tether.gui.movie_panel.MovieOverlay] per view, by view name.

    The views are those of
    [`spot_views`][tether.services.preview.spot_views]: the detected spots, and the
    control-point residuals of a native fit, in the raw camera frame.
    """
    from tether.gui.movie_panel import MovieOverlay

    return {
        VIEW_NAMES[key]: MovieOverlay(
            donor_xy=green, acceptor_xy=red, aperture_radius=aperture_radius
        )
        for key, (green, red) in preview_service.spot_views(preview, frame_shape).items()
    }


@dataclass
class _Inputs:
    """The inputs of one preview or extraction, kept to read its outcome."""

    movie: str
    source: str
    source_path: str | None
    profile: ExtractionProfile


class ExtractionSection:
    """The page of the Prepare section: one movie, its options, a preview and a run.

    ``status`` shows one line in the window's status bar; ``open_project`` opens a
    ``.tether`` in the shell; ``window`` parents the file dialogs. ``panel_factory`` makes
    the movie viewer, a [`NapariMoviePanel`][tether.gui.movie_panel.NapariMoviePanel] by
    default; it is called on the first preview that has spots to show. Create this on the
    main thread, after the ``QApplication``. The page is [`widget`][].
    """

    def __init__(
        self,
        *,
        status: Callable[[str], None],
        open_project: Callable[[Path], object] | None = None,
        window: Any = None,
        panel_factory: Callable[[], Any] | None = None,
    ) -> None:
        from tether.gui.task_service import TaskService

        self._status = status
        self._open_project = open_project
        self._window = window
        self._panel_factory = panel_factory or _napari_panel
        self._tasks = TaskService(poll_ms=_POLL_MS)
        self._panel: Any = None
        self._reader: PreviewMovie | None = None
        self._preview: ExtractionPreview | None = None
        self._result: ExtractResult | None = None
        self._overlay_views: tuple[str, ...] = ()
        self._inputs: dict[str, Any] = {}
        self._build()

    # --- widgets ------------------------------------------------------------------

    def _build(self) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui import theme

        page = self._widget = QtWidgets.QWidget()
        page.setAccessibleName("Prepare section")
        # The content scrolls inside the page, so the page does not raise the minimum size
        # of the window: a small screen still shows the other sections whole.
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_scroll = QtWidgets.QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        page_layout.addWidget(page_scroll)
        content = QtWidgets.QWidget()
        page_scroll.setWidget(content)
        outer = QtWidgets.QVBoxLayout(content)
        heading = QtWidgets.QLabel("Prepare")
        heading.setFont(theme.heading_font())
        intro = QtWidgets.QLabel(
            "Choose a movie and its options, preview each extraction stage, then extract the "
            "movie into a new project. To make a project from a folder of Deep-LASI files, "
            "choose Import → Deep-LASI bundle…."
        )
        intro.setWordWrap(True)
        outer.addWidget(heading)
        outer.addWidget(intro)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        movie_box = QtWidgets.QGroupBox("Movie")
        movie_form = QtWidgets.QFormLayout(movie_box)
        self._movie_field = QtWidgets.QLineEdit()
        self._movie_field.setAccessibleName("Movie file")
        movie_form.addRow("Movie", _with_button(self._movie_field, "Choose…", self._choose_movie))
        self._source_combo = QtWidgets.QComboBox()
        self._source_combo.setAccessibleName("Registration source")
        for source in REGISTRATION_SOURCES:
            self._source_combo.addItem(_SOURCE_LABELS[source], source)
        movie_form.addRow("Registration", self._source_combo)
        self._source_field = QtWidgets.QLineEdit()
        self._source_field.setAccessibleName("Registration file")
        movie_form.addRow("Map file", _with_button(self._source_field, "Choose…", self._choose_map))
        left_layout.addWidget(movie_box)

        options_box = QtWidgets.QGroupBox("Options")
        options_form = QtWidgets.QFormLayout(options_box)
        self._fields: dict[str, Any] = {}
        texts = preview_service.default_texts()
        for spec in preview_service.FORM_FIELDS:
            if spec.kind == "choice":
                widget = QtWidgets.QComboBox()
                if spec.optional:
                    widget.addItem("(none)", "")
                for choice in spec.choices:
                    widget.addItem(choice, choice)
                widget.setCurrentIndex(max(widget.findData(texts[spec.name]), 0))
            else:
                widget = QtWidgets.QLineEdit(texts[spec.name])
                if spec.optional:
                    widget.setPlaceholderText("(none)")
            widget.setAccessibleName(spec.name)
            options_form.addRow(spec.name, widget)
            self._fields[spec.name] = widget
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(options_box)
        left_layout.addWidget(scroll, 1)
        self._error_label = QtWidgets.QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setAccessibleName("Options error")
        left_layout.addWidget(self._error_label)
        buttons = QtWidgets.QHBoxLayout()
        self._preview_button = QtWidgets.QPushButton("&Preview")
        self._preview_button.clicked.connect(self.preview)
        self._extract_button = QtWidgets.QPushButton("&Extract…")
        self._extract_button.clicked.connect(self._extract_dialog)
        buttons.addWidget(self._preview_button)
        buttons.addWidget(self._extract_button)
        left_layout.addLayout(buttons)
        splitter.addWidget(left)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        self._stage_table = QtWidgets.QTableWidget(0, 3)
        self._stage_table.setAccessibleName("Extraction stages")
        self._stage_table.setHorizontalHeaderLabels(["Stage", "Outcome", "Detail"])
        self._stage_table.horizontalHeader().setStretchLastSection(True)
        self._stage_table.verticalHeader().setVisible(False)
        self._stage_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        right_layout.addWidget(self._stage_table)
        self._residual_label = QtWidgets.QLabel()
        self._residual_label.setWordWrap(True)
        right_layout.addWidget(self._residual_label)
        self._result_label = QtWidgets.QLabel()
        self._result_label.setWordWrap(True)
        self._result_label.setAccessibleName("Extraction result")
        right_layout.addWidget(self._result_label)
        self._donor_side_label = QtWidgets.QLabel()
        self._donor_side_label.setWordWrap(True)
        self._donor_side_label.setAccessibleName("Donor-side check")
        right_layout.addWidget(self._donor_side_label)
        self._open_button = QtWidgets.QPushButton("&Open the new project")
        # Shown only once an extraction has made a project: hidden, not disabled, since no
        # readiness stage is missing for it to name (#898).
        self._open_button.setHidden(True)
        self._open_button.clicked.connect(self.open_result)
        right_layout.addWidget(self._open_button)
        self._view_combo = QtWidgets.QComboBox()
        self._view_combo.setAccessibleName("Overlay")
        self._view_combo.currentIndexChanged.connect(self._show_view)
        right_layout.addWidget(self._view_combo)
        self._viewer_host = QtWidgets.QVBoxLayout()
        self._viewer_note = QtWidgets.QLabel("Run a preview to see the spots on the movie.")
        self._viewer_note.setWordWrap(True)
        self._viewer_host.addWidget(self._viewer_note)
        right_layout.addLayout(self._viewer_host, 1)
        # The Apertures and background tab (#922) reviews the inputs of this page.
        from tether.gui.apertures import ApertureSection

        self._apertures = ApertureSection(
            status=self._status, inputs=self.review_inputs, window=self._window
        )
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setAccessibleName("Prepare tabs")
        self._tabs.addTab(right, "&Stages")
        self._tabs.addTab(self._apertures.widget, "&Apertures and background")
        splitter.addWidget(self._tabs)
        for widget in (self._movie_field, self._source_field, *self._fields.values()):
            if hasattr(widget, "findData"):
                widget.currentIndexChanged.connect(self._apertures.inputs_changed)
            else:
                widget.editingFinished.connect(self._apertures.inputs_changed)
        self._source_combo.currentIndexChanged.connect(self._apertures.inputs_changed)
        self._show_stages(None)

    # --- the state a test reads ---------------------------------------------------

    @property
    def widget(self) -> Any:
        """The page: the ``QWidget`` the shell places in the Prepare section."""
        return self._widget

    @property
    def fields(self) -> Mapping[str, Any]:
        """The input widget of each form row, by field name."""
        return dict(self._fields)

    @property
    def apertures(self) -> Any:
        """The Apertures and background tab (#922).

        An [`ApertureSection`][tether.gui.apertures.ApertureSection].
        """
        return self._apertures

    @property
    def tabs(self) -> Any:
        """The ``QTabWidget`` of the page: Stages, and Apertures and background (#922)."""
        return self._tabs

    @property
    def stage_table(self) -> Any:
        """The ``QTableWidget`` of the stages: stage, outcome, detail."""
        return self._stage_table

    @property
    def error_label(self) -> Any:
        """The label under the form that says why the options or the inputs are refused."""
        return self._error_label

    @property
    def result_label(self) -> Any:
        """The label with the summary of the last extraction."""
        return self._result_label

    @property
    def donor_side_label(self) -> Any:
        """The label with the donor-side check of the last extraction."""
        return self._donor_side_label

    @property
    def overlay_views(self) -> tuple[str, ...]:
        """The names of the overlay views the movie viewer holds, in switcher order."""
        return self._overlay_views

    @property
    def panel(self) -> Any:
        """The movie viewer, or ``None`` before a preview had spots to show."""
        return self._panel

    @property
    def last_preview(self) -> ExtractionPreview | None:
        """The last preview, or ``None``."""
        return self._preview

    @property
    def last_result(self) -> ExtractResult | None:
        """The result of the last extraction that finished, ``None`` when it failed."""
        return self._result

    @property
    def busy(self) -> bool:
        """Whether a preview or an extraction runs, its outcome not yet applied."""
        return self._tasks.busy

    # --- inputs -------------------------------------------------------------------

    def set_movie(self, path: str | PathLike[str]) -> None:
        """Set the movie file."""
        self._movie_field.setText(str(path))

    def set_registration(self, source: str, path: str | PathLike[str] | None = None) -> None:
        """Set the registration source (one of [`REGISTRATION_SOURCES`][]) and its file."""
        if source not in REGISTRATION_SOURCES:
            raise ValueError(f"source must be one of {REGISTRATION_SOURCES}, got {source!r}")
        self._source_combo.setCurrentIndex(REGISTRATION_SOURCES.index(source))
        self._source_field.setText("" if path is None else str(path))

    def set_field(self, name: str, text: str) -> None:
        """Set the text of one form row (for a choice, the choice; ``""`` for none)."""
        widget = self._fields[name]
        if hasattr(widget, "findData"):
            index = widget.findData(text)
            if index < 0:  # a value the list does not offer; ExtractOptions refuses it
                widget.addItem(text, text)
                index = widget.count() - 1
            widget.setCurrentIndex(index)
        else:
            widget.setText(text)

    def form_texts(self) -> dict[str, str]:
        """Return the text of each form row."""
        return {
            name: widget.currentData() if hasattr(widget, "findData") else widget.text()
            for name, widget in self._fields.items()
        }

    def _read_inputs(self) -> _Inputs | None:
        """Return the checked inputs, or ``None`` after showing why they are refused."""
        from tether.services import RequestError

        movie = self._movie_field.text().strip()
        source = self._source_combo.currentData()
        source_path = self._source_field.text().strip() or None
        try:
            if not movie:
                raise RequestError("choose a movie first")
            if source != "native" and source_path is None:
                raise RequestError(f"choose the {_SOURCE_LABELS[source]} first")
            profile = preview_service.profile_from_texts(self.form_texts())
        except RequestError as exc:
            self._error_label.setText(f"Not started: {exc}")
            return None
        self._error_label.setText("")
        return _Inputs(movie, source, source_path if source != "native" else None, profile)

    def review_inputs(self) -> tuple[str, ExtractOptions, dict[str, str | None]] | None:
        """Return the movie, the options and the registration source of the form, or ``None``.

        The inputs of the Apertures and background tab (#922), checked as a preview checks
        them: ``None`` after the label under the form says why they are refused.
        """
        inputs = self._read_inputs()
        if inputs is None:
            return None
        return inputs.movie, inputs.profile.extract_options(), _source_kwargs(inputs)

    # --- preview ------------------------------------------------------------------

    def preview(self) -> bool:
        """Start the stage preview of the movie; return whether it started."""
        if self._refuse_while_busy("Preview"):
            return False
        inputs = self._read_inputs()
        if inputs is None:
            # Refused before it started: the last preview describes other inputs.
            self._clear_preview()
            return False
        options = inputs.profile.extract_options()
        kwargs = _source_kwargs(inputs)
        self._status(f"Previewing the extraction of {Path(inputs.movie).name}…")
        task = self._tasks.start(
            _preview_task(preview_service.run_preview, inputs.movie, options, kwargs),
            context=inputs,
            on_finished=self._finish_preview,
        )
        return task is not None

    def _finish_preview(self, outcome: TaskOutcome) -> None:
        from tether.gui.tasks import TaskState

        if outcome.state is not TaskState.DONE:
            # The last preview's movie and residuals belong to other inputs: clear them.
            self._clear_preview()
            self._error_label.setText(f"Preview failed: {outcome.error}")
            self._status(f"Preview failed: {outcome.error}")
            return
        reader, preview = outcome.value
        self._preview = preview
        self._show_stages(preview)
        self._residual_label.setText(preview_service.residual_summary(preview))
        inputs: _Inputs = outcome.context
        self._show_overlays(reader, preview, inputs.profile.disk_radius)
        verdict = "completed" if preview.completed else f"stopped at {preview.stages[-1].name}"
        self._status(f"Preview of {Path(inputs.movie).name} {verdict}")

    def _clear_preview(self) -> None:
        """Clear the last preview's stages, residual line, movie and overlays."""
        self._preview = None
        self._show_stages(None)
        self._residual_label.setText("")
        self._clear_movie()

    def _show_stages(self, preview: ExtractionPreview | None) -> None:
        from pyqtgraph.Qt import QtWidgets

        rows = preview_service.stage_rows(preview)
        self._stage_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                item = QtWidgets.QTableWidgetItem(text)
                item.setToolTip(text)
                self._stage_table.setItem(row, column, item)

    def _show_overlays(
        self, reader: PreviewMovie, preview: ExtractionPreview, radius: float
    ) -> None:
        """Show the preview's overlays on the movie, replacing the last preview's."""
        frame_shape = tuple(int(n) for n in reader.shape[-2:])
        views = preview_overlays(preview, frame_shape, aperture_radius=radius)  # type: ignore[arg-type]
        self._clear_movie()
        if not views:
            reader.close()
            self._viewer_note.setText("No stage found spots to show.")
            return
        try:
            if self._panel is None:
                self._panel = self._panel_factory()
                self._viewer_host.addWidget(self._panel.qt_window)
            for name, overlay in views.items():
                self._panel.add_movie(reader, overlay=overlay, name=name)
        except Exception as exc:  # noqa: BLE001 - a viewer that cannot show must not fail the page
            reader.close()
            self._viewer_note.setText(f"Movie viewer unavailable: {type(exc).__name__}: {exc}")
            return
        self._reader = reader
        self._overlay_views = tuple(views)
        self._viewer_note.setText("")
        self._view_combo.blockSignals(True)
        self._view_combo.addItems(list(views))
        self._view_combo.blockSignals(False)

    def _show_view(self, index: int) -> None:
        if self._panel is not None and 0 <= index < len(self._overlay_views):
            self._panel.set_active_movie(index)

    def _clear_movie(self) -> None:
        self._view_combo.blockSignals(True)
        self._view_combo.clear()
        self._view_combo.blockSignals(False)
        self._overlay_views = ()
        if self._panel is not None:
            self._panel.clear_movies()
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    # --- extraction ---------------------------------------------------------------

    def extract(self, output: str | PathLike[str]) -> bool:
        """Start extracting the movie into a new ``output`` project; return whether it started.

        The ``extract`` service command runs on a background worker; an existing ``output``
        is refused, never replaced. When it finishes, the page shows the summary and the
        donor-side check, and **Open the new project** opens it.
        """
        from tether.services import ExtractRequest

        if self._refuse_while_busy("Extract"):
            return False
        inputs = self._read_inputs()
        if inputs is None:
            return False
        request = ExtractRequest(
            movie=inputs.movie,
            output=str(output),
            **_source_kwargs(inputs),
            **inputs.profile.extraction_values(),
        )
        self._result = None
        self._open_button.setHidden(True)
        self._result_label.setText("")
        self._donor_side_label.setText("")
        self._status(f"Extracting {Path(inputs.movie).name}…")
        from tether.services import run as run_service

        task = self._tasks.start(
            run_service, request, context=inputs, on_finished=self._finish_extract
        )
        return task is not None

    def _finish_extract(self, outcome: TaskOutcome) -> None:
        from tether.gui.tasks import TaskState

        if outcome.state is not TaskState.DONE:
            self._result = None
            self._result_label.setText(f"Extraction failed: {outcome.error}")
            self._status(f"Extraction failed: {outcome.error}")
            return
        result: ExtractResult = outcome.value
        inputs: _Inputs = outcome.context
        self._result = result
        summary = result.summary
        assert summary is not None  # noqa: S101 - the page never asks for count_only
        text = (
            f"Extracted {summary.n_molecules} molecule(s) into {summary.output_path.name}; "
            f"registration {summary.registration_source}, RMS {summary.rms_residual:.3f} px"
        )
        if summary.low_confidence_registration:
            text += " (over the gate: molecules tagged 'low-confidence-registration')"
        self._result_label.setText(text)
        map_file = Path(inputs.source_path).name if inputs.source == "map-file" else None
        note = preview_service.donor_side_note(
            summary, donor_side=inputs.profile.donor_side, map_file=map_file
        )
        if note is None:
            self._donor_side_label.setText("")
        else:
            level, sentence = note
            prefix = "Warning: " if level == "warning" else "Donor side: "
            self._donor_side_label.setText(f"{prefix}{sentence}.")
        self._open_button.setHidden(self._open_project is None)
        self._status(text)

    def open_result(self) -> object:
        """Open the project of the last extraction in the shell; return what the shell returns."""
        if self._result is None or self._result.summary is None or self._open_project is None:
            return None
        return self._open_project(self._result.summary.output_path)

    # --- lifecycle ----------------------------------------------------------------

    def _refuse_while_busy(self, feature: str) -> bool:
        if not self._tasks.busy:
            return False
        self._status(f"{feature} unavailable: wait for the running preview or extraction")
        return True

    def close(self) -> None:
        """Forget a running task, stop the worker, and close the movie and the viewer.

        The last preview is cleared too: its stages hold views of the movie's frames, so
        keeping it would keep the movie's memory map or temporary copy (#1415).
        """
        self._tasks.abandon()
        self._tasks.shutdown()
        self._apertures.close()
        self._clear_preview()
        if self._panel is not None:
            self._panel.close()
            self._panel = None

    # --- dialogs ------------------------------------------------------------------

    def _choose_movie(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, "Movie to extract", "", movie_filter()
        )
        if path:
            self.set_movie(path)

    def _choose_map(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        source = self._source_combo.currentData()
        if source == "native":
            self._status("A native fit needs no map file; choose a map file or .tmap first")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._window, _SOURCE_LABELS[source], "", _SOURCE_FILTERS[source]
        )
        if path:
            self._source_field.setText(path)

    def _extract_dialog(self) -> None:  # pragma: no cover - interactive file dialog
        from pyqtgraph.Qt import QtWidgets

        movie = self._movie_field.text().strip()
        start = str(Path(movie).with_suffix(".tether")) if movie else ""
        output, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._window, "New project", start, _TETHER_FILTER
        )
        if not output:
            self._status("Extract cancelled")
            return
        if not output.lower().endswith(".tether"):
            output += ".tether"
        self.extract(output)


def _source_kwargs(inputs: _Inputs) -> dict[str, str | None]:
    """Return the ``tmap`` and ``map_path`` arguments of the source of ``inputs``."""
    return {
        "tmap": inputs.source_path if inputs.source == "tmap" else None,
        "map_path": inputs.source_path if inputs.source == "map-file" else None,
    }


def _preview_task(
    fn: Callable[..., Any], movie: str, options: ExtractOptions, kwargs: dict[str, Any]
) -> Callable[[], Any]:
    """Return the preview work as one call, for the background worker."""
    return lambda: fn(movie, options, **kwargs)


def _napari_panel() -> Any:
    from tether.gui.movie_panel import NapariMoviePanel

    return NapariMoviePanel(title="Tether — extraction preview", show=False)


def _with_button(field: Any, text: str, slot: Callable[[], None]) -> Any:
    """``field`` and a push button beside it, in one row widget."""
    from pyqtgraph.Qt import QtWidgets

    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(field, 1)
    button = QtWidgets.QPushButton(text)
    button.clicked.connect(slot)
    layout.addWidget(button)
    return row
