# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.analysis.plot_export — vector (PDF/SVG) + raster (PNG) plot export (PRD §7.9).

FR-EXPORT §7.9: *"every plot [exports] as vector PDF/SVG + PNG ... all exports are
stamped with provenance and parameters."* This module renders the M6 analysis
plot-data dataclasses (the tMAVEN Appendix-C plot types the ``tether.analysis``
functions produce) to Matplotlib figures and writes each figure to a **vector PDF**,
a **vector SVG**, and a **raster PNG**, each carrying a provenance stamp.

Design
------
- **Headless, Qt-free.** Rendering uses Matplotlib's object-oriented
  `matplotlib.figure.Figure` API directly (never ``pyplot``), so no GUI
  backend is ever selected — this keeps the base environment free of PyQt5/Tk
  (the §4.1 no-PyQt5 base invariant that motivated shipping ``matplotlib-base``,
  ADR-0044). ``Figure.savefig`` attaches the format-appropriate canvas (Agg for
  PNG, ``backend_pdf`` for PDF, ``backend_svg`` for SVG) on demand.
- **Rendering is separate from IO.** Each ``render_*`` returns a bare
  `Figure` (no stamp, no file); [`export_figure`][]
  adds the visible provenance footer and writes the three files + the sidecar.
  A caller can therefore embed the same figure in the GUI and export it
  identically.
- **Provenance travels three ways** (§8 NFR-REPRO, invariant §1.3(4)): a
  ``<stem>.provenance.json`` sidecar (reusing
  [`tether.project.export.write_provenance_sidecar`][], the single stamping
  convention shared with the table/subset exports); a **visible on-figure footer**
  (``Tether <version> · <utc> · <kind>``) so the stamp travels *on the plot* a
  reader sees; and **embedded file metadata** (PDF/SVG/PNG document metadata).
- **Deterministic output.** The timestamp is threaded once through the sidecar,
  footer, and embedded metadata so they agree exactly; SVG element ids use a fixed
  ``svg.hashsalt`` and the auto-generated SVG/PDF creation date is pinned to the
  stamp time, so a re-export of the same figure at the same ``created_utc`` is
  reproducible.

This module is **lazily re-exported** by the ``tether.analysis`` package: its
``__init__`` resolves ``plot_export`` and every name in this module's ``__all__`` on
first attribute access through a module ``__getattr__``
([PEP 562](https://peps.python.org/pep-0562/)), and
``dir(tether.analysis)`` lists them. A plain ``import tether.analysis`` therefore
still does not import Matplotlib, which the lightweight headless paths do not need.
Either spelling works, and both import Matplotlib at that point::

    from tether.analysis import export_figure, render_histogram1d
    from tether.analysis.plot_export import export_figure, render_histogram1d

The names are not in ``tether.analysis.__all__``, so ``from tether.analysis import *``
stays Matplotlib-free.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib import colormaps, rc_context
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.colors import LogNorm, Normalize
from matplotlib.figure import Figure

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
from tether.project.export import write_provenance_sidecar

__all__ = [
    "DEFAULT_EXPORT_DPI",
    "DEFAULT_FIGSIZE",
    "DEFAULT_PLOT_FORMATS",
    "DEFAULT_TDP_COLOR_SCALE",
    "SERIES_COLORS",
    "SERIES_COLOR_CYCLE",
    "SERIES_LINESTYLES",
    "TDP_COLOR_SCALES",
    "PlotExportResult",
    "SeriesStyle",
    "export_figure",
    "render_alpha_shape",
    "render_cross_correlation",
    "render_dwell_survival",
    "render_histogram1d",
    "render_histogram2d",
    "render_per_condition_histograms",
    "render_population_anticorrelation",
    "render_raw_fret_cloud",
    "render_state_number",
    "render_state_number_elbow",
    "render_transition_density",
    "render_transition_prob",
    "render_transition_sync_histogram2d",
    "series_style",
]

#: Raster (PNG) resolution in dots-per-inch. Vector formats are resolution-independent;
#: this only governs the PNG. A named default (not hardcoded per call), following the
#: ``tether.analysis`` module-constant convention — a cosmetic output knob, not a §11.2
#: scientific/algorithmic tunable.
DEFAULT_EXPORT_DPI = 200

#: Default figure size (inches). Single-panel analysis plots.
DEFAULT_FIGSIZE: tuple[float, float] = (6.4, 4.8)

#: The three export formats §7.9 mandates: vector PDF, vector SVG, raster PNG.
DEFAULT_PLOT_FORMATS: tuple[str, ...] = ("pdf", "svg", "png")

#: Fixed SVG id hash-salt so re-exports are byte-reproducible (matplotlib otherwise
#: salts element ids per process).
_SVG_HASHSALT = "tether.analysis.plot_export"

#: The colour scales `render_transition_density` can draw the B1 TDP with.
TDP_COLOR_SCALES: tuple[str, ...] = ("log", "linear")

#: PRD Appendix C B1 names the TDP *log-normalized* [McKinney2006], as tMAVEN draws it
#: (``data_tdp.py`` ``hist_log = True``), so the export defaults to a log colour scale
#: (#556). Display only: the stored histogram is never transformed, and the scale is
#: recorded in the plot provenance. A cosmetic output knob, not a §11.2 tunable.
DEFAULT_TDP_COLOR_SCALE = "log"

#: How many decades the TDP log colour scale's floor sits below the smallest non-empty
#: bin. At zero the weakest transition would share the floor colour with an empty bin,
#: which is the "low-count transitions vanish" failure #556 fixes; one decade lifts it
#: clear of the floor while the scale still spans the data. Display only.
_TDP_LOG_FLOOR_DECADES = 1.0

#: The log colour floor of a *smoothed* TDP, as a fraction of its peak: ``checkTDP.py``'s
#: and tMAVEN ``data_tdp.py``'s ``color_floor = 0.0001`` on their peak-normalized array.
#: A spline view has no smallest non-empty bin to anchor the floor to — its tail decays
#: smoothly toward 0 — so the floor is fixed below the peak instead. Display only.
_TDP_SMOOTH_LOG_FLOOR = 1e-4

#: Figure attribute a ``render_*`` helper sets to tell `export_figure` how it
#: drew the data (for example the TDP colour scale), so the provenance sidecar records
#: what the reader actually sees without the caller having to repeat it.
_DISPLAY_ATTR = "_tether_display_parameters"

_STATE_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b")

#: Line styles the per-condition overlay steps through once it has used every colour of
#: the ``tab10`` palette, so condition 11 is never drawn identical to condition 1. Ten
#: colours by four styles give 40 distinct curves; condition 41 repeats condition 1.
_CONDITION_LINESTYLES = ("-", "--", "-.", ":")

#: The most molecule rows the anticorrelation event raster labels by ``molecule_key``;
#: above it the rows are labelled by store-order index, so the tick labels stay legible.
_ANTICORR_MAX_KEY_LABELS = 30

#: The name of the one colour cycle every figure of two or more series draws them with
#: (#371). It is recorded in the provenance file as ``parameters.display.series_cycle``,
#: so a reader of the file knows which colours meant which series.
SERIES_COLOR_CYCLE = "okabe-ito-4"

#: The colours of `SERIES_COLOR_CYCLE`, in the order series take them: blue, vermilion,
#: bluish green and reddish purple of the Okabe–Ito set (Wong 2011,
#: doi:10.1038/nmeth.1618; the set ``tether.gui.theme`` already cites for its dark
#: ``safe`` palette). Its orange, sky blue, yellow and black are left out: the first three
#: reach less than 3 : 1 against the white export background (WCAG 2.x 1.4.11), and black
#: is the colour of the reference lines and model curves. ``tests/test_plot_export_series.py``
#: checks both the 3 : 1 floor and a CIEDE2000 difference of at least 10 between every two
#: of the four under simulated protanopia, deuteranopia and tritanopia, the check
#: ``tests/test_gui_theme.py`` applies to the trace palette. A cosmetic output choice, not
#: a §11.2 tunable.
SERIES_COLORS: tuple[str, ...] = ("#0072b2", "#d55e00", "#009e73", "#cc79a7")

#: The line styles the cycle steps through once every colour of `SERIES_COLORS` is
#: used, so series 5 is never drawn like series 1. Four colours by four styles give 16
#: distinct series; series 17 repeats series 1, and only its legend label tells them apart.
SERIES_LINESTYLES: tuple[str, ...] = ("-", "--", "-.", ":")

#: The bar hatches that stand in for `SERIES_LINESTYLES` where a series is drawn as bars
#: (the C1 state-number chart), one per line style.
_SERIES_HATCHES: tuple[str, ...] = ("", "//", "..", "xx")

#: The point markers that stand in for `SERIES_LINESTYLES` where a series may be drawn
#: as points alone (the B2 survival markers of an unfitted series), one per line style.
_SERIES_MARKERS: tuple[str, ...] = ("o", "s", "^", "D")

#: Figure attribute a ``render_*`` helper sets when it was given a sequence of series:
#: one record per series, which `export_figure` writes to ``parameters.series``.
_SERIES_ATTR = "_tether_series"

#: Array fields of a result that are parameters of the series (its grid, a smoothed
#: transition density's interpolation ``grid`` included, its probed values, and the
#: parameters of a model drawn with it: the Gaussian overlay's means,
#: variances and weights, a dwell fit's rates and amplitudes with their errors), and so
#: travel into the provenance file. Every other array is data, including a model's
#: sampled curve (``components``, ``total``, ``model_survival``).
_PARAMETER_ARRAYS = frozenset(
    {
        "bin_edges",
        "edges",
        "time_edges",
        "signal_edges",
        "grid",
        "states",
        "lags",
        "k_values",
        "hdr_coverages",
        "means",
        "variances",
        "frac",
        "rates",
        "amplitudes",
        "rate_stderr",
        "amplitude_stderr",
        "rate_ci",
        "amplitude_ci",
    }
)


@dataclass(frozen=True)
class SeriesStyle:
    """How one series of a several-series figure is drawn (#371).

    Attributes
    ----------
    color
        The series colour, from `SERIES_COLORS`.
    linestyle
        The Matplotlib line style, from `SERIES_LINESTYLES`.
    hatch
        The bar hatch that stands in for the line style where the series is drawn as bars.
    """

    color: str
    linestyle: str
    hatch: str


def series_style(index: int) -> SeriesStyle:
    """The style of the series at position ``index`` of a figure (`SERIES_COLOR_CYCLE`).

    The cycle is deterministic: the same position always gets the same style, so
    re-rendering one composition gives the same figure. Colours change first, then the
    line style; after 16 series the cycle starts again.

    Parameters
    ----------
    index
        The zero-based position of the series in the figure's ordered sequence.

    Returns
    -------
    SeriesStyle
        The colour, line style and bar hatch of that series.

    Raises
    ------
    ValueError
        ``index`` is negative.
    """
    if index < 0:
        raise ValueError(f"a series index cannot be negative, got {index}")
    n_colors = len(SERIES_COLORS)
    k = index % (n_colors * len(SERIES_LINESTYLES))
    return SeriesStyle(
        SERIES_COLORS[k % n_colors],
        SERIES_LINESTYLES[k // n_colors],
        _SERIES_HATCHES[k // n_colors],
    )


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO).

    Mirrors the ``_app_version`` helper deliberately duplicated across the
    ``tether.project`` writers (leakage/gamma/correct/export/…) rather than shared:
    resolve the git-derived package version, never raising from a stamp.
    """
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; __version__ has its own fallback
        return "0.0.0+unknown"


@dataclass(frozen=True)
class PlotExportResult:
    """The artifacts a plot export wrote.

    ``paths`` maps each requested format (``"pdf"``/``"svg"``/``"png"``) to the file
    written; ``provenance_path`` is the single ``<stem>.provenance.json`` sidecar
    describing all of them.
    """

    stem: Path
    paths: dict[str, Path]
    provenance_path: Path
    formats: tuple[str, ...]


def _resolve_created(created_utc: str | None) -> tuple[str, datetime]:
    """Return ``(iso_string, datetime)`` for the stamp, from a caller-supplied ISO
    timestamp or *now* (offset-aware UTC). The datetime feeds the PDF/SVG document
    date; the string feeds the sidecar, footer, and PNG metadata — so all agree."""
    if created_utc is None:
        now = datetime.now(UTC)
        return now.isoformat(), now
    try:
        parsed = datetime.fromisoformat(created_utc)
    except ValueError:  # pragma: no cover - defensive; a stamp must never crash export
        parsed = datetime.now(UTC)
    return created_utc, parsed


def _embedded_metadata(
    fmt: str, *, title: str, kind: str, version: str, created_iso: str, created: datetime
) -> dict[str, object]:
    """Per-format document metadata embedded in the file (§7.9 provenance-in-file).

    Each backend accepts a different metadata schema, so the dict is built per format:
    the PDF/SVG *creation date* is pinned to the stamp time (reproducible re-export),
    and the Tether version + export kind travel in the standard document fields.
    """
    creator = f"Tether {version}"
    if fmt == "pdf":
        # matplotlib PDF backend: Title/Author/Subject/Creator/CreationDate (datetime).
        return {
            "Title": title,
            "Author": "Tether",
            "Subject": kind,
            "Creator": creator,
            "CreationDate": created,
        }
    if fmt == "svg":
        # matplotlib SVG backend: Dublin-Core keys; Date pins the otherwise auto-now date.
        return {"Title": title, "Description": f"{kind} — {creator}", "Date": created_iso}
    if fmt == "png":
        # matplotlib Agg PNG: latin-1 text chunks; the PNG "Creation Time" keyword.
        return {
            "Software": creator,
            "Title": title,
            "Description": kind,
            "Creation Time": created_iso,
        }
    return {}


def _add_footer(figure: Figure, *, version: str, created_iso: str, kind: str) -> None:
    """Draw the visible provenance stamp in the figure's bottom margin."""
    figure.text(
        0.01,
        0.005,
        f"Tether {version} · {kind}",
        fontsize=6,
        ha="left",
        va="bottom",
        alpha=0.55,
    )
    figure.text(
        0.99,
        0.005,
        created_iso,
        fontsize=6,
        ha="right",
        va="bottom",
        alpha=0.55,
    )


def _merged_display_parameters(figure: Figure, parameters: dict[str, object]) -> dict[str, object]:
    """The figure's own display record, checked against any the caller passed.

    A ``render_*`` helper records how it drew the data on the figure; the caller may
    repeat it under ``parameters["display"]`` but may not contradict it, or the
    provenance would describe a picture other than the one written.
    """
    drawn = dict(getattr(figure, _DISPLAY_ATTR, None) or {})
    given = parameters.get("display")
    if given is None:
        return drawn
    if not isinstance(given, dict):
        raise ValueError(f"parameters['display'] must be a dict, got {type(given).__name__}")
    clashes = sorted(k for k in given.keys() & drawn.keys() if given[k] != drawn[k])
    if clashes:
        detail = ", ".join(f"{k}: given {given[k]!r}, drawn {drawn[k]!r}" for k in clashes)
        raise ValueError(f"parameters['display'] contradicts how the figure was drawn ({detail})")
    return {**given, **drawn}


def _merged_series(figure: Figure, parameters: dict[str, object]) -> list[dict[str, object]] | None:
    """The figure's per-series records, each merged with what the caller gave for it.

    A ``render_*`` helper given a sequence of series records each one (its label, its
    result type and the parameters the result carries) on the figure. The caller may add
    keys of its own per series under ``parameters["series"]`` (the selection, the levers,
    the frame time), one dict per series in figure order, but may not contradict a key the
    figure recorded. A figure drawn from a bare result records nothing, and the caller's
    ``"series"`` entry, if any, passes through as given.
    """
    drawn = getattr(figure, _SERIES_ATTR, None)
    given = parameters.get("series")
    if drawn is None:
        return None
    if given is None:
        return [dict(record) for record in drawn]
    if not isinstance(given, list | tuple) or len(given) != len(drawn):
        count = len(given) if isinstance(given, list | tuple) else type(given).__name__
        raise ValueError(
            f"parameters['series'] must be a list of one dict per drawn series ({len(drawn)}), "
            f"got {count}"
        )
    merged: list[dict[str, object]] = []
    for k, (extra, record) in enumerate(zip(given, drawn, strict=True)):
        if not isinstance(extra, dict):
            raise ValueError(
                f"parameters['series'][{k}] must be a dict, got {type(extra).__name__}"
            )
        extra = _finite_record(extra)  # a caller's NaN would otherwise reach the file as NaN
        clashes = sorted(key for key in extra.keys() & record.keys() if extra[key] != record[key])
        if clashes:
            raise ValueError(
                f"parameters['series'][{k}] contradicts series {record['label']!r} as drawn "
                f"(keys {', '.join(clashes)})"
            )
        merged.append({**extra, **record})
    return merged


def export_figure(
    figure: Figure,
    out_stem: str | os.PathLike[str],
    *,
    tether_export: str,
    source: str,
    parameters: dict[str, object],
    title: str | None = None,
    formats: tuple[str, ...] = DEFAULT_PLOT_FORMATS,
    dpi: int = DEFAULT_EXPORT_DPI,
    stamp: bool = True,
    created_utc: str | None = None,
) -> PlotExportResult:
    """Write ``figure`` to a vector PDF, a vector SVG, and a raster PNG, all stamped.

    Parameters
    ----------
    figure
        The rendered figure (typically from a ``render_*`` helper).
    out_stem
        The output path *without* an extension; each format appends its own
        (``<stem>.pdf``/``.svg``/``.png``). The sidecar is ``<stem>.provenance.json``.
    tether_export
        The export kind (e.g. ``"plot:tdp"``), recorded in the stamp and metadata.
    source
        The source project / dataset identifier for the provenance sidecar.
    parameters
        The plot parameters to record (bins, ranges, model name, …) — the §7.9
        *"stamped with provenance and parameters"* payload. How the ``render_*``
        helper drew the data (for example the TDP ``color_scale``) is added under
        ``"display"`` from the figure itself; a ``"display"`` entry here that
        contradicts the figure raises `ValueError`. A figure drawn from a sequence
        of ``(label, result)`` series adds ``"series"``: one record per series, in figure
        order, with its ``label``, its ``result`` type, the ``parameters`` that result
        carries and its ``display`` record. To record more per series (the selection,
        its levers, its frame time), pass ``parameters["series"]`` as a list of one dict
        per series; each is merged into its record, and a key that contradicts the record
        raises `ValueError`.
    title
        Optional human title embedded in the document metadata; defaults to
        ``tether_export``.
    formats
        Which of ``{"pdf", "svg", "png"}`` to write (default all three).
    dpi
        Raster (PNG) resolution; ignored by the vector formats.
    stamp
        Whether to draw the visible on-figure footer (default ``True``).
    created_utc
        Optional fixed ISO-8601 UTC timestamp (for reproducible exports); defaults
        to *now*.

    Returns
    -------
    PlotExportResult
        The stem, the ``{format: path}`` map, and the provenance sidecar path.
    """
    unknown = set(formats) - set(DEFAULT_PLOT_FORMATS)
    if unknown:
        raise ValueError(
            f"unknown plot export format(s) {sorted(unknown)}; expected a subset of "
            f"{list(DEFAULT_PLOT_FORMATS)}"
        )
    if not formats:
        raise ValueError("at least one export format is required")

    display = _merged_display_parameters(figure, parameters)
    series = _merged_series(figure, parameters)
    if series is None and "series" in parameters:
        # A bare-result figure passes the caller's series entry through; it is held to
        # the same strict-JSON rule as the records a figure makes.
        parameters = {**parameters, "series": _finite(parameters["series"])}

    stem = Path(out_stem)
    version = _app_version()
    created_iso, created = _resolve_created(created_utc)
    kind = tether_export
    doc_title = title if title is not None else tether_export

    if stamp:
        _add_footer(figure, version=version, created_iso=created_iso, kind=kind)

    paths: dict[str, Path] = {}
    with rc_context({"svg.hashsalt": _SVG_HASHSALT}):
        for fmt in DEFAULT_PLOT_FORMATS:
            if fmt not in formats:
                continue
            out_path = stem.with_name(stem.name + f".{fmt}")
            figure.savefig(
                out_path,
                format=fmt,
                dpi=dpi,
                metadata=_embedded_metadata(
                    fmt,
                    title=doc_title,
                    kind=kind,
                    version=version,
                    created_iso=created_iso,
                    created=created,
                ),
            )
            paths[fmt] = out_path

    provenance = write_provenance_sidecar(
        stem,
        tether_export=tether_export,
        source=source,
        parameters={
            **parameters,
            **({"display": display} if display else {}),
            **({"series": series} if series is not None else {}),
            "formats": [f for f in DEFAULT_PLOT_FORMATS if f in formats],
            "dpi": int(dpi),
            "outputs": {f: p.name for f, p in paths.items()},
        },
        created_utc=created_iso,
    )
    return PlotExportResult(
        stem=stem,
        paths=paths,
        provenance_path=provenance,
        formats=tuple(f for f in DEFAULT_PLOT_FORMATS if f in formats),
    )


def _new_figure(figsize: tuple[float, float] | None) -> Figure:
    """A constrained-layout `Figure` (no pyplot, so no GUI backend)."""
    return Figure(figsize=figsize or DEFAULT_FIGSIZE, layout="constrained")


def _bin_centers(edges: np.ndarray) -> np.ndarray:
    edges = np.asarray(edges, dtype="float64")
    return 0.5 * (edges[:-1] + edges[1:])


# ------------------------------------------------------------ several series (#371)


def _as_series(data: object, types: tuple[type, ...], what: str) -> list[tuple[str, Any]] | None:
    """``data`` as a validated list of ``(label, result)`` pairs, or ``None`` when bare.

    A bare result (an instance of ``types``) is ``None``, so the caller draws it exactly as
    before. Anything else must be a non-empty ordered sequence of pairs whose labels are
    distinct non-empty strings and whose results are instances of ``types``.
    """
    if isinstance(data, types):
        return None
    if isinstance(data, str | bytes) or not isinstance(data, Sequence):
        raise TypeError(
            f"expected a {what} or an ordered sequence of (label, {what}) pairs, "
            f"got {type(data).__name__}"
        )
    series = list(data)
    if not series:
        raise ValueError("at least one series is required")
    seen: set[str] = set()
    for i, item in enumerate(series):
        if not (isinstance(item, tuple) and len(item) == 2):
            raise TypeError(f"series {i} must be a (label, {what}) pair, got {type(item).__name__}")
        label, result = item
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"series {i} needs a non-empty string label, got {label!r}")
        if label in seen:
            raise ValueError(f"two series are labelled {label!r}; each series needs its own label")
        if not isinstance(result, types):
            raise TypeError(f"series {label!r} is a {type(result).__name__}, not a {what}")
        seen.add(label)
    return series


def _aligned(given: object, n: int, kind: type, what: str, *, bare: bool) -> list[Any]:
    """A per-series companion argument (an overlay, a cloud) as one entry per series.

    ``None`` is ``None`` for every series. A single ``kind`` belongs to one series only, so
    it is accepted for a bare result or a one-series sequence. Otherwise ``given`` must be
    a sequence of ``n`` entries, each a ``kind`` or ``None``.
    """
    if given is None:
        return [None] * n
    if isinstance(given, kind):
        if n == 1:
            return [given]
        raise ValueError(
            f"one {what} cannot belong to {n} series; pass a sequence of one {what} "
            "(or None) per series, in series order"
        )
    if bare or isinstance(given, str | bytes) or not isinstance(given, Sequence):
        raise TypeError(f"{what} must be a {kind.__name__} or None, got {type(given).__name__}")
    entries = list(given)
    if len(entries) != n:
        raise ValueError(f"{len(entries)} {what}s were given for {n} series; give one per series")
    for i, entry in enumerate(entries):
        if entry is not None and not isinstance(entry, kind):
            raise TypeError(
                f"{what} {i} must be a {kind.__name__} or None, got {type(entry).__name__}"
            )
    return entries


def _same(a: object, b: object) -> bool:
    """Whether two grid values are identical (arrays element for element, no tolerance)."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        a_arr, b_arr = np.asarray(a), np.asarray(b)
        return a_arr.shape == b_arr.shape and bool(np.array_equal(a_arr, b_arr))
    return a == b


def _shown(value: object) -> str:
    """A grid value as the refusal message names it."""
    if isinstance(value, np.ndarray):
        flat = np.asarray(value).ravel()
        if flat.size == 0:
            return "no values"
        return f"{flat.size} values from {flat[0]:g} to {flat[-1]:g}"
    return repr(value)


def _difference(name: str, ours: object, theirs: object) -> str:
    """How one grid value differs from the first series', as the refusal states it.

    Two arrays of one shape are told apart by their first unequal entry, since a summary
    of their size and ends can read the same for both; anything else by its summary. The
    entries are written in full (``repr``), because a shortened form can print two
    unequal values alike.
    """
    if isinstance(ours, np.ndarray) and isinstance(theirs, np.ndarray):
        a, b = np.asarray(ours), np.asarray(theirs)
        if a.shape == b.shape:
            index = tuple(int(i) for i in np.argwhere(a != b)[0])
            at = index[0] if len(index) == 1 else index
            return f"{name} has {b[index].item()!r} at index {at}, not {a[index].item()!r}"
    return f"{name} is {_shown(theirs)}, not {_shown(ours)}"


#: A grid key: the name a refusal gives it, and how to read it off a result.
_GridKey = tuple[str, Callable[[Any], object]]


def _require_shared_grid(series: Sequence[tuple[str, Any]], keys: Sequence[_GridKey]) -> None:
    """Refuse two series that are not on one grid; never resample either.

    Every series is compared with the first. The first key that differs is named with
    both values, so the reason says what to change.
    """
    first_label, first = series[0]
    for label, result in series[1:]:
        for name, read in keys:
            ours, theirs = read(first), read(result)
            if not _same(ours, theirs):
                raise ValueError(
                    f"series {label!r} is not on the grid of series {first_label!r}: its "
                    f"{_difference(name, ours, theirs)}. Series on one figure "
                    "must share their grid, and Tether does not resample one onto another"
                )


def _range(value: tuple[float, float]) -> tuple[float, float]:
    """A ``(low, high)`` range as plain floats, so a NumPy and a Python pair compare equal."""
    low, high = value
    return float(low), float(high)


#: The marker `_parameter_value` returns for a field that is data, not a parameter.
_SKIP = object()


def _finite(value: object) -> object:
    """``value`` with every NaN or infinity, at any depth of a list or dict, set to ``None``.

    A result may carry a non-finite parameter on purpose (the ``alpha`` of an empty alpha
    shape, the parameters of a failed fit). JSON has no token for it, and a provenance
    file must stay readable by a strict JSON reader, so it is recorded as ``null``.
    """
    if isinstance(value, dict):
        return {key: _finite(v) for key, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    if isinstance(value, np.generic):
        return _finite(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _finite_record(record: dict[Any, object]) -> dict[Any, object]:
    """A caller's per-series dict with every non-finite value, at any depth, set to ``None``."""
    return {key: _finite(value) for key, value in record.items()}


def _parameter_value(name: str, value: object) -> object:
    """``value`` as a provenance entry, or `_SKIP` when it is data rather than a parameter."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, np.generic):
        return _finite(value.item())
    if isinstance(value, int | float):
        return _finite(value)
    if isinstance(value, np.ndarray):
        return _finite(np.asarray(value).tolist()) if name in _PARAMETER_ARRAYS else _SKIP
    if is_dataclass(value) and not isinstance(value, type):
        return _result_parameters(value)
    if isinstance(value, tuple) and all(
        isinstance(v, bool | str | int | float | np.generic) for v in value
    ):
        return _finite([v.item() if isinstance(v, np.generic) else v for v in value])
    return _SKIP


def _result_parameters(result: object) -> dict[str, object]:
    """The parameters a result carries: its scalars, ranges and grid, never its data.

    A nested result (the histogram of a bootstrap, the fit of a dwell analysis) is
    recorded as its own dict. A per-condition overlay also names its conditions and their
    molecule counts, which it holds only inside its histograms.
    """
    out: dict[str, object] = {}
    for f in fields(result):  # type: ignore[arg-type]
        entry = _parameter_value(f.name, getattr(result, f.name))
        if entry is not _SKIP:
            out[f.name] = entry
    if isinstance(result, PerConditionHistograms):
        out["condition_ids"] = list(result.condition_ids)
        out["molecule_counts"] = dict(result.molecule_counts)
    return out


def _record_series(
    figure: Figure,
    series: Sequence[tuple[str, Any]],
    *,
    display: Sequence[dict[str, object]] | None = None,
    companions: Sequence[dict[str, object]] | None = None,
) -> None:
    """Record each drawn series on ``figure`` for `export_figure` (``parameters.series``).

    ``display`` gives each series' colour and line style, or its panel. Without it the
    figure is a one-series sequence drawn as the bare result, in that result's own
    colours, and its record says so (``{"as_bare_result": True}``).
    """
    records: list[dict[str, object]] = []
    for k, (label, result) in enumerate(series):
        record: dict[str, object] = {
            "label": label,
            "result": type(result).__name__,
            "parameters": _result_parameters(result),
        }
        if companions is not None and companions[k]:
            record.update(companions[k])
        record["display"] = {"as_bare_result": True} if display is None else dict(display[k])
        records.append(record)
    setattr(figure, _SERIES_ATTR, records)


def _series_marker(index: int) -> str:
    """The point marker of series ``index``: it steps with the line style, never the colour.

    A series drawn as points alone has no line to show its style, so its marker carries
    the style slot instead, and series 5 is never drawn like series 1.
    """
    n_colors = len(SERIES_COLORS)
    return _SERIES_MARKERS[(index % (n_colors * len(SERIES_LINESTYLES))) // n_colors]


def _set_display(figure: Figure, **entries: object) -> None:
    """Add ``entries`` to the figure's display record (see `_DISPLAY_ATTR`)."""
    setattr(figure, _DISPLAY_ATTR, {**(getattr(figure, _DISPLAY_ATTR, None) or {}), **entries})


def _overlay_style(k: int) -> dict[str, object]:
    """The display record of the ``k``-th overlaid series (its colour and line style)."""
    style = series_style(k)
    return {"color": style.color, "linestyle": style.linestyle}


def _drawn(label: str) -> str:
    r"""A series label as it is drawn: literal text, never Matplotlib math text.

    A label is the caller's name for a selection, so a ``$`` in it is a character, not
    the start of math: ``$\unknown$`` would otherwise be parsed as math text and fail
    when the figure is saved. Each ``$`` is escaped, which Matplotlib draws as a plain
    dollar sign both in plain text and beside intended math such as a fit's ``$R^2$``.
    The provenance keeps the label as given.
    """
    return label.replace("$", r"\$")


def _labelled(label: str, n_molecules: int | None) -> str:
    """A legend entry: the series label and, when known, its molecule count."""
    return _drawn(label) if n_molecules is None else f"{_drawn(label)} (N = {n_molecules})"


def _series_legend(ax: Any, handles: list[Any], **kwargs: Any) -> None:
    """Draw a legend of exactly ``handles``, under the labels they were drawn with.

    Matplotlib's automatic legend leaves out every artist whose label starts with an
    underscore, which would drop a series a caller named ``_control`` while its curve is
    still drawn. The handles are passed explicitly, in drawing order, so every series
    keeps its entry under the name the caller gave it.
    """
    ax.legend(handles=handles, labels=[h.get_label() for h in handles], **kwargs)


def _panel_shape(n: int) -> tuple[int, int]:
    """``(rows, columns)`` of a near-square grid of ``n`` small multiples."""
    cols = math.ceil(math.sqrt(n))
    return math.ceil(n / cols), cols


def _panels(figure: Figure, n: int, *, sharey: bool = True) -> list[Any]:
    """``n`` small-multiple axes in reading order, sharing their axes; spare cells removed."""
    rows, cols = _panel_shape(n)
    grid = figure.subplots(rows, cols, sharex=True, sharey=sharey, squeeze=False).ravel()
    for spare in grid[n:]:
        spare.remove()
    return list(grid[:n])


def _panels_figsize(figsize: tuple[float, float] | None, n: int) -> tuple[float, float]:
    """The caller's ``figsize``, else one that gives each small multiple a readable panel."""
    if figsize is not None:
        return figsize
    rows, cols = _panel_shape(n)
    return (4.2 * cols + 1.2, 3.6 * rows + 0.6)


def _shared_norm(arrays: Sequence[np.ndarray]) -> Normalize:
    """One linear colour scale over every panel, so a colour means one value on all."""
    finite = [a[np.isfinite(a)] for a in arrays]
    values = np.concatenate([f.ravel() for f in finite]) if finite else np.empty(0)
    if values.size == 0:
        return Normalize(vmin=0.0, vmax=1.0)
    return Normalize(vmin=float(values.min()), vmax=float(values.max()))


def _small_multiples_display(
    figure: Figure, n: int, *, color_scale: bool = True
) -> list[dict[str, object]]:
    """Mark ``figure`` as small multiples and return each series' panel record.

    ``color_scale`` says the panels share one colour scale (every heatmap and raster
    does); panels without one, the per-condition overlay or clouds with no density
    surface, record none.
    """
    entries: dict[str, object] = {"series_layout": "small_multiples"}
    if color_scale:
        entries["series_color_scale"] = "shared"
    _set_display(figure, **entries)
    return [{"panel": k} for k in range(n)]


def _overlay_display(figure: Figure, n: int) -> list[dict[str, object]]:
    """Mark ``figure`` as an overlay drawn with the cycle and return each series' style."""
    _set_display(figure, series_layout="overlay", series_cycle=SERIES_COLOR_CYCLE)
    return [_overlay_style(k) for k in range(n)]


def render_histogram1d(
    hist: Histogram1D
    | HistogramBootstrapCI
    | Sequence[tuple[str, Histogram1D | HistogramBootstrapCI]],
    *,
    overlay: ModelGaussianOverlay | Sequence[ModelGaussianOverlay | None] | None = None,
    title: str = "FRET-efficiency histogram (A1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the A1 1-D population apparent-E histogram (Appendix C A1).

    Accepts a bare [`Histogram1D`][tether.analysis.histogram.Histogram1D] or a
    [`HistogramBootstrapCI`][tether.analysis.histogram.HistogramBootstrapCI] (whose per-bin
    percentile band is drawn as a shaded region). An optional
    [`ModelGaussianOverlay`][tether.analysis.histogram.ModelGaussianOverlay] draws the idealized
    model's per-state Gaussians and their sum.

    **Several series** are overlaid on one axis: each is an unfilled step outline in its
    `series_style`, labelled with its label and ``N``; a bootstrap band is shaded in the
    series colour. A model overlay of a series is drawn as its total only, dotted in the
    series colour; the per-state components are left out, because their state colours
    would read as more series.

    Parameters
    ----------
    hist
        One histogram, or an ordered sequence of ``(label, histogram)`` series. A
        one-series sequence draws the same figure as the bare histogram.
    overlay
        For one histogram, one model overlay or ``None``. For several series, a sequence
        of one overlay (or ``None``) per series, in series order.
    title
        The axes title.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].

    Raises
    ------
    ValueError
        Two series differ in their bin edges, value range or density normalization, or a
        single overlay was given for several series.
    """
    series = _as_series(
        hist, (Histogram1D, HistogramBootstrapCI), "Histogram1D or HistogramBootstrapCI"
    )
    overlays = _aligned(
        overlay,
        1 if series is None else len(series),
        ModelGaussianOverlay,
        "overlay",
        bare=series is None,
    )
    if series is None or len(series) == 1:
        one = hist if series is None else series[0][1]
        fig = _one_histogram1d(one, overlay=overlays[0], title=title, figsize=figsize)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series, companions=_overlay_records(overlays))
        return fig
    bases = [
        (label, h.histogram if isinstance(h, HistogramBootstrapCI) else h) for label, h in series
    ]
    _require_shared_grid(
        bases,
        (
            ("bin edges", lambda h: h.bin_edges),
            ("value range", lambda h: _range(h.value_range)),
            ("density normalization", lambda h: h.density),
        ),
    )
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    handles: list[Any] = []
    for k, ((label, h), (_, base), model) in enumerate(zip(series, bases, overlays, strict=True)):
        style = series_style(k)
        edges = np.asarray(base.bin_edges, dtype="float64")
        handle = ax.stairs(
            np.asarray(base.counts, dtype="float64"),
            edges,
            fill=False,
            color=style.color,
            linestyle=style.linestyle,
            lw=1.5,
            label=_labelled(label, base.n_molecules),
        )
        handles.append(handle)
        if isinstance(h, HistogramBootstrapCI):
            band = ax.fill_between(
                _bin_centers(edges),
                np.asarray(h.ci_low, dtype="float64"),
                np.asarray(h.ci_high, dtype="float64"),
                step="mid",
                color=style.color,
                alpha=0.2,
                linewidth=0,
                label=f"{_drawn(label)}: {h.ci_level:.0%} CI",
            )
            handles.append(band)
        if model is not None:
            (line,) = ax.plot(
                np.asarray(model.x, dtype="float64"),
                np.asarray(model.total, dtype="float64"),
                color=style.color,
                lw=1.0,
                ls=(0, (1, 1)),
                label=f"{_drawn(label)}: model",
            )
            handles.append(line)
    first = bases[0][1]
    ax.set_xlim(first.value_range)
    ax.set_xlabel("Apparent FRET efficiency $E$")
    ax.set_ylabel("Density" if first.density else "Counts")
    ax.set_title(title)
    _series_legend(ax, handles, loc="upper left", fontsize=8, frameon=False)
    _record_series(
        fig,
        series,
        display=_overlay_display(fig, len(series)),
        companions=_overlay_records(overlays),
    )
    return fig


def _overlay_records(overlays: Sequence[ModelGaussianOverlay | None]) -> list[dict[str, object]]:
    """The model overlay of each series, as its provenance record (``None`` when none).

    A series drawn without a model still records ``overlay: None``, so a caller key
    claiming a model that was never drawn contradicts the figure and is refused.
    """
    return [{"overlay": None if o is None else _result_parameters(o)} for o in overlays]


def _one_histogram1d(
    hist: Histogram1D | HistogramBootstrapCI,
    *,
    overlay: ModelGaussianOverlay | None = None,
    title: str = "FRET-efficiency histogram (A1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_histogram1d`` has always drawn."""
    if isinstance(hist, HistogramBootstrapCI):
        ci = hist
        base = hist.histogram
    else:
        ci = None
        base = hist
    edges = np.asarray(base.bin_edges, dtype="float64")
    counts = np.asarray(base.counts, dtype="float64")
    centers = _bin_centers(edges)

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    ax.stairs(counts, edges, fill=True, color="#4c72b0", alpha=0.7, label="histogram")
    if ci is not None:
        ax.fill_between(
            centers,
            np.asarray(ci.ci_low, dtype="float64"),
            np.asarray(ci.ci_high, dtype="float64"),
            step="mid",
            color="#4c72b0",
            alpha=0.25,
            linewidth=0,
            label=f"{ci.ci_level:.0%} CI",
        )
    if overlay is not None:
        x = np.asarray(overlay.x, dtype="float64")
        ax.plot(x, np.asarray(overlay.total, dtype="float64"), color="k", lw=1.5, label="model")
        components = np.asarray(overlay.components, dtype="float64")
        for k in range(components.shape[0]):
            ax.plot(
                x,
                components[k],
                color=_STATE_COLORS[k % len(_STATE_COLORS)],
                lw=1.0,
                ls="--",
                alpha=0.8,
            )
    ax.set_xlim(base.value_range)
    ax.set_xlabel("Apparent FRET efficiency $E$")
    ax.set_ylabel("Density" if base.density else "Counts")
    ax.set_title(title)
    n_mol = base.n_molecules
    if n_mol is not None:
        ax.annotate(
            f"N = {n_mol}",
            xy=(0.97, 0.95),
            xycoords="axes fraction",
            ha="right",
            va="top",
            fontsize=9,
        )
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    return fig


def render_histogram2d(
    hist: Histogram2D | Sequence[tuple[str, Histogram2D]],
    *,
    title: str = "Time-vs-signal heatmap (A2)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the A2 2-D time-vs-signal occupancy heatmap (Appendix C A2).

    **Several series** are drawn as small multiples, one panel per series in series order
    and titled with its label: one heatmap overplotted on another hides both. The panels
    share their axes and one colour scale, so a colour means the same value in every
    panel, and one colour bar serves them all.

    Parameters
    ----------
    hist
        One heatmap, or an ordered sequence of ``(label, heatmap)`` series. A one-series
        sequence draws the same figure as the bare heatmap.
    title
        The axes title; for several series, the figure title above the panels.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][] for one heatmap and to a
        size that fits the panels for several.

    Raises
    ------
    ValueError
        Two series differ in their time or signal bin edges, frame time, signal range or
        density normalization.
    """
    series = _as_series(hist, (Histogram2D,), "Histogram2D")
    if series is None or len(series) == 1:
        fig = _one_histogram2d(
            hist if series is None else series[0][1], title=title, figsize=figsize
        )  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series,
        (
            ("time bin edges", lambda h: h.time_edges),
            ("signal bin edges", lambda h: h.signal_edges),
            ("frame time (time_dt)", lambda h: float(h.time_dt)),
            ("signal range", lambda h: _range(h.signal_range)),
            ("density normalization", lambda h: h.density),
        ),
    )
    norm = _shared_norm([np.asarray(h.counts, dtype="float64") for _, h in series])
    fig = _new_figure(_panels_figsize(figsize, len(series)))
    axes = _panels(fig, len(series))
    mesh = None
    for ax, (label, h) in zip(axes, series, strict=True):
        counts = np.asarray(h.counts, dtype="float64")
        mesh = ax.pcolormesh(
            np.asarray(h.time_edges, dtype="float64"),
            np.asarray(h.signal_edges, dtype="float64"),
            counts.T,
            cmap="magma",
            norm=norm,
            shading="flat",
        )
        ax.set_xlabel("Time (s)" if h.time_dt != 1.0 else "Frame")
        ax.set_ylabel("Apparent FRET efficiency $E$")
        ax.set_ylim(h.signal_range)
        ax.set_title(_drawn(label))
        ax.annotate(
            f"N = {h.n_molecules}",
            xy=(0.97, 0.95),
            xycoords="axes fraction",
            ha="right",
            va="top",
            color="white",
            fontsize=9,
        )
    fig.colorbar(mesh, ax=axes, label="Density" if series[0][1].density else "Counts")
    fig.suptitle(title)
    _record_series(fig, series, display=_small_multiples_display(fig, len(series)))
    return fig


def _one_histogram2d(
    hist: Histogram2D,
    *,
    title: str = "Time-vs-signal heatmap (A2)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_histogram2d`` has always drawn."""
    counts = np.asarray(hist.counts, dtype="float64")  # (time_bins, signal_bins)
    time_edges = np.asarray(hist.time_edges, dtype="float64")
    signal_edges = np.asarray(hist.signal_edges, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    # counts is [time, signal]; show time on x, signal on y → transpose so rows=signal.
    mesh = ax.pcolormesh(time_edges, signal_edges, counts.T, cmap="magma", shading="flat")
    fig.colorbar(mesh, ax=ax, label="Density" if hist.density else "Counts")
    ax.set_xlabel("Time (s)" if hist.time_dt != 1.0 else "Frame")
    ax.set_ylabel("Apparent FRET efficiency $E$")
    ax.set_ylim(hist.signal_range)
    ax.set_title(title)
    ax.annotate(
        f"N = {hist.n_molecules}",
        xy=(0.97, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        color="white",
        fontsize=9,
    )
    return fig


def _tdp_norm(counts: np.ndarray, color_scale: str, *, smoothed: bool = False) -> Normalize:
    """The colour normalization for a TDP ``counts`` array.

    ``"log"`` is zero-safe: `LogNorm` masks every bin
    ``<= 0`` instead of taking ``log(0) = -inf``, and its limits come from the positive
    finite bins only. The floor sits `_TDP_LOG_FLOOR_DECADES` below the smallest
    non-empty bin, so the weakest transition is drawn visibly above the floor colour
    that marks an empty bin — or, for a ``smoothed`` view, `_TDP_SMOOTH_LOG_FLOOR` times
    its peak. With no positive bin at all (an empty TDP) there is no magnitude to span,
    so the limits are a nominal ``[1, 10]`` and every bin is masked.
    """
    positive = counts[np.isfinite(counts) & (counts > 0)]
    if color_scale == "log":
        if positive.size == 0:
            return LogNorm(vmin=1.0, vmax=10.0)
        peak = float(positive.max())
        if smoothed:
            floor = peak * _TDP_SMOOTH_LOG_FLOOR
        else:
            floor = float(positive.min()) / 10.0**_TDP_LOG_FLOOR_DECADES
        return LogNorm(vmin=floor, vmax=peak)
    finite = counts[np.isfinite(counts)]
    vmax = float(finite.max()) if finite.size else 1.0
    return Normalize(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def render_transition_density(
    tdp: (
        TransitionDensityPlot
        | SmoothedTransitionDensity
        | Sequence[tuple[str, TransitionDensityPlot | SmoothedTransitionDensity]]
    ),
    *,
    title: str = "Transition-density plot (B1)",
    figsize: tuple[float, float] | None = None,
    color_scale: str = DEFAULT_TDP_COLOR_SCALE,
) -> Figure:
    """Render the B1 real transition-density plot: initial vs final idealized E.

    **Several series** are drawn as small multiples, one panel per series in series order
    and titled with its label, because one density overplotted on another hides both. The
    panels share their axes and one colour scale (log or linear, over the bins of every
    panel), so a colour means the same density in every panel, and one colour bar serves
    them all.

    Parameters
    ----------
    tdp
        The binned transition density to draw, or its smoothed view from
        [`smooth_transition_density`][tether.analysis.tdp.smooth_transition_density]. The
        counts are drawn bin by bin; the smoothed view is drawn as an image on its
        interpolation grid, as the published ``checkTDP.py`` draws it, and its smoothing
        settings are recorded in the provenance sidecar under
        ``parameters["display"]["smoothing"]``. Or an ordered sequence of
        ``(label, density)`` series: a one-series sequence draws the same figure as the
        bare density, and several series must all be binned densities.
    title
        The axes title; for several series, the figure title above the panels.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][] for one density and to a
        size that fits the panels for several.
    color_scale
        ``"log"`` (default, [`DEFAULT_TDP_COLOR_SCALE`][]) or ``"linear"``. PRD
        Appendix C B1 names the TDP log-normalized, so a rare transition stays visible
        beside a dominant one. The log scale is zero-safe: an empty bin is drawn in the
        colour map's floor colour, never as ``-inf`` or a hole. It is display only — the
        drawn array is ``tdp.counts`` (or the smoothed ``tdp.values``) unchanged — and
        [`export_figure`][] records it in the provenance sidecar under
        ``parameters["display"]["color_scale"]``.

    Raises
    ------
    ValueError
        ``color_scale`` is unknown, two series differ in their signal bin edges, signal
        range or density normalization, or one of several series is a smoothed view.
    """
    series = _as_series(
        tdp, (TransitionDensityPlot, SmoothedTransitionDensity), "TransitionDensityPlot"
    )
    if series is None or len(series) == 1:
        one = tdp if series is None else series[0][1]
        fig = _one_transition_density(one, title=title, figsize=figsize, color_scale=color_scale)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    if color_scale not in TDP_COLOR_SCALES:
        raise ValueError(
            f"unknown TDP color_scale {color_scale!r}; expected one of {list(TDP_COLOR_SCALES)}"
        )
    for label, d in series:
        if isinstance(d, SmoothedTransitionDensity):
            raise ValueError(
                f"series {label!r} is a smoothed transition density. Several series are drawn "
                "as binned densities on one shared colour scale, and a smoothed view is not "
                "binned, so draw it on its own figure"
            )
    _require_shared_grid(
        series,
        (
            ("signal bin edges", lambda d: d.signal_edges),
            ("signal range", lambda d: _range(d.signal_range)),
            ("density normalization", lambda d: d.density),
        ),
    )
    pooled = np.concatenate([np.asarray(d.counts, dtype="float64").ravel() for _, d in series])
    norm = _tdp_norm(pooled, color_scale)
    cmap = colormaps["viridis"]
    cmap = cmap.with_extremes(bad=cmap(0.0), under=cmap(0.0))
    fig = _new_figure(_panels_figsize(figsize, len(series)))
    axes = _panels(fig, len(series))
    mesh = None
    for ax, (label, d) in zip(axes, series, strict=True):
        edges = np.asarray(d.signal_edges, dtype="float64")
        mesh = ax.pcolormesh(
            edges,
            edges,
            np.asarray(d.counts, dtype="float64").T,
            cmap=cmap,
            norm=norm,
            shading="flat",
        )
        lo, hi = d.signal_range
        ax.plot([lo, hi], [lo, hi], color="w", lw=0.8, ls=":", alpha=0.7)
        ax.set_xlim(d.signal_range)
        ax.set_ylim(d.signal_range)
        ax.set_xlabel("Initial $E$")
        ax.set_ylabel("Final $E$")
        ax.set_aspect("equal")
        ax.set_title(_drawn(label))
        ax.annotate(
            f"{d.n_transitions} transitions · N = {d.n_molecules}",
            xy=(0.03, 0.95),
            xycoords="axes fraction",
            ha="left",
            va="top",
            color="white",
            fontsize=8,
        )
    fig.colorbar(mesh, ax=axes, label="Density" if series[0][1].density else "Counts")
    fig.suptitle(title)
    _set_display(fig, color_scale=color_scale)
    _record_series(fig, series, display=_small_multiples_display(fig, len(series)))
    return fig


def _one_transition_density(
    tdp: TransitionDensityPlot | SmoothedTransitionDensity,
    *,
    title: str = "Transition-density plot (B1)",
    figsize: tuple[float, float] | None = None,
    color_scale: str = DEFAULT_TDP_COLOR_SCALE,
) -> Figure:
    """Draw a single result, the figure ``render_transition_density`` has always drawn."""
    if color_scale not in TDP_COLOR_SCALES:
        raise ValueError(
            f"unknown TDP color_scale {color_scale!r}; expected one of {list(TDP_COLOR_SCALES)}"
        )
    smoothed = isinstance(tdp, SmoothedTransitionDensity)
    counts_tdp = tdp.source if smoothed else tdp

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    # Empty (masked) bins take the colour map's floor colour, as a zero does on the
    # linear scale, so the white annotation and diagonal stay readable on both.
    cmap = colormaps["viridis"]
    cmap = cmap.with_extremes(bad=cmap(0.0), under=cmap(0.0))
    display: dict[str, object] = {"color_scale": color_scale}
    if smoothed:
        values = np.asarray(tdp.values, dtype="float64")  # (n, n) [initial, final]
        grid = np.asarray(tdp.grid, dtype="float64")
        lo, hi = counts_tdp.signal_range
        # Each grid point is a pixel centre; the image spans half a step past the ends. A
        # one-point grid (one bin, not interpolated) is that bin, which spans the range.
        half = 0.5 * (grid[-1] - grid[0]) / (grid.size - 1) if grid.size > 1 else 0.5 * (hi - lo)
        # values is [initial, final]; x=initial, y=final → transpose so rows=final.
        mesh = ax.imshow(
            values.T,
            cmap=cmap,
            norm=_tdp_norm(values, color_scale, smoothed=True),
            origin="lower",
            interpolation="none",
            extent=(grid[0] - half, grid[-1] + half, grid[0] - half, grid[-1] + half),
        )
        display["smoothing"] = {
            "smooth": [float(tdp.smooth[0]), float(tdp.smooth[1])],
            "interp_res": int(tdp.interp_res),
            "normalized": bool(tdp.normalized),
        }
        if tdp.normalized:
            label = "Normalized density"
        else:
            label = "Smoothed density" if counts_tdp.density else "Smoothed counts"
    else:
        counts = np.asarray(tdp.counts, dtype="float64")  # (bins, bins) [init, final]
        edges = np.asarray(tdp.signal_edges, dtype="float64")
        # counts is [initial, final]; x=initial, y=final → transpose so rows=final.
        mesh = ax.pcolormesh(
            edges,
            edges,
            counts.T,
            cmap=cmap,
            norm=_tdp_norm(counts, color_scale),
            shading="flat",
        )
        label = "Density" if tdp.density else "Counts"
    setattr(fig, _DISPLAY_ATTR, display)
    fig.colorbar(mesh, ax=ax, label=label)
    lo, hi = counts_tdp.signal_range
    ax.plot([lo, hi], [lo, hi], color="w", lw=0.8, ls=":", alpha=0.7)
    ax.set_xlim(counts_tdp.signal_range)
    ax.set_ylim(counts_tdp.signal_range)
    ax.set_xlabel("Initial $E$")
    ax.set_ylabel("Final $E$")
    ax.set_aspect("equal")
    ax.set_title(title)
    ax.annotate(
        f"{counts_tdp.n_transitions} transitions · N = {counts_tdp.n_molecules}",
        xy=(0.03, 0.95),
        xycoords="axes fraction",
        ha="left",
        va="top",
        color="white",
        fontsize=8,
    )
    return fig


def render_dwell_survival(
    analysis: DwellTimeAnalysis | Sequence[tuple[str, DwellTimeAnalysis]],
    *,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the B2 dwell-time survival curve for one state, with its exponential fit
    and (when fitted) a residuals subplot.

    **Several series** are overlaid on one log axis: each series' survival markers and fit
    curve take its colour, the fit its line style, and the markers (survival and residual)
    a shape that steps with the line style, so an unfitted fifth series is not drawn like
    the first. The residuals of every fitted series share the residual panel. The legend
    names each series' state and ``N``; the note lists each fit's rates and each series'
    dwell count and idealized FRET level.

    Parameters
    ----------
    analysis
        One dwell analysis, or an ordered sequence of ``(label, analysis)`` series. A
        one-series sequence draws the same figure as the bare analysis.
    title
        The axes title; defaults to one that names the state (for several series,
        ``"Dwell survival (B2)"``).
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].

    Raises
    ------
    ValueError
        Two series differ in their frame time ``dt``, so their dwell times are not on one
        axis.
    """
    series = _as_series(analysis, (DwellTimeAnalysis,), "DwellTimeAnalysis")
    if series is None or len(series) == 1:
        fig = _one_dwell_survival(
            analysis if series is None else series[0][1], title=title, figsize=figsize
        )  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(series, (("frame time (dt)", lambda a: float(a.dt)),))
    fitted = [a.fit is not None and a.fit.success for _, a in series]
    fig = _new_figure(figsize)
    if any(fitted):
        ax, ax_res = fig.subplots(2, 1, height_ratios=[3, 1], sharex=True)
        ax_res.axhline(0.0, color="k", lw=0.6)
    else:
        ax = fig.add_subplot()
        ax_res = None
    notes: list[str] = []
    handles: list[Any] = []
    markers = [_series_marker(k) for k in range(len(series))]
    for k, ((label, a), ok) in enumerate(zip(series, fitted, strict=True)):
        style = series_style(k)
        (points,) = ax.semilogy(
            np.asarray(a.tau, dtype="float64"),
            np.clip(np.asarray(a.survival, dtype="float64"), 1e-6, None),
            marker=markers[k],
            ls="none",
            ms=3,
            color=style.color,
            label=f"{_drawn(label)} · state {a.state} (N = {a.n_molecules})",
        )
        handles.append(points)
        note = f"{_drawn(label)}: {a.n_dwells} dwells · level {a.level:.2f}"
        if ok and a.fit is not None:
            fit = a.fit
            (curve,) = ax.semilogy(
                np.asarray(fit.tau, dtype="float64"),
                np.clip(np.asarray(fit.model_survival, dtype="float64"), 1e-6, None),
                color=style.color,
                ls=style.linestyle,
                lw=1.5,
                label=f"{_drawn(label)}: {fit.model} exp fit ($R^2$={fit.r_squared:.3f})",
            )
            handles.append(curve)
            rates = ", ".join(f"{r:.3g}" for r in np.asarray(fit.rates, dtype="float64"))
            note += f" · k = {rates}"
            if ax_res is not None:
                ax_res.plot(
                    np.asarray(fit.tau, dtype="float64"),
                    np.asarray(fit.residuals, dtype="float64"),
                    marker=markers[k],
                    ls="none",
                    ms=2,
                    color=style.color,
                )
        notes.append(note)
    unit = "Dwell time (s)" if series[0][1].dt != 1.0 else "Dwell time (frames)"
    ax.set_ylabel("Survival $S(t)$")
    ax.set_title(title if title is not None else "Dwell survival (B2)")
    _series_legend(ax, handles, loc="upper right", fontsize=7, frameon=False)
    ax.annotate(
        "\n".join(notes),
        xy=(0.03, 0.03),
        xycoords="axes fraction",
        ha="left",
        va="bottom",
        fontsize=7,
    )
    if ax_res is not None:
        ax_res.set_ylabel("Resid.")
        ax_res.set_xlabel(unit)
    else:
        ax.set_xlabel(unit)
    _record_series(
        fig,
        series,
        display=[
            {**style, "marker": marker}
            for style, marker in zip(_overlay_display(fig, len(series)), markers, strict=True)
        ],
    )
    return fig


def _one_dwell_survival(
    analysis: DwellTimeAnalysis,
    *,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_dwell_survival`` has always drawn."""
    tau = np.asarray(analysis.tau, dtype="float64")
    survival = np.asarray(analysis.survival, dtype="float64")
    fit = analysis.fit
    heading = title if title is not None else f"Dwell survival — state {analysis.state} (B2)"

    fig = _new_figure(figsize)
    if fit is not None and fit.success:
        axes = fig.subplots(2, 1, height_ratios=[3, 1], sharex=True)
        ax, ax_res = axes
    else:
        ax = fig.add_subplot()
        ax_res = None

    ax.semilogy(tau, np.clip(survival, 1e-6, None), "o", ms=3, color="#4c72b0", label="survival")
    if fit is not None and fit.success:
        ax.semilogy(
            np.asarray(fit.tau, dtype="float64"),
            np.clip(np.asarray(fit.model_survival, dtype="float64"), 1e-6, None),
            "-",
            color="k",
            lw=1.5,
            label=f"{fit.model} exp fit ($R^2$={fit.r_squared:.3f})",
        )
        rates = ", ".join(f"{r:.3g}" for r in np.asarray(fit.rates, dtype="float64"))
        ax.annotate(
            f"k = {rates}",
            xy=(0.97, 0.95),
            xycoords="axes fraction",
            ha="right",
            va="top",
            fontsize=9,
        )
    ax.set_ylabel("Survival $S(t)$")
    ax.set_title(heading)
    ax.legend(loc="upper right", fontsize=8, frameon=False)
    ax.annotate(
        f"{analysis.n_dwells} dwells · N = {analysis.n_molecules} · level {analysis.level:.2f}",
        xy=(0.03, 0.03),
        xycoords="axes fraction",
        ha="left",
        va="bottom",
        fontsize=8,
    )

    if ax_res is not None and fit is not None:
        ax_res.axhline(0.0, color="k", lw=0.6)
        ax_res.plot(
            np.asarray(fit.tau, dtype="float64"),
            np.asarray(fit.residuals, dtype="float64"),
            ".",
            ms=3,
            color="#c44e52",
        )
        ax_res.set_ylabel("Resid.")
        ax_res.set_xlabel("Dwell time (s)" if analysis.dt != 1.0 else "Dwell time (frames)")
    else:
        ax.set_xlabel("Dwell time (s)" if analysis.dt != 1.0 else "Dwell time (frames)")
    return fig


def render_transition_prob(
    hist: TransitionProbHistogram | Sequence[tuple[str, TransitionProbHistogram]],
    *,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the B3 transition-probability histogram for one ``init → final`` pair,
    with the optional Gaussian-KDE overlay.

    **Several series** are overlaid on one axis as unfilled step outlines in their
    `series_style`, each labelled with its label and ``N``; a series' KDE is drawn in its
    colour. The series must be histograms of the same pair, because the axis names one.

    Parameters
    ----------
    hist
        One histogram, or an ordered sequence of ``(label, histogram)`` series. A
        one-series sequence draws the same figure as the bare histogram.
    title
        The axes title; defaults to one that names the pair.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].

    Raises
    ------
    ValueError
        Two series differ in their bin edges, probability range, density normalization or
        ``init → final`` pair.
    """
    series = _as_series(hist, (TransitionProbHistogram,), "TransitionProbHistogram")
    if series is None or len(series) == 1:
        fig = _one_transition_prob(
            hist if series is None else series[0][1], title=title, figsize=figsize
        )  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series,
        (
            ("bin edges", lambda h: h.edges),
            ("probability range", lambda h: _range(h.prob_range)),
            ("density normalization", lambda h: h.density),
            ("init → final pair", lambda h: (int(h.init_state), int(h.final_state))),
        ),
    )
    first = series[0][1]
    pair = f"$P({first.init_state}\\to{first.final_state})$"
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    handles: list[Any] = []
    for k, (label, h) in enumerate(series):
        style = series_style(k)
        handle = ax.stairs(
            np.asarray(h.counts, dtype="float64"),
            np.asarray(h.edges, dtype="float64"),
            fill=False,
            color=style.color,
            linestyle=style.linestyle,
            lw=1.5,
            label=_labelled(label, h.n_molecules),
        )
        handles.append(handle)
        if h.kde_x is not None and h.kde_y is not None:
            ax.plot(
                np.asarray(h.kde_x, dtype="float64"),
                np.asarray(h.kde_y, dtype="float64"),
                color=style.color,
                ls=style.linestyle,
                lw=1.0,
                alpha=0.8,
            )
    ax.set_xlim(first.prob_range)
    ax.set_xlabel(f"{pair} per molecule")
    ax.set_ylabel("Density" if first.density else "Counts")
    ax.set_title(title if title is not None else f"Transition-probability {pair} (B3)")
    _series_legend(ax, handles, loc="upper left", fontsize=8, frameon=False)
    _record_series(fig, series, display=_overlay_display(fig, len(series)))
    return fig


def _one_transition_prob(
    hist: TransitionProbHistogram,
    *,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_transition_prob`` has always drawn."""
    counts = np.asarray(hist.counts, dtype="float64")
    edges = np.asarray(hist.edges, dtype="float64")
    heading = (
        title
        if title is not None
        else f"Transition-probability $P({hist.init_state}\\to{hist.final_state})$ (B3)"
    )

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    ax.stairs(counts, edges, fill=True, color="#55a868", alpha=0.7, label="histogram")
    if hist.kde_x is not None and hist.kde_y is not None:
        ax.plot(
            np.asarray(hist.kde_x, dtype="float64"),
            np.asarray(hist.kde_y, dtype="float64"),
            color="k",
            lw=1.5,
            label="KDE",
        )
    ax.set_xlim(hist.prob_range)
    ax.set_xlabel(f"$P({hist.init_state}\\to{hist.final_state})$ per molecule")
    ax.set_ylabel("Density" if hist.density else "Counts")
    ax.set_title(heading)
    ax.annotate(
        f"N = {hist.n_molecules}",
        xy=(0.97, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=9,
    )
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    return fig


def render_state_number(
    counts: StateNumberCounts | Sequence[tuple[str, StateNumberCounts]],
    *,
    title: str = "State-number distribution (C1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the C1 state-number bar chart: molecules by number of occupied states.

    **Several series** are drawn as grouped bars: at each state count, one bar per series
    side by side in series order, in its `series_style` colour (and hatch past the fourth
    series), each series labelled with its label and ``N``.

    Parameters
    ----------
    counts
        One distribution, or an ordered sequence of ``(label, distribution)`` series. A
        one-series sequence draws the same figure as the bare distribution.
    title
        The axes title.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].

    Raises
    ------
    ValueError
        Two series count different state numbers (their ``states`` differ).
    """
    series = _as_series(counts, (StateNumberCounts,), "StateNumberCounts")
    if series is None or len(series) == 1:
        fig = _one_state_number(
            counts if series is None else series[0][1], title=title, figsize=figsize
        )  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series, (("state numbers", lambda c: np.asarray(c.states, dtype="int64")),)
    )
    states = np.asarray(series[0][1].states, dtype="int64")
    width = 0.8 / len(series)
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    handles: list[Any] = []
    for k, (label, c) in enumerate(series):
        style = series_style(k)
        name = _labelled(label, c.n_molecules)
        if c.n_out_of_range:
            name += f", {c.n_out_of_range} out of range"
        bars = ax.bar(
            states + (k - (len(series) - 1) / 2) * width,
            np.asarray(c.counts, dtype="int64"),
            width=width,
            color=style.color,
            hatch=style.hatch,
            edgecolor="white",
            linewidth=0.5,
            label=name,
        )
        handles.append(bars)
    ax.set_xticks(states)
    ax.set_xlabel("Number of occupied states")
    ax.set_ylabel("Molecules")
    ax.set_title(title)
    _series_legend(ax, handles, loc="best", fontsize=8, frameon=False)
    _set_display(fig, series_layout="grouped_bars", series_cycle=SERIES_COLOR_CYCLE)
    bars = [series_style(k) for k in range(len(series))]
    _record_series(fig, series, display=[{"color": b.color, "hatch": b.hatch} for b in bars])
    return fig


def _one_state_number(
    counts: StateNumberCounts,
    *,
    title: str = "State-number distribution (C1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_state_number`` has always drawn."""
    states = np.asarray(counts.states, dtype="int64")
    bar_counts = np.asarray(counts.counts, dtype="int64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    ax.bar(states, bar_counts, color="#8172b3", alpha=0.85, width=0.8)
    ax.set_xticks(states)
    ax.set_xlabel("Number of occupied states")
    ax.set_ylabel("Molecules")
    ax.set_title(title)
    note = f"N = {counts.n_molecules}"
    if counts.n_out_of_range:
        note += f" ({counts.n_out_of_range} out of range)"
    ax.annotate(
        note,
        xy=(0.97, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=9,
    )
    return fig


def render_raw_fret_cloud(
    cloud: RawFretCloud | Sequence[tuple[str, RawFretCloud]],
    *,
    title: str = "Raw FRET cloud (QC)",
    max_points: int = 20000,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the raw FRET cloud QC view: the pooled ``(time, apparent-E)`` scatter with
    the KDE density surface and highest-density-region contours when present.

    ``max_points`` caps the scatter overlay for a manageable vector file; the density
    surface (when computed) is unaffected by that cap — but it was fit by
    [`tether.analysis.cloud.raw_fret_cloud`][] on the in-range points only
    (``n_out_of_range`` counts the rest).

    **Several series** are drawn as small multiples, one panel per series in series order
    and titled with its label: two clouds and two density surfaces overplotted hide each
    other. The panels share their axes and one colour scale for the density surfaces, and
    ``max_points`` caps each panel's scatter.

    Parameters
    ----------
    cloud
        One cloud, or an ordered sequence of ``(label, cloud)`` series. A one-series
        sequence draws the same figure as the bare cloud.
    title
        The axes title; for several series, the figure title above the panels.
    max_points
        Cap on each scatter overlay.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][] for one cloud and to a
        size that fits the panels for several.

    Raises
    ------
    ValueError
        Two series differ in their time or signal bin edges, their time or signal range,
        or their frame time.
    """
    series = _as_series(cloud, (RawFretCloud,), "RawFretCloud")
    if series is None or len(series) == 1:
        one = cloud if series is None else series[0][1]
        fig = _one_raw_fret_cloud(one, title=title, max_points=max_points, figsize=figsize)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series,
        (
            ("time bin edges", lambda c: c.time_edges),
            ("signal bin edges", lambda c: c.signal_edges),
            ("time range", lambda c: _range(c.time_range)),
            ("signal range", lambda c: _range(c.signal_range)),
            ("frame time (time_dt)", lambda c: float(c.time_dt)),
        ),
    )
    surfaces = [np.asarray(c.density, dtype="float64") for _, c in series if c.density is not None]
    norm = _shared_norm(surfaces)
    fig = _new_figure(_panels_figsize(figsize, len(series)))
    axes = _panels(fig, len(series))
    mesh = None
    for ax, (label, c) in zip(axes, series, strict=True):
        time_edges = np.asarray(c.time_edges, dtype="float64")
        signal_edges = np.asarray(c.signal_edges, dtype="float64")
        if c.density is not None:
            density = np.asarray(c.density, dtype="float64")
            mesh = ax.pcolormesh(
                time_edges, signal_edges, density.T, cmap="Blues", norm=norm, shading="flat"
            )
            if c.hdr_levels is not None:
                levels = np.asarray(c.hdr_levels, dtype="float64")
                levels = np.unique(levels[np.isfinite(levels) & (levels > 0)])
                if levels.size:
                    ax.contour(
                        _bin_centers(time_edges),
                        _bin_centers(signal_edges),
                        density.T,
                        levels=levels,
                        colors="k",
                        linewidths=0.7,
                        alpha=0.7,
                    )
        points = np.asarray(c.points, dtype="float64")
        n = points.shape[0]
        if n:
            shown = points[:: int(np.ceil(n / max_points))] if n > max_points else points
            ax.scatter(shown[:, 0], shown[:, 1], s=2, color="#c44e52", alpha=0.25, linewidths=0)
        ax.set_xlim(c.time_range)
        ax.set_ylim(c.signal_range)
        ax.set_xlabel("Time (s)" if c.time_dt != 1.0 else "Frame")
        ax.set_ylabel("Apparent FRET efficiency $E$")
        ax.set_title(_drawn(label))
        ax.annotate(
            f"{c.n_samples} points · N = {c.n_molecules}",
            xy=(0.03, 0.95),
            xycoords="axes fraction",
            ha="left",
            va="top",
            fontsize=8,
        )
    if mesh is not None:
        fig.colorbar(mesh, ax=axes, label="KDE density")
    fig.suptitle(title)
    _record_series(
        fig,
        series,
        # A shared scale is recorded only when a density surface drew one.
        display=_small_multiples_display(fig, len(series), color_scale=mesh is not None),
    )
    return fig


def _one_raw_fret_cloud(
    cloud: RawFretCloud,
    *,
    title: str = "Raw FRET cloud (QC)",
    max_points: int = 20000,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_raw_fret_cloud`` has always drawn."""
    points = np.asarray(cloud.points, dtype="float64")
    time_edges = np.asarray(cloud.time_edges, dtype="float64")
    signal_edges = np.asarray(cloud.signal_edges, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    if cloud.density is not None:
        density = np.asarray(cloud.density, dtype="float64")  # (time_bins, signal_bins)
        mesh = ax.pcolormesh(time_edges, signal_edges, density.T, cmap="Blues", shading="flat")
        fig.colorbar(mesh, ax=ax, label="KDE density")
        if cloud.hdr_levels is not None:
            centers_t = _bin_centers(time_edges)
            centers_s = _bin_centers(signal_edges)
            levels = np.asarray(cloud.hdr_levels, dtype="float64")
            levels = np.unique(levels[np.isfinite(levels) & (levels > 0)])
            if levels.size:
                ax.contour(
                    centers_t,
                    centers_s,
                    density.T,
                    levels=levels,
                    colors="k",
                    linewidths=0.7,
                    alpha=0.7,
                )
    n = points.shape[0]
    if n:
        if n > max_points:
            step = int(np.ceil(n / max_points))
            shown = points[::step]
        else:
            shown = points
        ax.scatter(shown[:, 0], shown[:, 1], s=2, color="#c44e52", alpha=0.25, linewidths=0)
    ax.set_xlim(cloud.time_range)
    ax.set_ylim(cloud.signal_range)
    ax.set_xlabel("Time (s)" if cloud.time_dt != 1.0 else "Frame")
    ax.set_ylabel("Apparent FRET efficiency $E$")
    ax.set_title(title)
    ax.annotate(
        f"{cloud.n_samples} points · N = {cloud.n_molecules}",
        xy=(0.03, 0.95),
        xycoords="axes fraction",
        ha="left",
        va="top",
        fontsize=8,
    )
    return fig


def render_cross_correlation(
    xc: CrossCorrelation | Sequence[tuple[str, CrossCorrelation]],
    *,
    title: str = "Donor–acceptor cross-correlation",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the donor–acceptor cross-correlation curve vs lag.

    **Several series** are overlaid on one axis, each curve in its `series_style` and
    labelled with its label and, when known, ``N``.

    Parameters
    ----------
    xc
        One cross-correlation, or an ordered sequence of ``(label, cross-correlation)``
        series. A one-series sequence draws the same figure as the bare curve.
    title
        The axes title.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].

    Raises
    ------
    ValueError
        Two series differ in their lags or their normalization.
    """
    series = _as_series(xc, (CrossCorrelation,), "CrossCorrelation")
    if series is None or len(series) == 1:
        fig = _one_cross_correlation(
            xc if series is None else series[0][1], title=title, figsize=figsize
        )  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series,
        (("lags", lambda c: np.asarray(c.lags)), ("normalization", lambda c: c.normalize)),
    )
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    ax.axhline(0.0, color="k", lw=0.6)
    ax.axvline(0.0, color="k", lw=0.6, ls=":")
    handles: list[Any] = []
    for k, (label, c) in enumerate(series):
        style = series_style(k)
        (line,) = ax.plot(
            np.asarray(c.lags, dtype="float64"),
            np.asarray(c.values, dtype="float64"),
            color=style.color,
            ls=style.linestyle,
            lw=1.2,
            label=f"{_labelled(label, c.n_molecules)} · {c.n_frames} frames",
        )
        handles.append(line)
    ax.set_xlabel("Lag (frames)")
    ax.set_ylabel(f"Cross-correlation ({series[0][1].normalize})")
    ax.set_title(title)
    _series_legend(ax, handles, loc="upper right", fontsize=8, frameon=False)
    _record_series(fig, series, display=_overlay_display(fig, len(series)))
    return fig


def _one_cross_correlation(
    xc: CrossCorrelation,
    *,
    title: str = "Donor–acceptor cross-correlation",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_cross_correlation`` has always drawn."""
    lags = np.asarray(xc.lags, dtype="float64")
    values = np.asarray(xc.values, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    ax.axhline(0.0, color="k", lw=0.6)
    ax.axvline(0.0, color="k", lw=0.6, ls=":")
    ax.plot(lags, values, "-", color="#4c72b0", lw=1.2)
    ax.set_xlabel("Lag (frames)")
    ax.set_ylabel(f"Cross-correlation ({xc.normalize})")
    ax.set_title(title)
    note = f"{xc.n_frames} frames"
    if xc.n_molecules is not None:
        note += f" · N = {xc.n_molecules}"
    ax.annotate(
        note,
        xy=(0.97, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=8,
    )
    return fig


def _count(n: int, noun: str) -> str:
    """``n`` and ``noun``, plural unless ``n`` is one (``"1 event"``, ``"3 events"``)."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def render_per_condition_histograms(
    overlay: PerConditionHistograms | Sequence[tuple[str, PerConditionHistograms]],
    *,
    title: str = "Per-condition FRET-efficiency histograms (A1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the §7.7 per-condition overlay: every condition's A1 histogram on one axis.

    Each condition is drawn as an unfilled step outline on the overlay's shared
    [`bin_edges`][tether.analysis.histogram.PerConditionHistograms.bin_edges] grid, in
    the overlay's own (deterministic) order, and labelled with its condition id and its
    molecule count ``N``. Colours follow the ``tab10`` palette, and past ten conditions
    the line style changes too, so up to 40 conditions no two curves share both a colour
    and a line style. Past 40 the pairs repeat (condition 41 is drawn like condition 1),
    and only the legend labels tell those curves apart.

    **Several series** are drawn as small multiples, one panel per series in series order
    and titled with its label: each series is already an overlay of conditions, so a
    second overlay on the same axis would need a second visual channel to tell selection
    from condition. A condition keeps one colour and line style in every panel, assigned
    in the order the conditions first appear across the series, and the panels share
    their axes.

    Parameters
    ----------
    overlay
        The per-condition histograms to draw, from
        [`per_condition_apparent_e_histograms`][tether.analysis.histogram.per_condition_apparent_e_histograms],
        or an ordered sequence of ``(label, overlay)`` series. A one-series sequence draws
        the same figure as the bare overlay.
    title
        The axes title; for several series, the figure title above the panels.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][] for one overlay and to a
        size that fits the panels for several.

    Raises
    ------
    ValueError
        A condition's histogram is not binned on the overlay's shared grid, so it
        cannot be drawn on the same axis without misplacing its bins; or two series differ
        in their bin edges, value range or density normalization.
    """
    series = _as_series(overlay, (PerConditionHistograms,), "PerConditionHistograms")
    if series is None or len(series) == 1:
        one = overlay if series is None else series[0][1]
        fig = _one_per_condition_histograms(one, title=title, figsize=figsize)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series,
        (
            ("bin edges", lambda o: o.bin_edges),
            ("value range", lambda o: _range(o.value_range)),
            ("density normalization", lambda o: o.density),
        ),
    )
    slot: dict[str, int] = {}
    for _, o in series:
        for condition in o.condition_ids:
            slot.setdefault(condition, len(slot))
    fig = _new_figure(_panels_figsize(figsize, len(series)))
    axes = _panels(fig, len(series))
    for ax, (label, o) in zip(axes, series, strict=True):
        _condition_panel(ax, o, [slot[c] for c in o.condition_ids], title=_drawn(label))
    fig.suptitle(title)
    _record_series(
        fig, series, display=_small_multiples_display(fig, len(series), color_scale=False)
    )
    return fig


def _one_per_condition_histograms(
    overlay: PerConditionHistograms,
    *,
    title: str = "Per-condition FRET-efficiency histograms (A1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_per_condition_histograms`` has always drawn."""
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    _condition_panel(ax, overlay, range(overlay.n_conditions), title=title)
    return fig


def _condition_panel(
    ax: Any, overlay: PerConditionHistograms, slots: Sequence[int] | range, *, title: str
) -> None:
    """Draw one per-condition overlay on ``ax``; condition ``i`` takes style ``slots[i]``.

    The style of a slot is a ``tab10`` colour and, past ten, the next line style. One
    overlay alone gives each condition its own position; small multiples give a condition
    the same slot in every panel.
    """
    edges = np.asarray(overlay.bin_edges, dtype="float64")
    palette = colormaps["tab10"].colors
    for k, ch in zip(slots, overlay.histograms, strict=True):
        own = np.asarray(ch.histogram.bin_edges, dtype="float64")
        if own.shape != edges.shape or not np.array_equal(own, edges):
            raise ValueError(
                f"condition {ch.condition_id!r} is not binned on the overlay's shared grid"
            )
        ax.stairs(
            np.asarray(ch.histogram.counts, dtype="float64"),
            edges,
            fill=False,
            color=palette[k % len(palette)],
            linestyle=_CONDITION_LINESTYLES[(k // len(palette)) % len(_CONDITION_LINESTYLES)],
            lw=1.5,
            label=f"{ch.condition_id} (N = {ch.n_molecules})",
        )
    ax.set_xlim(overlay.value_range)
    ax.set_xlabel("Apparent FRET efficiency $E$")
    ax.set_ylabel("Density" if overlay.density else "Counts")
    ax.set_title(title)
    if overlay.n_conditions:
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    else:
        ax.annotate(
            "No condition has a queried molecule",
            xy=(0.5, 0.5),
            xycoords="axes fraction",
            ha="center",
            va="center",
            fontsize=9,
        )
    ax.annotate(
        f"{_count(overlay.n_conditions, 'condition')} · N = {overlay.total_molecules}",
        xy=(0.97, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=9,
    )


def _sync_state_label(state: int) -> str:
    """A post-sync state selector as a reader sees it: ``-1`` selects any state."""
    return "any" if int(state) < 0 else str(int(state))


def render_transition_sync_histogram2d(
    hist: TransitionSyncHistogram2D | Sequence[tuple[str, TransitionSyncHistogram2D]],
    *,
    title: str = "Transition-synchronized heatmap (A2b)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the A2 post-synchronized heatmap: occupancy around the selected transitions.

    The time axis is **relative to the transition**: the heatmap's
    [`time_edges`][tether.analysis.histogram.TransitionSyncHistogram2D.time_edges] run
    negative before it, and a dotted vertical line marks relative time zero, the
    [`sync_preframe`][tether.analysis.histogram.TransitionSyncHistogram2D.sync_preframe]
    column. The annotation names the selected ``from → to`` states (``any`` for ``-1``)
    and the transition and molecule counts.

    **Several series** are drawn as small multiples, one panel per series in series order
    and titled with its label: one heatmap overplotted on another hides both. The panels
    share their axes and one colour scale, and one colour bar serves them all.

    Parameters
    ----------
    hist
        The transition-aligned heatmap to draw, from
        [`population_transition_sync_histogram2d`][tether.analysis.histogram.population_transition_sync_histogram2d],
        or an ordered sequence of ``(label, heatmap)`` series. A one-series sequence draws
        the same figure as the bare heatmap.
    title
        The axes title; for several series, the figure title above the panels.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][] for one heatmap and to a
        size that fits the panels for several.

    Raises
    ------
    ValueError
        Two series differ in their time or signal bin edges, frame time, signal range or
        density normalization.
    """
    series = _as_series(hist, (TransitionSyncHistogram2D,), "TransitionSyncHistogram2D")
    if series is None or len(series) == 1:
        one = hist if series is None else series[0][1]
        fig = _one_transition_sync_histogram2d(one, title=title, figsize=figsize)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    _require_shared_grid(
        series,
        (
            ("time bin edges", lambda h: h.time_edges),
            ("signal bin edges", lambda h: h.signal_edges),
            ("frame time (time_dt)", lambda h: float(h.time_dt)),
            ("signal range", lambda h: _range(h.signal_range)),
            ("density normalization", lambda h: h.density),
        ),
    )
    norm = _shared_norm([np.asarray(h.counts, dtype="float64") for _, h in series])
    fig = _new_figure(_panels_figsize(figsize, len(series)))
    axes = _panels(fig, len(series))
    mesh = None
    for ax, (label, h) in zip(axes, series, strict=True):
        time_edges = np.asarray(h.time_edges, dtype="float64")
        mesh = ax.pcolormesh(
            time_edges,
            np.asarray(h.signal_edges, dtype="float64"),
            np.asarray(h.counts, dtype="float64").T,
            cmap="magma",
            norm=norm,
            shading="flat",
        )
        ax.axvline(0.0, color="w", lw=0.8, ls=":", alpha=0.8)
        seconds = h.time_dt != 1.0
        ax.set_xlabel("Time from transition (s)" if seconds else "Frames from transition")
        ax.set_ylabel("Apparent FRET efficiency $E$")
        ax.set_xlim(float(time_edges[0]), float(time_edges[-1]))
        ax.set_ylim(h.signal_range)
        ax.set_title(_drawn(label))
        window = "single dwell" if h.single_dwell else "fixed window"
        ax.annotate(
            f"{_sync_state_label(h.from_state)} → {_sync_state_label(h.to_state)} ({window})"
            f" · {_count(h.n_transitions, 'transition')} · N = {h.n_molecules}",
            xy=(0.03, 0.95),
            xycoords="axes fraction",
            ha="left",
            va="top",
            color="white",
            fontsize=8,
        )
    fig.colorbar(mesh, ax=axes, label="Density" if series[0][1].density else "Counts")
    fig.suptitle(title)
    _record_series(fig, series, display=_small_multiples_display(fig, len(series)))
    return fig


def _one_transition_sync_histogram2d(
    hist: TransitionSyncHistogram2D,
    *,
    title: str = "Transition-synchronized heatmap (A2b)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_transition_sync_histogram2d`` has always drawn."""
    counts = np.asarray(hist.counts, dtype="float64")  # (time_columns, signal_bins)
    time_edges = np.asarray(hist.time_edges, dtype="float64")
    signal_edges = np.asarray(hist.signal_edges, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    # counts is [time, signal]; show time on x, signal on y → transpose so rows=signal.
    mesh = ax.pcolormesh(time_edges, signal_edges, counts.T, cmap="magma", shading="flat")
    fig.colorbar(mesh, ax=ax, label="Density" if hist.density else "Counts")
    ax.axvline(0.0, color="w", lw=0.8, ls=":", alpha=0.8)
    seconds = hist.time_dt != 1.0
    ax.set_xlabel("Time from transition (s)" if seconds else "Frames from transition")
    ax.set_ylabel("Apparent FRET efficiency $E$")
    ax.set_xlim(float(time_edges[0]), float(time_edges[-1]))
    ax.set_ylim(hist.signal_range)
    ax.set_title(title)
    window = "single dwell" if hist.single_dwell else "fixed window"
    ax.annotate(
        f"{_sync_state_label(hist.from_state)} → {_sync_state_label(hist.to_state)} ({window})"
        f" · {_count(hist.n_transitions, 'transition')} · N = {hist.n_molecules}",
        xy=(0.03, 0.95),
        xycoords="axes fraction",
        ha="left",
        va="top",
        color="white",
        fontsize=8,
    )
    return fig


def render_alpha_shape(
    shape: AlphaShape | Sequence[tuple[str, AlphaShape]],
    *,
    cloud: RawFretCloud | Sequence[RawFretCloud | None] | None = None,
    time_dt: float | None = None,
    title: str = "FRET-cloud support (alpha shape)",
    max_points: int = 20000,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the α-shape support boundary of the raw FRET cloud (§7.7).

    Draws [`boundary_edges`][tether.analysis.cloud.AlphaShape.boundary_edges] as line
    segments in the original ``(time, E)`` coordinates, optionally over the cloud's
    scatter. A shape with nothing to bound is drawn **empty** — no segment at all — and
    the annotation says which of the two empty states it is: the cloud spans no 2-D
    region ([`no_region`][tether.analysis.cloud.AlphaShape.no_region]), or ``alpha``
    was too small to keep any triangle. A boundary is never fabricated.

    **Several series** are overlaid on one axis, because comparing where two populations
    have support is what the boundary is for: each boundary in its `series_style`, each
    series' cloud (when given) scattered faintly in its colour, and one note line per
    series.

    Parameters
    ----------
    shape
        The α-shape to draw, from
        [`population_fret_cloud_alpha_shape`][tether.analysis.cloud.population_fret_cloud_alpha_shape],
        or an ordered sequence of ``(label, shape)`` series. A one-series sequence draws
        the same figure as the bare shape.
    cloud
        Optional [`RawFretCloud`][tether.analysis.cloud.RawFretCloud] the shape was
        computed from. When given, its points are drawn underneath (decimated to
        ``max_points``), its ranges set the axis limits and its ``time_dt`` the time unit.
        For several series, a sequence of one cloud (or ``None``) per series.
    time_dt
        Frame duration of the shape's time coordinate, for the axis label when no
        ``cloud`` is given: ``Time (s)`` when it is not ``1.0``, otherwise ``Frame``.
        Defaults to the cloud's ``time_dt``, else ``1.0``.
    title
        The axes title.
    max_points
        Cap on the scatter overlay drawn from ``cloud``; the boundary is never thinned.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].

    Raises
    ------
    ValueError
        ``time_dt`` contradicts the given ``cloud``'s ``time_dt``; or, for several
        series, a single cloud was given, or two series differ in their frame time or
        (where both have a cloud) their time or signal range.
    """
    series = _as_series(shape, (AlphaShape,), "AlphaShape")
    clouds = _aligned(
        cloud, 1 if series is None else len(series), RawFretCloud, "cloud", bare=series is None
    )
    if series is None or len(series) == 1:
        one = shape if series is None else series[0][1]
        fig = _one_alpha_shape(
            one,  # type: ignore[arg-type]
            cloud=clouds[0],
            time_dt=time_dt,
            title=title,
            max_points=max_points,
            figsize=figsize,
        )
        if series is not None:
            _record_series(fig, series, companions=_cloud_records(clouds, [time_dt]))
        return fig
    dts: list[float] = []
    for (label, _), c in zip(series, clouds, strict=True):
        if c is not None and time_dt is not None and float(time_dt) != float(c.time_dt):
            raise ValueError(
                f"time_dt={time_dt!r} contradicts the cloud of series {label!r}, "
                f"whose time_dt is {c.time_dt!r}"
            )
        dts.append(float(c.time_dt) if c is not None else float(time_dt or 1.0))
    _require_shared_grid(
        [(label, dt) for (label, _), dt in zip(series, dts, strict=True)],
        (("frame time", lambda dt: dt),),
    )
    with_cloud = [(label, c) for (label, _), c in zip(series, clouds, strict=True) if c is not None]
    if with_cloud:
        _require_shared_grid(
            with_cloud,
            (
                ("cloud time range", lambda c: _range(c.time_range)),
                ("cloud signal range", lambda c: _range(c.signal_range)),
            ),
        )
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    notes: list[str] = []
    handles: list[Any] = []
    for k, ((label, s), c) in enumerate(zip(series, clouds, strict=True)):
        style = series_style(k)
        if c is not None:
            points = np.asarray(c.points, dtype="float64")
            n = points.shape[0]
            if n:
                shown = points[:: int(np.ceil(n / max_points))] if n > max_points else points
                ax.scatter(
                    shown[:, 0], shown[:, 1], s=2, color=style.color, alpha=0.15, linewidths=0
                )
        if not s.is_empty:
            boundary = LineCollection(
                np.asarray(s.boundary_edges, dtype="float64"),
                colors=style.color,
                linestyles=style.linestyle,
                linewidths=1.2,
                label=_drawn(label),
            )
            ax.add_collection(boundary)
            handles.append(boundary)
        notes.append(f"{_drawn(label)}: {_alpha_note(s)} · {s.n_points} points")
    if with_cloud:
        (x_lo, x_hi), (y_lo, y_hi) = (
            _range(with_cloud[0][1].time_range),
            _range(with_cloud[0][1].signal_range),
        )
        for (_, s), c in zip(series, clouds, strict=True):
            if c is None and not s.is_empty:
                # A cloudless boundary has no range of its own to agree with, so the
                # limits widen to hold it rather than cut it off.
                ends = np.asarray(s.boundary_edges, dtype="float64").reshape(-1, 2)
                x_lo, x_hi = min(x_lo, float(ends[:, 0].min())), max(x_hi, float(ends[:, 0].max()))
                y_lo, y_hi = min(y_lo, float(ends[:, 1].min())), max(y_hi, float(ends[:, 1].max()))
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(y_lo, y_hi)
    else:
        ax.autoscale_view()
    ax.set_xlabel("Time (s)" if dts[0] != 1.0 else "Frame")
    ax.set_ylabel("Apparent FRET efficiency $E$")
    ax.set_title(title)
    if handles:
        _series_legend(ax, handles, loc="lower right", fontsize=8, frameon=False)
    ax.annotate(
        "\n".join(notes),
        xy=(0.03, 0.95),
        xycoords="axes fraction",
        ha="left",
        va="top",
        fontsize=7,
    )
    _record_series(
        fig,
        series,
        display=_overlay_display(fig, len(series)),
        companions=_cloud_records(clouds, dts),
    )
    return fig


def _alpha_note(shape: AlphaShape) -> str:
    """What an α-shape bounds, or which of its two empty states it is in."""
    if not shape.spans_region:
        return f"No 2-D region: {shape.n_points} points cannot be triangulated"
    if shape.n_kept == 0:
        return f"No support at α = {shape.alpha:.3g}: no triangle kept"
    return (
        f"α = {shape.alpha:.3g} · {shape.n_kept} of {shape.n_triangles} triangles"
        f" · area {shape.area:.3g}"
    )


def _cloud_records(
    clouds: Sequence[RawFretCloud | None], dts: Sequence[float | None]
) -> list[dict[str, object]]:
    """Each series' cloud and frame time, as its provenance record.

    A series drawn without a cloud still records ``cloud: None``, so a caller key
    claiming a cloud that was never drawn contradicts the figure and is refused.
    """
    records: list[dict[str, object]] = []
    for c, dt in zip(clouds, dts, strict=True):
        record: dict[str, object] = {"cloud": None if c is None else _result_parameters(c)}
        if dt is not None:
            record["time_dt"] = float(dt)
        records.append(record)
    return records


def _one_alpha_shape(
    shape: AlphaShape,
    *,
    cloud: RawFretCloud | None = None,
    time_dt: float | None = None,
    title: str = "FRET-cloud support (alpha shape)",
    max_points: int = 20000,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_alpha_shape`` has always drawn."""
    if cloud is not None and time_dt is not None and float(time_dt) != float(cloud.time_dt):
        raise ValueError(f"time_dt={time_dt!r} contradicts the cloud's time_dt={cloud.time_dt!r}")
    dt = float(cloud.time_dt) if cloud is not None else float(time_dt or 1.0)

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    if cloud is not None:
        points = np.asarray(cloud.points, dtype="float64")
        n = points.shape[0]
        if n:
            shown = points[:: int(np.ceil(n / max_points))] if n > max_points else points
            ax.scatter(shown[:, 0], shown[:, 1], s=2, color="#c44e52", alpha=0.25, linewidths=0)
        ax.set_xlim(cloud.time_range)
        ax.set_ylim(cloud.signal_range)
    if not shape.is_empty:
        segments = np.asarray(shape.boundary_edges, dtype="float64")
        ax.add_collection(LineCollection(segments, colors="k", linewidths=1.0))
        if cloud is None:
            ax.autoscale_view()
    note = _alpha_note(shape)
    ax.set_xlabel("Time (s)" if dt != 1.0 else "Frame")
    ax.set_ylabel("Apparent FRET efficiency $E$")
    ax.set_title(title)
    ax.annotate(
        f"{note} · {shape.n_points} points",
        xy=(0.03, 0.95),
        xycoords="axes fraction",
        ha="left",
        va="top",
        fontsize=8,
    )
    return fig


def render_state_number_elbow(
    elbow: StateNumberElbow | Sequence[tuple[str, StateNumberElbow]],
    *,
    title: str = "State-number hint (k-vs-RMSE elbow)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the k-vs-RMSE elbow curve and the state count it suggests (§7.7).

    Draws the within-cluster RMSE at each probed ``k``, the first-to-last chord that the
    knee criterion measures from, and — when there is one — the elbow point with a
    vertical guide at ``elbow_k``. The elbow is a heuristic **hint**, not a state count
    of record (the HMM model view is that), and the annotation says so. A curve with no
    interior elbow is drawn without a marker and annotated "no elbow".

    **Several series** are overlaid on one axis, each curve in its `series_style` with its
    elbow (when it has one) circled in its colour, and one note line per series. The
    chords are left out, because a dashed chord per series would read as more series.
    The probed ``k`` may differ between series: ``k`` is a count, not a binned grid, so the
    axis carries every ``k`` any series probed.

    Parameters
    ----------
    elbow
        The elbow curve to draw, from
        [`population_fret_cloud_state_number_elbow`][tether.analysis.cloud.population_fret_cloud_state_number_elbow],
        or an ordered sequence of ``(label, curve)`` series. A one-series sequence draws
        the same figure as the bare curve.
    title
        The axes title.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][].
    """
    series = _as_series(elbow, (StateNumberElbow,), "StateNumberElbow")
    if series is None or len(series) == 1:
        one = elbow if series is None else series[0][1]
        fig = _one_state_number_elbow(one, title=title, figsize=figsize)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    notes: list[str] = []
    probed: set[int] = set()
    handles: list[Any] = []
    for k, (label, e) in enumerate(series):
        style = series_style(k)
        ks = np.asarray(e.k_values, dtype="int64")
        rmse = np.asarray(e.rmse, dtype="float64")
        probed.update(int(v) for v in ks)
        if ks.size:
            (line,) = ax.plot(
                ks,
                rmse,
                marker="o",
                color=style.color,
                ls=style.linestyle,
                ms=4,
                lw=1.2,
                label=_drawn(label),
            )
            handles.append(line)
        if e.elbow_k is not None:
            (at,) = np.flatnonzero(ks == int(e.elbow_k))
            ax.plot([ks[at]], [rmse[at]], "o", ms=9, mfc="none", mec=style.color, mew=1.5)
            notes.append(
                f"{_drawn(label)}: suggested k = {e.elbow_k} · {e.n_samples} samples"
                f" · seed {e.seed}"
            )
        else:
            notes.append(f"{_drawn(label)}: no elbow · {e.n_samples} samples · seed {e.seed}")
    if probed:
        ax.set_xticks(sorted(probed))
        _series_legend(ax, handles, loc="upper right", fontsize=8, frameon=False)
    ax.set_xlabel("Number of clusters $k$")
    ax.set_ylabel("Within-cluster RMSE of $E$")
    ax.set_title(title)
    notes.append("The elbow is a hint; the HMM state count is of record")
    ax.annotate(
        "\n".join(notes),
        xy=(0.97, 0.70),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=7,
    )
    _record_series(fig, series, display=_overlay_display(fig, len(series)))
    return fig


def _one_state_number_elbow(
    elbow: StateNumberElbow,
    *,
    title: str = "State-number hint (k-vs-RMSE elbow)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_state_number_elbow`` has always drawn."""
    ks = np.asarray(elbow.k_values, dtype="int64")
    rmse = np.asarray(elbow.rmse, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    if ks.size:
        ax.plot(ks, rmse, "o-", color="#4c72b0", ms=4, lw=1.2, label="RMSE")
        ax.set_xticks(ks)
    if ks.size >= 2:
        ax.plot(
            [ks[0], ks[-1]],
            [rmse[0], rmse[-1]],
            color="0.5",
            lw=0.8,
            ls="--",
            label="chord",
        )
    if elbow.elbow_k is not None:
        (at,) = np.flatnonzero(ks == int(elbow.elbow_k))
        ax.axvline(float(elbow.elbow_k), color="#c44e52", lw=0.8, ls=":")
        ax.plot(
            [ks[at]],
            [rmse[at]],
            "o",
            ms=9,
            mfc="none",
            mec="#c44e52",
            mew=1.5,
            label=f"elbow k = {elbow.elbow_k}",
        )
        note = f"Suggested k = {elbow.elbow_k} (hint; the HMM state count is of record)"
    else:
        note = "No elbow (fewer than three k, or a flat curve)"
    ax.set_xlabel("Number of clusters $k$")
    ax.set_ylabel("Within-cluster RMSE of $E$")
    ax.set_title(title)
    if ks.size:
        ax.legend(loc="upper right", fontsize=8, frameon=False)
    ax.annotate(
        f"{note}\n{elbow.n_samples} samples · seed {elbow.seed}",
        xy=(0.97, 0.70),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=8,
    )
    return fig


def render_population_anticorrelation(
    population: PopulationAnticorrelation | Sequence[tuple[str, PopulationAnticorrelation]],
    *,
    title: str = "Donor–acceptor anticorrelation events",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the population's anticorrelation events as a per-molecule raster (§7.7).

    One row per scanned molecule, in the population's store order (top to bottom), with
    each event drawn as a bar over its ``[start, stop)`` frame span and coloured by its
    peak strength ``|lag-0|``; a tick marks each event's peak frame. A molecule scanned
    with no event keeps its empty row, so the raster shows how many molecules carried
    none. Rows are labelled by ``molecule_key`` up to 30 molecules, by index above that.

    **Several series** are drawn as small multiples stacked top to bottom in series order,
    one raster per series titled with its label: a row is one molecule of one series, so
    two rasters cannot share rows. The panels share the frame axis and the one
    ``|lag-0|`` colour bar.

    Parameters
    ----------
    population
        The per-molecule scans to draw, from
        [`population_anticorrelation_events`][tether.analysis.anticorrelation.population_anticorrelation_events],
        or an ordered sequence of ``(label, population)`` series. A one-series sequence
        draws the same figure as the bare population.
    title
        The axes title; for several series, the figure title above the panels.
    figsize
        Figure size in inches; defaults to [`DEFAULT_FIGSIZE`][] for one population and
        to a size that fits the stacked panels for several.
    """
    series = _as_series(population, (PopulationAnticorrelation,), "PopulationAnticorrelation")
    if series is None or len(series) == 1:
        one = population if series is None else series[0][1]
        fig = _one_population_anticorrelation(one, title=title, figsize=figsize)  # type: ignore[arg-type]
        if series is not None:
            _record_series(fig, series)
        return fig
    n = len(series)
    fig = _new_figure(figsize or (DEFAULT_FIGSIZE[0], 2.4 * n + 0.8))
    axes = list(fig.subplots(n, 1, sharex=True, squeeze=False).ravel())
    cmap = colormaps["viridis"]
    norm = Normalize(vmin=0.0, vmax=1.0)
    longest = max((m.scan.n_frames for _, p in series for m in p.molecules), default=0)
    for ax, (label, p) in zip(axes, series, strict=True):
        _anticorrelation_rows(ax, p, cmap, norm)
        ax.set_xlim(0.0, float(max(longest, 1)))
        ax.set_ylabel("Molecule")
        ax.set_title(_drawn(label))
    axes[-1].set_xlabel("Frame (analysis window)")
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, label="Peak |lag-0| correlation")
    fig.suptitle(title)
    _record_series(fig, series, display=_small_multiples_display(fig, n))
    return fig


def _one_population_anticorrelation(
    population: PopulationAnticorrelation,
    *,
    title: str = "Donor–acceptor anticorrelation events",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Draw a single result, the figure ``render_population_anticorrelation`` has always drawn."""
    molecules = population.molecules
    cmap = colormaps["viridis"]
    norm = Normalize(vmin=0.0, vmax=1.0)

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    _anticorrelation_rows(ax, population, cmap, norm)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, label="Peak |lag-0| correlation")
    longest = max((mol.scan.n_frames for mol in molecules), default=0)
    ax.set_xlim(0.0, float(max(longest, 1)))
    ax.set_xlabel("Frame (analysis window)")
    ax.set_ylabel("Molecule")
    ax.set_title(title)
    return fig


def _anticorrelation_rows(
    ax: Any, population: PopulationAnticorrelation, cmap: Any, norm: Normalize
) -> None:
    """Draw one population's event raster on ``ax``: its rows, ticks and notes."""
    molecules = population.molecules
    peaks_x: list[float] = []
    peaks_y: list[float] = []
    for row, mol in enumerate(molecules):
        events = mol.scan.events
        if not events:
            continue
        ax.broken_barh(
            [(float(e.start), float(e.n_frames)) for e in events],
            (row - 0.35, 0.7),
            facecolors=[cmap(norm(e.peak_magnitude)) for e in events],
            edgecolors="none",
        )
        peaks_x.extend(float(e.peak_frame) for e in events)
        peaks_y.extend(float(row) for _ in events)
    if peaks_x:
        ax.plot(peaks_x, peaks_y, "|", color="k", ms=8, mew=1.0, ls="none", label="peak")
    n_rows = len(molecules)
    ax.set_ylim(n_rows - 0.5 if n_rows else 0.5, -0.5)
    if n_rows <= _ANTICORR_MAX_KEY_LABELS:
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([mol.molecule_key for mol in molecules], fontsize=6)
    if not n_rows:
        ax.annotate(
            "No molecule was long enough to scan",
            xy=(0.5, 0.5),
            xycoords="axes fraction",
            ha="center",
            va="center",
            fontsize=9,
        )
    ax.annotate(
        f"{_count(population.n_events, 'event')} · N = {population.n_molecules}"
        f" · window {population.window}",
        xy=(0.97, 0.02),
        xycoords="axes fraction",
        ha="right",
        va="bottom",
        fontsize=8,
    )
