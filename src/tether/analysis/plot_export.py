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
  :class:`matplotlib.figure.Figure` API directly (never ``pyplot``), so no GUI
  backend is ever selected — this keeps the base environment free of PyQt5/Tk
  (the §4.1 no-PyQt5 base invariant that motivated shipping ``matplotlib-base``,
  ADR-0044). ``Figure.savefig`` attaches the format-appropriate canvas (Agg for
  PNG, ``backend_pdf`` for PDF, ``backend_svg`` for SVG) on demand.
- **Rendering is separate from IO.** Each ``render_*`` returns a bare
  :class:`~matplotlib.figure.Figure` (no stamp, no file); :func:`export_figure`
  adds the visible provenance footer and writes the three files + the sidecar.
  A caller can therefore embed the same figure in the GUI and export it
  identically.
- **Provenance travels three ways** (§8 NFR-REPRO, invariant §1.3(4)): a
  ``<stem>.provenance.json`` sidecar (reusing
  :func:`tether.project.export.write_provenance_sidecar`, the single stamping
  convention shared with the table/subset exports); a **visible on-figure footer**
  (``Tether <version> · <utc> · <kind>``) so the stamp travels *on the plot* a
  reader sees; and **embedded file metadata** (PDF/SVG/PNG document metadata).
- **Deterministic output.** The timestamp is threaded once through the sidecar,
  footer, and embedded metadata so they agree exactly; SVG element ids use a fixed
  ``svg.hashsalt`` and the auto-generated SVG/PDF creation date is pinned to the
  stamp time, so a re-export of the same figure at the same ``created_utc`` is
  reproducible.

This module is intentionally **not** eagerly imported by ``tether.analysis``'s
package ``__init__`` — importing it pulls in Matplotlib, which the lightweight
headless test paths do not always have. Import it explicitly::

    from tether.analysis.plot_export import export_figure, render_histogram1d
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from matplotlib import rc_context
from matplotlib.figure import Figure

from tether.analysis.cloud import RawFretCloud
from tether.analysis.crosscorr import CrossCorrelation
from tether.analysis.dwell import DwellTimeAnalysis
from tether.analysis.histogram import (
    Histogram1D,
    Histogram2D,
    HistogramBootstrapCI,
    ModelGaussianOverlay,
)
from tether.analysis.state_number import StateNumberCounts
from tether.analysis.tdp import TransitionDensityPlot
from tether.analysis.transition_prob import TransitionProbHistogram
from tether.project.export import write_provenance_sidecar

__all__ = [
    "DEFAULT_EXPORT_DPI",
    "DEFAULT_FIGSIZE",
    "DEFAULT_PLOT_FORMATS",
    "PlotExportResult",
    "export_figure",
    "render_cross_correlation",
    "render_dwell_survival",
    "render_histogram1d",
    "render_histogram2d",
    "render_raw_fret_cloud",
    "render_state_number",
    "render_transition_density",
    "render_transition_prob",
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

_STATE_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b")


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
        *"stamped with provenance and parameters"* payload.
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
    """A constrained-layout :class:`Figure` (no pyplot, so no GUI backend)."""
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

    Accepts a bare :class:`~tether.analysis.histogram.Histogram1D` or a
    :class:`~tether.analysis.histogram.HistogramBootstrapCI` (whose per-bin
    percentile band is drawn as a shaded region). An optional
    :class:`~tether.analysis.histogram.ModelGaussianOverlay` draws the idealized
    model's per-state Gaussians and their sum.
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
    """Render the A2 2-D time-vs-signal occupancy heatmap (Appendix C A2)."""
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


def render_transition_density(
    tdp: TransitionDensityPlot,
    *,
    title: str = "Transition-density plot (B1)",
    figsize: tuple[float, float] | None = None,
) -> Figure:
    """Render the B1 real transition-density plot: initial vs final idealized E."""
    counts = np.asarray(tdp.counts, dtype="float64")  # (signal_bins, signal_bins) [init, final]
    edges = np.asarray(tdp.signal_edges, dtype="float64")

    fig = _new_figure(figsize)
    ax = fig.add_subplot()
    # counts is [initial, final]; x=initial, y=final → transpose so rows=final.
    mesh = ax.pcolormesh(edges, edges, counts.T, cmap="viridis", shading="flat")
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
    """Render the B2 dwell-time survival curve for one state, with its exponential fit
    and (when fitted) a residuals subplot."""
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
    """Render the B3 transition-probability histogram for one ``init → final`` pair,
    with the optional Gaussian-KDE overlay."""
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
    """Render the C1 state-number bar chart: molecules by number of occupied states."""
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
    """Render the raw FRET cloud QC view: the pooled ``(time, apparent-E)`` scatter with
    the KDE density surface and highest-density-region contours when present.

    ``max_points`` caps the scatter overlay for a manageable vector file; the density
    surface (when computed) is unaffected by that cap — but it was fit by
    :func:`tether.analysis.cloud.raw_fret_cloud` on the in-range points only
    (``n_out_of_range`` counts the rest).
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
    """Render the donor–acceptor cross-correlation curve vs lag."""
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
