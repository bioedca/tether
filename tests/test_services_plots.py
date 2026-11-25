# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The plot registry of the Analyze section (#374): every population plot, one renderer.

``tether.services.plots`` is Qt-free, so these run with no ``QApplication``. They check the
promises the section rests on:

* all sixteen population plot functions of ``tether.analysis`` have an entry — closing the
  "5 of 7 Appendix C plot types unreachable" defect — and nothing else does
  (``pooled_exit_rates`` pools rates and is not a plot);
* each entry draws one series and two named series on a store that has a model;
* the figure drawn is the ``Figure`` an export writes, through ``export_figure``;
* a plot whose prerequisites are missing says what is missing and draws nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from tether.services import plots
from tether.services.plots import ALL_MOLECULES, PLOTS, PlotSeries, compose_plot, plot_spec

pytest.importorskip("matplotlib")

#: The fifteen entry points #374 names and the successive-frame cloud of #1085, in no
#: particular order.
SIXTEEN = {
    "population_anticorrelation_events",
    "population_raw_fret_cloud",
    "population_fret_cloud_alpha_shape",
    "population_fret_cloud_state_number_elbow",
    "population_cross_correlation",
    "population_dwell_times",
    "population_apparent_e_histogram",
    "population_apparent_e_histogram_ci",
    "per_condition_apparent_e_histograms",
    "population_model_gaussian_overlay",
    "population_time_signal_histogram2d",
    "population_transition_sync_histogram2d",
    "population_state_number",
    "population_transition_density",
    "population_transition_prob_histogram",
    "population_successive_frame_cloud",
}

MODEL = "vbconhmm"


@pytest.fixture(scope="module")
def modelled(tmp_path_factory):
    """A six-molecule store with a two-state model, and its molecule keys."""
    from _analysis_store import build_store_with_model

    n, t = 6, 60
    states = np.array([[(j // (5 + i)) % 2 for j in range(t)] for i in range(n)])
    project, keys = build_store_with_model(
        tmp_path_factory.mktemp("plots"), states, np.array([0.2, 0.8]), model_name=MODEL
    )
    return project.path, keys


@pytest.fixture(scope="module")
def unmodelled(tmp_path_factory):
    """A three-molecule store with explicit channels and no model."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(3)
    donor = rng.uniform(100.0, 200.0, size=(3, 30))
    acceptor = rng.uniform(100.0, 200.0, size=(3, 30))
    project, _keys = build_store_with_channels(tmp_path_factory.mktemp("bare"), donor, acceptor)
    return project.path


def _two(path, keys):
    from tether.analysis.saved_selection import SavedSelection

    half = len(keys) // 2
    return [
        PlotSeries("first", SavedSelection("first", path, keys[:half])),
        PlotSeries("second", SavedSelection("second", path, keys[half:])),
    ]


def _texts(figure) -> list[str]:
    from matplotlib.text import Text

    return [t.get_text() for t in figure.findobj(Text)]


# --- the registry ---------------------------------------------------------------


def test_the_registry_has_exactly_the_sixteen_population_plots() -> None:
    ids = [spec.id for spec in PLOTS]
    assert len(ids) == len(set(ids)) == 16
    assert set(ids) == SIXTEEN
    assert "pooled_exit_rates" not in ids  # a rate-pooling helper, not a plot


def test_each_entry_names_a_population_function_of_tether_analysis() -> None:
    import tether.analysis as analysis

    for spec in PLOTS:
        assert callable(getattr(analysis, spec.id)), spec.id
        assert spec.title.strip(), spec.id


def test_the_model_derived_entries_are_the_ones_that_take_a_model_name() -> None:
    import inspect

    import tether.analysis as analysis

    for spec in PLOTS:
        params = inspect.signature(getattr(analysis, spec.id)).parameters
        assert spec.needs_model == ("model_name" in params), spec.id


def test_an_unknown_plot_is_refused() -> None:
    with pytest.raises(KeyError, match="no plot 'population_no_such_plot'"):
        plot_spec("population_no_such_plot")


# --- drawing --------------------------------------------------------------------


@pytest.mark.parametrize("plot_id", [spec.id for spec in PLOTS])
def test_each_entry_draws_one_series_and_two_named_series(modelled, plot_id) -> None:
    path, keys = modelled
    one = compose_plot(path, plot_id, [PlotSeries(ALL_MOLECULES, None)], model_name=MODEL)
    assert one.drawn, one.missing
    assert [label for label, _ in one.results] == [ALL_MOLECULES]

    two = compose_plot(path, plot_id, _two(path, keys), model_name=MODEL)
    assert two.drawn, two.missing
    assert [label for label, _ in two.results] == ["first", "second"]
    texts = " ".join(_texts(two.figure))
    assert "first" in texts and "second" in texts  # each series is named on the figure


def test_a_series_draws_its_own_molecules(modelled) -> None:
    from tether.analysis import population_apparent_e_histogram

    path, keys = modelled
    first, second = _two(path, keys)
    outcome = compose_plot(path, "population_apparent_e_histogram", [first, second])
    drawn = dict(outcome.results)
    for series in (first, second):
        expected = population_apparent_e_histogram(path, molecule_keys=series.selection)
        np.testing.assert_allclose(drawn[series.label].counts, expected.counts)
        assert drawn[series.label].n_molecules == expected.n_molecules == len(keys) // 2


def test_the_drawn_figure_is_the_figure_an_export_writes(modelled, tmp_path) -> None:
    # One renderer (ADR-0124): the section previews outcome.figure, and export_figure writes
    # that same Figure object, so what is previewed is what is exported.
    from tether.analysis.plot_export import export_figure

    path, keys = modelled
    outcome = compose_plot(
        path, "population_transition_density", _two(path, keys), model_name=MODEL
    )
    assert outcome.drawn
    result = export_figure(
        outcome.figure,
        tmp_path / "tdp",
        tether_export="test",
        source=str(path),
        parameters={},
        created_utc="2026-11-24T00:00:00Z",
    )
    assert set(result.paths) == {"pdf", "svg", "png"}
    assert all(p.is_file() for p in result.paths.values())
    assert outcome.figure.axes  # the figure was not consumed or replaced by the export


# --- missing prerequisites --------------------------------------------------------


def test_no_project_and_no_series_say_what_is_missing(modelled) -> None:
    path, _keys = modelled
    assert compose_plot(None, "population_apparent_e_histogram", []).missing == (
        "Open a project to draw a plot."
    )
    empty = compose_plot(path, "population_apparent_e_histogram", [])
    assert not empty.drawn and empty.figure is None
    assert empty.missing == (
        "FRET-efficiency histogram (A1): add a selection to the layer list to draw it."
    )


@pytest.mark.parametrize("plot_id", [spec.id for spec in PLOTS if spec.needs_model])
def test_a_model_plot_on_a_project_with_no_model_names_the_missing_model(
    unmodelled, plot_id
) -> None:
    outcome = compose_plot(unmodelled, plot_id, [PlotSeries(ALL_MOLECULES, None)])
    assert not outcome.drawn and outcome.figure is None
    assert "needs an idealization model, and this project has none" in outcome.missing
    assert plot_spec(plot_id).title in outcome.missing


def test_a_model_plot_names_a_model_the_project_does_not_have(modelled) -> None:
    path, _keys = modelled
    series = [PlotSeries(ALL_MOLECULES, None)]
    unchosen = compose_plot(path, "population_state_number", series)
    assert unchosen.missing.endswith(f"Choose one of: {MODEL}.")
    absent = compose_plot(path, "population_state_number", series, model_name="ebhmm")
    assert "'ebhmm', which this project does not have" in absent.missing


@pytest.mark.parametrize(
    ("plot_id", "states", "words"),
    [
        ("population_dwell_times", {"state": 2}, "the state 2"),
        ("population_transition_prob_histogram", {"init_state": 5}, "the from-state 5"),
        ("population_transition_prob_histogram", {"final_state": -1}, "the to-state -1"),
    ],
)
def test_a_state_the_model_does_not_have_is_named(modelled, plot_id, states, words) -> None:
    path, _keys = modelled
    outcome = compose_plot(
        path, plot_id, [PlotSeries(ALL_MOLECULES, None)], model_name=MODEL, states=states
    )
    assert not outcome.drawn
    assert "which has the states 0 to 1" in outcome.missing
    assert f"{words} is not one" in outcome.missing


def test_a_refusal_of_the_population_function_is_shown_not_drawn(unmodelled, monkeypatch) -> None:
    import tether.analysis as analysis

    def refuse(*_args, **_kwargs):
        raise ValueError("the traces are too short for this lag")

    monkeypatch.setattr(analysis, "population_cross_correlation", refuse)
    outcome = compose_plot(
        unmodelled, "population_cross_correlation", [PlotSeries(ALL_MOLECULES, None)]
    )
    assert not outcome.drawn
    assert outcome.missing == (
        "Donor–acceptor cross-correlation cannot be drawn for 'All molecules': "
        "the traces are too short for this lag"
    )


def test_a_series_needs_a_label() -> None:
    with pytest.raises(ValueError, match="non-empty label"):
        PlotSeries("  ")


def test_the_presets_are_the_one_click_selections_of_the_project(modelled) -> None:
    path, keys = modelled
    presets = plots.selection_presets(path)
    assert list(presets) == list(plots.PRESET_TITLES)
    for groups in presets.values():
        assert sum(s.n_molecules for s in groups) == len(keys)
    assert plots.idealization_models(path) == [MODEL]
