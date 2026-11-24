# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The numbers behind an exported figure, as CSV (#661).

Each acceptance criterion of #661 has its tests here:

* every plot type ``export_figure`` can write also has a data table — all thirteen
  renderers, bare and as several series;
* each table read back equals the plotted result exactly: the expected columns below are
  taken from the result's own arrays, not from ``tether.analysis.plot_data``;
* each CSV names its provenance file and the Tether version;
* every column of every table is documented, with its unit, in ``docs/reference/exports.md``;
* the figure files are the same bytes with or without the option.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")
pytest.importorskip("scipy")

from matplotlib.figure import Figure  # noqa: E402

from _analysis_store import MEANS, build_store_with_model  # noqa: E402
from test_plot_export import STATES  # noqa: E402
from test_plot_export_series import KINDS, OTHER_STATES, RENDER, _results  # noqa: E402
from tether import __version__  # noqa: E402
from tether.analysis import plot_export as pe  # noqa: E402
from tether.analysis.dwell import (  # noqa: E402
    DwellTimeAnalysis,
    fit_survival,
    survival_curve,
)
from tether.analysis.histogram import (  # noqa: E402
    population_apparent_e_histogram,
    population_apparent_e_histogram_ci,
    population_model_gaussian_overlay,
)
from tether.analysis.tdp import smooth_transition_density  # noqa: E402

PINNED = "2026-01-01T00:00:00+00:00"
EXPORTS_MD = Path(__file__).resolve().parents[1] / "docs" / "reference" / "exports.md"


@pytest.fixture(scope="module")
def projects(tmp_path_factory):
    base = tmp_path_factory.mktemp("plotdata")
    proj_a, _ = build_store_with_model(base, STATES, MEANS, name="wt.tether")
    proj_b, _ = build_store_with_model(base, OTHER_STATES, MEANS, name="mut.tether")
    return proj_a, proj_b


@pytest.fixture(scope="module")
def results(projects):
    """``{kind: (wild_type, mutant)}`` results for every renderer."""
    a, b = _results(projects[0], 0.0), _results(projects[1], 1.0)
    return {k: (a[k], b[k]) for k in KINDS}


def _fitted_dwell(dt: float = 1.0) -> DwellTimeAnalysis:
    """A dwell analysis whose single-exponential fit succeeds: 400 geometric dwells."""
    lengths = np.random.default_rng(7).geometric(0.2, 400)
    tau, survival = survival_curve(lengths)
    tau = np.asarray(tau, dtype="float64") * dt
    return DwellTimeAnalysis(
        state=1,
        level=0.5,
        dt=dt,
        dwell_lengths=lengths,
        n_dwells=int(lengths.size),
        n_molecules=40,
        tau=tau,
        survival=survival,
        fit=fit_survival(tau, survival),
    )


def _export(fig, stem, *, data_csv=True, parameters=None, formats=pe.DEFAULT_PLOT_FORMATS):
    return pe.export_figure(
        fig,
        stem,
        tether_export="plot:t",
        source="wt.tether",
        parameters=parameters or {},
        created_utc=PINNED,
        data_csv=data_csv,
        formats=formats,
    )


def _read(path: Path) -> tuple[dict[str, str], list[str], list[list[str]]]:
    """``(header, columns, rows)`` of a data table, read without Tether's code."""
    header: dict[str, str] = {}
    body: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            key, _, value = line[2:].partition(": ")
            header[key] = value
        else:
            body.append(line)
    columns, *rows = list(csv.reader(io.StringIO("\n".join(body))))
    return header, columns, rows


def _column(columns, rows, name) -> np.ndarray:
    i = columns.index(name)
    return np.array([float(r[i]) if r[i] != "" else np.nan for r in rows])


def _text(columns, rows, name) -> list[str]:
    i = columns.index(name)
    return [r[i] for r in rows]


def _cells(x_edges, y_edges, values) -> dict[str, np.ndarray]:
    """The long form of ``values[x, y]``, x slowest, from the result's own arrays."""
    x_edges, y_edges = np.asarray(x_edges), np.asarray(y_edges)
    values = np.asarray(values, dtype="float64")
    nx, ny = values.shape
    return {
        "x_low": np.repeat(x_edges[:-1], ny),
        "x_high": np.repeat(x_edges[1:], ny),
        "y_low": np.tile(y_edges[:-1], nx),
        "y_high": np.tile(y_edges[1:], nx),
        "value": values.ravel(),
    }


def _bins(edges, values) -> dict[str, np.ndarray]:
    edges = np.asarray(edges)
    return {"bin_left": edges[:-1], "bin_right": edges[1:], "value": np.asarray(values)}


def _expected(kind: str, r) -> dict[str, object]:
    """The main table of one result, column by column, straight from the result."""
    if kind == "h1":
        return _bins(r.bin_edges, r.counts)
    if kind in ("h2", "sync"):
        return _cells(r.time_edges, r.signal_edges, r.counts)
    if kind == "tdp":
        return _cells(r.signal_edges, r.signal_edges, r.counts)
    if kind == "dwell":
        return {"time": r.tau, "survival": r.survival}
    if kind == "tprob":
        return _bins(r.edges, r.counts)
    if kind == "statenum":
        return {"n_states": r.states, "molecules": r.counts}
    if kind == "cloud":
        return {"time": r.points[:, 0], "e": r.points[:, 1]}
    if kind == "xcorr":
        return {"lag": r.lags, "value": r.values}
    if kind == "percond":
        n = len(r.bin_edges) - 1
        return {
            "condition": [c.condition_id for c in r.histograms for _ in range(n)],
            "bin_left": np.tile(r.bin_edges[:-1], r.n_conditions),
            "bin_right": np.tile(r.bin_edges[1:], r.n_conditions),
            "value": np.concatenate([c.histogram.counts for c in r.histograms]),
        }
    if kind == "alpha":
        e = np.asarray(r.boundary_edges)
        return {
            "edge": np.arange(1, e.shape[0] + 1),
            "time_start": e[:, 0, 0],
            "e_start": e[:, 0, 1],
            "time_end": e[:, 1, 0],
            "e_end": e[:, 1, 1],
        }
    if kind == "elbow":
        return {"k": r.k_values, "rmse": r.rmse, "elbow": r.k_values == r.elbow_k}
    if kind == "anticorr":
        events = [(i, m.molecule_key, e) for i, m in enumerate(r.molecules) for e in m.scan.events]
        return {
            "row": [i for i, _, _ in events],
            "molecule_key": [k for _, k, _ in events],
            "start": [e.start for _, _, e in events],
            "stop": [e.stop for _, _, e in events],
            "peak_frame": [e.peak_frame for _, _, e in events],
            "peak_magnitude": [e.peak_magnitude for _, _, e in events],
        }
    raise AssertionError(kind)


def _assert_table(columns, rows, expected: dict[str, object]) -> None:
    assert [c for c in columns if c != "series"] == list(expected)
    for name, want in expected.items():
        if isinstance(want, list) and want and isinstance(want[0], str):
            assert _text(columns, rows, name) == want, name
        else:
            got = _column(columns, rows, name)
            np.testing.assert_array_equal(got, np.asarray(want, dtype="float64"), err_msg=name)


# ------------------------------------- AC 1 + 2: a table per plot type, equal to the result


@pytest.mark.parametrize("kind", KINDS)
def test_every_plot_type_writes_a_table_equal_to_the_result(results, tmp_path, kind):
    result = results[kind][0]
    out = _export(RENDER[kind](result), tmp_path / kind)
    assert out.data_paths["data"] == tmp_path / f"{kind}.csv"
    _, columns, rows = _read(out.data_paths["data"])
    assert "series" not in columns  # a bare result has no series column
    _assert_table(columns, rows, _expected(kind, result))


@pytest.mark.parametrize("kind", KINDS)
def test_several_series_give_one_table_with_a_series_column(results, tmp_path, kind):
    wt, mut = results[kind]
    out = _export(RENDER[kind]([("WT", wt), ("mutant", mut)]), tmp_path / kind)
    _, columns, rows = _read(out.data_paths["data"])
    assert columns[0] == "series"
    labels = _text(columns, rows, "series")
    n_wt = labels.count("WT")
    assert labels == ["WT"] * n_wt + ["mutant"] * (len(labels) - n_wt)
    _assert_table(columns, rows[:n_wt], _expected(kind, wt))
    _assert_table(columns, rows[n_wt:], _expected(kind, mut))


def test_the_tables_are_the_inputs_as_drawn_not_as_changed_later(results, tmp_path):
    wt, mut = results["h1"]
    drawn = {"counts": np.array(wt.counts, copy=True)}
    series = [("WT", wt)]
    fig = pe.render_histogram1d(series)
    series[0] = ("mutant", mut)  # replaced after drawing
    series.append(("extra", mut))  # and a series the figure never drew
    in_place = isinstance(wt.counts, np.ndarray) and wt.counts.flags.writeable
    try:
        if in_place:
            wt.counts[...] = 0  # and the drawn result changed in place
        _, columns, rows = _read(_export(fig, tmp_path / "later").data_paths["data"])
    finally:
        if in_place:
            wt.counts[...] = drawn["counts"]  # the module fixture is shared
    assert set(_text(columns, rows, "series")) == {"WT"}
    _assert_table(columns, rows, _bins(wt.bin_edges, drawn["counts"]))


def test_a_one_series_sequence_names_its_series(results, tmp_path):
    out = _export(pe.render_histogram1d([("WT", results["h1"][0])]), tmp_path / "one")
    _, columns, rows = _read(out.data_paths["data"])
    assert columns[0] == "series" and set(_text(columns, rows, "series")) == {"WT"}


def test_the_bootstrap_interval_and_the_model_are_written(projects, tmp_path):
    proj = projects[0]
    ci = population_apparent_e_histogram_ci(proj, n_resamples=50, seed=1)
    overlay = population_model_gaussian_overlay(proj, "vbconhmm")
    out = _export(pe.render_histogram1d(ci, overlay=overlay), tmp_path / "a1")
    assert set(out.data_paths) == {"data", "model"}
    _, columns, rows = _read(out.data_paths["data"])
    _assert_table(
        columns,
        rows,
        {
            **_bins(ci.histogram.bin_edges, ci.histogram.counts),
            "ci_low": ci.ci_low,
            "ci_high": ci.ci_high,
        },
    )
    _, columns, rows = _read(out.data_paths["model"])
    curves = _text(columns, rows, "curve")
    n = len(overlay.x)
    k = np.asarray(overlay.components).shape[0]
    assert curves == ["total"] * n + [f"state {i + 1}" for i in range(k) for _ in range(n)]
    np.testing.assert_array_equal(_column(columns, rows, "x"), np.tile(overlay.x, k + 1))
    np.testing.assert_array_equal(
        _column(columns, rows, "value"),
        np.concatenate([overlay.total, *np.asarray(overlay.components)]),
    )


def test_an_overlaid_model_is_written_as_drawn_its_total_alone(projects, results, tmp_path):
    overlay = population_model_gaussian_overlay(projects[0], "vbconhmm")
    wt, mut = results["h1"]
    fig = pe.render_histogram1d([("WT", wt), ("mutant", mut)], overlay=[overlay, None])
    _, columns, rows = _read(_export(fig, tmp_path / "a1").data_paths["model"])
    assert set(_text(columns, rows, "series")) == {"WT"}
    assert set(_text(columns, rows, "curve")) == {"total"}


def test_a_series_without_an_interval_has_blank_interval_cells(projects, results, tmp_path):
    ci = population_apparent_e_histogram_ci(projects[0], n_resamples=50, seed=1)
    fig = pe.render_histogram1d([("boot", ci), ("plain", results["h1"][1])])
    _, columns, rows = _read(_export(fig, tmp_path / "a1").data_paths["data"])
    plain = [r for r in rows if r[0] == "plain"]
    assert plain and all(r[columns.index("ci_low")] == "" for r in plain)


def test_a_dwell_fit_writes_its_curves_and_its_parameters(tmp_path):
    analysis = _fitted_dwell()
    fit = analysis.fit
    assert fit is not None and fit.success
    out = _export(pe.render_dwell_survival(analysis), tmp_path / "b2")
    assert set(out.data_paths) == {"data", "model", "fit"}
    _, columns, rows = _read(out.data_paths["model"])
    n = len(fit.tau)
    assert _text(columns, rows, "curve") == ["model_survival"] * n + ["residual"] * n
    np.testing.assert_array_equal(
        _column(columns, rows, "value"), np.concatenate([fit.model_survival, fit.residuals])
    )
    _, columns, rows = _read(out.data_paths["fit"])
    params = dict(
        zip(_text(columns, rows, "parameter"), _column(columns, rows, "value"), strict=True)
    )
    assert params["rate_1"] == float(fit.rates[0])
    assert params["amplitude_1"] == float(fit.amplitudes[0])
    assert params["r_squared"] == float(fit.r_squared)
    units = dict(zip(_text(columns, rows, "parameter"), _text(columns, rows, "unit"), strict=True))
    assert units["rate_1"] == "1/frame"  # the default dt of 1 is a frame
    assert set(_text(columns, rows, "model")) == {"single"}


def test_a_cloud_writes_its_density_and_levels(results, tmp_path):
    cloud = results["cloud"][0]
    assert cloud.density is not None and cloud.hdr_levels is not None
    out = _export(pe.render_raw_fret_cloud(cloud), tmp_path / "cloud")
    assert set(out.data_paths) == {"data", "density", "levels"}
    _, columns, rows = _read(out.data_paths["density"])
    _assert_table(columns, rows, _cells(cloud.time_edges, cloud.signal_edges, cloud.density))
    _, columns, rows = _read(out.data_paths["levels"])
    _assert_table(columns, rows, {"coverage": cloud.hdr_coverages, "level": cloud.hdr_levels})


def test_an_alpha_shape_over_its_cloud_writes_the_points(results, tmp_path):
    shape, cloud = results["alpha"][0], results["cloud"][0]
    out = _export(pe.render_alpha_shape(shape, cloud=cloud), tmp_path / "alpha")
    _, columns, rows = _read(out.data_paths["points"])
    _assert_table(columns, rows, {"time": cloud.points[:, 0], "e": cloud.points[:, 1]})


def test_a_smoothed_tdp_is_written_on_its_grid(results, tmp_path):
    smoothed = smooth_transition_density(results["tdp"][0], interp_res=20)
    header, columns, rows = _read(
        _export(pe.render_transition_density(smoothed), tmp_path / "b1").data_paths["data"]
    )
    grid = np.asarray(smoothed.grid)
    _assert_table(
        columns,
        rows,
        {
            "x": np.repeat(grid, grid.size),
            "y": np.tile(grid, grid.size),
            "value": np.asarray(smoothed.values).ravel(),
        },
    )
    assert "value = normalized density (peak = 1)" in header["units"]


def test_a_time_axis_in_seconds_says_so(tmp_path):
    out = _export(pe.render_dwell_survival(_fitted_dwell(dt=0.1)), tmp_path / "b2")
    header, _, _ = _read(out.data_paths["data"])
    assert "time = s;" in header["units"]
    _, columns, rows = _read(out.data_paths["fit"])
    units = dict(zip(_text(columns, rows, "parameter"), _text(columns, rows, "unit"), strict=True))
    assert units["rate_1"] == "1/s"


# ---------------------------------------------------- AC 3: a file found alone traces back


def test_each_csv_names_its_provenance_file_and_the_tether_version(results, tmp_path):
    ci_fig = pe.render_raw_fret_cloud(results["cloud"][0])
    out = _export(ci_fig, tmp_path / "cloud", parameters={"selection": "accepted v2"})
    for name, path in out.data_paths.items():
        header, _, _ = _read(path)
        assert header["tether_version"] == str(__version__)
        assert header["provenance"] == out.provenance_path.name == "cloud.provenance.json"
        assert header["plot"] == "plot:t"
        assert header["created_utc"] == PINNED
        assert header["source"] == "wt.tether"
        assert json.loads(header["selection"]) == "accepted v2"
        assert header["table"] == name
    sidecar = json.loads(out.provenance_path.read_text(encoding="utf-8"))
    assert sidecar["parameters"]["data_tables"] == {n: p.name for n, p in out.data_paths.items()}


def test_each_series_is_named_with_its_selection(results, tmp_path):
    wt, mut = results["xcorr"]
    out = _export(
        pe.render_cross_correlation([("WT", wt), ("mutant", mut)]),
        tmp_path / "xc",
        parameters={"series": [{"selection": "WT v1"}, {"selection": "mutant v1"}]},
    )
    header, _, _ = _read(out.data_paths["data"])
    assert json.loads(header["series 1"]) == {"label": "WT", "selection": "WT v1"}
    assert json.loads(header["series 2"]) == {"label": "mutant", "selection": "mutant v1"}
    assert json.loads(header["selection"]) is None


def test_a_label_with_a_hash_or_quote_reads_back_with_pandas(results, tmp_path):
    pd = pytest.importorskip("pandas")
    wt, mut = results["h1"]
    labels = ['WT #1 "fresh"', "mutant, old"]
    fig = pe.render_histogram1d(list(zip(labels, (wt, mut), strict=True)))
    frame = pd.read_csv(
        _export(fig, tmp_path / "a1").data_paths["data"], comment="#", float_precision="round_trip"
    )
    assert list(dict.fromkeys(frame["series"])) == labels
    np.testing.assert_array_equal(frame["value"][: len(wt.counts)], wt.counts)


def test_a_non_finite_value_is_a_blank_cell(results, tmp_path):
    from dataclasses import replace

    xc = results["xcorr"][0]
    values = np.asarray(xc.values, dtype="float64").copy()
    values[0] = np.nan
    out = _export(pe.render_cross_correlation(replace(xc, values=values)), tmp_path / "xc")
    _, columns, rows = _read(out.data_paths["data"])
    assert rows[0][columns.index("value")] == ""


# ------------------------------------------------------- AC 5: the figure files unchanged


@pytest.mark.parametrize("kind", KINDS)
def test_the_figure_files_are_unchanged_by_the_option(results, tmp_path, kind):
    result = results[kind][0]
    (tmp_path / "without").mkdir()
    (tmp_path / "with").mkdir()
    without = _export(RENDER[kind](result), tmp_path / "without" / kind, data_csv=False)
    with_data = _export(RENDER[kind](result), tmp_path / "with" / kind)
    assert without.data_paths == {}
    for fmt in pe.DEFAULT_PLOT_FORMATS:
        assert with_data.paths[fmt].read_bytes() == without.paths[fmt].read_bytes(), fmt
    sidecar = json.loads(without.provenance_path.read_text(encoding="utf-8"))
    assert "data_tables" not in sidecar["parameters"]


def test_a_figure_no_renderer_drew_is_refused_before_anything_is_written(tmp_path):
    fig = Figure()
    fig.add_subplot().plot([0, 1], [0, 1])
    with pytest.raises(ValueError, match="render_\\* helper"):
        _export(fig, tmp_path / "custom")
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------ AC 4: every column is documented


def test_every_column_of_every_table_is_documented_with_its_unit(projects, results, tmp_path):
    proj = projects[0]
    figures = [RENDER[k]([("WT", results[k][0])]) for k in KINDS]
    figures += [
        pe.render_histogram1d(
            population_apparent_e_histogram_ci(proj, n_resamples=20, seed=1),
            overlay=population_model_gaussian_overlay(proj, "vbconhmm"),
        ),
        pe.render_dwell_survival(_fitted_dwell()),
        pe.render_alpha_shape(results["alpha"][0], cloud=results["cloud"][0]),
        pe.render_transition_density(smooth_transition_density(results["tdp"][0], interp_res=8)),
        pe.render_transition_prob(results["tprob"][0]),
    ]
    section = EXPORTS_MD.read_text(encoding="utf-8").split("### The numbers behind a figure", 1)[1]
    section = section.split("\n## ", 1)[0].split("\n### ", 1)[0]
    documented = set(re.findall(r"`([a-z_0-9]+)`", section))
    for i, fig in enumerate(figures):
        for name, path in _export(fig, tmp_path / f"f{i}").data_paths.items():
            assert f"`{name}`" in section, name
            _, columns, _ = _read(path)
            assert set(columns) <= documented, (name, set(columns) - documented)


def test_the_histogram_counts_read_back_from_a_store(projects, tmp_path):
    hist = population_apparent_e_histogram(projects[0], bins=11)
    out = _export(pe.render_histogram1d(hist), tmp_path / "a1", formats=("png",))
    _, columns, rows = _read(out.data_paths["data"])
    assert len(rows) == 11
    np.testing.assert_array_equal(_column(columns, rows, "value"), hist.counts)


def test_the_provenance_file_with_data_tables_validates(results, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = EXPORTS_MD.parent / "tether-provenance-schema.json"
    validator = jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    out = _export(pe.render_raw_fret_cloud(results["cloud"][0]), tmp_path / "cloud")
    sidecar = json.loads(out.provenance_path.read_text(encoding="utf-8"))
    assert list(validator.iter_errors(sidecar)) == []
    sidecar["parameters"]["data_tables"] = {"model": "cloud.model.csv"}  # no main table
    assert list(validator.iter_errors(sidecar))
