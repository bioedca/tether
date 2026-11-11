# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for :mod:`tether.analysis.plot_export` — vector/raster plot export (§7.9).

Covers the export mechanism (three formats + provenance stamp) and that each M6
analysis plot-data dataclass renders to a figure that round-trips through the export.
Gated on ``h5py``/``matplotlib``/``scipy`` so the lightweight headless tiers skip it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")
pytest.importorskip("matplotlib")

from matplotlib.collections import PathCollection, QuadMesh  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Rectangle, StepPatch  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import plot_export as pe  # noqa: E402
from tether.analysis.cloud import population_raw_fret_cloud  # noqa: E402
from tether.analysis.crosscorr import population_cross_correlation  # noqa: E402
from tether.analysis.dwell import population_dwell_times  # noqa: E402
from tether.analysis.histogram import (  # noqa: E402
    apparent_e_histogram,
    population_apparent_e_histogram_ci,
    population_model_gaussian_overlay,
    population_time_signal_histogram2d,
)
from tether.analysis.state_number import population_state_number  # noqa: E402
from tether.analysis.tdp import population_transition_density  # noqa: E402
from tether.analysis.transition_prob import population_transition_prob_histogram  # noqa: E402

# Three molecules × 12 frames over states {0,1,2} with transitions and dwells, so the
# TDP / dwell / transition-prob / state-number plots all have real data.
STATES = np.array(
    [
        [0, 0, 0, 1, 1, 2, 2, 2, 0, 0, 1, 1],
        [1, 1, 2, 2, 2, 1, 1, 0, 0, 0, 1, 2],
        [2, 2, 1, 1, 0, 0, 0, 1, 1, 2, 2, 2],
    ],
    dtype="int64",
)

_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _assert_valid(result: pe.PlotExportResult, formats: tuple[str, ...] = pe.DEFAULT_PLOT_FORMATS):
    """Every requested output exists and carries its format's signature; the others do not."""
    assert set(result.paths) == set(formats)
    assert result.formats == tuple(f for f in pe.DEFAULT_PLOT_FORMATS if f in formats)
    for fmt in formats:
        path = result.paths[fmt]
        assert path.exists(), fmt
        data = path.read_bytes()
        assert data, f"{fmt} is empty"
        if fmt == "pdf":
            assert data.startswith(_PDF_MAGIC)
        elif fmt == "png":
            assert data.startswith(_PNG_MAGIC)
        elif fmt == "svg":
            assert b"<svg" in data[:2000]
    for fmt in set(pe.DEFAULT_PLOT_FORMATS) - set(formats):
        assert not result.stem.with_name(result.stem.name + f".{fmt}").exists()
    assert result.provenance_path.exists()


def _mesh(ax) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The plotted ``(rows, cols)`` value array and the x / y edges of the axes' one QuadMesh.

    A ``shading="flat"`` mesh stores row ``r`` / column ``c`` as the cell between
    ``y_edges[r:r+2]`` and ``x_edges[c:c+2]``, so ``array[r, c]`` is the value drawn at
    ``(x, y)`` — what the reader of the figure sees, whatever array the renderer passed.
    """
    meshes = [c for c in ax.collections if isinstance(c, QuadMesh)]
    assert len(meshes) == 1
    coords = meshes[0].get_coordinates()  # (n_rows + 1, n_cols + 1, 2)
    return np.asarray(meshes[0].get_array()), coords[0, :, 0], coords[:, 0, 1]


def _value_at(array: np.ndarray, x_edges: np.ndarray, y_edges: np.ndarray, x: float, y: float):
    """The plotted mesh value in the cell that holds the point ``(x, y)``."""
    col = int(np.searchsorted(x_edges, x, side="right")) - 1
    row = int(np.searchsorted(y_edges, y, side="right")) - 1
    return array[row, col]


def _simple_hist():
    rng = np.random.default_rng(0)
    values = np.concatenate([rng.normal(0.3, 0.05, 400), rng.normal(0.7, 0.05, 400)])
    return apparent_e_histogram(values)


# --------------------------------------------------------------------------- engine


def test_export_writes_vector_and_raster_with_sidecar(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    result = pe.export_figure(
        fig,
        tmp_path / "a1",
        tether_export="plot:a1",
        source="probe.tether",
        parameters={"bins": 151},
    )
    _assert_valid(result)
    assert result.provenance_path.name == "a1.provenance.json"


def test_stamped_svg_embeds_provenance_metadata(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    result = pe.export_figure(
        fig,
        tmp_path / "stamped",
        tether_export="plot:a1",
        source="probe.tether",
        parameters={},
        created_utc="2026-07-10T00:00:00+00:00",
    )
    svg = result.paths["svg"].read_text(encoding="utf-8")
    # Provenance is embedded in the SVG's document metadata (Dublin Core): the version,
    # the export kind, and the timestamp all travel inside the vector file itself.
    assert "Tether" in svg
    assert "plot:a1" in svg
    assert "2026-07-10T00:00:00" in svg


def test_visible_footer_present_when_stamped(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    pe.export_figure(
        fig,
        tmp_path / "footer",
        tether_export="plot:a1",
        source="s",
        parameters={},
        formats=("svg",),
        created_utc="2026-07-10T00:00:00+00:00",
    )
    # The visible on-figure footer is a pair of figure-level text artists (drawn as
    # glyphs in the vector output, so asserted at the artist level, not by string search).
    footer = [t.get_text() for t in fig.texts]
    assert any(t.startswith("Tether") for t in footer)
    assert any("2026-07-10T00:00:00" in t for t in footer)


def test_provenance_sidecar_records_params_and_outputs(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    result = pe.export_figure(
        fig,
        tmp_path / "prov",
        tether_export="plot:a1",
        source="src.tether",
        parameters={"bins": 151, "range": [0.0, 1.0]},
        created_utc="2026-07-10T00:00:00+00:00",
    )
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["tether_export"] == "plot:a1"
    assert payload["source_project"] == "src.tether"
    assert payload["created_utc"] == "2026-07-10T00:00:00+00:00"
    assert "app_version" in payload
    params = payload["parameters"]
    assert params["bins"] == 151
    assert params["range"] == [0.0, 1.0]
    assert params["dpi"] == pe.DEFAULT_EXPORT_DPI
    assert params["formats"] == list(pe.DEFAULT_PLOT_FORMATS)
    assert params["outputs"] == {"pdf": "prov.pdf", "svg": "prov.svg", "png": "prov.png"}


def test_export_format_subset(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    result = pe.export_figure(
        fig,
        tmp_path / "onlypdf",
        tether_export="plot:a1",
        source="src.tether",
        parameters={},
        formats=("pdf",),
    )
    _assert_valid(result, formats=("pdf",))
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["formats"] == ["pdf"]


def test_export_rejects_unknown_and_empty_formats(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    with pytest.raises(ValueError, match="unknown plot export format"):
        pe.export_figure(
            fig, tmp_path / "x", tether_export="k", source="s", parameters={}, formats=("jpeg",)
        )
    with pytest.raises(ValueError, match="at least one export format"):
        pe.export_figure(
            fig, tmp_path / "y", tether_export="k", source="s", parameters={}, formats=()
        )


def test_export_is_reproducible_for_pinned_timestamp(tmp_path):
    # Same figure + same created_utc + fixed svg hashsalt ⇒ byte-identical vector output.
    stamp = "2026-07-10T00:00:00+00:00"
    out = {}
    for name in ("first", "second"):
        fig = pe.render_histogram1d(_simple_hist())
        res = pe.export_figure(
            fig,
            tmp_path / name,
            tether_export="plot:a1",
            source="s.tether",
            parameters={},
            created_utc=stamp,
        )
        out[name] = res.paths["svg"].read_bytes()
    assert out["first"] == out["second"]


def test_no_stamp_option_omits_visible_footer(tmp_path):
    fig = pe.render_histogram1d(_simple_hist())
    result = pe.export_figure(
        fig,
        tmp_path / "bare",
        tether_export="plot:a1",
        source="s.tether",
        parameters={},
        formats=("svg",),
        stamp=False,
        created_utc="2026-07-10T00:00:00+00:00",
    )
    # No visible footer artist was added to the figure ...
    assert not any(t.get_text().startswith("Tether") for t in fig.texts)
    # ... but the machine-readable sidecar still stamps provenance (it always travels).
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["created_utc"] == "2026-07-10T00:00:00+00:00"


# --------------------------------------------------------------- per-plot renderers
#
# Each renderer test reads the artists of the returned Figure, not only the exported files:
# a file with the right signature can hold a mirrored histogram or a transposed TDP (#729
# PRJ-F7). The values are compared with the analysis result that was rendered, and each
# orientation is also checked against the fixture itself (STATES / MEANS), so a renderer that
# reorders the data and a test helper that reorders it the same way cannot agree by accident.


def test_render_histogram1d_with_ci_and_overlay(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    ci = population_apparent_e_histogram_ci(proj, n_resamples=50)
    overlay = population_model_gaussian_overlay(proj, "vbconhmm")
    fig = pe.render_histogram1d(ci, overlay=overlay)
    assert isinstance(fig, Figure)
    ax = fig.axes[0]
    # The bars are the histogram counts, bin for bin, on the histogram's own edges.
    (bars,) = [p for p in ax.patches if isinstance(p, StepPatch)]
    drawn = bars.get_data()
    np.testing.assert_array_equal(drawn.values, ci.histogram.counts)
    np.testing.assert_array_equal(drawn.edges, ci.histogram.bin_edges)
    # Orientation against the fixture: every state level has a filled bar, and the mirror
    # image of each level about E = 0.5 (not a level of the fixture) has none.
    edges = np.asarray(drawn.edges)
    for level in MEANS:
        assert drawn.values[np.searchsorted(edges, level, side="right") - 1] > 0, level
        assert drawn.values[np.searchsorted(edges, 1.0 - level, side="right") - 1] == 0, level
    # The model overlay: the total curve, then one dashed curve per state component.
    total, *components = ax.lines
    np.testing.assert_array_equal(total.get_xdata(), overlay.x)
    np.testing.assert_array_equal(total.get_ydata(), overlay.total)
    assert len(components) == len(MEANS)
    for line, comp in zip(components, np.asarray(overlay.components), strict=True):
        np.testing.assert_array_equal(line.get_ydata(), comp)
    assert ax.get_xlim() == pytest.approx(ci.histogram.value_range)
    assert ax.get_xlabel() == "Apparent FRET efficiency $E$"
    assert ax.get_ylabel() == ("Density" if ci.histogram.density else "Counts")
    result = pe.export_figure(
        fig, tmp_path / "a1full", tether_export="plot:a1", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_histogram2d(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    h = population_time_signal_histogram2d(proj, time_bins=6)
    fig = pe.render_histogram2d(h)
    ax = fig.axes[0]
    drawn, x_edges, y_edges = _mesh(ax)
    # counts is [time, signal]; the figure shows time on x and E on y.
    np.testing.assert_array_equal(x_edges, h.time_edges)
    np.testing.assert_array_equal(y_edges, h.signal_edges)
    np.testing.assert_array_equal(drawn, np.asarray(h.counts).T)
    # Against the fixture: frame 0 holds one molecule in each state, so the first time
    # column is filled at each state level and empty at a level no molecule takes.
    t0 = 0.5 * (x_edges[0] + x_edges[1])
    for level in MEANS:
        assert _value_at(drawn, x_edges, y_edges, t0, level) > 0, level
    assert _value_at(drawn, x_edges, y_edges, t0, 0.4) == 0
    assert ax.get_xlabel() == "Frame"
    assert ax.get_ylabel() == "Apparent FRET efficiency $E$"
    assert ax.get_ylim() == pytest.approx(h.signal_range)
    result = pe.export_figure(
        fig, tmp_path / "a2", tether_export="plot:a2", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_transition_density(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    tdp = population_transition_density(proj, "vbconhmm")
    assert tdp.n_transitions > 0
    fig = pe.render_transition_density(tdp)
    ax = fig.axes[0]
    drawn, x_edges, y_edges = _mesh(ax)
    # counts is [initial, final]; the figure shows initial E on x and final E on y.
    np.testing.assert_array_equal(x_edges, tdp.signal_edges)
    np.testing.assert_array_equal(y_edges, tdp.signal_edges)
    np.testing.assert_array_equal(drawn, np.asarray(tdp.counts).T)
    # Against the fixture: STATES has more 0 -> 1 than 1 -> 0 transitions, so the cell at
    # (initial = level 0, final = level 1) must be the brighter of the two mirror cells. A
    # transposed plot swaps them.
    forward = _value_at(drawn, x_edges, y_edges, MEANS[0], MEANS[1])
    backward = _value_at(drawn, x_edges, y_edges, MEANS[1], MEANS[0])
    assert forward > backward > 0
    assert ax.get_xlabel() == "Initial $E$"
    assert ax.get_ylabel() == "Final $E$"
    assert ax.get_xlim() == pytest.approx(tdp.signal_range)
    assert ax.get_ylim() == pytest.approx(tdp.signal_range)
    result = pe.export_figure(
        fig, tmp_path / "b1", tether_export="plot:tdp", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_dwell_survival(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    analysis = population_dwell_times(proj, "vbconhmm", state=1)
    fig = pe.render_dwell_survival(analysis)
    ax = fig.axes[0]
    # The survival markers are S(t) at each dwell time (clipped at 1e-6 for the log axis).
    survival = ax.lines[0]
    np.testing.assert_array_equal(survival.get_xdata(), analysis.tau)
    np.testing.assert_array_equal(
        survival.get_ydata(), np.clip(np.asarray(analysis.survival), 1e-6, None)
    )
    assert ax.get_yscale() == "log"
    assert ax.get_ylabel() == "Survival $S(t)$"
    if analysis.fit is not None and analysis.fit.success:
        fit_line = ax.lines[1]
        np.testing.assert_array_equal(fit_line.get_xdata(), analysis.fit.tau)
        np.testing.assert_array_equal(
            fit_line.get_ydata(), np.clip(np.asarray(analysis.fit.model_survival), 1e-6, None)
        )
    else:
        assert len(ax.lines) == 1
        assert ax.get_xlabel() == "Dwell time (frames)"
    result = pe.export_figure(
        fig, tmp_path / "b2", tether_export="plot:dwell", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_transition_prob(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    h = population_transition_prob_histogram(proj, "vbconhmm", 0, 1)
    fig = pe.render_transition_prob(h)
    ax = fig.axes[0]
    (bars,) = [p for p in ax.patches if isinstance(p, StepPatch)]
    drawn = bars.get_data()
    np.testing.assert_array_equal(drawn.values, h.counts)
    np.testing.assert_array_equal(drawn.edges, h.edges)
    assert drawn.values.sum() > 0
    if h.kde_x is not None:
        (kde,) = ax.lines
        np.testing.assert_array_equal(kde.get_xdata(), h.kde_x)
        np.testing.assert_array_equal(kde.get_ydata(), h.kde_y)
    assert ax.get_xlim() == pytest.approx(h.prob_range)
    assert ax.get_xlabel() == "$P(0\\to1)$ per molecule"
    result = pe.export_figure(
        fig, tmp_path / "b3", tether_export="plot:tprob", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_state_number(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    counts = population_state_number(proj, "vbconhmm")
    fig = pe.render_state_number(counts)
    ax = fig.axes[0]
    bars = [p for p in ax.patches if isinstance(p, Rectangle)]
    drawn = {round(b.get_x() + b.get_width() / 2): b.get_height() for b in bars}
    assert drawn == dict(zip(counts.states.tolist(), counts.counts.tolist(), strict=True))
    # Against the fixture: each of the three molecules visits all three states.
    assert drawn[3] == STATES.shape[0]
    assert ax.get_xlabel() == "Number of occupied states"
    assert ax.get_ylabel() == "Molecules"
    result = pe.export_figure(
        fig, tmp_path / "c1", tether_export="plot:statenum", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_raw_fret_cloud(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    cloud = population_raw_fret_cloud(proj)
    fig = pe.render_raw_fret_cloud(cloud)
    ax = fig.axes[0]
    # The scatter is the pooled (time, E) points, unthinned below max_points.
    (scatter,) = [c for c in ax.collections if isinstance(c, PathCollection)]
    np.testing.assert_array_equal(scatter.get_offsets(), cloud.points)
    # Against the fixture: every plotted E is one of the state levels.
    assert np.isin(np.round(cloud.points[:, 1], 9), np.round(MEANS, 9)).all()
    assert cloud.density is not None
    drawn, x_edges, y_edges = _mesh(ax)
    np.testing.assert_array_equal(x_edges, cloud.time_edges)
    np.testing.assert_array_equal(y_edges, cloud.signal_edges)
    np.testing.assert_array_equal(drawn, np.asarray(cloud.density).T)
    assert ax.get_xlabel() == "Frame"
    assert ax.get_ylabel() == "Apparent FRET efficiency $E$"
    result = pe.export_figure(
        fig, tmp_path / "cloud", tether_export="plot:cloud", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_cross_correlation(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    xc = population_cross_correlation(proj)
    fig = pe.render_cross_correlation(xc)
    ax = fig.axes[0]
    # Two reference lines (y = 0, lag = 0), then the curve itself.
    curve = ax.lines[-1]
    np.testing.assert_array_equal(curve.get_xdata(), xc.lags)
    np.testing.assert_array_equal(curve.get_ydata(), xc.values)
    assert ax.get_xlabel() == "Lag (frames)"
    assert ax.get_ylabel() == f"Cross-correlation ({xc.normalize})"
    result = pe.export_figure(
        fig, tmp_path / "xcorr", tether_export="plot:xcorr", source="s", parameters={}
    )
    _assert_valid(result)
