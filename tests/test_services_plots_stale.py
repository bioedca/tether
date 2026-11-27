# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stale fits in the Analyze section's plots (#375, ADR-0124): exclude, badge, stamped opt-out.

``tether.services.plots`` is Qt-free, so these run with no ``QApplication``. They check:

* a model-derived plot leaves each series' stale fits out by default, and the opt-out of one
  series draws them, so the two give different numbers, each attributed to its own series
  and equal to the population function's own answer for that cohort;
* the badge count is ``Project.stale_idealization_keys`` within the series' accepted
  molecules, and a rejected stale molecule, which would be left out anyway, is not counted;
* every model-derived plot is driven through the population function's own
  ``include_stale`` — ``population_transition_sync_histogram2d`` included, whose
  ``/traces`` width guard catches no re-extraction of a single molecule — with the selection
  passed on unfiltered;
* the model-Gaussian overlay over a cohort with stale members carries a warning, and its
  histogram leaves the stale molecules out unless the series opts in;
* the choice and the counts reach an export's provenance file.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tether.services.plots import PLOTS, PlotSeries, compose_plot

pytest.importorskip("matplotlib")

MODEL = "vbconhmm"
#: Molecules 1 and 4 have a stale fit; molecule 5 is stale and rejected.
STALE = [False, True, False, False, True, True]
REJECTED = [False, False, False, False, False, True]
MODEL_PLOTS = [spec.id for spec in PLOTS if spec.needs_model]


@pytest.fixture(scope="module")
def stale_store(tmp_path_factory):
    """A six-molecule store with a two-state model; three fits stale, one of them rejected."""
    from _analysis_store import build_store_with_model

    n, t = 6, 60
    states = np.array([[(j // (3 + 4 * i)) % 2 for j in range(t)] for i in range(n)])
    project, keys = build_store_with_model(
        tmp_path_factory.mktemp("stale"),
        states,
        np.array([0.2, 0.8]),
        stale=STALE,
        rejected=REJECTED,
        model_name=MODEL,
    )
    return project, keys


def _halves(path, keys, *, include_first: bool = False):
    from tether.analysis.saved_selection import SavedSelection

    return [
        PlotSeries("first", SavedSelection("first", path, keys[:3]), include_first),
        PlotSeries("second", SavedSelection("second", path, keys[3:])),
    ]


def test_the_store_has_the_stale_fits_the_tests_rely_on(stale_store) -> None:
    project, keys = stale_store
    stale = [k for k, s in zip(keys, STALE, strict=True) if s]
    assert project.stale_idealization_keys(MODEL) == stale
    assert project.live_idealization_keys(MODEL) == [k for k in keys if k not in stale]


def test_the_default_and_the_opt_out_give_different_correctly_attributed_numbers(
    stale_store,
) -> None:
    from tether.analysis import population_transition_density

    project, keys = stale_store
    default = compose_plot(
        project.path,
        "population_transition_density",
        _halves(project.path, keys),
        model_name=MODEL,
    )
    opted = compose_plot(
        project.path,
        "population_transition_density",
        _halves(project.path, keys, include_first=True),
        model_name=MODEL,
    )
    assert default.drawn and opted.drawn
    d_first, d_second = (r for _, r in default.results)
    o_first, o_second = (r for _, r in opted.results)
    # "first" holds molecule 1, stale: the default leaves it out, the opt-out draws it.
    assert d_first.n_molecules == 2 and o_first.n_molecules == 3
    assert not np.array_equal(d_first.counts, o_first.counts)
    # Each number is the population function's own for that series and that choice.
    expected_default = population_transition_density(project, MODEL, molecule_keys=keys[:3])
    expected_opted = population_transition_density(
        project, MODEL, molecule_keys=keys[:3], include_stale=True
    )
    np.testing.assert_array_equal(d_first.counts, expected_default.counts)
    np.testing.assert_array_equal(o_first.counts, expected_opted.counts)
    # "second" did not opt in: the choice of "first" changes nothing of it. Of its three
    # molecules, 4 is stale and 5 is stale and rejected, so one is drawn.
    assert d_second.n_molecules == o_second.n_molecules == 1
    np.testing.assert_array_equal(d_second.counts, o_second.counts)
    # The badge explains the smaller N of each series.
    d_badges = [(e.label, e.stale, e.excluded) for e in default.staleness]
    o_badges = [(e.label, e.stale, e.excluded) for e in opted.staleness]
    assert d_badges == [("first", 1, 1), ("second", 1, 1)]
    assert o_badges == [("first", 1, 0), ("second", 1, 1)]
    assert d_first.n_molecules + default.staleness[0].excluded == o_first.n_molecules


def test_the_badge_says_how_many_and_why(stale_store) -> None:
    project, keys = stale_store
    default = compose_plot(
        project.path, "population_state_number", _halves(project.path, keys), model_name=MODEL
    )
    opted = compose_plot(
        project.path,
        "population_state_number",
        _halves(project.path, keys, include_first=True),
        model_name=MODEL,
    )
    assert default.staleness[0].badge() == "1 stale excluded (inputs changed since the fit)"
    assert opted.staleness[0].badge() == "includes 1 stale (inputs changed since the fit)"


def test_a_rejected_stale_molecule_is_not_counted(stale_store) -> None:
    # Molecule 5 is stale but rejected: curation leaves it out whatever the choice, so the
    # badge does not count it as left out for being stale.
    project, keys = stale_store
    outcome = compose_plot(
        project.path,
        "population_state_number",
        [PlotSeries("all", None), PlotSeries("tail", keys[3:])],
        model_name=MODEL,
    )
    assert outcome.drawn, outcome.missing
    assert [e.stale for e in outcome.staleness] == [2, 1]


def test_two_rows_that_share_a_stale_key_count_as_two_molecules(stale_store, monkeypatch) -> None:
    # A molecule_key can name several rows (§7.10); the freshness rule leaves the key out
    # whole, so both rows are left out and the badge counts two, not one (#1481 review).
    import tether.imaging.extract as extract
    from tether.project.core import Project

    project, _keys = stale_store
    rows = np.zeros(3, dtype=[("molecule_key", "U8"), ("curation_label", "i4")])
    rows["molecule_key"] = ["K", "K", "F"]
    monkeypatch.setattr(extract, "read_molecules", lambda _path: rows)
    monkeypatch.setattr(Project, "stale_idealization_keys", lambda _self, _model: ["K"])
    from tether.services.plots import _staleness

    counted = _staleness(
        project.path,
        MODEL,
        [PlotSeries("all", None), PlotSeries("one key", ["K"]), PlotSeries("fresh", ["F"])],
    )
    assert [entry.stale for entry in counted] == [2, 2, 0]


def test_a_series_with_no_stale_fit_has_no_badge(stale_store) -> None:
    project, keys = stale_store
    outcome = compose_plot(
        project.path,
        "population_state_number",
        [PlotSeries("fresh", keys[:1])],
        model_name=MODEL,
    )
    assert outcome.staleness[0].stale == 0
    assert outcome.staleness[0].badge() is None


@pytest.mark.parametrize("plot_id", MODEL_PLOTS)
def test_every_model_plot_passes_include_stale_to_its_population_function(
    stale_store, plot_id, monkeypatch
) -> None:
    import tether.analysis as analysis

    project, keys = stale_store
    real = getattr(analysis, plot_id)
    calls: list[tuple[object, bool]] = []

    def spy(*args, **kwargs):
        calls.append((kwargs["molecule_keys"], kwargs["include_stale"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(analysis, plot_id, spy)
    selection = keys[:3]
    for include in (False, True):
        outcome = compose_plot(
            project.path, plot_id, [PlotSeries("s", selection, include)], model_name=MODEL
        )
        assert outcome.drawn, outcome.missing
    # The function's own lever, with the selection passed on as given — not pre-filtered.
    assert calls == [(selection, False), (selection, True)]


def test_the_sync_heatmap_leaves_a_stale_single_molecule_out(stale_store) -> None:
    # parameters.md: the heatmap's only guard compares a state path against the width of the
    # whole /traces array, so a re-extraction of one molecule is not caught. The builder
    # therefore drives it with include_stale, and the stale molecule is left out.
    from tether.analysis import population_transition_sync_histogram2d

    project, keys = stale_store
    plot_id = "population_transition_sync_histogram2d"
    default = compose_plot(project.path, plot_id, [PlotSeries("s", keys[:3])], model_name=MODEL)
    opted = compose_plot(project.path, plot_id, [PlotSeries("s", keys[:3], True)], model_name=MODEL)
    fresh = population_transition_sync_histogram2d(
        project, MODEL, molecule_keys=[keys[0], keys[2]], include_stale=True
    )
    (_, d), (_, o) = default.results[0], opted.results[0]
    np.testing.assert_array_equal(d.counts, fresh.counts)
    assert d.n_molecules == fresh.n_molecules < o.n_molecules


def test_the_overlay_over_a_stale_cohort_warns(stale_store) -> None:
    project, keys = stale_store
    plot_id = "population_model_gaussian_overlay"
    default = compose_plot(project.path, plot_id, _halves(project.path, keys), model_name=MODEL)
    assert default.drawn
    assert len(default.warnings) == 2
    assert default.warnings[0].startswith(
        "'first' has 1 molecule(s) whose fit in 'vbconhmm' is stale"
    )
    assert "the histogram leaves them out" in default.warnings[0]
    opted = compose_plot(
        project.path, plot_id, _halves(project.path, keys, include_first=True), model_name=MODEL
    )
    assert "drawn over them" in opted.warnings[0]
    # The histogram under the curves follows the choice: 2 fresh molecules, or all 3.
    assert default.results[0][1][0].n_molecules == 2
    assert opted.results[0][1][0].n_molecules == 3


def test_the_overlay_over_a_fresh_cohort_does_not_warn(stale_store) -> None:
    project, keys = stale_store
    outcome = compose_plot(
        project.path,
        "population_model_gaussian_overlay",
        [PlotSeries("fresh", [keys[0], keys[2]])],
        model_name=MODEL,
    )
    assert outcome.drawn and outcome.warnings == ()


def test_the_choice_is_stamped_into_the_export_provenance(stale_store, tmp_path) -> None:
    from tether.analysis.plot_export import export_figure

    project, keys = stale_store
    outcome = compose_plot(
        project.path,
        "population_transition_density",
        _halves(project.path, keys, include_first=True),
        model_name=MODEL,
    )
    result = export_figure(
        outcome.figure,
        tmp_path / "tdp",
        tether_export="test",
        source=str(project.path),
        parameters=outcome.export_parameters(),
        created_utc="2026-11-26T00:00:00Z",
    )
    stamped = json.loads(result.provenance_path.read_text())["parameters"]
    assert stamped["model_name"] == MODEL
    records = [
        (s["label"], s["include_stale"], s["stale_excluded"], s["stale_included"])
        for s in stamped["series"]
    ]
    assert records == [("first", True, 0, 1), ("second", False, 1, 0)]


def test_a_plot_that_reads_no_model_has_no_staleness(stale_store) -> None:
    project, _keys = stale_store
    outcome = compose_plot(
        project.path, "population_apparent_e_histogram", [PlotSeries("all", None, True)]
    )
    assert outcome.drawn
    assert outcome.staleness == () and outcome.warnings == ()
    assert outcome.export_parameters() == {}
