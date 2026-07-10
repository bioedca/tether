# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exports from a ``.tether`` project (PRD §7.9 FR-EXPORT).

Three provenance-stamped exports that read a project store and produce artifacts a
downstream tool or a portable re-analysis can consume:

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
* :func:`export_subset_tether` — a **movie-less subset ``.tether``** (§7.9, §5.4):
  a self-contained new project embedding the selected molecules' coordinates,
  patches, corrected traces, and idealization models, with the source **raw**
  traces optional. It carries **no** ``/movies`` rows (definitionally movie-less)
  so it never opens a source movie, and — because ``corrected = raw − background``
  exactly — omitting raw also omits the per-frame background, so raw is genuinely
  **not reconstructable** from the subset (the §5.4 invariant). Unlike the two
  table exports it *writes a new store*, but it never mutates the source and adds
  no structure to the M0-frozen skeleton (it builds the subset via
  :func:`tether.io.schema.create_project` and writes only additive data).

Every export writes a companion ``<file>.provenance.json`` sidecar stamping the
Tether app version, a UTC timestamp, the source project, and the export
parameters (§8 NFR-REPRO — "all exports are stamped with provenance and
parameters"); the subset ``.tether`` additionally stamps that provenance into its
own root attributes (its embedded provenance, §7.9). Flat text has no metadata
slot, and the ``.txt`` must stay Deep-LASI-faithful (no header lines), so its
stamp travels in the sidecar rather than inline.

The table exports are the orchestration layer: they read the store, derive E, and
feed pure serializers (:func:`tether.io.deeplasi.write_deeplasi_txt`, the stdlib
:mod:`csv`) — mirroring how :mod:`tether.project.handoff` drives the SMD
:func:`tether.idealize.write_smd` primitive. They are read-only on the store. The
subset export is a filtered store-to-store copy (:mod:`h5py`), also read-only on
the source.
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
    "export_subset_tether",
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
    created_utc: str | None = None,
) -> Path:
    """Write a ``<data_path>.provenance.json`` stamp beside an export.

    Records the three-part NFR-REPRO provenance — ``app_version`` (git-derived) +
    ``created_utc`` (offset-aware ISO-8601 UTC) + the export ``parameters`` — plus
    the export kind and source project, so an exported file traces back to the build
    and project that produced it. ``created_utc`` defaults to *now*; a caller that also
    stamps the timestamp elsewhere (e.g. into a subset ``.tether``'s root attributes)
    passes its own so the sidecar and the in-file stamp agree exactly. Returns the
    sidecar path.
    """
    data_path = Path(data_path)
    sidecar = data_path.with_name(data_path.name + ".provenance.json")
    payload = {
        "tether_export": tether_export,
        "app_version": _app_version(),
        "created_utc": datetime.now(UTC).isoformat() if created_utc is None else created_utc,
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
    frames, frame range, curation label, category, ML ``quality_class``, tags) plus a
    derived apparent-E summary — mean/median of ``A / (D + A)`` over the molecule's
    analysis window (falling back to its frame range) and the finite-frame count. The
    ``window_start``/``window_end`` columns report the **resolved** window actually
    used for that summary (i.e. the frame-range fallback when ``analysis_window`` is
    unset), so the reported window always matches the range the E stats cover.
    Apparent E is used for the summary because it is always well-defined; the raw α/γ
    factors are emitted as their own columns so a consumer can recompute the corrected
    E. A ``<file>.provenance.json`` sidecar is written beside the CSV.

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
                    lo,
                    hi,
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


# --------------------------------------------------------------------------- #
# Subset ``.tether`` export (PRD §7.9, §5.3, §5.4)
# --------------------------------------------------------------------------- #

#: ``/traces`` layers always embedded in a subset — the corrected (apparent-E
#: substrate) donor/acceptor arrays.
_SUBSET_CORRECTED_LAYERS: tuple[str, ...] = ("donor_corrected", "acceptor_corrected")

#: ``/traces`` layers embedded **only** when raw is included. Background rides with
#: raw, not corrected: ``corrected = raw − background`` exactly (the aperture
#: top-hat, :mod:`tether.imaging.aperture`), so keeping background alongside
#: corrected would let raw be reconstructed as ``corrected + background`` — omitting
#: raw must therefore omit background too, or the §5.4 "raw is not reconstructable
#: there" invariant is silently violated.
_SUBSET_RAW_LAYERS: tuple[str, ...] = (
    "donor_raw",
    "acceptor_raw",
    "donor_background",
    "acceptor_background",
)

#: ``/idealization/{model}`` datasets that are **row-aligned with the model's
#: molecules** and so must be filtered to the exported subset (mirrors the writer,
#: :func:`tether.project.idealize.write_idealization_model`). Every other model
#: member (``mean``/``var``/``tmatrix``/``norm_tmatrix``/``rates``/``pi``/``frac``/
#: the ``priors`` group) is a **global** consensus-model array, copied verbatim.
_IDEALIZATION_PER_MOLECULE: frozenset[str] = frozenset(
    {"idealized", "state_path", "molecule_key", "molecule_id", "input_hash"}
)

#: Metadata groups copied **verbatim** into a subset (small; they preserve
#: provenance and the ``/molecules → /conditions`` referential integrity). ``/movies``
#: and ``/features`` are deliberately **not** here: the subset is definitionally
#: movie-less (no ``/movies`` rows), and per-molecule ML ``/features`` are outside
#: the §7.9 subset embed set (patches/coordinates/corrected/idealization/provenance).
_SUBSET_VERBATIM_GROUPS: tuple[str, ...] = ("conditions", "settings", "calibration", "models")


def _selected_keys(molecules: np.ndarray, rows: list[int]) -> set[str]:
    """The set of ``molecule_key`` values carried by the selected store rows."""
    return {_to_str(molecules["molecule_key"][i]) for i in rows}


def _selected_ids(molecules: np.ndarray, rows: list[int]) -> set[str]:
    """The set of **unique** ``molecule_id`` values of the selected store rows.

    Idealization is joined per-molecule on ``molecule_id`` (the stable UUID), **not**
    on the non-unique ``molecule_key`` (§7.10 — two distinct rows can quantize to the
    same key): that is the identity :func:`tether.project.idealize.stale_molecule_keys`
    itself joins on. Filtering a model's rows by ``molecule_key`` instead would, when a
    key is duplicated and the selection splits it (e.g. one namesake dropped as
    ``REJECT``), copy the un-exported row's idealization as an orphan whose
    ``molecule_id`` has no ``/molecules`` row — which the staleness recompute then reads
    as stale, silently dropping the *exported* molecule's live idealization.
    """
    return {_to_str(molecules["molecule_id"][i]) for i in rows}


def _copy_molecule_rows(src: object, dst: object, rows: list[int]) -> None:
    """Append the selected ``/molecules`` rows into the fresh subset (store order)."""
    from tether.io.schema import TABLE  # noqa: PLC0415

    selected = src["molecules"][TABLE][()][rows]
    table = dst["molecules"][TABLE]
    table.resize((selected.shape[0],))
    table[:] = selected


def _copy_trace_layers(src: object, dst: object, rows: list[int], include_raw: bool) -> list[str]:
    """Copy the corrected (and, if ``include_raw``, raw+background) ``/traces`` layers,
    row-subset to the selected molecules, preserving chunked+gzip float storage.

    Returns the layer names actually written. A layer absent from the source (e.g. a
    store that never had raw) is skipped rather than fabricated.
    """
    layers = list(_SUBSET_CORRECTED_LAYERS)
    if include_raw:
        layers += list(_SUBSET_RAW_LAYERS)
    src_traces = src["traces"]
    dst_traces = dst["traces"]
    written: list[str] = []
    for layer in layers:
        if layer not in src_traces:
            continue
        ds = src_traces[layer]
        block = ds[()][rows]  # read (N, T) then row-subset (rows is strictly increasing)
        dst_traces.create_dataset(
            layer,
            data=block,
            dtype=ds.dtype,
            chunks=True,
            compression="gzip",
            maxshape=(None, None),
        )
        written.append(layer)
    return written


def _copy_patches(src: object, dst: object, rows: list[int]) -> None:
    """Copy both ``/patches`` channel stacks, row-subset to the selected molecules.

    Patches are always embedded — they are what makes a movie-less subset curatable
    and drives the static overlap view (§5.1).
    """
    src_patches = src["patches"]
    dst_patches = dst["patches"]
    for channel in src_patches:
        ds = src_patches[channel]
        block = ds[()][rows]  # (N, w, w) -> (n_sel, w, w)
        dst_patches.create_dataset(
            channel,
            data=block,
            dtype=ds.dtype,
            chunks=True,
            compression="gzip",
            maxshape=(None,) + ds.shape[1:],
        )


def _copy_idealization(src: object, dst: object, selected_ids: set[str]) -> list[str]:
    """Copy each ``/idealization`` model, filtering its per-molecule rows to the subset.

    Global model arrays (levels/transition matrix/rates/priors) are copied verbatim;
    the row-aligned per-molecule datasets (:data:`_IDEALIZATION_PER_MOLECULE`) are
    subset to the exported molecules **by ``molecule_id``** (the unique per-row identity
    the staleness join uses — see :func:`_selected_ids`), never by the non-unique
    ``molecule_key``. A model with **no** exported molecule is skipped entirely (it would
    otherwise be an empty, meaningless model). Returns the model names actually written.
    """
    src_ideal = src["idealization"]
    dst_ideal = dst["idealization"]
    written: list[str] = []
    for name in src_ideal:
        group = src_ideal[name]
        model_ids = [_to_str(x) for x in group["molecule_id"][()]]
        keep = [j for j, mid in enumerate(model_ids) if mid in selected_ids]
        if not keep:
            continue  # none of this model's molecules are in the subset
        dst_group = dst_ideal.create_group(name)
        for attr_key, attr_val in group.attrs.items():
            dst_group.attrs[attr_key] = attr_val
        dst_group.attrs["n_molecules"] = len(keep)
        for member in group:
            item = group[member]
            if member in _IDEALIZATION_PER_MOLECULE:
                subset = item[()][keep]  # keep is strictly increasing
                create_kw = {"compression": "gzip"} if member in ("idealized", "state_path") else {}
                dst_group.create_dataset(member, data=subset, dtype=item.dtype, **create_kw)
            else:
                # A global model array (or the priors group) — copy verbatim, attrs
                # and all. Any future model member not in the per-molecule set is
                # preserved rather than silently dropped.
                src.copy(item, dst_group, name=member)
        written.append(name)
    return written


def _copy_labels(src: object, dst: object, selected_keys: set[str]) -> int:
    """Copy the ``/labels`` provenance rows whose molecule is in the subset.

    ``molecule_key`` travels so a labeled subset row always resolves to its canonical
    molecule on merge-back (§5.1/§7.10). Returns the number of label rows copied.
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    labels = src["labels"][TABLE][()]
    if labels.shape[0] == 0:
        return 0
    mask = np.array([_to_str(k) in selected_keys for k in labels["molecule_key"]], dtype=bool)
    kept = labels[mask]
    if kept.shape[0] == 0:
        return 0
    table = dst["labels"][TABLE]
    table.resize((kept.shape[0],))
    table[:] = kept
    return int(kept.shape[0])


def _copy_verbatim_groups(src: object, dst: object) -> None:
    """Replace the fresh subset's empty metadata groups with the source's verbatim.

    Preserves ``/conditions`` (+ its per-condition category lists and re-key audit),
    ``/settings`` (extraction provenance), ``/calibration``, and ``/models`` intact —
    small metadata that keeps the subset self-describing and referentially complete.
    """
    for grp in _SUBSET_VERBATIM_GROUPS:
        if grp not in src:
            continue
        if grp in dst:
            del dst[grp]
        src.copy(grp, dst, name=grp)


def export_subset_tether(
    project: Project | str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    molecule_keys: list[str] | None = None,
    include_rejected: bool = False,
    include_raw: bool = False,
    overwrite: bool = False,
) -> ExportResult:
    """Export a **movie-less subset ``.tether``** (PRD §7.9, §5.3, §5.4).

    Builds a self-contained new ``.tether`` embedding, for the selected molecules:
    their ``/molecules`` rows (coordinates + identity + all scalar fields), the two
    ``/patches`` channel stacks, the corrected ``/traces`` layers, every
    ``/idealization`` model (filtered to the exported molecules), the ``/labels``
    provenance rows, and the ``/conditions``/``/settings``/``/calibration``/``/models``
    metadata verbatim. The source **raw** traces are embedded only when
    ``include_raw=True``.

    Movie-less & raw-reconstructability (§5.4). The subset carries **no** ``/movies``
    rows, so it is definitionally movie-less — there is no source movie to resolve or
    relink (the movie file is not part of the export; per-molecule origin provenance
    still travels via ``molecule_key``, ``source_filename``, and the coordinates).
    Because ``corrected = raw − background`` exactly, ``include_raw`` controls the raw
    **and** background layers together: with it ``False`` the subset holds only
    corrected traces and raw is genuinely **not reconstructable** from the file (no
    movie, no raw, no background to back it out), satisfying the §5.4 invariant; with
    it ``True`` the full six-layer trace record travels.

    Selection mirrors :func:`export_deeplasi_txt`: molecules are taken in **store
    order**; the §7.5 curation filter drops ``REJECT`` unless ``include_rejected``; an
    optional ``molecule_keys`` list subselects by ``molecule_key`` (a duplicate key,
    §7.10, matches every store row that carries it).

    Schema freeze. The subset is written through
    :func:`tether.io.schema.create_project` and populated with **additive data only**;
    it adds no structure to the M0-frozen skeleton and never mutates the source
    (``schema-guard`` neutral). A ``<out_path>.provenance.json`` sidecar is written and
    the same provenance is stamped into the subset's root attributes.

    Parameters
    ----------
    project:
        A :class:`~tether.project.core.Project` or a path to the source ``.tether``.
    out_path:
        Destination ``.tether`` path (must differ from the source).
    molecule_keys:
        Molecules to export (``None`` = every extracted molecule in store order).
    include_rejected:
        Keep ``REJECT`` molecules (default drops them — the curated subset).
    include_raw:
        Embed the raw + background ``/traces`` layers (default omits both, keeping raw
        non-reconstructable).
    overwrite:
        Truncate an existing ``out_path`` (default refuses to clobber).

    Returns
    -------
    ExportResult
        The subset path, its provenance sidecar, and the molecule count exported.

    Raises
    ------
    KeyError
        If a requested ``molecule_key`` matches no store row (a typo — fail loudly).
    ValueError
        If the source is not a valid ``.tether``; the selection is empty (nothing to
        export); or ``out_path`` resolves to the source path.
    """
    import h5py  # noqa: PLC0415

    from tether.io.schema import assert_is_compatible_project, create_project  # noqa: PLC0415

    path = _project_path(project)
    assert_is_compatible_project(path)
    out_path = Path(out_path)
    if out_path.resolve() == path.resolve():
        raise ValueError(f"subset out_path {out_path} is the source project; refusing to overwrite")

    molecules = read_molecules(path)
    rows = _selected_rows(molecules, molecule_keys, include_rejected)
    if not rows:
        raise ValueError(
            "no molecules selected to export (empty selection, or every match rejected "
            "with include_rejected=False)"
        )
    selected_keys = _selected_keys(molecules, rows)
    selected_ids = _selected_ids(molecules, rows)

    created_utc = datetime.now(UTC).isoformat()
    create_project(out_path, overwrite=overwrite)
    with h5py.File(path, "r") as src, h5py.File(out_path, "r+") as dst:
        _copy_molecule_rows(src, dst, rows)
        layers = _copy_trace_layers(src, dst, rows, include_raw)
        _copy_patches(src, dst, rows)
        # Idealization joins per-molecule on molecule_id (unique); labels join on
        # molecule_key (the cross-file merge-back key, §5.1/§7.10) — deliberately different.
        models = _copy_idealization(src, dst, selected_ids)
        n_labels = _copy_labels(src, dst, selected_keys)
        _copy_verbatim_groups(src, dst)
        # Embedded provenance (§7.9): stamp the subset's own root attributes so the
        # file is self-describing even detached from its sidecar.
        dst.attrs["tether_subset_of"] = path.name
        dst.attrs["tether_subset_created_utc"] = created_utc
        dst.attrs["tether_subset_include_raw"] = int(bool(include_raw))
        dst.attrs["tether_subset_n_molecules"] = int(len(rows))

    provenance = write_provenance_sidecar(
        out_path,
        tether_export="subset-tether",
        source=path.name,
        created_utc=created_utc,
        parameters={
            "include_raw": include_raw,
            "include_rejected": include_rejected,
            "n_molecules": len(rows),
            "n_trace_layers": len(layers),
            "trace_layers": layers,
            "n_idealization_models": len(models),
            "idealization_models": models,
            "n_label_rows": n_labels,
            "molecule_keys": None if molecule_keys is None else [str(k) for k in molecule_keys],
        },
    )
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=len(rows))
