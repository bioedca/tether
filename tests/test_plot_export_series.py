# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Several named series on one figure, for all thirteen plot renderers (#371, ADR-0127).

Each acceptance criterion of #371 has its tests here:

* every renderer takes an ordered sequence of ``(label, result)`` and draws legend- or
  panel-labelled series, and a one-series sequence draws **the same bytes** as the bare
  result that ``tests/test_plot_export.py`` checks artist by artist;
* the series take their colours from the one named cycle, and re-rendering one
  composition is byte-reproducible;
* the heatmaps, the TDP and the raw FRET cloud (and the per-condition overlay and the
  event raster, whose series cannot share an axis either) are small multiples;
* two series on different grids are refused with the reason, never resampled;
* the parameters of each series reach the provenance file, per series.

The colour check reuses the contrast floor and the colour-vision model of
``tests/test_gui_theme.py`` (WCAG 2.x 3 : 1 for graphical objects; Machado et al. 2009
matrices and CIEDE2000 ≥ 10), applied against the white export background.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")

from matplotlib.collections import LineCollection, QuadMesh  # noqa: E402
from matplotlib.patches import Rectangle, StepPatch  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from test_gui_theme import _MACHADO, CVD_FLOOR, LINE_FLOOR, _ciede2000, _lab  # noqa: E402
from test_plot_export import (  # noqa: E402
    STATES,
    _anticorr_population,
    _grid_points,
    _sync_pairs,
    _two_condition_overlay,
)
from tether.analysis import plot_export as pe  # noqa: E402
from tether.analysis.cloud import (  # noqa: E402
    alpha_shape,
    k_rmse_elbow,
    population_raw_fret_cloud,
)
from tether.analysis.crosscorr import population_cross_correlation  # noqa: E402
from tether.analysis.dwell import population_dwell_times  # noqa: E402
from tether.analysis.histogram import (  # noqa: E402
    population_apparent_e_histogram,
    population_apparent_e_histogram_ci,
    population_model_gaussian_overlay,
    population_time_signal_histogram2d,
    transition_sync_histogram2d,
)
from tether.analysis.state_number import population_state_number  # noqa: E402
from tether.analysis.tdp import population_transition_density  # noqa: E402
from tether.analysis.transition_prob import population_transition_prob_histogram  # noqa: E402
from tether.gui import theme  # noqa: E402

PINNED = "2026-01-01T00:00:00+00:00"

#: A second population: the same three molecules visit the states in another order, so
#: every result has the shape and grid of the first but different values.
OTHER_STATES = STATES[:, ::-1].copy()

OVERLAID = ("h1", "dwell", "tprob", "statenum", "xcorr", "alpha", "elbow")
SMALL_MULTIPLES = ("h2", "tdp", "cloud", "percond", "sync", "anticorr")
KINDS = OVERLAID + SMALL_MULTIPLES

RENDER = {
    "h1": pe.render_histogram1d,
    "h2": pe.render_histogram2d,
    "tdp": pe.render_transition_density,
    "dwell": pe.render_dwell_survival,
    "tprob": pe.render_transition_prob,
    "statenum": pe.render_state_number,
    "cloud": pe.render_raw_fret_cloud,
    "xcorr": pe.render_cross_correlation,
    "percond": pe.render_per_condition_histograms,
    "sync": pe.render_transition_sync_histogram2d,
    "alpha": pe.render_alpha_shape,
    "elbow": pe.render_state_number_elbow,
    "anticorr": pe.render_population_anticorrelation,
}


def _results(proj, shift: float) -> dict[str, object]:
    """One result of each kind; ``shift`` changes the synthetic ones, never their grid."""
    rng = np.random.default_rng(int(shift * 10))
    overlay = _two_condition_overlay()
    if shift:
        first = overlay.histograms[0]
        counts = np.asarray(first.histogram.counts)[::-1].copy()
        flipped = replace(first, histogram=replace(first.histogram, counts=counts))
        overlay = replace(overlay, histograms=(flipped,) + overlay.histograms[1:])
    population = _anticorr_population()
    if shift:
        population = replace(population, molecules=population.molecules[::-1])
    return {
        "h1": population_apparent_e_histogram(proj),
        "h2": population_time_signal_histogram2d(proj, time_bins=6),
        "tdp": population_transition_density(proj, "vbconhmm"),
        "dwell": population_dwell_times(proj, "vbconhmm", state=1)[1],
        "tprob": population_transition_prob_histogram(proj, "vbconhmm", 0, 1),
        "statenum": population_state_number(proj, "vbconhmm"),
        "cloud": population_raw_fret_cloud(proj),
        "xcorr": population_cross_correlation(proj),
        "percond": overlay,
        "sync": transition_sync_histogram2d(_sync_pairs()[:1] if shift else _sync_pairs()),
        "alpha": alpha_shape(_grid_points() * (1.0 - 0.2 * shift), alpha=10.0),
        "elbow": k_rmse_elbow(
            np.repeat([0.2, 0.5, 0.8], 50) + rng.normal(0.0, 0.01 + 0.02 * shift, 150)
        ),
        "anticorr": population,
    }


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """``{kind: (wild_type, mutant)}`` results for every renderer."""
    base = tmp_path_factory.mktemp("series")
    proj_a, _ = build_store_with_model(base, STATES, MEANS, name="wt.tether")
    proj_b, _ = build_store_with_model(base, OTHER_STATES, MEANS, name="mut.tether")
    a, b = _results(proj_a, 0.0), _results(proj_b, 1.0)
    return {k: (a[k], b[k]) for k in KINDS}


def _two(results, kind):
    wt, mut = results[kind]
    return [("WT", wt), ("mutant", mut)]


def _export(fig, stem, parameters=None):
    return pe.export_figure(
        fig,
        stem,
        tether_export="plot:t",
        source="s",
        parameters=parameters or {},
        created_utc=PINNED,
    )


def _sidecar(result) -> dict:
    return json.loads(result.provenance_path.read_text(encoding="utf-8"))


def _panels(fig) -> list:
    """The data axes of a figure, without the colour bars."""
    return [ax for ax in fig.axes if ax.get_label() != "<colorbar>"]


# ------------------------------------------------- AC 1: one series is today's figure


@pytest.mark.parametrize("kind", KINDS)
def test_a_one_series_sequence_draws_the_bare_figure_byte_for_byte(results, tmp_path, kind):
    wt, _ = results[kind]
    bare = _export(RENDER[kind](wt), tmp_path / "bare")
    one = _export(RENDER[kind]([("WT", wt)]), tmp_path / "one")
    for fmt in pe.DEFAULT_PLOT_FORMATS:
        assert bare.paths[fmt].read_bytes() == one.paths[fmt].read_bytes(), fmt
    # The label is not drawn on a one-series figure, but it is recorded.
    (record,) = _sidecar(one)["parameters"]["series"]
    assert record["label"] == "WT"
    assert record["result"] == type(wt).__name__
    assert "series" not in _sidecar(bare)["parameters"]


@pytest.mark.parametrize("kind", OVERLAID)
def test_overlaid_series_are_drawn_in_one_axis_with_a_label_each(results, kind):
    fig = RENDER[kind](_two(results, kind))
    legend = _panels(fig)[0].get_legend()
    assert legend is not None
    texts = [t.get_text() for t in legend.get_texts()]
    assert any(t.startswith("WT") for t in texts), texts
    assert any(t.startswith("mutant") for t in texts), texts


@pytest.mark.parametrize("kind", OVERLAID)
def test_a_series_label_starting_with_an_underscore_keeps_its_legend_entry(results, kind):
    # Matplotlib's automatic legend skips labels that start with "_"; a caller's own
    # series name must not lose its entry that way while its curve is still drawn.
    wt, mut = results[kind]
    fig = RENDER[kind]([("_control", wt), ("mutant", mut)])
    texts = [t.get_text() for t in _panels(fig)[0].get_legend().get_texts()]
    assert any(t.startswith("_control") for t in texts), texts
    assert any(t.startswith("mutant") for t in texts), texts


@pytest.mark.parametrize("kind", SMALL_MULTIPLES)
def test_series_that_cannot_share_an_axis_are_small_multiples(results, tmp_path, kind):
    fig = RENDER[kind](_two(results, kind))
    panels = _panels(fig)
    assert [ax.get_title() for ax in panels] == ["WT", "mutant"]
    if kind in ("h2", "tdp", "cloud", "sync"):
        # One heatmap per panel, never two on one axis, and one colour bar for all.
        for ax in panels:
            assert len([c for c in ax.collections if isinstance(c, QuadMesh)]) == 1
        meshes = [c for ax in panels for c in ax.collections if isinstance(c, QuadMesh)]
        assert len({(m.norm.vmin, m.norm.vmax) for m in meshes}) == 1
        assert len([ax for ax in fig.axes if ax.get_label() == "<colorbar>"]) == 1
    display = _sidecar(_export(fig, tmp_path / "sm"))["parameters"]["display"]
    assert display["series_layout"] == "small_multiples"


def test_the_two_dimensional_panels_show_each_series_own_values(results):
    wt, mut = results["tdp"]
    fig = pe.render_transition_density([("WT", wt), ("mutant", mut)])
    for ax, d in zip(_panels(fig), (wt, mut), strict=True):
        (mesh,) = [c for c in ax.collections if isinstance(c, QuadMesh)]
        np.testing.assert_array_equal(np.asarray(mesh.get_array()), np.asarray(d.counts).T)
    # The shared log scale spans every panel's bins.
    norm = _panels(fig)[0].collections[0].norm
    pooled = np.concatenate([np.ravel(wt.counts), np.ravel(mut.counts)])
    assert norm.vmax == pytest.approx(float(pooled.max()))


def test_a_condition_keeps_its_colour_in_every_panel():
    one = _two_condition_overlay()
    reordered = replace(one, histograms=one.histograms[::-1])
    fig = pe.render_per_condition_histograms([("A", one), ("B", reordered)])
    colours = []
    for ax in _panels(fig):
        steps = [p for p in ax.patches if isinstance(p, StepPatch)]
        colours.append({p.get_label().split(" ")[0]: p.get_edgecolor() for p in steps})
    assert colours[0] == colours[1]


def test_state_number_series_are_grouped_bars(results):
    wt, mut = results["statenum"]
    fig = pe.render_state_number([("WT", wt), ("mutant", mut)])
    ax = fig.axes[0]
    bars = [p for p in ax.patches if isinstance(p, Rectangle)]
    by_colour: dict[tuple, list] = {}
    for b in bars:
        by_colour.setdefault(tuple(b.get_facecolor()), []).append(b)
    assert len(by_colour) == 2
    for (label, c), group in zip((("WT", wt), ("mutant", mut)), by_colour.values(), strict=True):
        assert [b.get_height() for b in group] == list(np.asarray(c.counts)), label


def test_histogram_series_draw_their_band_and_model_in_their_colour(tmp_path):
    proj, _ = build_store_with_model(tmp_path, STATES, MEANS)
    ci = population_apparent_e_histogram_ci(proj, n_resamples=20)
    model = population_model_gaussian_overlay(proj, "vbconhmm")
    fig = pe.render_histogram1d([("WT", ci), ("again", ci.histogram)], overlay=[model, None])
    ax = fig.axes[0]
    steps = [p for p in ax.patches if isinstance(p, StepPatch)]
    assert [p.get_label() for p in steps] == [
        f"WT (N = {ci.n_molecules})",
        f"again (N = {ci.n_molecules})",
    ]
    (model_line,) = ax.lines
    assert model_line.get_color() == pe.SERIES_COLORS[0]
    np.testing.assert_array_equal(model_line.get_ydata(), model.total)
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    assert f"WT: {ci.ci_level:.0%} CI" in labels and "WT: model" in labels


# ------------------------------------------------ AC 2: one named, deterministic cycle


def test_series_take_the_named_cycle_in_order():
    assert pe.SERIES_COLOR_CYCLE == "okabe-ito-4"
    styles = [pe.series_style(k) for k in range(17)]
    assert [s.color for s in styles[:4]] == list(pe.SERIES_COLORS)
    assert {s.linestyle for s in styles[:4]} == {"-"}
    assert styles[4] == pe.SeriesStyle(pe.SERIES_COLORS[0], "--", "//")
    assert len(set(styles[:16])) == 16
    assert styles[16] == styles[0]
    with pytest.raises(ValueError, match="negative"):
        pe.series_style(-1)


def test_the_drawn_colours_are_the_cycle(results):
    wt, mut = results["xcorr"]
    series = [(f"s{k}", wt if k % 2 else mut) for k in range(5)]
    ax = pe.render_cross_correlation(series).axes[0]
    curves = ax.lines[2:]  # after the y = 0 and lag = 0 reference lines
    assert [(c.get_color(), c.get_linestyle()) for c in curves] == [
        (pe.series_style(k).color, pe.series_style(k).linestyle) for k in range(5)
    ]


def test_every_cycle_colour_reaches_3_to_1_on_the_white_export():
    for colour in pe.SERIES_COLORS:
        assert theme.contrast_ratio(colour, "#ffffff") >= LINE_FLOOR, colour


@pytest.mark.parametrize("vision", ["normal", *sorted(_MACHADO)])
def test_every_two_cycle_colours_stay_apart_under_colour_vision_deficiency(vision):
    labs = {c: _lab(c, vision) for c in pe.SERIES_COLORS}
    for a, b in itertools.combinations(pe.SERIES_COLORS, 2):
        assert _ciede2000(labs[a], labs[b]) >= CVD_FLOOR, (vision, a, b)


@pytest.mark.parametrize("kind", KINDS)
def test_re_rendering_a_composition_is_byte_reproducible(results, tmp_path, kind):
    first = _export(RENDER[kind](_two(results, kind)), tmp_path / "a")
    second = _export(RENDER[kind](_two(results, kind)), tmp_path / "b")
    for fmt in pe.DEFAULT_PLOT_FORMATS:
        assert first.paths[fmt].read_bytes() == second.paths[fmt].read_bytes(), fmt


# ------------------------------------------------- AC 4: different grids are refused


def _off_grid(results):
    """``{kind: (series, grid key the refusal must name)}`` with the mutant off-grid."""
    out = {}
    wt, mut = results["h1"]
    out["h1"] = (replace(mut, bin_edges=np.asarray(mut.bin_edges) * 0.5), "bin edges")
    wt, mut = results["h2"]
    out["h2"] = (replace(mut, time_dt=0.1), "frame time")
    wt, mut = results["tdp"]
    out["tdp"] = (replace(mut, signal_range=(0.0, 0.5)), "signal range")
    wt, mut = results["dwell"]
    out["dwell"] = (replace(mut, dt=0.05), "frame time")
    wt, mut = results["tprob"]
    out["tprob"] = (replace(mut, final_state=2), "init → final pair")
    wt, mut = results["statenum"]
    out["statenum"] = (replace(mut, states=np.asarray(mut.states) + 1), "state numbers")
    wt, mut = results["cloud"]
    out["cloud"] = (replace(mut, time_range=(0.0, 99.0)), "time range")
    wt, mut = results["xcorr"]
    out["xcorr"] = (replace(mut, normalize="none"), "normalization")
    wt, mut = results["percond"]
    out["percond"] = (replace(mut, value_range=(0.0, 2.0)), "value range")
    wt, mut = results["sync"]
    out["sync"] = (replace(mut, density=not mut.density), "density")
    return out


@pytest.mark.parametrize(
    "kind", ["h1", "h2", "tdp", "dwell", "tprob", "statenum", "cloud", "xcorr", "percond", "sync"]
)
def test_series_on_different_grids_are_refused_with_the_reason(results, kind):
    wt, _ = results[kind]
    mutant, key = _off_grid(results)[kind]
    with pytest.raises(ValueError, match="not on the grid") as caught:
        RENDER[kind]([("WT", wt), ("mutant", mutant)])
    message = str(caught.value)
    assert key in message and "'mutant'" in message and "does not resample" in message


def test_alpha_shapes_with_clouds_of_two_frame_times_are_refused(results):
    cloud_a, cloud_b = results["cloud"]
    shape_a, shape_b = results["alpha"]
    with pytest.raises(ValueError, match="frame time"):
        pe.render_alpha_shape(
            [("WT", shape_a), ("mutant", shape_b)],
            cloud=[cloud_a, replace(cloud_b, time_dt=0.1)],
        )
    with pytest.raises(ValueError, match="contradicts the cloud of series 'WT'"):
        pe.render_alpha_shape(
            [("WT", shape_a), ("mutant", shape_b)], cloud=[cloud_a, None], time_dt=0.5
        )


def test_elbow_series_may_probe_different_k(results):
    wt, mut = results["elbow"]
    short = replace(
        mut, k_values=np.asarray(mut.k_values)[:3], rmse=np.asarray(mut.rmse)[:3], elbow_k=None
    )
    ax = pe.render_state_number_elbow([("WT", wt), ("short", short)]).axes[0]
    assert list(ax.get_xticks()) == sorted(set(np.asarray(wt.k_values).tolist()))


# ------------------------------------------------ AC 5: parameters travel per series


@pytest.mark.parametrize("kind", KINDS)
def test_each_series_parameters_reach_the_provenance_file(results, tmp_path, kind):
    sidecar = _sidecar(_export(RENDER[kind](_two(results, kind)), tmp_path / "p"))
    records = sidecar["parameters"]["series"]
    assert [r["label"] for r in records] == ["WT", "mutant"]
    wt, mut = results[kind]
    for record, result in zip(records, (wt, mut), strict=True):
        assert record["result"] == type(result).__name__
        assert record["parameters"]
        assert record["display"]


def test_the_grid_and_counts_of_each_series_are_recorded(results, tmp_path):
    wt, mut = results["h1"]
    sidecar = _sidecar(_export(pe.render_histogram1d(_two(results, "h1")), tmp_path / "h"))
    for record, h in zip(sidecar["parameters"]["series"], (wt, mut), strict=True):
        params = record["parameters"]
        assert params["bin_edges"] == np.asarray(h.bin_edges).tolist()
        assert params["value_range"] == list(h.value_range)
        assert params["n_molecules"] == h.n_molecules
        assert "counts" not in params  # data, not a parameter
    assert [r["display"]["color"] for r in sidecar["parameters"]["series"]] == list(
        pe.SERIES_COLORS[:2]
    )
    display = sidecar["parameters"]["display"]
    assert display == {"series_layout": "overlay", "series_cycle": "okabe-ito-4"}


def test_the_caller_adds_its_own_parameters_per_series(results, tmp_path):
    fig = pe.render_dwell_survival(_two(results, "dwell"))
    given = [{"selection": "wt v1", "include_stale": False}, {"selection": "mut v2", "dt": 1.0}]
    records = _sidecar(_export(fig, tmp_path / "d", parameters={"series": given}))["parameters"][
        "series"
    ]
    assert records[0]["selection"] == "wt v1" and records[0]["include_stale"] is False
    assert records[1]["selection"] == "mut v2" and records[1]["label"] == "mutant"


def test_a_caller_series_entry_may_not_contradict_the_figure(results, tmp_path):
    with pytest.raises(ValueError, match="contradicts series 'WT'"):
        _export(
            pe.render_cross_correlation(_two(results, "xcorr")),
            tmp_path / "x",
            parameters={"series": [{"label": "wild type"}, {}]},
        )
    with pytest.raises(ValueError, match="one dict per drawn series"):
        _export(
            pe.render_cross_correlation(_two(results, "xcorr")),
            tmp_path / "y",
            parameters={"series": [{}]},
        )


def test_a_bare_figure_passes_the_callers_series_entry_through(results, tmp_path):
    wt, _ = results["xcorr"]
    sidecar = _sidecar(
        _export(pe.render_cross_correlation(wt), tmp_path / "z", parameters={"series": "mine"})
    )
    assert sidecar["parameters"]["series"] == "mine"


def test_small_multiples_keep_the_tdp_colour_scale_in_the_display_record(results, tmp_path):
    fig = pe.render_transition_density(_two(results, "tdp"), color_scale="linear")
    display = _sidecar(_export(fig, tmp_path / "t"))["parameters"]["display"]
    assert display["color_scale"] == "linear"
    assert display["series_color_scale"] == "shared"


# ------------------------------------------------------------- malformed series


@pytest.mark.parametrize(
    ("series", "error", "match"),
    [
        ([], ValueError, "at least one series"),
        ("WT", TypeError, "ordered sequence"),
        ([("WT",)], TypeError, r"\(label, CrossCorrelation\) pair"),
        ([("", None)], ValueError, "non-empty string label"),
        ([("WT", 1)], TypeError, "is a int, not a CrossCorrelation"),
    ],
)
def test_malformed_series_are_refused(series, error, match):
    with pytest.raises(error, match=match):
        pe.render_cross_correlation(series)


def test_two_series_may_not_share_a_label(results):
    wt, mut = results["xcorr"]
    with pytest.raises(ValueError, match="two series are labelled 'WT'"):
        pe.render_cross_correlation([("WT", wt), ("WT", mut)])


def test_one_overlay_cannot_belong_to_several_series(results, tmp_path):
    proj, _ = build_store_with_model(tmp_path, STATES, MEANS)
    model = population_model_gaussian_overlay(proj, "vbconhmm")
    with pytest.raises(ValueError, match="one overlay cannot belong to 2 series"):
        pe.render_histogram1d(_two(results, "h1"), overlay=model)
    with pytest.raises(ValueError, match="1 overlays were given for 2 series"):
        pe.render_histogram1d(_two(results, "h1"), overlay=[model])


def test_the_alpha_boundaries_of_several_series_are_overlaid(results):
    wt, mut = results["alpha"]
    ax = pe.render_alpha_shape([("WT", wt), ("mutant", mut)]).axes[0]
    boundaries = [c for c in ax.collections if isinstance(c, LineCollection)]
    assert [c.get_label() for c in boundaries] == ["WT", "mutant"]
    for c, s in zip(boundaries, (wt, mut), strict=True):
        assert len(c.get_segments()) == s.n_boundary_edges
