# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.analysis.plot_data — the numbers behind an exported figure, as CSV (PRD §7.9).

A figure tells a reader what a result looks like; a researcher who replots it in the lab's
own style, compares it in another program or deposits a journal's source data needs the
numbers. [`export_figure`][tether.analysis.plot_export.export_figure] with
``data_csv=True`` writes them beside the figure, from this module.

Design
------
- **The values written are the values plotted.** Every ``render_*`` helper of
  [`tether.analysis.plot_export`][] records on the figure the result object it drew, with
  the companions it drew beside it (a model overlay, a cloud); the tables are built from
  that same object at export time, never recomputed.
- **One table per kind of mark.** The bars, cells or points of a figure are its main
  table, ``<stem>.csv``. A curve drawn over them (a model, a KDE, a fit and its
  residuals), the parameters of a fit, a density surface or its contour levels are tables
  of their own, ``<stem>.<name>.csv``, because they live on another grid.
- **Long form.** A 1-D result is one row per bin or point; a 2-D result one row per cell
  (``x_low, x_high, y_low, y_high, value``) or grid point (``x, y, value``). A figure of
  several series (#371) gives one table with a leading ``series`` column holding each
  series' label; a figure of a bare result has no such column.
- **A file found alone can be traced.** Each file starts with ``#`` comment lines naming
  the Tether version, the plot kind, the table, the provenance file beside it, the source
  and the selection, and the unit of every column.
- **Exact numbers.** A float is written as its shortest round-trip representation
  (``repr``), so a table read back equals the result; NaN and infinity are left blank, as
  in the molecule-table CSV. Every text cell is quoted, so a ``#`` in a series label is
  never read as a comment.

Nothing here imports Matplotlib.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from tether.analysis.anticorrelation import PopulationAnticorrelation
from tether.analysis.cloud import AlphaShape, RawFretCloud, StateNumberElbow
from tether.analysis.crosscorr import CrossCorrelation
from tether.analysis.dwell import DwellTimeAnalysis
from tether.analysis.histogram import (
    Histogram1D,
    Histogram2D,
    HistogramBootstrapCI,
    ModelGaussianOverlay,
    PerConditionHistograms,
    TransitionSyncHistogram2D,
)
from tether.analysis.state_number import StateNumberCounts
from tether.analysis.tdp import SmoothedTransitionDensity, TransitionDensityPlot
from tether.analysis.transition_prob import TransitionProbHistogram

__all__ = [
    "MAIN_TABLE",
    "DataTable",
    "PlottedData",
    "data_tables",
    "table_path",
    "write_data_tables",
]

#: The name of a figure's main table, written to ``<stem>.csv``. Every other table of the
#: figure is written to ``<stem>.<name>.csv``.
MAIN_TABLE = "data"

_E = "dimensionless (apparent FRET efficiency E)"
_DIMENSIONLESS = "dimensionless"


@dataclass(frozen=True)
class PlottedData:
    """What a ``render_*`` helper drew, as it recorded it on the figure.

    Attributes
    ----------
    renderer
        The name of the ``render_*`` helper.
    data
        The result it was given: one result, or the ordered ``(label, result)`` series.
    companions
        The keyword arguments drawn beside the data, by name: ``overlay`` (the model of a
        1-D histogram), ``cloud`` and ``time_dt`` (of an alpha shape). An argument the
        caller did not pass is absent.
    """

    renderer: str
    data: object
    companions: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DataTable:
    """One table of a figure's numbers.

    Attributes
    ----------
    name
        `MAIN_TABLE` for the marks of the figure, otherwise the table's own name
        (``model``, ``fit``, ``density``, ``levels``, ``points``).
    columns
        The column names, in order.
    units
        The unit of each column, aligned with ``columns``.
    rows
        One tuple per row, aligned with ``columns``. A cell is a ``str``, an ``int``, a
        ``float`` or ``None`` (a blank cell).
    """

    name: str
    columns: tuple[str, ...]
    units: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


# A table under construction: ``{column: unit}`` in order, and rows as dicts.
_Spec = tuple[dict[str, str], list[dict[str, object]]]


def _time_unit(dt: float) -> str:
    """Name the unit of a time axis drawn at frame time ``dt``: seconds unless ``dt`` is 1."""
    return "s" if float(dt) != 1.0 else "frames"


def _value_unit(density: bool) -> str:
    return "density" if density else "counts"


def _cell(value: object) -> object:
    """``value`` as a cell: a Python scalar, with NaN and infinity left blank."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _bins(
    edges: np.ndarray, values: np.ndarray, *, lo: str = "bin_left", hi: str = "bin_right"
) -> list[dict[str, object]]:
    edges = np.asarray(edges, dtype="float64")
    values = np.asarray(values, dtype="float64")
    return [
        {lo: float(edges[i]), hi: float(edges[i + 1]), "value": float(values[i])}
        for i in range(values.shape[0])
    ]


def _cells(x_edges: np.ndarray, y_edges: np.ndarray, values: np.ndarray) -> list[dict[str, object]]:
    """One row per cell of ``values[x, y]``, x varying slowest."""
    x_edges = np.asarray(x_edges, dtype="float64")
    y_edges = np.asarray(y_edges, dtype="float64")
    values = np.asarray(values, dtype="float64")
    return [
        {
            "x_low": float(x_edges[i]),
            "x_high": float(x_edges[i + 1]),
            "y_low": float(y_edges[j]),
            "y_high": float(y_edges[j + 1]),
            "value": float(values[i, j]),
        }
        for i in range(values.shape[0])
        for j in range(values.shape[1])
    ]


def _cell_units(x: str, y: str, value: str) -> dict[str, str]:
    return {"x_low": x, "x_high": x, "y_low": y, "y_high": y, "value": value}


def _curve(curve: str, x: np.ndarray, y: np.ndarray) -> list[dict[str, object]]:
    return [
        {"curve": curve, "x": float(a), "value": float(b)}
        for a, b in zip(np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64"), strict=True)
    ]


def _histogram1d(hist: Histogram1D | HistogramBootstrapCI) -> dict[str, _Spec]:
    base = hist.histogram if isinstance(hist, HistogramBootstrapCI) else hist
    unit = _value_unit(base.density)
    units = {"bin_left": _E, "bin_right": _E, "value": unit}
    rows = _bins(base.bin_edges, base.counts)
    if isinstance(hist, HistogramBootstrapCI):
        units.update(ci_low=unit, ci_high=unit)
        for row, low, high in zip(rows, hist.ci_low, hist.ci_high, strict=True):
            row.update(ci_low=float(low), ci_high=float(high))
    return {MAIN_TABLE: (units, rows)}


def _overlay(model: ModelGaussianOverlay, density: bool, *, total_only: bool) -> dict[str, _Spec]:
    """Tabulate the model curves a histogram figure draws: the total, alone also each state."""
    units = {"curve": "", "x": _E, "value": _value_unit(density)}
    rows = _curve("total", model.x, model.total)
    if not total_only:
        components = np.asarray(model.components, dtype="float64")
        for k in range(components.shape[0]):
            rows += _curve(f"state {k + 1}", model.x, components[k])
    return {"model": (units, rows)}


def _histogram2d(hist: Histogram2D) -> dict[str, _Spec]:
    units = _cell_units(_time_unit(hist.time_dt), _E, _value_unit(hist.density))
    return {MAIN_TABLE: (units, _cells(hist.time_edges, hist.signal_edges, hist.counts))}


def _transition_density(tdp: TransitionDensityPlot | SmoothedTransitionDensity) -> dict[str, _Spec]:
    if isinstance(tdp, SmoothedTransitionDensity):
        if tdp.normalized:
            unit = "normalized density (peak = 1)"
        else:
            unit = "smoothed " + _value_unit(tdp.source.density)
        grid = np.asarray(tdp.grid, dtype="float64")
        values = np.asarray(tdp.values, dtype="float64")
        rows: list[dict[str, object]] = [
            {"x": float(grid[i]), "y": float(grid[j]), "value": float(values[i, j])}
            for i in range(values.shape[0])
            for j in range(values.shape[1])
        ]
        return {MAIN_TABLE: ({"x": _E, "y": _E, "value": unit}, rows)}
    units = _cell_units(_E, _E, _value_unit(tdp.density))
    return {MAIN_TABLE: (units, _cells(tdp.signal_edges, tdp.signal_edges, tdp.counts))}


def _dwell(analysis: DwellTimeAnalysis) -> dict[str, _Spec]:
    time = _time_unit(analysis.dt)
    rows: list[dict[str, object]] = [
        {"time": float(t), "survival": float(s)}
        for t, s in zip(
            np.asarray(analysis.tau, dtype="float64"),
            np.asarray(analysis.survival, dtype="float64"),
            strict=True,
        )
    ]
    tables: dict[str, _Spec] = {
        MAIN_TABLE: ({"time": time, "survival": "fraction of dwells"}, rows)
    }
    fit = analysis.fit
    if fit is None or not fit.success:
        return tables  # a failed fit is not drawn, so it has no curve and no parameters
    model = _curve("model_survival", fit.tau, fit.model_survival) + _curve(
        "residual", fit.tau, fit.residuals
    )
    tables["model"] = ({"curve": "", "x": time, "value": "fraction of dwells"}, model)
    params: list[dict[str, object]] = []
    rate_unit = f"1/{'s' if time == 's' else 'frame'}"
    for k in range(np.asarray(fit.rates).shape[0]):
        params.append(
            {
                "parameter": f"rate_{k + 1}",
                "unit": rate_unit,
                "value": float(fit.rates[k]),
                "stderr": float(fit.rate_stderr[k]),
                "ci_halfwidth": float(fit.rate_ci[k]),
            }
        )
    for k in range(np.asarray(fit.amplitudes).shape[0]):
        params.append(
            {
                "parameter": f"amplitude_{k + 1}",
                "unit": _DIMENSIONLESS,
                "value": float(fit.amplitudes[k]),
                "stderr": float(fit.amplitude_stderr[k]),
                "ci_halfwidth": float(fit.amplitude_ci[k]),
            }
        )
    if fit.beta is not None:
        params.append(
            {
                "parameter": "beta",
                "unit": _DIMENSIONLESS,
                "value": float(fit.beta),
                "stderr": None if fit.beta_stderr is None else float(fit.beta_stderr),
                "ci_halfwidth": None if fit.beta_ci is None else float(fit.beta_ci),
            }
        )
    params.append({"parameter": "r_squared", "unit": _DIMENSIONLESS, "value": fit.r_squared})
    for row in params:
        row.update(model=fit.model, ci_level=float(fit.ci_level))
    tables["fit"] = (
        {
            "model": "",
            "parameter": "",
            "unit": "",
            "value": "see the unit column",
            "stderr": "see the unit column",
            "ci_halfwidth": "see the unit column",
            "ci_level": "fraction",
        },
        params,
    )
    return tables


def _transition_prob(hist: TransitionProbHistogram) -> dict[str, _Spec]:
    probability = "dimensionless (transition probability per molecule)"
    unit = _value_unit(hist.density)
    tables: dict[str, _Spec] = {
        MAIN_TABLE: (
            {"bin_left": probability, "bin_right": probability, "value": unit},
            _bins(hist.edges, hist.counts),
        )
    }
    if hist.kde_x is not None and hist.kde_y is not None:
        tables["model"] = (
            {"curve": "", "x": probability, "value": "density"},
            _curve("kde", hist.kde_x, hist.kde_y),
        )
    return tables


def _state_number(counts: StateNumberCounts) -> dict[str, _Spec]:
    rows: list[dict[str, object]] = [
        {"n_states": int(s), "molecules": int(c)}
        for s, c in zip(
            np.asarray(counts.states, dtype="int64"),
            np.asarray(counts.counts, dtype="int64"),
            strict=True,
        )
    ]
    return {MAIN_TABLE: ({"n_states": "states", "molecules": "molecules"}, rows)}


def _cloud(cloud: RawFretCloud) -> dict[str, _Spec]:
    """Tabulate a raw FRET cloud's points, and its density surface and HDR levels if any."""
    time = _time_unit(cloud.time_dt)
    points = np.asarray(cloud.points, dtype="float64").reshape(-1, 2)
    tables: dict[str, _Spec] = {
        MAIN_TABLE: (
            {"time": time, "e": _E},
            [{"time": float(t), "e": float(e)} for t, e in points],
        )
    }
    if cloud.density is not None:
        tables["density"] = (
            _cell_units(time, _E, "KDE density"),
            _cells(cloud.time_edges, cloud.signal_edges, cloud.density),
        )
        if cloud.hdr_levels is not None:
            tables["levels"] = (
                {"coverage": "fraction", "level": "KDE density"},
                [
                    {"coverage": float(c), "level": float(v)}
                    for c, v in zip(
                        np.asarray(cloud.hdr_coverages, dtype="float64"),
                        np.asarray(cloud.hdr_levels, dtype="float64"),
                        strict=True,
                    )
                ],
            )
    return tables


def _cross_correlation(xc: CrossCorrelation) -> dict[str, _Spec]:
    rows: list[dict[str, object]] = [
        {"lag": float(lag), "value": float(v)}
        for lag, v in zip(
            np.asarray(xc.lags, dtype="float64"),
            np.asarray(xc.values, dtype="float64"),
            strict=True,
        )
    ]
    return {MAIN_TABLE: ({"lag": "frames", "value": f"correlation ({xc.normalize})"}, rows)}


def _per_condition(overlay: PerConditionHistograms) -> dict[str, _Spec]:
    unit = _value_unit(overlay.density)
    rows: list[dict[str, object]] = []
    for ch in overlay.histograms:
        for row in _bins(overlay.bin_edges, ch.histogram.counts):
            rows.append({"condition": ch.condition_id, **row})
    return {MAIN_TABLE: ({"condition": "", "bin_left": _E, "bin_right": _E, "value": unit}, rows)}


def _transition_sync(hist: TransitionSyncHistogram2D) -> dict[str, _Spec]:
    units = _cell_units(_time_unit(hist.time_dt), _E, _value_unit(hist.density))
    return {MAIN_TABLE: (units, _cells(hist.time_edges, hist.signal_edges, hist.counts))}


def _alpha_shape(
    shape: AlphaShape, cloud: RawFretCloud | None, time_dt: float | None
) -> dict[str, _Spec]:
    dt = float(cloud.time_dt) if cloud is not None else float(time_dt or 1.0)
    time = _time_unit(dt)
    segments = np.asarray(shape.boundary_edges, dtype="float64").reshape(-1, 2, 2)
    rows: list[dict[str, object]] = [
        {
            "edge": k + 1,
            "time_start": float(seg[0, 0]),
            "e_start": float(seg[0, 1]),
            "time_end": float(seg[1, 0]),
            "e_end": float(seg[1, 1]),
        }
        for k, seg in enumerate(segments)
    ]
    units = {"edge": "", "time_start": time, "e_start": _E, "time_end": time, "e_end": _E}
    tables: dict[str, _Spec] = {MAIN_TABLE: (units, rows)}
    if cloud is not None:
        tables["points"] = _cloud(cloud)[MAIN_TABLE]
    return tables


def _elbow(elbow: StateNumberElbow) -> dict[str, _Spec]:
    rows: list[dict[str, object]] = [
        {
            "k": int(k),
            "rmse": float(r),
            "elbow": int(elbow.elbow_k is not None and k == elbow.elbow_k),
        }
        for k, r in zip(
            np.asarray(elbow.k_values, dtype="int64"),
            np.asarray(elbow.rmse, dtype="float64"),
            strict=True,
        )
    ]
    units = {"k": "clusters", "rmse": _E, "elbow": "1 = the suggested k"}
    return {MAIN_TABLE: (units, rows)}


def _anticorrelation(population: PopulationAnticorrelation) -> dict[str, _Spec]:
    rows: list[dict[str, object]] = []
    for row, mol in enumerate(population.molecules):
        for event in mol.scan.events:
            rows.append(
                {
                    "row": row,
                    "molecule_key": mol.molecule_key,
                    "start": int(event.start),
                    "stop": int(event.stop),
                    "peak_frame": int(event.peak_frame),
                    "peak_magnitude": float(event.peak_magnitude),
                }
            )
    units = {
        "row": "",
        "molecule_key": "",
        "start": "frames",
        "stop": "frames",
        "peak_frame": "frames",
        "peak_magnitude": "|lag-0 Pearson correlation|",
    }
    return {MAIN_TABLE: (units, rows)}


def _one(plotted: PlottedData, k: int, result: object, n_series: int) -> dict[str, _Spec]:
    """Build the tables of the ``k``-th of ``n_series`` series of a figure."""
    companions = plotted.companions

    def companion(name: str) -> Any:
        # The renderer already checked the companion: one object belongs to the one
        # series there is, a sequence gives one entry (or None) per series.
        given = companions.get(name)
        if isinstance(given, Sequence) and not isinstance(given, str | bytes):
            return list(given)[k]
        return given

    if isinstance(result, Histogram1D | HistogramBootstrapCI):
        tables = _histogram1d(result)
        model = companion("overlay")
        if model is not None:
            base = result.histogram if isinstance(result, HistogramBootstrapCI) else result
            tables.update(_overlay(model, base.density, total_only=n_series > 1))
        return tables
    if isinstance(result, AlphaShape):
        return _alpha_shape(result, companion("cloud"), companions.get("time_dt"))  # type: ignore[arg-type]
    builders: tuple[tuple[type, Any], ...] = (
        (Histogram2D, _histogram2d),
        (TransitionDensityPlot | SmoothedTransitionDensity, _transition_density),
        (DwellTimeAnalysis, _dwell),
        (TransitionProbHistogram, _transition_prob),
        (StateNumberCounts, _state_number),
        (RawFretCloud, _cloud),
        (CrossCorrelation, _cross_correlation),
        (PerConditionHistograms, _per_condition),
        (TransitionSyncHistogram2D, _transition_sync),
        (StateNumberElbow, _elbow),
        (PopulationAnticorrelation, _anticorrelation),
    )
    for kind, build in builders:
        if isinstance(result, kind):
            return build(result)  # type: ignore[no-any-return]
    raise TypeError(f"no data table for a {type(result).__name__}")


def data_tables(plotted: PlottedData) -> tuple[DataTable, ...]:
    """Build the tables of the figure ``plotted`` describes, the main table first.

    A figure of several series gives each table once, with a leading ``series`` column; a
    column one series has and another lacks (the bootstrap interval of one histogram among
    plain ones) is blank in the rows of the series without it.

    Parameters
    ----------
    plotted
        What a ``render_*`` helper drew, as it recorded it on the figure.

    Returns
    -------
    tuple[DataTable, ...]
        The main table (`MAIN_TABLE`) and then the others, in the order the first series
        gives them.

    Raises
    ------
    TypeError
        The result is of a type no renderer draws.
    """
    data = plotted.data
    if isinstance(data, Sequence) and not isinstance(data, str | bytes):
        series: list[tuple[str | None, object]] = [(str(lb), r) for lb, r in data]
    else:
        series = [(None, data)]
    merged: dict[str, _Spec] = {}
    for k, (label, result) in enumerate(series):
        for name, (units, rows) in _one(plotted, k, result, len(series)).items():
            if label is not None:
                units = {"series": "", **units}
                rows = [{"series": label, **row} for row in rows]
            if name not in merged:
                merged[name] = (dict(units), list(rows))
            else:
                known, known_rows = merged[name]
                for column, unit in units.items():
                    known.setdefault(column, unit)
                known_rows.extend(rows)
    order = sorted(merged, key=lambda name: name != MAIN_TABLE)
    tables: list[DataTable] = []
    for name in order:
        units, rows = merged[name]
        columns = tuple(units)
        tables.append(
            DataTable(
                name=name,
                columns=columns,
                units=tuple(units[c] for c in columns),
                rows=tuple(tuple(_cell(row.get(c)) for c in columns) for row in rows),
            )
        )
    return tuple(tables)


def _text(value: object) -> str:
    """Write a CSV cell: text always quoted, a float by ``repr``, ``None`` blank."""
    if value is None:
        return ""
    if isinstance(value, str):
        return '"' + value.replace('"', '""') + '"'
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _header_value(value: object) -> str:
    """Write a header value: a one-line string as it is, anything else as JSON."""
    if isinstance(value, str) and value.isprintable():
        return value
    return json.dumps(value)


def table_path(stem: Path, name: str) -> Path:
    """Where the table ``name`` of a figure exported to ``stem`` is written."""
    suffix = ".csv" if name == MAIN_TABLE else f".{name}.csv"
    return stem.with_name(stem.name + suffix)


def write_data_tables(
    stem: str | Path,
    tables: Sequence[DataTable],
    *,
    header: Sequence[tuple[str, object]],
) -> dict[str, Path]:
    """Write each table beside the figure, ``<stem>.csv`` and ``<stem>.<name>.csv``.

    Each file starts with one ``# key: value`` comment line per ``header`` entry (a value
    that is not a plain string is written as JSON), then ``# table:`` and ``# units:``
    lines, then the column names and the rows.

    Parameters
    ----------
    stem
        The figure's output stem, without an extension.
    tables
        The tables, as `data_tables` gives them.
    header
        The ``(key, value)`` pairs every file starts with, in order.

    Returns
    -------
    dict[str, Path]
        Each table's name and the file it was written to.
    """
    stem = Path(stem)
    written: dict[str, Path] = {}
    for table in tables:
        lines = [f"# {key}: {_header_value(value)}" for key, value in header]
        lines.append(f"# table: {table.name}")
        units = "; ".join(
            f"{c} = {u}" for c, u in zip(table.columns, table.units, strict=True) if u
        )
        lines.append(f"# units: {units}")
        lines.append(",".join(table.columns))
        lines.extend(",".join(_text(v) for v in row) for row in table.rows)
        path = table_path(stem, table.name)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written[table.name] = path
    return written
