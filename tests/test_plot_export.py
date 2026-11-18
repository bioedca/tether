# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for :mod:`tether.analysis.plot_export` — vector/raster plot export (§7.9).

Covers the export mechanism (three formats + provenance stamp) and that each M6
analysis plot-data dataclass renders to a figure that round-trips through the export.
Gated on ``h5py``/``scipy`` so the lightweight headless tiers skip it. ``matplotlib`` is a
plain import: the base stack requires it for the plot export, and an ``importorskip`` made
an install without it one skip and a green run with no plot-export test (#726).
"""

from __future__ import annotations

import doctest
import inspect
import json
from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")

# A plain import, not importorskip: a missing matplotlib must fail the run (#726).
from matplotlib.collections import (  # noqa: E402
    LineCollection,
    PathCollection,
    PolyCollection,
    QuadMesh,
)
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import Rectangle, StepPatch  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from tether.analysis import plot_export as pe  # noqa: E402
from tether.analysis.anticorrelation import (  # noqa: E402
    MoleculeAnticorrelation,
    PopulationAnticorrelation,
    find_anticorrelation_events,
)
from tether.analysis.cloud import (  # noqa: E402
    AlphaShape,
    StateNumberElbow,
    alpha_shape,
    k_rmse_elbow,
    population_fret_cloud_alpha_shape,
    population_raw_fret_cloud,
)
from tether.analysis.crosscorr import population_cross_correlation  # noqa: E402
from tether.analysis.dwell import population_dwell_times  # noqa: E402
from tether.analysis.histogram import (  # noqa: E402
    ConditionHistogram,
    PerConditionHistograms,
    apparent_e_histogram,
    population_apparent_e_histogram_ci,
    population_model_gaussian_overlay,
    population_time_signal_histogram2d,
    transition_sync_histogram2d,
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


def _tdp_with_empty_bins():
    """A tiny TDP: one dominant cell (40 counts), one weak cell (1 count), the rest empty."""
    from tether.analysis.tdp import transition_density

    # 0.2 -> 0.8 forty times (one transition per molecule), 0.8 -> 0.2 once.
    up = [np.array([0.2, 0.2, 0.8, 0.8, 0.8])] * 40
    down = [np.array([0.8, 0.8, 0.2, 0.2, 0.2])]
    tdp = transition_density(up + down)
    counts = np.asarray(tdp.counts)
    assert counts.max() == 40 and np.count_nonzero(counts) == 2 and (counts == 0).any()
    return tdp


def test_render_transition_density_defaults_to_a_zero_safe_log_scale(tmp_path):
    """#556: PRD Appendix C B1 names a log-normalized TDP. The default colour scale is log,
    so a 1-count transition next to a 40-count one is still visibly coloured, and empty bins
    are drawn in the colour map's floor colour rather than becoming -inf or a hole."""
    from matplotlib.colors import LogNorm

    tdp = _tdp_with_empty_bins()
    fig = pe.render_transition_density(tdp)
    ax = fig.axes[0]
    mesh = next(c for c in ax.collections if isinstance(c, QuadMesh))
    assert isinstance(mesh.norm, LogNorm)
    drawn, x_edges, y_edges = _mesh(ax)
    # The stored histogram is drawn unchanged: the log is display only (NFR-REPRO).
    np.testing.assert_array_equal(drawn, np.asarray(tdp.counts).T)
    weak = _value_at(drawn, x_edges, y_edges, 0.8, 0.2)
    strong = _value_at(drawn, x_edges, y_edges, 0.2, 0.8)
    assert (weak, strong) == (1, 40)
    floor_rgba = np.asarray(mesh.cmap(0.0))
    rgba = mesh.to_rgba(np.asarray([[0.0, weak, strong]]))[0]
    assert np.all(np.isfinite(rgba))
    # The empty bin takes the floor colour, opaque.
    np.testing.assert_allclose(rgba[0], floor_rgba)
    assert rgba[0][3] == 1.0
    # The weakest transition is not the floor colour an empty bin takes: the log floor sits
    # one decade below it, so 1 count of 40 is drawn 1/log10(400) ~ 38 % up the colour map,
    # where a linear scale would put it at 2.5 %.
    assert mesh.norm(1.0) == pytest.approx(1.0 / np.log10(400.0))
    assert mesh.norm(1.0) > 0.3
    assert not np.allclose(rgba[1], floor_rgba)
    assert mesh.norm(40.0) == pytest.approx(1.0)
    result = pe.export_figure(
        fig, tmp_path / "b1", tether_export="plot:b1", source="s", parameters={}
    )
    _assert_valid(result)
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["display"] == {"color_scale": "log"}


def test_render_transition_density_linear_scale_is_selectable_and_recorded(tmp_path):
    from matplotlib.colors import LogNorm

    tdp = _tdp_with_empty_bins()
    fig = pe.render_transition_density(tdp, color_scale="linear")
    mesh = next(c for c in fig.axes[0].collections if isinstance(c, QuadMesh))
    assert not isinstance(mesh.norm, LogNorm)
    assert mesh.norm.vmin == 0.0 and mesh.norm.vmax == 40.0
    result = pe.export_figure(
        fig, tmp_path / "b1", tether_export="plot:b1", source="s", parameters={"nskip": 2}
    )
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["display"] == {"color_scale": "linear"}
    assert payload["parameters"]["nskip"] == 2


def test_render_transition_density_log_scale_survives_an_empty_tdp(tmp_path):
    """No transitions at all: every bin is empty, and the log scale must still render."""
    from tether.analysis.tdp import transition_density

    tdp = transition_density([np.full(6, 0.5)])
    assert tdp.n_transitions == 0
    fig = pe.render_transition_density(tdp)
    mesh = next(c for c in fig.axes[0].collections if isinstance(c, QuadMesh))
    rgba = mesh.to_rgba(np.asarray(tdp.counts))
    assert np.all(np.isfinite(rgba))
    np.testing.assert_allclose(rgba.reshape(-1, 4)[0], mesh.cmap(0.0))
    result = pe.export_figure(
        fig, tmp_path / "b1", tether_export="plot:b1", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_transition_density_draws_the_smoothed_view(tmp_path):
    """#1029: the smoothed view is drawn as checkTDP.py draws it — an image on its
    interpolation grid, initial E on x, final E on y — and its settings are recorded."""
    from matplotlib.colors import LogNorm
    from matplotlib.image import AxesImage

    from tether.analysis.tdp import smooth_transition_density

    tdp = _tdp_with_empty_bins()
    counts_before = np.asarray(tdp.counts).copy()
    view = smooth_transition_density(tdp)
    fig = pe.render_transition_density(view)
    ax = fig.axes[0]
    assert not [c for c in ax.collections if isinstance(c, QuadMesh)]
    (image,) = [i for i in ax.get_images() if isinstance(i, AxesImage)]
    # values is [initial, final]; the image rows are final E (origin lower), columns initial E.
    np.testing.assert_array_equal(np.asarray(image.get_array()), view.values.T)
    assert image.origin == "lower"
    step = view.grid[1] - view.grid[0]
    lo, hi = tdp.signal_range
    np.testing.assert_allclose(image.get_extent(), [lo - step / 2, hi + step / 2] * 2)
    assert ax.get_xlim() == pytest.approx(tdp.signal_range)
    # The peak is at (initial 0.2, final 0.8): the 40-count transition, not its mirror.
    rows, cols = np.unravel_index(np.argmax(image.get_array()), view.values.T.shape)
    assert abs(view.grid[cols] - 0.2) < 0.02 and abs(view.grid[rows] - 0.8) < 0.02
    # Log floor at 1e-4 of the peak (checkTDP.py's color_floor on its normalized array).
    assert isinstance(image.norm, LogNorm)
    assert image.norm.vmax == pytest.approx(1.0)
    assert image.norm.vmin == pytest.approx(1e-4)
    assert fig.axes[1].get_ylabel() == "Normalized density"
    # The counts underneath are untouched (NFR-REPRO).
    np.testing.assert_array_equal(tdp.counts, counts_before)
    result = pe.export_figure(
        fig, tmp_path / "b1s", tether_export="plot:b1", source="s", parameters={}
    )
    _assert_valid(result)
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["display"] == {
        "color_scale": "log",
        "smoothing": {"smooth": [1.0, 1.0], "interp_res": 800, "normalized": True},
    }


def test_render_smoothed_view_linear_and_unnormalized(tmp_path):
    from tether.analysis.tdp import smooth_transition_density

    tdp = _tdp_with_empty_bins()
    view = smooth_transition_density(tdp, interp_res=0, normalize=False)
    fig = pe.render_transition_density(view, color_scale="linear")
    (image,) = fig.axes[0].get_images()
    assert image.norm.vmin == 0.0
    assert image.norm.vmax == pytest.approx(float(view.values.max()))
    assert fig.axes[1].get_ylabel() == "Smoothed counts"
    result = pe.export_figure(
        fig, tmp_path / "b1s", tether_export="plot:b1", source="s", parameters={}
    )
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["display"]["smoothing"] == {
        "smooth": [1.0, 1.0],
        "interp_res": 0,
        "normalized": False,
    }


def test_render_smoothed_view_of_one_bin_fills_the_signal_range():
    from tether.analysis.tdp import smooth_transition_density, transition_density

    tdp = transition_density(
        [np.array([0.2, 0.8])], nskip=1, signal_bins=1, signal_range=(-0.25, 1.25)
    )
    view = smooth_transition_density(tdp, interp_res=0)
    assert view.grid.size == 1
    fig = pe.render_transition_density(view)
    (image,) = fig.axes[0].get_images()
    np.testing.assert_allclose(image.get_extent(), [-0.25, 1.25, -0.25, 1.25])


def test_render_smoothed_view_of_an_empty_tdp(tmp_path):
    from tether.analysis.tdp import smooth_transition_density, transition_density

    view = smooth_transition_density(transition_density([np.full(6, 0.5)]))
    fig = pe.render_transition_density(view)
    (image,) = fig.axes[0].get_images()
    rgba = image.to_rgba(view.values)
    assert np.all(np.isfinite(rgba))
    result = pe.export_figure(
        fig, tmp_path / "b1s", tether_export="plot:b1", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_transition_density_rejects_an_unknown_color_scale():
    tdp = _tdp_with_empty_bins()
    with pytest.raises(ValueError, match="color_scale"):
        pe.render_transition_density(tdp, color_scale="sqrt")


def test_export_refuses_a_display_record_that_contradicts_the_figure(tmp_path):
    """The figure, not the caller, says how it was drawn: a caller-supplied ``display``
    entry that disagrees would make the provenance describe a different picture."""
    fig = pe.render_transition_density(_tdp_with_empty_bins())
    with pytest.raises(ValueError, match="display"):
        pe.export_figure(
            fig,
            tmp_path / "b1",
            tether_export="plot:b1",
            source="s",
            parameters={"display": {"color_scale": "linear"}},
        )
    # Agreeing with the figure is allowed and changes nothing.
    result = pe.export_figure(
        fig,
        tmp_path / "b1",
        tether_export="plot:b1",
        source="s",
        parameters={"display": {"color_scale": "log"}},
    )
    payload = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert payload["parameters"]["display"] == {"color_scale": "log"}


def test_render_dwell_survival(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    analysis = population_dwell_times(proj, "vbconhmm", state=1)[1]
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


# ------------------------------------------------- the five renderers of #370


def _two_condition_overlay():
    """Two conditions binned on one shared grid, as ``per_condition_apparent_e_histograms``."""
    rng = np.random.default_rng(1)
    low = replace(apparent_e_histogram(rng.normal(0.3, 0.05, 300), bins=40), n_molecules=4)
    high = replace(apparent_e_histogram(rng.normal(0.7, 0.05, 500), bins=40), n_molecules=7)
    return PerConditionHistograms(
        histograms=(ConditionHistogram("low", low), ConditionHistogram("high", high)),
        bin_edges=low.bin_edges,
        value_range=low.value_range,
        density=True,
        per_molecule_equal_weight=False,
        intensity_quantity="corrected",
    )


def test_render_per_condition_histograms_overlays_conditions_on_one_axis(tmp_path):
    overlay = _two_condition_overlay()
    fig = pe.render_per_condition_histograms(overlay)
    assert len(fig.axes) == 1  # the overlay is ONE A1 axis, never one panel per condition
    ax = fig.axes[0]
    steps = [p for p in ax.patches if isinstance(p, StepPatch)]
    assert len(steps) == overlay.n_conditions == 2
    # In the overlay's own order, each curve on the shared grid with its own counts.
    for step, ch in zip(steps, overlay.histograms, strict=True):
        values, edges, _baseline = step.get_data()
        np.testing.assert_array_equal(edges, overlay.bin_edges)
        np.testing.assert_array_equal(values, ch.histogram.counts)
        assert not step.get_fill()
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    assert labels == ["low (N = 4)", "high (N = 7)"]
    # Against the inputs: the low condition peaks below the high one.
    peak = [overlay.bin_centers[np.argmax(ch.histogram.counts)] for ch in overlay.histograms]
    assert peak[0] < 0.5 < peak[1]
    assert ax.get_xlim() == pytest.approx(overlay.value_range)
    assert ax.get_xlabel() == "Apparent FRET efficiency $E$"
    assert ax.get_ylabel() == "Density"
    assert any("N = 11" in t.get_text() for t in ax.texts)
    result = pe.export_figure(
        fig, tmp_path / "a1cond", tether_export="plot:a1-conditions", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_per_condition_histograms_keeps_forty_conditions_distinct():
    base = apparent_e_histogram(np.linspace(0.1, 0.9, 50), bins=20)
    overlay = PerConditionHistograms(
        histograms=tuple(ConditionHistogram(f"c{i}", base) for i in range(41)),
        bin_edges=base.bin_edges,
        value_range=base.value_range,
        density=True,
        per_molecule_equal_weight=False,
        intensity_quantity="corrected",
    )
    steps = [
        p
        for p in pe.render_per_condition_histograms(overlay).axes[0].patches
        if isinstance(p, StepPatch)
    ]
    styles = [(tuple(s.get_edgecolor()), str(s.get_linestyle())) for s in steps]
    # The documented bound: 10 colours x 4 line styles give 40 distinct curves, and
    # condition 41 is drawn like condition 1 (only its legend label tells them apart).
    assert len(set(styles[:40])) == 40
    assert styles[40] == styles[0]
    labels = [t.get_text() for t in steps[0].axes.get_legend().get_texts()]
    assert labels[0] != labels[40]


def test_render_per_condition_histograms_refuses_a_curve_off_the_shared_grid():
    overlay = _two_condition_overlay()
    other = apparent_e_histogram(np.linspace(0.1, 0.9, 50), bins=10)
    bad = replace(overlay, histograms=(*overlay.histograms, ConditionHistogram("odd", other)))
    with pytest.raises(ValueError, match="'odd' is not binned on the overlay's shared grid"):
        pe.render_per_condition_histograms(bad)


def test_render_per_condition_histograms_with_no_condition_says_so():
    overlay = replace(_two_condition_overlay(), histograms=())
    ax = pe.render_per_condition_histograms(overlay).axes[0]
    assert not [p for p in ax.patches if isinstance(p, StepPatch)]
    assert ax.get_legend() is None
    assert any(t.get_text() == "No condition has a queried molecule" for t in ax.texts)


def _sync_pairs():
    """Two molecules that each jump 0 → 1 once, so the sync column has real data."""
    path_a = np.array([0] * 6 + [1] * 6, dtype="int64")
    path_b = np.array([0] * 4 + [1] * 8, dtype="int64")
    signal_a = np.where(path_a == 0, 0.2, 0.8).astype("float64")
    signal_b = np.where(path_b == 0, 0.2, 0.8).astype("float64")
    return [(path_a, signal_a), (path_b, signal_b)]


def test_render_transition_sync_histogram2d(tmp_path):
    h = transition_sync_histogram2d(
        _sync_pairs(), from_state=0, to_state=1, sync_preframe=5, time_bins=10
    )
    fig = pe.render_transition_sync_histogram2d(h)
    ax = fig.axes[0]
    drawn, x_edges, y_edges = _mesh(ax)
    np.testing.assert_array_equal(x_edges, h.time_edges)
    np.testing.assert_array_equal(y_edges, h.signal_edges)
    np.testing.assert_array_equal(drawn, np.asarray(h.counts).T)
    # The time axis is relative to the transition: it runs negative before it, and the
    # dotted guide sits at relative time zero, the centre of the sync column.
    assert x_edges[0] < 0.0 < x_edges[-1]
    assert h.time_centers[h.sync_preframe] == 0.0
    (guide,) = [ln for ln in ax.lines if list(ln.get_xdata()) == [0.0, 0.0]]
    assert guide.get_linestyle() == ":"
    # Against the fixture: before the jump the signal sits at 0.2, after it at 0.8.
    assert _value_at(drawn, x_edges, y_edges, -1.0, 0.2) > 0
    assert _value_at(drawn, x_edges, y_edges, -1.0, 0.8) == 0
    assert _value_at(drawn, x_edges, y_edges, 1.0, 0.8) > 0
    assert _value_at(drawn, x_edges, y_edges, 1.0, 0.2) == 0
    assert ax.get_xlabel() == "Frames from transition"
    assert ax.get_ylabel() == "Apparent FRET efficiency $E$"
    assert ax.get_ylim() == pytest.approx(h.signal_range)
    (note,) = [t.get_text() for t in ax.texts if "transitions" in t.get_text()]
    assert note == "0 → 1 (single dwell) · 2 transitions · N = 2"
    result = pe.export_figure(
        fig, tmp_path / "a2sync", tether_export="plot:a2-sync", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_transition_sync_histogram2d_labels_seconds_and_any_state():
    h = transition_sync_histogram2d(_sync_pairs(), time_dt=0.1, single_dwell=False)
    ax = pe.render_transition_sync_histogram2d(h).axes[0]
    assert ax.get_xlabel() == "Time from transition (s)"
    assert any(t.get_text().startswith("any → any (fixed window)") for t in ax.texts)


def _grid_points() -> np.ndarray:
    """A filled unit square of ``(time, E)`` points, 11 × 11."""
    t, e = np.meshgrid(np.linspace(0.0, 1.0, 11), np.linspace(0.0, 1.0, 11))
    return np.column_stack([t.ravel(), e.ravel()])


def _segments(ax) -> list[np.ndarray]:
    return [
        seg for c in ax.collections if isinstance(c, LineCollection) for seg in c.get_segments()
    ]


def test_render_alpha_shape_draws_the_boundary_it_was_given(tmp_path):
    shape = alpha_shape(_grid_points(), alpha=10.0)
    assert shape is not None and not shape.is_empty
    fig = pe.render_alpha_shape(shape)
    ax = fig.axes[0]
    drawn = np.asarray(_segments(ax))
    np.testing.assert_array_equal(drawn, shape.boundary_edges)
    # Geometry: at this α the shape is the hull of the square, so every boundary
    # segment lies on the square's perimeter, every vertex closes the loop (degree 2),
    # and the segments add up to the perimeter of 4.
    on_edge = np.isclose(drawn, 0.0) | np.isclose(drawn, 1.0)
    assert (on_edge[:, :, 0].all(axis=1) & (drawn[:, 0, 0] == drawn[:, 1, 0])).sum() + (
        on_edge[:, :, 1].all(axis=1) & (drawn[:, 0, 1] == drawn[:, 1, 1])
    ).sum() == drawn.shape[0]
    _vertices, degree = np.unique(drawn.reshape(-1, 2), axis=0, return_counts=True)
    assert (degree == 2).all()
    lengths = np.linalg.norm(drawn[:, 1] - drawn[:, 0], axis=1)
    assert lengths.sum() == pytest.approx(4.0)
    assert ax.get_xlabel() == "Frame"
    assert ax.get_ylabel() == "Apparent FRET efficiency $E$"
    assert any(t.get_text().startswith("α = 10 · ") for t in ax.texts)
    result = pe.export_figure(
        fig, tmp_path / "alpha", tether_export="plot:alpha", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_alpha_shape_with_no_support_draws_nothing_and_says_why():
    starved = alpha_shape(_grid_points(), alpha=1e-6)
    assert starved is not None and starved.n_kept == 0 and starved.spans_region
    ax = pe.render_alpha_shape(starved).axes[0]
    assert _segments(ax) == []
    assert any(t.get_text().startswith("No support at α = 1e-06") for t in ax.texts)

    flat = AlphaShape.no_region(2)
    ax = pe.render_alpha_shape(flat).axes[0]
    assert _segments(ax) == []
    assert any(
        t.get_text().startswith("No 2-D region: 2 points cannot be triangulated") for t in ax.texts
    )


def test_render_alpha_shape_over_its_store_cloud(tmp_path):
    proj, _keys = build_store_with_model(tmp_path, STATES, MEANS)
    cloud = population_raw_fret_cloud(proj, kde=False)
    shape = population_fret_cloud_alpha_shape(proj)
    fig = pe.render_alpha_shape(shape, cloud=cloud)
    ax = fig.axes[0]
    (scatter,) = [c for c in ax.collections if isinstance(c, PathCollection)]
    np.testing.assert_array_equal(scatter.get_offsets(), cloud.points)
    drawn = _segments(ax)
    assert len(drawn) == shape.n_boundary_edges
    if drawn:
        np.testing.assert_array_equal(np.asarray(drawn), shape.boundary_edges)
    assert ax.get_xlim() == pytest.approx(cloud.time_range)
    assert ax.get_ylim() == pytest.approx(cloud.signal_range)
    with pytest.raises(ValueError, match="contradicts the cloud's time_dt"):
        pe.render_alpha_shape(shape, cloud=cloud, time_dt=0.05)
    assert pe.render_alpha_shape(shape, time_dt=0.05).axes[0].get_xlabel() == "Time (s)"


def test_render_state_number_elbow(tmp_path):
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(m, 0.02, 200) for m in (0.2, 0.5, 0.8)])
    elbow = k_rmse_elbow(values, k_max=8)
    assert elbow.elbow_k == 3
    fig = pe.render_state_number_elbow(elbow)
    ax = fig.axes[0]
    curve, chord, guide, marker = ax.lines
    np.testing.assert_array_equal(curve.get_xdata(), elbow.k_values)
    np.testing.assert_array_equal(curve.get_ydata(), elbow.rmse)
    # Geometry: the chord joins the curve's first and last points, the guide is the
    # vertical at elbow_k, and the marker sits on the curve at the elbow.
    np.testing.assert_array_equal(chord.get_xdata(), elbow.k_values[[0, -1]])
    np.testing.assert_array_equal(chord.get_ydata(), elbow.rmse[[0, -1]])
    assert list(guide.get_xdata()) == [3.0, 3.0]
    at = int(np.flatnonzero(elbow.k_values == 3)[0])
    assert list(marker.get_xdata()) == [3]
    assert list(marker.get_ydata()) == [elbow.rmse[at]]
    # The elbow point lies below the chord: the knee of a decreasing convex curve.
    k0, k1 = elbow.k_values[[0, -1]]
    r0, r1 = elbow.rmse[[0, -1]]
    assert elbow.rmse[at] < r0 + (r1 - r0) * (3 - k0) / (k1 - k0)
    np.testing.assert_array_equal(ax.get_xticks(), elbow.k_values)
    assert ax.get_xlabel() == "Number of clusters $k$"
    assert ax.get_ylabel() == "Within-cluster RMSE of $E$"
    assert any("Suggested k = 3 (hint" in t.get_text() for t in ax.texts)
    result = pe.export_figure(
        fig, tmp_path / "elbow", tether_export="plot:elbow", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_state_number_elbow_without_an_elbow_marks_none():
    flat = StateNumberElbow(
        k_values=np.array([1, 2], dtype=np.int64),
        rmse=np.array([0.2, 0.1]),
        elbow_k=None,
        n_samples=10,
        seed=0,
    )
    ax = pe.render_state_number_elbow(flat).axes[0]
    assert len(ax.lines) == 2  # the curve and its chord, no guide and no marker
    assert any(t.get_text().startswith("No elbow") for t in ax.texts)
    empty = replace(flat, k_values=np.empty(0, dtype=np.int64), rmse=np.empty(0))
    assert len(pe.render_state_number_elbow(empty).axes[0].lines) == 0


def _anticorr_population() -> PopulationAnticorrelation:
    """One molecule with a localized anti-phase burst, one correlated with no event."""
    t = np.arange(120, dtype="float64")
    donor = 500.0 + 3.0 * t
    acceptor = 0.5 * donor + 100.0
    osc = 120.0 * np.sin(2.0 * np.pi * (t[40:80] - 40) / 12.0)
    donor[40:80] = 500.0 + osc
    acceptor[40:80] = 500.0 - osc
    burst = find_anticorrelation_events(donor, acceptor)
    quiet = find_anticorrelation_events(500.0 + 3.0 * t, 0.5 * (500.0 + 3.0 * t) + 100.0)
    assert burst.n_events >= 1 and quiet.n_events == 0
    return PopulationAnticorrelation(
        molecules=(MoleculeAnticorrelation("m0", burst), MoleculeAnticorrelation("m1", quiet)),
        window=burst.window,
        step=burst.step,
        min_magnitude=burst.min_magnitude,
        min_windows=burst.min_windows,
        n_molecules=2,
        n_events=burst.n_events,
    )


def test_render_population_anticorrelation(tmp_path):
    population = _anticorr_population()
    fig = pe.render_population_anticorrelation(population)
    ax = fig.axes[0]
    events = population.molecules[0].scan.events
    bars = [c for c in ax.collections if isinstance(c, PolyCollection)]
    assert len(bars) == 1  # only the molecule with events gets bars
    spans = []
    for path in bars[0].get_paths():
        xs, ys = path.vertices[:, 0], path.vertices[:, 1]
        spans.append((xs.min(), xs.max()))
        assert ys.min() == pytest.approx(-0.35) and ys.max() == pytest.approx(0.35)  # row 0
    assert spans == [(float(e.start), float(e.stop)) for e in events]
    (peaks,) = [ln for ln in ax.lines if ln.get_marker() == "|"]
    np.testing.assert_array_equal(peaks.get_xdata(), [e.peak_frame for e in events])
    np.testing.assert_array_equal(peaks.get_ydata(), [0.0] * len(events))
    # Against the fixture: the event sits inside the injected burst.
    assert all(e.start >= 30 and e.stop <= 90 for e in events)
    # Both molecules keep a row, top to bottom in store order.
    assert [t.get_text() for t in ax.get_yticklabels()] == ["m0", "m1"]
    assert ax.get_ylim() == pytest.approx((1.5, -0.5))
    assert ax.get_xlim() == pytest.approx((0.0, 120.0))
    assert ax.get_xlabel() == "Frame (analysis window)"
    assert ax.get_ylabel() == "Molecule"
    assert population.n_events == 1  # the burst is one event, and the note says "1 event"
    assert any(t.get_text() == f"1 event · N = 2 · window {population.window}" for t in ax.texts)
    result = pe.export_figure(
        fig, tmp_path / "anticorr", tether_export="plot:anticorr", source="s", parameters={}
    )
    _assert_valid(result)


def test_render_population_anticorrelation_with_no_molecule_says_so():
    empty = replace(_anticorr_population(), molecules=(), n_molecules=0, n_events=0)
    ax = pe.render_population_anticorrelation(empty).axes[0]
    assert not [c for c in ax.collections if isinstance(c, PolyCollection)]
    assert any(t.get_text() == "No molecule was long enough to scan" for t in ax.texts)


def _five_new_figures():
    return {
        "a1-conditions": lambda: pe.render_per_condition_histograms(_two_condition_overlay()),
        "a2-sync": lambda: pe.render_transition_sync_histogram2d(
            transition_sync_histogram2d(_sync_pairs())
        ),
        "alpha": lambda: pe.render_alpha_shape(alpha_shape(_grid_points(), alpha=10.0)),
        "elbow": lambda: pe.render_state_number_elbow(
            k_rmse_elbow(np.repeat([0.2, 0.5, 0.8], 50) + np.tile([0.0, 0.01], 75))
        ),
        "anticorr": lambda: pe.render_population_anticorrelation(_anticorr_population()),
    }


@pytest.mark.parametrize("kind", sorted(_five_new_figures()))
def test_new_renderers_are_public_bare_and_byte_reproducible(tmp_path, kind):
    render = _five_new_figures()[kind]
    fig = render()
    assert isinstance(fig, Figure)
    # A bare figure: no provenance footer until export_figure stamps one.
    assert not any(t.get_text().startswith("Tether ") for t in fig.texts)
    pinned = "2026-01-01T00:00:00+00:00"
    first = pe.export_figure(
        fig,
        tmp_path / "a",
        tether_export=f"plot:{kind}",
        source="s",
        parameters={},
        created_utc=pinned,
    )
    second = pe.export_figure(
        render(),
        tmp_path / "b",
        tether_export=f"plot:{kind}",
        source="s",
        parameters={},
        created_utc=pinned,
    )
    _assert_valid(first)
    for fmt in pe.DEFAULT_PLOT_FORMATS:
        assert first.paths[fmt].read_bytes() == second.paths[fmt].read_bytes(), fmt
    sidecar = json.loads(first.provenance_path.read_text(encoding="utf-8"))
    assert sidecar["tether_export"] == f"plot:{kind}"


def test_the_five_renderers_are_exported():
    for name in (
        "render_per_condition_histograms",
        "render_transition_sync_histogram2d",
        "render_alpha_shape",
        "render_state_number_elbow",
        "render_population_anticorrelation",
    ):
        assert name in pe.__all__
        assert callable(getattr(pe, name))


# ------------------------------------------------- the docstring examples (#1172)

_SECTIONS = ("Parameters", "Returns", "Raises", "Examples")


def _public_functions() -> list[str]:
    return [name for name in pe.__all__ if inspect.isfunction(getattr(pe, name))]


def test_every_public_function_documents_its_sections_in_order():
    """#1172: Rule 3 of the docstring standard, for each function a script calls."""
    names = _public_functions()
    assert len(names) == 14  # export_figure and the thirteen render_* functions
    for name in names:
        doc = inspect.getdoc(getattr(pe, name))
        at = {s: doc.find(f"\n{s}\n{'-' * len(s)}\n") for s in _SECTIONS}
        found = sorted((s for s in _SECTIONS if at[s] >= 0), key=at.__getitem__)
        assert {"Parameters", "Returns", "Examples"} <= set(found), (name, found)
        assert found == [s for s in _SECTIONS if s in found], (name, found)
        assert "figsize" not in doc or "in inches" in doc, name


def test_every_docstring_example_runs():
    """#1172: each Examples section runs and shows what the call returns."""
    tests = {t.name.rsplit(".", 1)[-1]: t for t in doctest.DocTestFinder().find(pe) if t.examples}
    assert set(_public_functions()) <= set(tests)
    runner = doctest.DocTestRunner(optionflags=doctest.ELLIPSIS)
    for test in tests.values():
        runner.run(test)
    assert runner.summarize(verbose=False).failed == 0
