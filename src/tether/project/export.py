# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Flat tabular exports from a ``.tether`` project (PRD §7.9 FR-EXPORT).

Two provenance-stamped table exports that read a project store and write plain
text a downstream tool (Deep-LASI, a spreadsheet, pandas) can consume:

* :func:`export_deeplasi_txt` — the Deep-LASI ``…-donc-accc-w.txt``
  corrected-trace matrix (a ``T × 2N`` table, one interleaved ``(donor, acceptor)``
  column pair per molecule; the write-side companion to
  :func:`tether.io.deeplasi.read_deeplasi_txt`), trimmed to the molecules' shared
  native frame extent so a round-trip preserves the traces to the 5-decimal text
  rounding.
* :func:`export_molecule_table_csv` — one CSV row per molecule carrying the
  ``/molecules`` scalar fields (identity, correction factors, photobleach frames,
  curation state, windows) plus the derived per-molecule **apparent-E** summary
  (mean/median over the analysis window, and the finite-frame count).

Every export writes a companion ``<file>.provenance.json`` sidecar stamping the
Tether app version, a UTC timestamp, the source project, and the export
parameters (§8 NFR-REPRO — "all exports are stamped with provenance and
parameters"). Flat text has no metadata slot, and the ``.txt`` must stay
Deep-LASI-faithful (no header lines), so the stamp travels in the sidecar rather
than inline.

This is the orchestration layer: it reads the store, derives E, and feeds pure
serializers (:func:`tether.io.deeplasi.write_deeplasi_txt`, the stdlib
:mod:`csv`) — mirroring how :mod:`tether.project.handoff` drives the SMD
:func:`tether.idealize.write_smd` primitive. Read-only on the store: it writes
external files only, never mutates the ``.tether`` (schema-freeze neutral).
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tether.fret.efficiency import apparent_fret
from tether.imaging.extract import read_molecules, read_traces
from tether.io.deeplasi import write_deeplasi_txt
from tether.project.core import Project
from tether.project.labels import CurationLabel
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = [
    "ExportResult",
    "export_deeplasi_txt",
    "export_molecule_table_csv",
    "write_provenance_sidecar",
]

#: The per-molecule CSV column order (frozen; a reader may key on these names).
MOLECULE_TABLE_COLUMNS: tuple[str, ...] = (
    "molecule_id",
    "molecule_key",
    "movie_id",
    "source_filename",
    "condition_id",
    "condition_id_provisional",
    "curation_label",
    "category",
    "quality_class",
    "aperture_id",
    "alpha",
    "gamma",
    "delta",
    "correction_method",
    "correction_confidence",
    "donor_bleach_frame",
    "acceptor_bleach_frame",
    "frame_start",
    "frame_end",
    "window_start",
    "window_end",
    "tags",
    "n_finite_frames",
    "mean_apparent_e",
    "median_apparent_e",
)

_CURATION_TEXT = {
    int(CurationLabel.ACCEPT): "accept",
    int(CurationLabel.UNCURATED): "uncurated",
    int(CurationLabel.REJECT): "reject",
}


@dataclass(frozen=True)
class ExportResult:
    """The artifacts a table export wrote: the data file, its provenance sidecar,
    and the molecule count actually exported (after the curation/selection filter)."""

    path: Path
    provenance_path: Path
    n_molecules: int


def _app_version() -> str:
    """Best-effort Tether version for the provenance stamp (NFR-REPRO).

    Mirrors the ``_app_version`` helper duplicated across the ``tether.project``
    writers (leakage/gamma/correct/…): resolve the git-derived package version,
    never raise from a provenance stamp.
    """
    try:
        from tether import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - defensive; __version__ has its own fallback
        return "0.0.0+unknown"


def _project_path(project: Project | str | os.PathLike[str]) -> Path:
    """Resolve a project reference (an open :class:`Project` or a path) to its path."""
    if isinstance(project, Project):
        return Path(project.path)
    return Path(project)


def _to_str(value: object) -> str:
    """Decode an ``h5py`` structured-array cell (``bytes``) or coerce to ``str``."""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _curation_text(value: int) -> str:
    return _CURATION_TEXT.get(int(value), str(int(value)))


def _fmt_float(value: float | None) -> str:
    """CSV cell for a float: empty string for ``None``/non-finite (a gap), else the value."""
    if value is None:
        return ""
    scalar = float(value)
    if not np.isfinite(scalar):
        return ""
    return repr(scalar)


def _layers(intensity_quantity: str) -> tuple[str, str]:
    """The (donor, acceptor) ``/traces`` dataset names for an intensity quantity."""
    try:
        return INTENSITY_QUANTITY_LAYERS[intensity_quantity]
    except KeyError:
        raise ValueError(
            f"unknown intensity_quantity {intensity_quantity!r}; expected one of "
            f"{sorted(INTENSITY_QUANTITY_LAYERS)}"
        ) from None


def _window(molecules: np.ndarray, i: int) -> tuple[int, int]:
    """The half-open analysis window for row ``i``, falling back to ``frame_range``
    when ``analysis_window`` is unset (``hi <= lo``) — matching the analysis views."""
    lo, hi = int(molecules["analysis_window"][i][0]), int(molecules["analysis_window"][i][1])
    if hi <= lo:
        lo, hi = int(molecules["frame_range"][i][0]), int(molecules["frame_range"][i][1])
    return lo, hi


def _selected_rows(
    molecules: np.ndarray,
    molecule_keys: list[str] | None,
    include_rejected: bool,
) -> list[int]:
    """Row indices to export, in store order, applying the §7.5 curation filter
    (drop ``REJECT`` unless ``include_rejected``) and an optional ``molecule_key``
    membership subselect. ``molecule_key`` is not unique (§7.10), so a requested key
    matches every store row that carries it.

    Raises
    ------
    KeyError
        If a requested ``molecule_key`` matches no store row — a caller typo fails
        loudly rather than silently under-exporting (matching the
        :func:`tether.project.handoff` selection).
    """
    wanted = None if molecule_keys is None else {str(k) for k in molecule_keys}
    reject = int(CurationLabel.REJECT)
    rows: list[int] = []
    seen: set[str] = set()
    for i in range(int(molecules.shape[0])):
        key = _to_str(molecules["molecule_key"][i])
        if wanted is not None and key not in wanted:
            continue
        # Record the match *before* the curation filter, so a requested key that
        # exists but is entirely REJECT reads as an empty selection, not "unknown".
        seen.add(key)
        if not include_rejected and int(molecules["curation_label"][i]) == reject:
            continue
        rows.append(i)
    if wanted is not None:
        missing = sorted(wanted - seen)
        if missing:
            raise KeyError(f"no molecule with molecule_key(s) {missing} in the store")
    return rows


def write_provenance_sidecar(
    data_path: str | os.PathLike[str],
    *,
    tether_export: str,
    source: str,
    parameters: dict[str, object],
) -> Path:
    """Write a ``<data_path>.provenance.json`` stamp beside a flat-text export.

    Records the three-part NFR-REPRO provenance — ``app_version`` (git-derived) +
    ``created_utc`` (offset-aware ISO-8601 UTC) + the export ``parameters`` — plus
    the export kind and source project, so a table file traces back to the build and
    project that produced it. Returns the sidecar path.
    """
    data_path = Path(data_path)
    sidecar = data_path.with_name(data_path.name + ".provenance.json")
    payload = {
        "tether_export": tether_export,
        "app_version": _app_version(),
        "created_utc": datetime.now(UTC).isoformat(),
        "source_project": source,
        "parameters": parameters,
    }
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sidecar


def export_deeplasi_txt(
    project: Project | str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    molecule_keys: list[str] | None = None,
    intensity_quantity: str = "corrected",
    include_rejected: bool = False,
) -> ExportResult:
    """Export corrected per-frame traces as a Deep-LASI ``…-donc-accc-w.txt``.

    Reads the ``intensity_quantity`` ``/traces`` layer (default ``"corrected"``),
    selects molecules in **store order** (the §7.5 curation filter drops ``REJECT``
    unless ``include_rejected``; an optional ``molecule_keys`` list subselects by
    ``molecule_key``), and writes the interleaved-donor-first ``T × 2N`` matrix via
    :func:`tether.io.deeplasi.write_deeplasi_txt`, plus a provenance sidecar.

    Raises
    ------
    KeyError
        If a requested ``molecule_key`` matches no store row (a typo — fail loudly).
    ValueError
        If ``intensity_quantity`` is unknown; the selection is empty (nothing to
        write — :func:`read_deeplasi_txt` rejects a zero-column table); or the
        selection spans molecules of differing native frame extent (see below).
    """
    path = _project_path(project)
    donor_layer, acceptor_layer = _layers(intensity_quantity)
    molecules = read_molecules(path)
    traces = read_traces(path)
    donor_all = np.asarray(traces[donor_layer], dtype=np.float64)
    acceptor_all = np.asarray(traces[acceptor_layer], dtype=np.float64)

    rows = _selected_rows(molecules, molecule_keys, include_rejected)
    if not rows:
        raise ValueError(
            "no molecules selected to export (empty selection, or every match rejected "
            "with include_rejected=False)"
        )

    # The /traces arrays are zero-padded to the store's experiment-max frame count as
    # movies of differing length are appended; each molecule's valid native extent is
    # its frame_range. A Deep-LASI .txt is a single shared frame axis (one movie per
    # file), so trim to the molecules' shared frame_range — never write a molecule's
    # zero pad as if it were a real corrected frame. A selection that spans differing
    # extents has no honest common axis; refuse it rather than pad/truncate silently.
    extents = {
        (int(molecules["frame_range"][i][0]), int(molecules["frame_range"][i][1])) for i in rows
    }
    if len(extents) != 1:
        raise ValueError(
            f"selected molecules span multiple frame ranges {sorted(extents)} (movies of "
            "differing length); a Deep-LASI .txt has a single shared frame axis — scope "
            "the export to one movie via molecule_keys"
        )
    lo, hi = extents.pop()

    out_path = Path(out_path)
    write_deeplasi_txt(out_path, donor_all[rows][:, lo:hi], acceptor_all[rows][:, lo:hi])
    provenance = write_provenance_sidecar(
        out_path,
        tether_export="deeplasi-txt",
        source=path.name,
        parameters={
            "intensity_quantity": intensity_quantity,
            "include_rejected": include_rejected,
            "n_molecules": len(rows),
            "n_frames": hi - lo,
            "frame_range": [lo, hi],
            "molecule_keys": None if molecule_keys is None else [str(k) for k in molecule_keys],
        },
    )
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=len(rows))


def export_molecule_table_csv(
    project: Project | str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    intensity_quantity: str = "corrected",
    include_rejected: bool = True,
) -> ExportResult:
    """Export one CSV row per molecule (the ``/molecules`` scalars + derived E).

    Columns are :data:`MOLECULE_TABLE_COLUMNS`. Each row carries the stored
    per-molecule fields (identity, condition, correction factors α/γ/δ, photobleach
    frames, frame range, analysis window, curation label, category, ML
    ``quality_class``, tags) plus a derived apparent-E summary — mean/median of
    ``A / (D + A)`` over the molecule's analysis window (falling back to its frame
    range) and the finite-frame count. Apparent E is used for the summary because it
    is always well-defined; the raw α/γ factors are emitted as their own columns so a
    consumer can recompute the corrected E. A ``<file>.provenance.json`` sidecar is
    written beside the CSV.

    By default every molecule is included (``include_rejected=True`` — the CSV is a
    full inventory); set it ``False`` to drop ``REJECT`` rows.

    Raises
    ------
    ValueError
        If ``intensity_quantity`` is unknown.
    """
    path = _project_path(project)
    donor_layer, acceptor_layer = _layers(intensity_quantity)
    molecules = read_molecules(path)
    traces = read_traces(path)
    donor_all = np.asarray(traces[donor_layer], dtype=np.float64)
    acceptor_all = np.asarray(traces[acceptor_layer], dtype=np.float64)

    rows = _selected_rows(molecules, molecule_keys=None, include_rejected=include_rejected)

    out_path = Path(out_path)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MOLECULE_TABLE_COLUMNS)
        for i in rows:
            lo, hi = _window(molecules, i)
            efficiency = apparent_fret(donor_all[i, lo:hi], acceptor_all[i, lo:hi])
            finite = efficiency[np.isfinite(efficiency)]
            n_finite = int(finite.size)
            mean_e = float(np.mean(finite)) if n_finite else None
            median_e = float(np.median(finite)) if n_finite else None
            bleach = molecules["bleach_frames"][i]
            frame_range = molecules["frame_range"][i]
            window = molecules["analysis_window"][i]
            writer.writerow(
                [
                    _to_str(molecules["molecule_id"][i]),
                    _to_str(molecules["molecule_key"][i]),
                    _to_str(molecules["movie_id"][i]),
                    _to_str(molecules["source_filename"][i]),
                    _to_str(molecules["condition_id"][i]),
                    _to_str(molecules["condition_id_provisional"][i]),
                    _curation_text(int(molecules["curation_label"][i])),
                    _to_str(molecules["category"][i]),
                    _fmt_float(float(molecules["quality_class"][i])),
                    int(molecules["aperture_id"][i]),
                    _fmt_float(float(molecules["alpha"][i])),
                    _fmt_float(float(molecules["gamma"][i])),
                    _fmt_float(float(molecules["delta"][i])),
                    _to_str(molecules["correction_method"][i]),
                    _fmt_float(float(molecules["correction_confidence"][i])),
                    int(bleach[0]),
                    int(bleach[1]),
                    int(frame_range[0]),
                    int(frame_range[1]),
                    int(window[0]),
                    int(window[1]),
                    _to_str(molecules["tags"][i]),
                    n_finite,
                    _fmt_float(mean_e),
                    _fmt_float(median_e),
                ]
            )

    provenance = write_provenance_sidecar(
        out_path,
        tether_export="molecule-table-csv",
        source=path.name,
        parameters={
            "intensity_quantity": intensity_quantity,
            "include_rejected": include_rejected,
            "n_molecules": len(rows),
        },
    )
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=len(rows))
