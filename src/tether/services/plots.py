# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The plot registry of the Analyze section: one entry for each population plot (#374).

The Analyze section of the application draws one plot at a time from a *layer list*: an
ordered list of named series, each a selection of molecules
([ADR-0124](../adr/0124-the-plot-builder-s-composition-unit-renderer-and-staleness-and-frame-time-policies.md)).
This module holds the part of that which is not a widget:

* [`PLOTS`][] — one [`PlotSpec`][] for each of the sixteen population plot functions of
  [`tether.analysis`][], keyed by the function's own name, in the order the section lists
  them;
* [`compose_plot`][] — run the entry's population function once for each series and draw the
  results with the entry's [`tether.analysis.plot_export`][] renderer, so the figure the
  section previews is the `Figure` an export writes (one renderer, ADR-0124);
* [`selection_presets`][] and [`idealization_models`][] — what the section offers to add to
  the layer list, and the models a model-derived plot can read.

**Stale fits** (#375, ADR-0124). A plot that reads an ``/idealization`` model leaves out the
molecules whose fit is stale — whose inputs changed since the fit (ADR-0103) — unless the series
opts in with ``include_stale``. [`compose_plot`][] passes each series' ``include_stale`` to the
population function itself, whose one freshness rule keeps only the model's live keys
(``Project.live_idealization_keys``, ADR-0109), and counts the stale members of each series with
``Project.stale_idealization_keys`` for the badge ([`SeriesStaleness`][]). The count and the
choice travel into an export's provenance through [`PlotOutcome.export_parameters`][]. The
model-Gaussian overlay draws the model's own curves whatever the selection, so over a cohort
with stale members the outcome carries a warning (``PlotOutcome.warnings``) that the section
shows with the figure.

A plot whose prerequisites are missing — no series, no idealization model, a state the
model does not have — is not drawn: [`compose_plot`][] returns a [`PlotOutcome`][] whose
``missing`` names what is missing, and the section shows that text in place of a figure. It
never draws a placeholder result (PRD §7.7).

Like a status read ([`tether.services.status`][]), drawing a plot writes nothing, so it is a
plain function here and not a registered service command. It imports no Qt module, and it
imports the core and Matplotlib only when it runs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from os import PathLike

    from matplotlib.figure import Figure

    from tether.analysis.saved_selection import SavedSelection
    from tether.analysis.selection import MoleculeSelection

__all__ = [
    "ALL_MOLECULES",
    "PLOTS",
    "PRESET_TITLES",
    "STALE_REASON",
    "PlotOutcome",
    "PlotSeries",
    "PlotSpec",
    "SeriesStaleness",
    "compose_plot",
    "idealization_models",
    "plot_spec",
    "selection_presets",
]

#: The name of the series that holds every molecule of the project.
ALL_MOLECULES = "All molecules"

#: The preset groupings the section offers, with the words its menu shows for each.
PRESET_TITLES: Mapping[str, str] = {
    "condition": "By condition",
    "movie": "By movie",
    "source_file": "By source file",
}

#: Why a fit is stale, in the words of ADR-0103: the badge says it beside the count.
STALE_REASON = "inputs changed since the fit"

# The state options a plot can take, with the value the section starts from.
_STATE_DEFAULTS: Mapping[str, int] = {"state": 0, "init_state": 0, "final_state": 1}
#: The words the section and the missing-prerequisite text use for each state option.
_STATE_WORDS: Mapping[str, str] = {
    "state": "the state",
    "init_state": "the from-state",
    "final_state": "the to-state",
}


@dataclass(frozen=True)
class PlotSpec:
    """One entry of the plot registry.

    ``id`` is the name of the population function in [`tether.analysis`][] and ``title`` the
    words the section lists. ``needs_model`` says the plot reads an ``/idealization`` model;
    ``states`` names the state options it takes (``state`` for the dwell times,
    ``init_state`` and ``final_state`` for the transition probabilities).
    """

    id: str
    title: str
    needs_model: bool
    compute: Callable[..., Any] = field(repr=False, compare=False)
    render: Callable[[list[tuple[str, Any]]], Figure] = field(repr=False, compare=False)
    states: tuple[str, ...] = ()

    def state_defaults(self) -> dict[str, int]:
        """Return the state options of this plot with the value the section starts from."""
        return {name: _STATE_DEFAULTS[name] for name in self.states}


@dataclass(frozen=True)
class PlotSeries:
    """One entry of the layer list: a name and the molecules it draws.

    ``selection`` is passed as the ``molecule_keys`` of the population function (ADR-0109):
    ``None`` is every molecule of the project, and a
    [`SavedSelection`][tether.analysis.saved_selection.SavedSelection] or an analysis-set
    version (#934) names its own. ``include_stale`` is the explicit opt-out of a plot that
    reads a model: ``False`` (the default) leaves the series' stale fits out, ``True`` keeps
    them, and an export records which (ADR-0124).
    """

    label: str
    selection: MoleculeSelection | None = None
    include_stale: bool = False

    def __post_init__(self) -> None:
        """Refuse an empty label: the renderer names each series by it."""
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("a series needs a non-empty label")


@dataclass(frozen=True)
class SeriesStaleness:
    """The stale fits of one series of a model-derived plot (#375, ADR-0124).

    ``stale`` is how many molecules of the series — selected and not rejected by curation —
    have a stale fit in the model ``model_name``: their inputs changed since the fit
    (ADR-0103), counted with ``Project.stale_idealization_keys``. With ``include_stale``
    ``False`` the plot left them out; with ``True`` it drew them.
    """

    label: str
    model_name: str
    include_stale: bool
    stale: int

    @property
    def excluded(self) -> int:
        """How many molecules the plot left out of this series for a stale fit."""
        return 0 if self.include_stale else self.stale

    def badge(self) -> str | None:
        """Return the words beside the series' N, or ``None`` when the series has no stale fit."""
        if self.stale == 0:
            return None
        if self.include_stale:
            return f"includes {self.stale} stale ({STALE_REASON})"
        return f"{self.stale} stale excluded ({STALE_REASON})"

    def record(self) -> dict[str, Any]:
        """Return the provenance record of this series: the choice and both counts."""
        return {
            "include_stale": self.include_stale,
            "stale_excluded": self.excluded,
            "stale_included": self.stale - self.excluded,
        }


@dataclass(frozen=True)
class PlotOutcome:
    """What [`compose_plot`][] made: a figure, or the reason there is none.

    Exactly one of ``figure`` and ``missing`` is set. ``results`` holds the result of the
    population function for each series, in layer-list order, when a figure was drawn.
    ``staleness`` holds one [`SeriesStaleness`][] per series, in the same order, when the
    plot reads a model, and is empty otherwise. ``warnings`` holds what the section shows
    with the figure: the model-Gaussian overlay drawn over a cohort with stale members.
    """

    plot_id: str
    figure: Figure | None = None
    missing: str | None = None
    results: tuple[tuple[str, Any], ...] = ()
    staleness: tuple[SeriesStaleness, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def drawn(self) -> bool:
        """Whether a figure was drawn."""
        return self.figure is not None

    def export_parameters(self) -> dict[str, Any]:
        """Return the ``parameters`` an export of this figure records (PRD §7.9, ADR-0124).

        For a plot that reads a model: the ``model_name`` and, under ``series``, one record
        per drawn series in figure order with its ``include_stale`` choice and how many
        stale molecules it left out and drew ([`SeriesStaleness.record`][]), so a figure
        drawn over stale fits says so in its provenance file. Empty for any other plot.
        Pass it as the ``parameters`` of
        [`export_figure`][tether.analysis.plot_export.export_figure].
        """
        if not self.staleness:
            return {}
        return {
            "model_name": self.staleness[0].model_name,
            "series": [entry.record() for entry in self.staleness],
        }


def _analysis() -> Any:
    import tether.analysis as analysis  # noqa: PLC0415 - the core loads only when a plot runs

    return analysis


def _plot_export() -> Any:
    from tether.analysis import plot_export  # noqa: PLC0415 - Matplotlib loads only here

    return plot_export


def _call(name: str) -> Callable[..., Any]:
    """Compute one series with the population function ``name`` (no model, no state)."""

    def compute(project: Any, selection: Any, **_: Any) -> Any:
        return getattr(_analysis(), name)(project, molecule_keys=selection)

    return compute


def _call_model(name: str) -> Callable[..., Any]:
    """Compute one series with the model-derived population function ``name``."""

    def compute(
        project: Any, selection: Any, *, model_name: str, include_stale: bool, **_: Any
    ) -> Any:
        return getattr(_analysis(), name)(
            project, model_name, molecule_keys=selection, include_stale=include_stale
        )

    return compute


def _dwell(
    project: Any, selection: Any, *, model_name: str, state: int, include_stale: bool, **_: Any
) -> Any:
    return _analysis().population_dwell_times(
        project, model_name, state=state, molecule_keys=selection, include_stale=include_stale
    )[state]


def _transition_prob(
    project: Any,
    selection: Any,
    *,
    model_name: str,
    init_state: int,
    final_state: int,
    include_stale: bool,
    **_: Any,
) -> Any:
    return _analysis().population_transition_prob_histogram(
        project,
        model_name,
        init_state,
        final_state,
        molecule_keys=selection,
        include_stale=include_stale,
    )


def _without_stale(project: Any, model_name: str, selection: Any) -> Any:
    """Return ``selection`` less the molecules whose fit in ``model_name`` is stale.

    The FRET histogram under the model Gaussians reads no model, so it has no
    ``include_stale`` of its own; this is the pre-filtered ``molecule_keys`` that leaves the
    series' stale fits out of it (#375), the molecules [`SeriesStaleness`][] counts. A
    selection with no stale member is passed on unchanged.
    """
    from tether.analysis.selection import resolve_selection  # noqa: PLC0415
    from tether.imaging.extract import read_molecules  # noqa: PLC0415
    from tether.project.core import Project  # noqa: PLC0415

    proj = project if isinstance(project, Project) else Project.open(project)
    stale = set(proj.stale_idealization_keys(model_name))
    if not stale:
        return selection
    keys = resolve_selection(selection, proj)
    if keys is None:
        keys = [_text(k) for k in read_molecules(proj.path)["molecule_key"]]
    return [k for k in keys if k not in stale]


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _gaussian_overlay(
    project: Any, selection: Any, *, model_name: str, include_stale: bool, **_: Any
) -> Any:
    analysis = _analysis()
    keys = selection if include_stale else _without_stale(project, model_name, selection)
    histogram = analysis.population_apparent_e_histogram(project, molecule_keys=keys)
    overlay = analysis.population_model_gaussian_overlay(
        project, model_name, molecule_keys=selection, include_stale=include_stale
    )
    return histogram, overlay


def _render(name: str) -> Callable[[list[tuple[str, Any]]], Figure]:
    def render(series: list[tuple[str, Any]]) -> Figure:
        return getattr(_plot_export(), name)(series)

    return render


def _render_gaussian_overlay(series: list[tuple[str, Any]]) -> Figure:
    histograms = [(label, pair[0]) for label, pair in series]
    overlays = [pair[1] for _, pair in series]
    return _plot_export().render_histogram1d(histograms, overlay=overlays)


def _spec(
    name: str, title: str, renderer: str, *, model: bool = False, states: tuple[str, ...] = ()
) -> PlotSpec:
    compute = _call_model(name) if model else _call(name)
    return PlotSpec(name, title, model, compute, _render(renderer), states)


#: The sixteen population plots, in the order the section lists them: the trace views
#: first, then the plots that read an ``/idealization`` model.
PLOTS: tuple[PlotSpec, ...] = (
    _spec(
        "population_apparent_e_histogram",
        "FRET-efficiency histogram (A1)",
        "render_histogram1d",
    ),
    _spec(
        "population_apparent_e_histogram_ci",
        "FRET-efficiency histogram with bootstrap band (A1)",
        "render_histogram1d",
    ),
    _spec(
        "per_condition_apparent_e_histograms",
        "Per-condition FRET-efficiency histograms (A1)",
        "render_per_condition_histograms",
    ),
    _spec(
        "population_time_signal_histogram2d",
        "Time-vs-signal heatmap (A2)",
        "render_histogram2d",
    ),
    _spec(
        "population_cross_correlation",
        "Donor–acceptor cross-correlation",
        "render_cross_correlation",
    ),
    _spec(
        "population_anticorrelation_events",
        "Donor–acceptor anticorrelation events",
        "render_population_anticorrelation",
    ),
    _spec("population_raw_fret_cloud", "Raw FRET cloud", "render_raw_fret_cloud"),
    _spec(
        "population_fret_cloud_alpha_shape",
        "FRET-cloud support (alpha shape)",
        "render_alpha_shape",
    ),
    _spec(
        "population_fret_cloud_state_number_elbow",
        "State-number hint (k-vs-RMSE elbow)",
        "render_state_number_elbow",
    ),
    _spec(
        "population_successive_frame_cloud",
        "Successive-frame FRET cloud (E at frame i vs i + 1)",
        "render_successive_frame_cloud",
    ),
    PlotSpec(
        "population_model_gaussian_overlay",
        "FRET-efficiency histogram with model Gaussians (A1)",
        True,
        _gaussian_overlay,
        _render_gaussian_overlay,
    ),
    _spec(
        "population_transition_sync_histogram2d",
        "Transition-synchronized heatmap (A2b)",
        "render_transition_sync_histogram2d",
        model=True,
    ),
    _spec(
        "population_transition_density",
        "Transition-density plot (B1)",
        "render_transition_density",
        model=True,
    ),
    PlotSpec(
        "population_dwell_times",
        "Dwell-time survival (B2)",
        True,
        _dwell,
        _render("render_dwell_survival"),
        ("state",),
    ),
    PlotSpec(
        "population_transition_prob_histogram",
        "Transition-probability histogram (B3)",
        True,
        _transition_prob,
        _render("render_transition_prob"),
        ("init_state", "final_state"),
    ),
    _spec(
        "population_state_number",
        "State-number distribution (C1)",
        "render_state_number",
        model=True,
    ),
)

_BY_ID: dict[str, PlotSpec] = {spec.id: spec for spec in PLOTS}


def plot_spec(plot_id: str) -> PlotSpec:
    """Return the registry entry ``plot_id``; raise `KeyError` for a name that is not one."""
    try:
        return _BY_ID[plot_id]
    except KeyError:
        raise KeyError(f"no plot {plot_id!r} in the plot registry") from None


def idealization_models(project: str | PathLike[str]) -> list[str]:
    """Return the names of the models under ``/idealization`` of ``project``, sorted."""
    from tether.project.idealize import list_idealizations  # noqa: PLC0415

    return list_idealizations(project)


def selection_presets(project: str | PathLike[str]) -> dict[str, tuple[SavedSelection, ...]]:
    """Return the one-click selections of ``project``, by preset grouping (ADR-0124).

    Each key of [`PRESET_TITLES`][] maps to one frozen
    [`SavedSelection`][tether.analysis.saved_selection.SavedSelection] per group value, in
    first-seen order, as [`preset_selections`][tether.analysis.saved_selection.preset_selections]
    makes them.
    """
    from tether.analysis.saved_selection import preset_selections  # noqa: PLC0415

    return {by: preset_selections(project, by) for by in PRESET_TITLES}


def _model_state_count(project: str | PathLike[str], model_name: str) -> int:
    """How many states the model ``model_name`` of ``project`` has."""
    from tether.project.idealize import read_idealization  # noqa: PLC0415

    return len(read_idealization(project, model_name).means)


def _missing_states(spec: PlotSpec, options: Mapping[str, Any], n_states: int) -> str | None:
    """Name a state option that the model does not have, or return ``None``."""
    for name in spec.states:
        value = options[name]
        if not 0 <= value < n_states:
            return (
                f"{spec.title} needs a state of the model {options['model_name']!r}, which has "
                f"the states 0 to {n_states - 1}; {_STATE_WORDS[name]} {value} is not one."
            )
    return None


def _missing_model(spec: PlotSpec, models: Sequence[str], model_name: str | None) -> str | None:
    if not models:
        return (
            f"{spec.title} needs an idealization model, and this project has none. "
            "Idealize the molecules in Curate and correct first."
        )
    if model_name is None:
        return f"{spec.title} needs an idealization model. Choose one of: {', '.join(models)}."
    if model_name not in models:
        return (
            f"{spec.title} needs the idealization model {model_name!r}, which this project "
            f"does not have. It has: {', '.join(models)}."
        )
    return None


def _staleness(
    project: str | PathLike[str], model_name: str, series: Sequence[PlotSeries]
) -> tuple[SeriesStaleness, ...]:
    """Count the stale fits of each series in ``model_name`` (the badge, ADR-0124).

    A molecule counts when the series selects it, curation does not reject it (the
    default of every population function, §7.5) and ``Project.stale_idealization_keys``
    names its key. The key-level granularity is the population functions' own: a
    ``molecule_key`` with any stale row is left out whole (ADR-0029). The count is of
    ``/molecules`` rows, not of keys, so two accepted rows that share a stale key (§7.10)
    count as the two molecules the plot leaves out.
    """
    from tether.analysis.selection import resolve_selection  # noqa: PLC0415
    from tether.imaging.extract import read_molecules  # noqa: PLC0415
    from tether.project.core import Project  # noqa: PLC0415
    from tether.project.labels import curation_filter_mask  # noqa: PLC0415

    proj = Project.open(project)
    stale = set(proj.stale_idealization_keys(model_name))
    counts: list[SeriesStaleness] = []
    accepted: list[str] | None = None
    for entry in series:
        if not stale:
            counts.append(SeriesStaleness(entry.label, model_name, entry.include_stale, 0))
            continue
        if accepted is None:
            molecules = read_molecules(proj.path)
            keep = curation_filter_mask(molecules)
            accepted = [_text(k) for k in molecules["molecule_key"][keep]]
        keys = resolve_selection(entry.selection, proj)
        chosen = None if keys is None else set(keys)
        n_stale = sum(1 for k in accepted if k in stale and (chosen is None or k in chosen))
        counts.append(SeriesStaleness(entry.label, model_name, entry.include_stale, n_stale))
    return tuple(counts)


def _once(project: str | PathLike[str], entry: PlotSeries) -> PlotSeries:
    """Return ``entry`` with a one-shot iterable of keys read into a list (#1481 review).

    The stale count reads a series' selection before its population function does, so a
    generator of keys would reach the function exhausted. A list of the same keys, in the
    same order, is passed instead; ``None`` and a
    [`MoleculeSelection`][tether.analysis.selection.MoleculeSelection], which can be asked
    again, are passed on as they are.
    """
    from tether.analysis.selection import MoleculeSelection  # noqa: PLC0415

    if entry.selection is None or isinstance(entry.selection, MoleculeSelection | list):
        return entry
    from tether.analysis.selection import resolve_selection  # noqa: PLC0415

    return replace(entry, selection=resolve_selection(entry.selection, project))


def _overlay_warnings(staleness: Sequence[SeriesStaleness]) -> tuple[str, ...]:
    """Return the warnings of a model-Gaussian overlay drawn over a cohort with stale members."""
    warnings: list[str] = []
    for entry in staleness:
        if entry.stale == 0:
            continue
        drawn = (
            "drawn over them"
            if entry.include_stale
            else "the histogram leaves them out but the curves still include their fit"
        )
        warnings.append(
            f"{entry.label!r} has {entry.stale} molecule(s) whose fit in {entry.model_name!r} "
            f"is stale ({STALE_REASON}). The model Gaussians are the model's own fit of every "
            f"molecule it was fitted on, {drawn}."
        )
    return tuple(warnings)


def compose_plot(
    project: str | PathLike[str] | None,
    plot_id: str,
    series: Sequence[PlotSeries],
    *,
    model_name: str | None = None,
    states: Mapping[str, int] | None = None,
) -> PlotOutcome:
    """Draw the plot ``plot_id`` over ``series``, or say what it needs first.

    Each series is computed by the entry's population function with its selection as
    ``molecule_keys``, its ``include_stale`` passed through to a function that reads a model
    (stale fits left out by default, #375), and the function's own defaults otherwise
    (curation filter on); the results are drawn by the entry's renderer as one figure, a
    series per layer in layer-list order (ADR-0132). A plot that reads a model also counts
    each series' stale fits into ``staleness``, and the model-Gaussian overlay over a cohort
    with stale members is drawn with a warning, worked out before it is drawn, in
    ``warnings``. Returns a [`PlotOutcome`][] whose ``missing`` names
    the first missing prerequisite — an open project, a series, an idealization model, or a
    ``ValueError`` or ``KeyError`` that the population function or the renderer refused with —
    rather than a figure. Raises `KeyError` for a ``plot_id`` that is not in [`PLOTS`][].
    """
    spec = plot_spec(plot_id)
    if project is None:
        return PlotOutcome(plot_id, missing="Open a project to draw a plot.")
    if not series:
        return PlotOutcome(
            plot_id, missing=f"{spec.title}: add a selection to the layer list to draw it."
        )
    series = [_once(project, entry) for entry in series]
    options: dict[str, Any] = spec.state_defaults()
    options.update({k: int(v) for k, v in (states or {}).items() if k in spec.states})
    staleness: tuple[SeriesStaleness, ...] = ()
    warnings: tuple[str, ...] = ()
    if spec.needs_model:
        missing = _missing_model(spec, idealization_models(project), model_name)
        if missing is not None:
            return PlotOutcome(plot_id, missing=missing)
        options["model_name"] = model_name
        if spec.states:
            missing = _missing_states(spec, options, _model_state_count(project, model_name))
            if missing is not None:
                return PlotOutcome(plot_id, missing=missing)
        try:
            staleness = _staleness(project, model_name, series)
        except (ValueError, KeyError) as exc:
            reason = exc.args[0] if exc.args else type(exc).__name__
            return PlotOutcome(plot_id, missing=f"{spec.title} cannot be drawn: {reason}")
        if spec.id == "population_model_gaussian_overlay":
            warnings = _overlay_warnings(staleness)
    results: list[tuple[str, Any]] = []
    for entry in series:
        extra = {"include_stale": entry.include_stale} if spec.needs_model else {}
        try:
            results.append(
                (entry.label, spec.compute(project, entry.selection, **options, **extra))
            )
        except (ValueError, KeyError) as exc:
            reason = exc.args[0] if exc.args else type(exc).__name__
            return PlotOutcome(
                plot_id, missing=f"{spec.title} cannot be drawn for {entry.label!r}: {reason}"
            )
    try:
        figure = spec.render(results)
    except (ValueError, KeyError) as exc:
        reason = exc.args[0] if exc.args else type(exc).__name__
        return PlotOutcome(plot_id, missing=f"{spec.title} cannot be drawn: {reason}")
    return PlotOutcome(
        plot_id, figure=figure, results=tuple(results), staleness=staleness, warnings=warnings
    )
