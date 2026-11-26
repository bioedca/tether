# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The page of the Analyze section: a plot, its layer list and its preview (#374, PRD §7.7).

[`AnalysisSection`][] is the central page of the shell's Analyze section. It holds three
things:

* the **plot** — one entry of the plot registry ([`tether.services.plots.PLOTS`][]), one for
  each of the sixteen population plot functions, with the idealization model and the states a
  model-derived plot reads;
* the **layer list** — an ordered list of named series, each a selection of molecules
  ([ADR-0124](../adr/0124-the-plot-builder-s-composition-unit-renderer-and-staleness-and-frame-time-policies.md)).
  *Add selection* offers every molecule of the project and the one-click presets (one
  selection per condition, movie or source file); *Remove* takes the selected series out;
* the **preview** — a Matplotlib `FigureCanvasQTAgg` holding the `Figure` that
  [`compose_plot`][tether.services.plots.compose_plot] drew with the
  [`tether.analysis.plot_export`][] renderer. The preview and an export are therefore the
  same figure (ADR-0124); there is no second, pyqtgraph drawing of it. The figure keeps the
  export's white background in a dark theme for the same reason.

Each change of the plot, the model, a state or the layer list draws the plot again, once.
*Save composition…* writes the plot, its project, the layer list and the plot parameters to
a file through the ``save-composition`` service command, and *Open composition…* reads one
back through ``open-composition`` to the same layer list (#1438,
[`tether.services.compositions`][]). A series whose project or molecules cannot be found is
named under the buttons and left out of the layer list; nothing is drawn from a guess.

When the plot cannot be drawn the preview shows why — no project, no series, no
idealization model, a state the model does not have, or the refusal of the population
function or the renderer — and never a placeholder result.

**Lazy imports.** Qt is imported in [`AnalysisSection.__init__`][] and Matplotlib's Qt
canvas on the first draw, so importing this module costs neither. Constructing an
[`AnalysisSection`][] needs a live ``QApplication``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from tether.services.base import ServiceError
from tether.services.compositions import (
    OpenCompositionRequest,
    OpenCompositionResult,
    SaveCompositionRequest,
    SaveCompositionResult,
    series_text,
)
from tether.services.plots import (
    ALL_MOLECULES,
    PLOTS,
    PRESET_TITLES,
    PlotOutcome,
    PlotSeries,
    compose_plot,
    idealization_models,
    plot_spec,
    selection_presets,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import PathLike

    from matplotlib.figure import Figure
    from pyqtgraph.Qt import QtWidgets

__all__ = ["AnalysisSection"]

#: The file filter of the composition dialogs.
_COMPOSITION_FILTER = "Plot composition (*.json);;All files (*)"

#: The words of each state option's spin box.
_STATE_LABELS = {"state": "State", "init_state": "From state", "final_state": "To state"}


def _unique(label: str, taken: set[str]) -> str:
    """``label``, or ``label (2)``, ``label (3)``… when a series already has that name."""
    if label not in taken:
        return label
    k = 2
    while f"{label} ({k})" in taken:
        k += 1
    return f"{label} ({k})"


def _same_file(a: str | PathLike[str], b: str | PathLike[str]) -> bool:
    """Say whether ``a`` and ``b`` name one file (by path when either cannot be read)."""
    import os  # noqa: PLC0415

    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(Path(a).resolve()) == os.path.normcase(Path(b).resolve())


def _drawn_message(outcome: PlotOutcome) -> str:
    """Return the status line after a draw: the plot, then each series with its N if known."""
    parts = []
    for label, result in outcome.results:
        n = getattr(result, "n_molecules", None)
        parts.append(label if n is None else f"{label}: {n} molecule(s)")
    return f"{plot_spec(outcome.plot_id).title} — {'; '.join(parts)}"


class AnalysisSection:
    """The Analyze section's page: plot choice, layer list and one Matplotlib preview.

    ``status`` receives a one-line message after each draw (the shell's status bar).
    Use as a context manager or call [`close`][].
    """

    def __init__(self, *, status: Callable[[str], None] | None = None) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        self._status = status
        self._project: Path | None = None
        self._series: list[PlotSeries] = []
        self._outcome: PlotOutcome = PlotOutcome(PLOTS[0].id, missing="")
        self._canvas: Any | None = None
        self._closed = False

        self._widget = QtWidgets.QWidget()
        self._widget.setObjectName("analysis-section")
        row = QtWidgets.QHBoxLayout(self._widget)

        controls = QtWidgets.QWidget()
        controls.setMaximumWidth(340)
        form = QtWidgets.QVBoxLayout(controls)
        form.setContentsMargins(0, 0, 0, 0)

        form.addWidget(QtWidgets.QLabel("Plot"))
        self._plot_combo = QtWidgets.QComboBox()
        for spec in PLOTS:
            self._plot_combo.addItem(spec.title, spec.id)
        form.addWidget(self._plot_combo)

        self._model_label = QtWidgets.QLabel("Idealization model")
        form.addWidget(self._model_label)
        self._model_combo = QtWidgets.QComboBox()
        form.addWidget(self._model_combo)

        states = QtWidgets.QFormLayout()
        self._state_boxes: dict[str, Any] = {}
        self._state_labels: dict[str, Any] = {}
        for name, words in _STATE_LABELS.items():
            box = QtWidgets.QSpinBox()
            box.setRange(0, 99)
            label = QtWidgets.QLabel(words)
            states.addRow(label, box)
            self._state_boxes[name] = box
            self._state_labels[name] = label
        form.addLayout(states)

        form.addWidget(QtWidgets.QLabel("Layers"))
        self._layers = QtWidgets.QListWidget()
        self._layers.setObjectName("analysis-layers")
        form.addWidget(self._layers, 1)

        buttons = QtWidgets.QHBoxLayout()
        self._add_button = QtWidgets.QToolButton()
        self._add_button.setText("Add selection")
        self._add_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._add_menu = QtWidgets.QMenu(self._add_button)
        self._add_button.setMenu(self._add_menu)
        buttons.addWidget(self._add_button)
        self._remove_button = QtWidgets.QPushButton("Remove")
        buttons.addWidget(self._remove_button)
        form.addLayout(buttons)

        files = QtWidgets.QHBoxLayout()
        self._save_button = QtWidgets.QPushButton("Save composition…")
        files.addWidget(self._save_button)
        self._open_button = QtWidgets.QPushButton("Open composition…")
        files.addWidget(self._open_button)
        form.addLayout(files)
        self._notice = QtWidgets.QLabel()
        self._notice.setObjectName("analysis-composition-notice")
        self._notice.setWordWrap(True)
        form.addWidget(self._notice)
        row.addWidget(controls)

        self._preview = QtWidgets.QStackedWidget()
        self._empty = QtWidgets.QLabel()
        self._empty.setObjectName("analysis-empty")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preview.addWidget(self._empty)
        self._canvas_host = QtWidgets.QWidget()
        self._canvas_layout = QtWidgets.QVBoxLayout(self._canvas_host)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._preview.addWidget(self._canvas_host)
        row.addWidget(self._preview, 1)

        self._plot_combo.currentIndexChanged.connect(self._on_plot_changed)
        self._model_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        for box in self._state_boxes.values():
            box.valueChanged.connect(lambda _v: self.refresh())
        self._remove_button.clicked.connect(self.remove_selected)
        self._save_button.clicked.connect(lambda _c=False: self._ask_save())
        self._open_button.clicked.connect(lambda _c=False: self._ask_open())
        self._layers.currentRowChanged.connect(lambda _r: self._sync_buttons())
        self._add_menu.aboutToShow.connect(self._rebuild_add_menu)

        self._sync_plot_controls()
        self._rebuild_add_menu()
        self.refresh()

    # --- accessors -----------------------------------------------------------

    @property
    def widget(self) -> QtWidgets.QWidget:
        """The page widget the shell puts in the Analyze section."""
        return self._widget

    @property
    def plot_combo(self) -> QtWidgets.QComboBox:
        """The plot choice: one item per registry entry, its data the entry's ``id``."""
        return self._plot_combo

    @property
    def model_combo(self) -> QtWidgets.QComboBox:
        """The idealization model a model-derived plot reads."""
        return self._model_combo

    @property
    def state_boxes(self) -> dict[str, Any]:
        """The state spin boxes, by option name (``state``, ``init_state``, ``final_state``)."""
        return dict(self._state_boxes)

    @property
    def layers(self) -> QtWidgets.QListWidget:
        """The layer list: one row per series, in drawing order."""
        return self._layers

    @property
    def add_button(self) -> QtWidgets.QToolButton:
        """*Add selection*: opens [`add_menu`][]; disabled while no project is open."""
        return self._add_button

    @property
    def add_menu(self) -> QtWidgets.QMenu:
        """The *Add selection* menu: every molecule, then the presets by grouping."""
        return self._add_menu

    @property
    def remove_button(self) -> QtWidgets.QPushButton:
        """Removes the selected series from the layer list."""
        return self._remove_button

    @property
    def save_button(self) -> QtWidgets.QPushButton:
        """*Save composition…*: without a project or a series it says which is missing."""
        return self._save_button

    @property
    def open_button(self) -> QtWidgets.QPushButton:
        """*Open composition…*: reads a composition file back to the layer list."""
        return self._open_button

    @property
    def notice_text(self) -> str:
        """What the last save or open said: the file, or each series that was left out."""
        return self._notice.text()

    @property
    def project(self) -> Path | None:
        """The project the plots read, or ``None``."""
        return self._project

    @property
    def plot_id(self) -> str:
        """The ``id`` of the current plot (the population function's name)."""
        return str(self._plot_combo.currentData())

    @property
    def model_name(self) -> str | None:
        """The current idealization model, or ``None`` when the project has none."""
        name = self._model_combo.currentText()
        return name or None

    @property
    def series(self) -> tuple[PlotSeries, ...]:
        """The layer list's series, in drawing order."""
        return tuple(self._series)

    @property
    def series_labels(self) -> list[str]:
        """The name of each series, in drawing order."""
        return [s.label for s in self._series]

    @property
    def outcome(self) -> PlotOutcome:
        """What the last draw made: a figure, or the reason there is none."""
        return self._outcome

    @property
    def figure(self) -> Figure | None:
        """The `Figure` on the preview canvas — the one an export writes — or ``None``."""
        return self._outcome.figure

    @property
    def canvas(self) -> Any | None:
        """The `FigureCanvasQTAgg` of the preview, or ``None`` while no figure shows."""
        return self._canvas

    @property
    def empty_text(self) -> str | None:
        """The text that shows in place of a figure, or ``None`` while a figure shows."""
        return None if self._outcome.drawn else self._empty.text()

    # --- the project, the plot and the layer list ----------------------------

    def set_project(self, project: str | PathLike[str] | None) -> None:
        """Read plots from ``project`` (``None``: no project) and empty the layer list.

        The series of another project are taken out: a saved selection names no molecule
        outside its own project (ADR-0124), so keeping them would draw empty series.
        """
        self._project = None if project is None else Path(project)
        self._series.clear()
        self._layers.clear()
        self._reload_models(reset_states=True)
        self._rebuild_add_menu()
        self.refresh()

    def set_plot(self, plot_id: str) -> None:
        """Choose the plot ``plot_id``; raise `KeyError` for a name not in the registry."""
        plot_spec(plot_id)
        self._plot_combo.setCurrentIndex(self._plot_combo.findData(plot_id))

    def set_model(self, model_name: str) -> None:
        """Choose the idealization model ``model_name``; raise `KeyError` if it is not one."""
        index = self._model_combo.findText(model_name)
        if index < 0:
            raise KeyError(f"the project has no idealization model {model_name!r}")
        self._model_combo.setCurrentIndex(index)

    def add_series(self, series: PlotSeries) -> PlotSeries:
        """Add ``series`` at the end of the layer list, draw again, and return it.

        A label that another series already has gets `` (2)``, `` (3)``… so every series
        keeps a distinct name on the figure and in the export.
        """
        label = _unique(series.label, {s.label for s in self._series})
        added = PlotSeries(label, series.selection)
        self._series.append(added)
        self._layers.addItem(label)
        self._layers.setCurrentRow(len(self._series) - 1)
        self.refresh()
        return added

    def remove_series(self, index: int) -> PlotSeries:
        """Take the series at ``index`` out of the layer list, draw again, and return it."""
        removed = self._series.pop(index)
        self._layers.takeItem(index)
        self.refresh()
        return removed

    def remove_selected(self) -> PlotSeries | None:
        """Take the selected series out (the *Remove* button); ``None`` when none is selected."""
        row = self._layers.currentRow()
        if not 0 <= row < len(self._series):
            return None
        return self.remove_series(row)

    # --- saving and opening a composition ------------------------------------

    def save_composition(self, path: str | PathLike[str]) -> SaveCompositionResult | None:
        """Write the current composition to ``path`` (``save-composition``), replacing it.

        Returns the result, or ``None`` when the command refused or failed; the notice
        under the buttons says which.
        """
        from tether.services import run  # noqa: PLC0415

        if self._project is None:
            self._notice.setText("Open a project to save a composition.")
            return None
        if not self._series:
            self._notice.setText("Add a selection to the layer list to save a composition.")
            return None
        spec = plot_spec(self.plot_id)
        request = SaveCompositionRequest(
            path=str(path),
            project=str(self._project),
            plot=spec.id,
            series=tuple(series_text(s) for s in self._series),
            model=self.model_name if spec.needs_model else None,
            states=tuple(f"{n}={self._state_boxes[n].value()}" for n in spec.states),
            overwrite=True,
        )
        try:
            result: SaveCompositionResult = run(request)
        except ServiceError as exc:
            self._notice.setText(f"The composition was not saved: {exc}")
            return None
        self._notice.setText(f"Saved the composition to {result.path.name}.")
        return result

    def open_composition(self, path: str | PathLike[str]) -> OpenCompositionResult | None:
        """Read ``path`` (``open-composition``) and show its plot and layer list.

        The layer list is replaced by the series of the file, in their order and with their
        names. A series whose project or molecules cannot be found is left out and named
        in the notice; a composition of a project other than the open one changes nothing.
        Returns the result, or ``None`` when the file was refused.
        """
        from tether.services import run  # noqa: PLC0415

        try:
            result: OpenCompositionResult = run(OpenCompositionRequest(path=str(path)))
        except ServiceError as exc:
            self._notice.setText(f"The composition was not opened: {exc}")
            return None
        composition = result.composition
        if self._project is None or not _same_file(self._project, composition.project_path):
            lines = [
                f"{Path(path).name} reads the project {composition.project_path}; "
                "open that project first.",
            ]
            if result.missing:
                lines.append(result.message())
            self._notice.setText("\n".join(lines))
            return result
        lines = [f"Opened {Path(path).name}."]
        lines.extend(f"Left out {label}: {reason}" for label, reason in result.missing)
        spec = plot_spec(composition.plot)
        blocked = [self._plot_combo, self._model_combo, *self._state_boxes.values()]
        previous = [w.blockSignals(True) for w in blocked]
        try:
            self._plot_combo.setCurrentIndex(self._plot_combo.findData(spec.id))
            self._sync_plot_controls()
            self._reload_models(reset_states=True)
            if spec.needs_model:
                # A composition that names no model draws none: never the model left chosen.
                name = composition.model_name
                index = -1 if name is None else self._model_combo.findText(name)
                self._model_combo.setCurrentIndex(index)
                if name is not None and index < 0:
                    lines.append(
                        f"The project has no idealization model {composition.model_name!r}."
                    )
            for name, value in composition.states:
                self._state_boxes[name].setValue(value)
        finally:
            for widget, was in zip(blocked, previous, strict=True):
                widget.blockSignals(was)
        self._series = list(result.found)
        self._layers.clear()
        self._layers.addItems([s.label for s in self._series])
        self.refresh()
        self._notice.setText("\n".join(lines))
        return result

    def _ask_save(self) -> None:
        from pyqtgraph.Qt import QtWidgets  # noqa: PLC0415

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self._widget, "Save composition", "", _COMPOSITION_FILTER
        )
        if path:
            self.save_composition(path)

    def _ask_open(self) -> None:
        from pyqtgraph.Qt import QtWidgets  # noqa: PLC0415

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._widget, "Open composition", "", _COMPOSITION_FILTER
        )
        if path:
            self.open_composition(path)

    # --- drawing -------------------------------------------------------------

    def refresh(self, *, announce: bool = True) -> PlotOutcome:
        """Draw the current plot over the layer list again, and return what was drawn.

        ``announce=False`` draws without a status-bar summary, so a delayed redraw behind
        another section does not replace that section's own result there.

        The model choices are read from the project again first, so a model that a fit or
        a tMAVEN return leg wrote after the project opened can be chosen, and the current
        choice is kept while the project still has it. A failure the registry does not name
        as a missing prerequisite still shows as the reason in the preview, so the
        application never stops on a plot.
        """
        if self._closed:
            return self._outcome
        self._reload_models(reset_states=False)
        spec = plot_spec(self.plot_id)
        states = {name: box.value() for name, box in self._state_boxes.items()}
        try:
            outcome = compose_plot(
                self._project,
                spec.id,
                self._series,
                model_name=self.model_name,
                states=states,
            )
        except Exception as exc:  # noqa: BLE001 - keep the GUI alive, show the cause
            outcome = PlotOutcome(spec.id, missing=f"{spec.title} cannot be drawn: {exc}")
        self._show(outcome, announce=announce)
        return outcome

    def _show(self, outcome: PlotOutcome, *, announce: bool = True) -> None:
        previous = self._canvas
        self._canvas = None
        if outcome.figure is not None:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: PLC0415

            self._canvas = FigureCanvasQTAgg(outcome.figure)
            self._canvas.setObjectName("analysis-canvas")
            self._canvas_layout.addWidget(self._canvas)
            self._preview.setCurrentWidget(self._canvas_host)
            self._canvas.draw_idle()
        else:
            self._empty.setText(outcome.missing or "")
            self._preview.setCurrentWidget(self._empty)
        if previous is not None:
            self._canvas_layout.removeWidget(previous)
            previous.setParent(None)
            previous.deleteLater()
        self._outcome = outcome
        self._sync_buttons()
        if announce and self._status is not None and outcome.drawn:
            self._status(_drawn_message(outcome))

    # --- controls ------------------------------------------------------------

    def _on_plot_changed(self, _index: int) -> None:
        self._sync_plot_controls()
        self.refresh()

    def _sync_plot_controls(self) -> None:
        """Show the model choice and the state boxes only for a plot that reads them."""
        spec = plot_spec(self.plot_id)
        self._model_label.setVisible(spec.needs_model)
        self._model_combo.setVisible(spec.needs_model)
        defaults = spec.state_defaults()
        for name, box in self._state_boxes.items():
            shown = name in defaults
            box.setVisible(shown)
            self._state_labels[name].setVisible(shown)

    def _sync_buttons(self) -> None:
        has_project = self._project is not None
        self._add_button.setEnabled(has_project)
        self._remove_button.setEnabled(0 <= self._layers.currentRow() < len(self._series))

    def _reload_models(self, *, reset_states: bool) -> None:
        models: list[str] = []
        if self._project is not None:
            try:
                models = idealization_models(self._project)
            except Exception:  # noqa: BLE001 - an unreadable project lists no model
                models = []
        current = self._model_combo.currentText()
        listed = [self._model_combo.itemText(i) for i in range(self._model_combo.count())]
        if models != listed:
            blocker = self._model_combo.blockSignals(True)
            try:
                self._model_combo.clear()
                self._model_combo.addItems(models)
                if current in models:
                    self._model_combo.setCurrentIndex(models.index(current))
            finally:
                self._model_combo.blockSignals(blocker)
        if not reset_states:
            return
        for name, value in {"state": 0, "init_state": 0, "final_state": 1}.items():
            box = self._state_boxes[name]
            blocker = box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(blocker)

    def _rebuild_add_menu(self) -> None:
        """Fill *Add selection* from the open project (on each opening, so it is current)."""
        menu = self._add_menu
        menu.clear()
        if self._project is None:
            menu.addAction("Open a project first").setEnabled(False)
            return
        everything = menu.addAction(ALL_MOLECULES)
        everything.triggered.connect(lambda: self.add_series(PlotSeries(ALL_MOLECULES, None)))
        try:
            presets = selection_presets(self._project)
        except Exception as exc:  # noqa: BLE001 - the presets are optional; say why
            menu.addAction(f"Presets unavailable: {exc}").setEnabled(False)
            return
        for by, title in PRESET_TITLES.items():
            sub = menu.addMenu(title)
            groups = presets.get(by, ())
            if not groups:
                sub.addAction("(none)").setEnabled(False)
            for selection in groups:
                action = sub.addAction(f"{selection.name} ({selection.n_molecules})")
                action.triggered.connect(
                    lambda _checked=False, s=selection: self.add_series(PlotSeries(s.name, s))
                )

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Release the preview and close the page (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if self._canvas is not None:
            self._canvas_layout.removeWidget(self._canvas)
            self._canvas.setParent(None)
            self._canvas.deleteLater()
            self._canvas = None
        self._widget.close()

    def __enter__(self) -> AnalysisSection:
        """Return the page, for a ``with`` block."""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Close the page at the end of a ``with`` block."""
        self.close()
