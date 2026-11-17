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
  [`matplotlib.figure.Figure`][] API directly (never ``pyplot``), so no GUI
  backend is ever selected — this keeps the base environment free of PyQt5/Tk
  (the §4.1 no-PyQt5 base invariant that motivated shipping ``matplotlib-base``,
  ADR-0044). ``Figure.savefig`` attaches the format-appropriate canvas (Agg for
  PNG, ``backend_pdf`` for PDF, ``backend_svg`` for SVG) on demand.
- **Rendering is separate from IO.** Each ``render_*`` returns a bare
  [`Figure`][matplotlib.figure.Figure] (no stamp, no file); [`export_figure`][]
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

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
from tether.analysis.tdp import TransitionDensityPlot
from tether.analysis.transition_prob import TransitionProbHistogram
from tether.project.export import write_provenance_sidecar

__all__ = [
    "DEFAULT_EXPORT_DPI",
    "DEFAULT_FIGSIZE",
    "DEFAULT_PLOT_FORMATS",
    "DEFAULT_TDP_COLOR_SCALE",
    "TDP_COLOR_SCALES",
    "PlotExportResult",
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
    figure : matplotlib.figure.Figure
        The figure to write, usually from one of the ``render_*`` functions.
    out_stem : str or os.PathLike
        The output path with no extension. Each format adds its own extension
        (``<stem>.pdf``, ``<stem>.svg``, ``<stem>.png``). The provenance file is
        ``<stem>.provenance.json``.
    tether_export : str
        The export kind, for example ``"plot:tdp"``. The stamp and the file metadata
        record it.
    source : str
        The name of the source project or data set, recorded in the provenance file.
    parameters : dict of str to object
        The plot parameters to record, such as the bins, the ranges and the model name.
        This is the *"stamped with provenance and parameters"* record of PRD §7.9. The
        function adds how the ``render_*`` function drew the data (for example the TDP
        ``color_scale``) under ``"display"``, read from the figure itself.
    title : str or None, optional
        The title in the document metadata. The default ``None`` uses
        ``tether_export``.
    formats : tuple of str, optional
        The formats to write, a subset of ``("pdf", "svg", "png")``. The default
        [`DEFAULT_PLOT_FORMATS`][] writes all three.
    dpi : int, optional
        The PNG resolution, in dots per inch. The vector formats ignore it. The default
        is [`DEFAULT_EXPORT_DPI`][] (200 dots per inch).
    stamp : bool, optional
        Whether to draw the visible provenance footer on the figure. The default is
        ``True``.
    created_utc : str or None, optional
        A fixed ISO-8601 UTC timestamp for the stamp. With it, the export of a freshly
        rendered, equal figure gives the same files again. Export each figure once: with
        ``stamp=True`` every call draws one more footer on the figure, so a second
        export of the same figure differs. The default ``None`` uses the current time.

    Returns
    -------
    PlotExportResult
        The stem, the map of each format to the path written, and the path of the
        provenance file.

    Raises
    ------
    ValueError
        If ``formats`` is empty or names a format other than ``"pdf"``, ``"svg"`` or
        ``"png"``, or if ``parameters["display"]`` contradicts how the figure was drawn.

    Examples
    --------
    >>> import tempfile
    >>> from pathlib import Path
    >>> import numpy as np
    >>> from tether.analysis.histogram import apparent_e_histogram
    >>> from tether.analysis.plot_export import export_figure, render_histogram1d
    >>> fig = render_histogram1d(apparent_e_histogram(np.array([0.2, 0.3, 0.7, 0.8])))
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     result = export_figure(
    ...         fig,
    ...         Path(tmp) / "a1",
    ...         tether_export="plot:a1",
    ...         source="demo",
    ...         parameters={"bins": 50},
    ...         created_utc="2026-01-01T00:00:00+00:00",
    ...     )
    ...     print(result.formats, result.provenance_path.name)
    ('pdf', 'svg', 'png') a1.provenance.json
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
    """A constrained-layout [`Figure`][] (no pyplot, so no GUI backend)."""
    return Figure(figsize=figsize or DEFAULT_FIGSIZE, layout="constrained")


def _bin_centers(edges: np.ndarray) -> np.ndarray:
    edges = np.asarray(edges, dtype="float64")
    return 0.5 * (edges[:-1] + edges[1:])


def render_histogram1d(
    hist: Histogram1D | HistogramBootstrapCI,
    *,
    overlay: ModelGaussianOverlay | None = None,
    title: str = "FRET-efficiency histogram (A1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the A1 1-D population apparent-E histogram (Appendix C A1).

    Parameters
    ----------
    hist : Histogram1D or HistogramBootstrapCI
        The histogram to draw: a bare
        [`Histogram1D`][tether.analysis.histogram.Histogram1D], or a
        [`HistogramBootstrapCI`][tether.analysis.histogram.HistogramBootstrapCI], whose
        per-bin percentile band is drawn as a shaded region. Apparent FRET efficiency is
        unitless.
    overlay : ModelGaussianOverlay or None, optional
        A [`ModelGaussianOverlay`][tether.analysis.histogram.ModelGaussianOverlay] to draw
        over the histogram: the Gaussian of each state of the idealized model, and their
        sum. The default ``None`` draws no overlay.
    title : str, optional
        The axes title. The default is ``"FRET-efficiency histogram (A1)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.histogram import apparent_e_histogram
    >>> from tether.analysis.plot_export import render_histogram1d
    >>> hist = apparent_e_histogram(np.array([0.2, 0.3, 0.3, 0.7, 0.8]))
    >>> fig = render_histogram1d(hist)
    >>> len(fig.axes)
    1
    """
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
    hist: Histogram2D,
    *,
    title: str = "Time-vs-signal heatmap (A2)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the A2 2-D time-vs-signal occupancy heatmap (Appendix C A2).

    Parameters
    ----------
    hist : Histogram2D
        The heatmap to draw, from
        [`time_signal_histogram2d`][tether.analysis.histogram.time_signal_histogram2d] or
        [`population_time_signal_histogram2d`][tether.analysis.histogram.population_time_signal_histogram2d].
        The time axis is drawn in seconds when its ``time_dt`` (seconds per frame) is
        not ``1.0``, and in frames otherwise. Apparent FRET efficiency is unitless.
    title : str, optional
        The axes title. The default is ``"Time-vs-signal heatmap (A2)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it. The figure has two axes: the
        heatmap and its color bar.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.histogram import time_signal_histogram2d
    >>> from tether.analysis.plot_export import render_histogram2d
    >>> hist = time_signal_histogram2d([np.array([0.2, 0.2, 0.8, 0.8])])
    >>> fig = render_histogram2d(hist)
    >>> len(fig.axes), fig.axes[0].get_xlabel()
    (2, 'Frame')
    """
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


def _tdp_norm(counts: np.ndarray, color_scale: str) -> Normalize:
    """The colour normalization for a TDP ``counts`` array.

    ``"log"`` is zero-safe: [`LogNorm`][matplotlib.colors.LogNorm] masks every bin
    ``<= 0`` instead of taking ``log(0) = -inf``, and its limits come from the positive
    finite bins only. The floor sits `_TDP_LOG_FLOOR_DECADES` below the smallest
    non-empty bin, so the weakest transition is drawn visibly above the floor colour
    that marks an empty bin. With no positive bin at all (an empty TDP) there is no
    magnitude to span, so the limits are a nominal ``[1, 10]`` and every bin is masked.
    """
    positive = counts[np.isfinite(counts) & (counts > 0)]
    if color_scale == "log":
        if positive.size == 0:
            return LogNorm(vmin=1.0, vmax=10.0)
        floor = float(positive.min()) / 10.0**_TDP_LOG_FLOOR_DECADES
        return LogNorm(vmin=floor, vmax=float(positive.max()))
    finite = counts[np.isfinite(counts)]
    vmax = float(finite.max()) if finite.size else 1.0
    return Normalize(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def render_transition_density(
    tdp: TransitionDensityPlot,
    *,
    title: str = "Transition-density plot (B1)",
    figsize: tuple[float, float] | None = None,
    color_scale: str = DEFAULT_TDP_COLOR_SCALE,
) -> Figure:
    """Render the B1 real transition-density plot: initial vs final idealized E.

    Parameters
    ----------
    tdp : TransitionDensityPlot
        The binned transition density to draw, from
        [`transition_density`][tether.analysis.tdp.transition_density] or
        [`population_transition_density`][tether.analysis.tdp.population_transition_density].
        Apparent FRET efficiency is unitless.
    title : str, optional
        The axes title. The default is ``"Transition-density plot (B1)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).
    color_scale : str, optional
        ``"log"`` or ``"linear"``. The default is [`DEFAULT_TDP_COLOR_SCALE`][]
        (``"log"``). PRD Appendix C B1 names the TDP log-normalized, so a rare
        transition stays visible beside a dominant one. The log scale is zero-safe: an
        empty bin is drawn in the floor color of the color map, never as ``-inf`` or a
        hole. The scale changes only the display: the drawn array is ``tdp.counts``
        unchanged. [`export_figure`][] records the scale in the provenance file under
        ``parameters["display"]["color_scale"]``.

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it. The figure has two axes: the
        plot and its color bar.

    Raises
    ------
    ValueError
        If ``color_scale`` is not one of [`TDP_COLOR_SCALES`][].

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.plot_export import render_transition_density
    >>> from tether.analysis.tdp import transition_density
    >>> tdp = transition_density([np.array([0.2, 0.2, 0.8, 0.8, 0.2])])
    >>> fig = render_transition_density(tdp, color_scale="linear")
    >>> len(fig.axes)
    2
    """
    if color_scale not in TDP_COLOR_SCALES:
        raise ValueError(
            f"unknown TDP color_scale {color_scale!r}; expected one of {list(TDP_COLOR_SCALES)}"
        )
    counts = np.asarray(tdp.counts, dtype="float64")  # (signal_bins, signal_bins) [init, final]
    edges = np.asarray(tdp.signal_edges, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    # Empty (masked) bins take the colour map's floor colour, as a zero does on the
    # linear scale, so the white annotation and diagonal stay readable on both.
    cmap = colormaps["viridis"]
    cmap = cmap.with_extremes(bad=cmap(0.0), under=cmap(0.0))
    # counts is [initial, final]; x=initial, y=final → transpose so rows=final.
    mesh = ax.pcolormesh(
        edges,
        edges,
        counts.T,
        cmap=cmap,
        norm=_tdp_norm(counts, color_scale),
        shading="flat",
    )
    setattr(fig, _DISPLAY_ATTR, {"color_scale": color_scale})
    fig.colorbar(mesh, ax=ax, label="Density" if tdp.density else "Counts")
    lo, hi = tdp.signal_range
    ax.plot([lo, hi], [lo, hi], color="w", lw=0.8, ls=":", alpha=0.7)
    ax.set_xlim(tdp.signal_range)
    ax.set_ylim(tdp.signal_range)
    ax.set_xlabel("Initial $E$")
    ax.set_ylabel("Final $E$")
    ax.set_aspect("equal")
    ax.set_title(title)
    ax.annotate(
        f"{tdp.n_transitions} transitions · N = {tdp.n_molecules}",
        xy=(0.03, 0.95),
        xycoords="axes fraction",
        ha="left",
        va="top",
        color="white",
        fontsize=8,
    )
    return fig


def render_dwell_survival(
    analysis: DwellTimeAnalysis,
    *,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the B2 dwell-time survival curve of one state, with its exponential fit.

    When the fit succeeded, a second axes below the curve shows the fit residuals.

    Parameters
    ----------
    analysis : DwellTimeAnalysis
        The dwell-time analysis of one state, from
        [`population_dwell_times`][tether.analysis.dwell.population_dwell_times]. The time
        axis is drawn in seconds when its ``dt`` (seconds per frame) is not ``1.0``, and
        in frames otherwise. The survival is unitless, and each fitted rate ``k`` is in
        ``1 / seconds`` or ``1 / frames`` to match.
    title : str or None, optional
        The axes title. The default ``None`` uses
        ``"Dwell survival — state <state> (B2)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it. The figure has two axes when
        the fit succeeded, and one otherwise.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.dwell import DwellTimeAnalysis, fit_survival, survival_curve
    >>> from tether.analysis.plot_export import render_dwell_survival
    >>> lengths = np.array([1, 2, 2, 3, 5])
    >>> tau, survival = survival_curve(lengths)
    >>> analysis = DwellTimeAnalysis(
    ...     state=0,
    ...     level=0.2,
    ...     dt=1.0,
    ...     dwell_lengths=lengths,
    ...     n_dwells=5,
    ...     n_molecules=2,
    ...     tau=tau,
    ...     survival=survival,
    ...     fit=fit_survival(tau, survival),
    ... )
    >>> fig = render_dwell_survival(analysis)
    >>> len(fig.axes), fig.axes[-1].get_xlabel()
    (2, 'Dwell time (frames)')
    """
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
    hist: TransitionProbHistogram,
    *,
    title: str | None = None,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the B3 transition-probability histogram of one ``init → final`` pair.

    When the histogram carries a Gaussian kernel density estimate (KDE), the estimate is
    drawn over the bars.

    Parameters
    ----------
    hist : TransitionProbHistogram
        The per-molecule transition probabilities to draw, from
        [`transition_prob_histogram`][tether.analysis.transition_prob.transition_prob_histogram]
        or
        [`population_transition_prob_histogram`][tether.analysis.transition_prob.population_transition_prob_histogram].
        A probability is unitless.
    title : str or None, optional
        The axes title. The default ``None`` uses
        ``"Transition-probability P(init → final) (B3)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.plot_export import render_transition_prob
    >>> from tether.analysis.transition_prob import transition_prob_histogram
    >>> paths = [np.array([0, 0, 1, 1, 0]), np.array([0, 1, 1, 0, 0])]
    >>> hist = transition_prob_histogram(paths, init_state=0, final_state=1)
    >>> fig = render_transition_prob(hist)
    >>> len(fig.axes)
    1
    """
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
    counts: StateNumberCounts,
    *,
    title: str = "State-number distribution (C1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the C1 state-number bar chart: molecules by number of occupied states.

    Parameters
    ----------
    counts : StateNumberCounts
        The molecule count of each number of occupied states, from
        [`state_number_counts`][tether.analysis.state_number.state_number_counts] or
        [`population_state_number`][tether.analysis.state_number.population_state_number].
    title : str, optional
        The axes title. The default is ``"State-number distribution (C1)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.plot_export import render_state_number
    >>> from tether.analysis.state_number import state_number_counts
    >>> counts = state_number_counts([np.array([0, 1, 1]), np.array([0, 0, 0])])
    >>> fig = render_state_number(counts)
    >>> len(fig.axes)
    1
    """
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
    cloud: RawFretCloud,
    *,
    title: str = "Raw FRET cloud (QC)",
    max_points: int = 20000,
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the raw FRET cloud QC view: the pooled ``(time, apparent-E)`` scatter.

    When the cloud carries them, the kernel density estimate (KDE) surface and the
    highest-density-region contours are drawn under the scatter.

    Parameters
    ----------
    cloud : RawFretCloud
        The pooled cloud to draw, from
        [`raw_fret_cloud`][tether.analysis.cloud.raw_fret_cloud] or
        [`population_raw_fret_cloud`][tether.analysis.cloud.population_raw_fret_cloud].
        The time axis is drawn in seconds when its ``time_dt`` (seconds per frame) is
        not ``1.0``, and in frames otherwise. Apparent FRET efficiency is unitless.
    title : str, optional
        The axes title. The default is ``"Raw FRET cloud (QC)"``.
    max_points : int, optional
        The most points the scatter draws, so that a vector file stays a manageable
        size. Above it the scatter shows every n-th point. The cap does not change the
        density surface, which [`raw_fret_cloud`][tether.analysis.cloud.raw_fret_cloud]
        fit on the in-range points only (``n_out_of_range`` counts the rest). The
        default is 20000.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it. The figure has two axes when
        the cloud carries a density surface (the plot and its color bar), and one
        otherwise.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.cloud import raw_fret_cloud
    >>> from tether.analysis.plot_export import render_raw_fret_cloud
    >>> traces = [np.array([0.2, 0.3, 0.7, 0.8]), np.array([0.25, 0.75, 0.7, 0.3])]
    >>> fig = render_raw_fret_cloud(raw_fret_cloud(traces, kde=False))
    >>> len(fig.axes)
    1
    """
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
    xc: CrossCorrelation,
    *,
    title: str = "Donor–acceptor cross-correlation",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the donor–acceptor cross-correlation curve against the lag.

    Parameters
    ----------
    xc : CrossCorrelation
        The cross-correlation to draw, from
        [`cross_correlation`][tether.analysis.crosscorr.cross_correlation] or
        [`population_cross_correlation`][tether.analysis.crosscorr.population_cross_correlation].
        The lag is in frames, and the Pearson-normalized correlation is unitless.
    title : str, optional
        The axes title. The default is ``"Donor–acceptor cross-correlation"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.crosscorr import cross_correlation
    >>> from tether.analysis.plot_export import render_cross_correlation
    >>> donor = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    >>> acceptor = np.array([3.0, 2.0, 1.0, 2.0, 3.0])
    >>> fig = render_cross_correlation(cross_correlation(donor, acceptor, max_lag=2))
    >>> fig.axes[0].get_xlabel()
    'Lag (frames)'
    """
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
    overlay: PerConditionHistograms,
    *,
    title: str = "Per-condition FRET-efficiency histograms (A1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the §7.7 per-condition overlay: every condition's A1 histogram on one axis.

    Each condition is drawn as an unfilled step outline on the shared
    [`bin_edges`][tether.analysis.histogram.PerConditionHistograms.bin_edges] grid of the
    overlay, in the overlay's own (deterministic) order. Its legend label gives its
    condition id and its molecule count ``N``. Colors follow the ``tab10`` palette. Past
    ten conditions the line style changes too, so that up to 40 conditions no two curves
    share both a color and a line style. Past 40 the pairs repeat (condition 41 is drawn
    like condition 1), and only the legend labels tell those curves apart.

    Parameters
    ----------
    overlay : PerConditionHistograms
        The per-condition histograms to draw, from
        [`per_condition_apparent_e_histograms`][tether.analysis.histogram.per_condition_apparent_e_histograms].
        Apparent FRET efficiency is unitless.
    title : str, optional
        The axes title. The default is
        ``"Per-condition FRET-efficiency histograms (A1)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Raises
    ------
    ValueError
        If the histogram of a condition is not binned on the shared grid of the
        overlay, so that it cannot be drawn on the same axis without misplacing its
        bins.

    Examples
    --------
    >>> from dataclasses import replace
    >>> import numpy as np
    >>> from tether.analysis.histogram import (
    ...     ConditionHistogram,
    ...     PerConditionHistograms,
    ...     apparent_e_histogram,
    ... )
    >>> from tether.analysis.plot_export import render_per_condition_histograms
    >>> low = replace(apparent_e_histogram(np.array([0.2, 0.3, 0.3])), n_molecules=2)
    >>> high = replace(apparent_e_histogram(np.array([0.7, 0.7, 0.8])), n_molecules=3)
    >>> overlay = PerConditionHistograms(
    ...     histograms=(ConditionHistogram("low", low), ConditionHistogram("high", high)),
    ...     bin_edges=low.bin_edges,
    ...     value_range=low.value_range,
    ...     density=True,
    ...     per_molecule_equal_weight=False,
    ...     intensity_quantity="corrected",
    ... )
    >>> fig = render_per_condition_histograms(overlay)
    >>> [text.get_text() for text in fig.axes[0].get_legend().get_texts()]
    ['low (N = 2)', 'high (N = 3)']
    """
    edges = np.asarray(overlay.bin_edges, dtype="float64")
    palette = colormaps["tab10"].colors

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    for k, ch in enumerate(overlay.histograms):
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
    return fig


def _sync_state_label(state: int) -> str:
    """A post-sync state selector as a reader sees it: ``-1`` selects any state."""
    return "any" if int(state) < 0 else str(int(state))


def render_transition_sync_histogram2d(
    hist: TransitionSyncHistogram2D,
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

    Parameters
    ----------
    hist : TransitionSyncHistogram2D
        The transition-aligned heatmap to draw, from
        [`transition_sync_histogram2d`][tether.analysis.histogram.transition_sync_histogram2d]
        or
        [`population_transition_sync_histogram2d`][tether.analysis.histogram.population_transition_sync_histogram2d].
        The time axis is drawn in seconds when its ``time_dt`` (seconds per frame) is
        not ``1.0``, and in frames otherwise. Apparent FRET efficiency is unitless.
    title : str, optional
        The axes title. The default is ``"Transition-synchronized heatmap (A2b)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it. The figure has two axes: the
        heatmap and its color bar.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.histogram import transition_sync_histogram2d
    >>> from tether.analysis.plot_export import render_transition_sync_histogram2d
    >>> path = np.array([0, 0, 0, 1, 1, 1])
    >>> signal = np.where(path == 0, 0.2, 0.8)
    >>> hist = transition_sync_histogram2d([(path, signal)], from_state=0, to_state=1)
    >>> fig = render_transition_sync_histogram2d(hist)
    >>> fig.axes[0].get_xlabel()
    'Frames from transition'
    """
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
    shape: AlphaShape,
    *,
    cloud: RawFretCloud | None = None,
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

    Parameters
    ----------
    shape : AlphaShape
        The α-shape to draw, from
        [`alpha_shape`][tether.analysis.cloud.alpha_shape] or
        [`population_fret_cloud_alpha_shape`][tether.analysis.cloud.population_fret_cloud_alpha_shape].
        Apparent FRET efficiency is unitless.
    cloud : RawFretCloud or None, optional
        The [`RawFretCloud`][tether.analysis.cloud.RawFretCloud] that the shape was
        computed from. When it is given, its points are drawn underneath (thinned to
        ``max_points``), its ranges set the axis limits, and its ``time_dt`` sets the
        time unit. The default ``None`` draws the boundary alone.
    time_dt : float or None, optional
        The frame duration of the time coordinate of the shape, in seconds per frame.
        It sets the axis label when no ``cloud`` is given: ``Time (s)`` when it is not
        ``1.0``, and ``Frame`` otherwise. The default ``None`` uses the ``time_dt`` of
        ``cloud``, or ``1.0`` when there is no cloud.
    title : str, optional
        The axes title. The default is ``"FRET-cloud support (alpha shape)"``.
    max_points : int, optional
        The most points of ``cloud`` that the scatter draws. The boundary is never
        thinned. The default is 20000.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Raises
    ------
    ValueError
        If both ``cloud`` and ``time_dt`` are given and ``time_dt`` is not the
        ``time_dt`` of ``cloud``.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.cloud import alpha_shape
    >>> from tether.analysis.plot_export import render_alpha_shape
    >>> t, e = np.meshgrid(np.linspace(0.0, 1.0, 5), np.linspace(0.0, 1.0, 5))
    >>> shape = alpha_shape(np.column_stack([t.ravel(), e.ravel()]), alpha=1.0)
    >>> fig = render_alpha_shape(shape, time_dt=0.1)
    >>> fig.axes[0].get_xlabel()
    'Time (s)'
    """
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
    if not shape.spans_region:
        note = f"No 2-D region: {shape.n_points} points cannot be triangulated"
    elif shape.n_kept == 0:
        note = f"No support at α = {shape.alpha:.3g}: no triangle kept"
    else:
        note = (
            f"α = {shape.alpha:.3g} · {shape.n_kept} of {shape.n_triangles} triangles"
            f" · area {shape.area:.3g}"
        )
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
    elbow: StateNumberElbow,
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

    Parameters
    ----------
    elbow : StateNumberElbow
        The elbow curve to draw, from
        [`k_rmse_elbow`][tether.analysis.cloud.k_rmse_elbow] or
        [`population_fret_cloud_state_number_elbow`][tether.analysis.cloud.population_fret_cloud_state_number_elbow].
        The RMSE is in apparent FRET efficiency, which is unitless.
    title : str, optional
        The axes title. The default is ``"State-number hint (k-vs-RMSE elbow)"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.cloud import k_rmse_elbow
    >>> from tether.analysis.plot_export import render_state_number_elbow
    >>> values = np.array([0.2, 0.21, 0.19, 0.5, 0.51, 0.49, 0.8, 0.81, 0.79])
    >>> elbow = k_rmse_elbow(values, k_max=5)
    >>> fig = render_state_number_elbow(elbow)
    >>> len(fig.axes)
    1
    """
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
    population: PopulationAnticorrelation,
    *,
    title: str = "Donor–acceptor anticorrelation events",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the population's anticorrelation events as a per-molecule raster (§7.7).

    The raster has one row for each scanned molecule, in the store order of the
    population (top to bottom). Each event is drawn as a bar over its ``[start, stop)``
    frame span, colored by its peak strength ``|lag-0|``, and a tick marks the peak frame
    of each event. A molecule scanned with no event keeps its empty row, so the raster
    shows how many molecules had none. Up to 30 molecules, each row is labeled by its
    ``molecule_key``. Above 30, the rows are labeled by index.

    Parameters
    ----------
    population : PopulationAnticorrelation
        The per-molecule scans to draw, from
        [`population_anticorrelation_events`][tether.analysis.anticorrelation.population_anticorrelation_events].
        The frame axis is in frames of the analysis window, and the correlation is
        unitless.
    title : str, optional
        The axes title. The default is ``"Donor–acceptor anticorrelation events"``.
    figsize : tuple of float or None, optional
        The figure size ``(width, height)``, in inches. The default ``None`` uses
        [`DEFAULT_FIGSIZE`][] (6.4 by 4.8 inches).

    Returns
    -------
    matplotlib.figure.Figure
        A bare figure: it carries no provenance stamp, and no file is written. Pass it
        to [`export_figure`][] to stamp it and write it. The figure has two axes: the
        raster and its color bar.

    Examples
    --------
    >>> import numpy as np
    >>> from tether.analysis.anticorrelation import (
    ...     MoleculeAnticorrelation,
    ...     PopulationAnticorrelation,
    ...     find_anticorrelation_events,
    ... )
    >>> from tether.analysis.plot_export import render_population_anticorrelation
    >>> donor = 500.0 + np.arange(60.0)
    >>> scan = find_anticorrelation_events(donor, 0.5 * donor)
    >>> population = PopulationAnticorrelation(
    ...     molecules=(MoleculeAnticorrelation("m0", scan),),
    ...     window=scan.window,
    ...     step=scan.step,
    ...     min_magnitude=scan.min_magnitude,
    ...     min_windows=scan.min_windows,
    ...     n_molecules=1,
    ...     n_events=scan.n_events,
    ... )
    >>> fig = render_population_anticorrelation(population)
    >>> [label.get_text() for label in fig.axes[0].get_yticklabels()]
    ['m0']
    """
    molecules = population.molecules
    cmap = colormaps["viridis"]
    norm = Normalize(vmin=0.0, vmax=1.0)

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
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
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, label="Peak |lag-0| correlation")

    n_rows = len(molecules)
    longest = max((mol.scan.n_frames for mol in molecules), default=0)
    ax.set_xlim(0.0, float(max(longest, 1)))
    ax.set_ylim(n_rows - 0.5 if n_rows else 0.5, -0.5)
    if n_rows <= _ANTICORR_MAX_KEY_LABELS:
        ax.set_yticks(range(n_rows))
        ax.set_yticklabels([mol.molecule_key for mol in molecules], fontsize=6)
    ax.set_xlabel("Frame (analysis window)")
    ax.set_ylabel("Molecule")
    ax.set_title(title)
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
    return fig
