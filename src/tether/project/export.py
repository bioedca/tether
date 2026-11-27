# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exports from a ``.tether`` project (PRD §7.9 FR-EXPORT).

Three provenance-stamped exports that read a project store and produce artifacts a
downstream tool or a portable re-analysis can consume:

* [`export_deeplasi_txt`][] — the Deep-LASI ``…-donc-accc-w.txt``
  corrected-trace matrix (a ``T × 2N`` table, one interleaved ``(donor, acceptor)``
  column pair per molecule; the write-side companion to
  [`tether.io.deeplasi.read_deeplasi_txt`][]), trimmed to the molecules' shared
  native frame extent so a round-trip preserves the traces to the 5-decimal text
  rounding.
* [`export_molecule_table_csv`][] — one CSV row per molecule carrying the
  ``/molecules`` scalar fields (identity, correction factors, photobleach frames,
  curation state, windows) plus the derived per-molecule **apparent-E** summary
  (mean/median over the analysis window, and the finite-frame count).
* [`export_subset_tether`][] — a **movie-less subset ``.tether``** (§7.9, §5.4):
  a self-contained new project embedding the selected molecules' coordinates,
  patches, corrected traces, and idealization models, with the source **raw**
  traces optional. It carries **no** ``/movies`` rows (definitionally movie-less)
  so it never opens a source movie, and — because ``corrected = raw − background``
  exactly — omitting raw also omits the per-frame background, so raw is genuinely
  **not reconstructable** from the subset (the §5.4 invariant). Unlike the two
  table exports it *writes a new store*, but it never mutates the source and adds
  no structure to the M0-frozen skeleton (it builds the subset via
  [`tether.io.schema.create_project`][] and writes only additive data).

Every export writes a companion ``<file>.provenance.json`` sidecar stamping the
Tether app version, a UTC timestamp, the source project, and the export
parameters (§8 NFR-REPRO — "all exports are stamped with provenance and
parameters"); the subset ``.tether`` additionally stamps that provenance into its
own root attributes (its embedded provenance, §7.9). Flat text has no metadata
slot, and the ``.txt`` must stay Deep-LASI-faithful (no header lines), so its
stamp travels in the sidecar rather than inline.

The table exports are the orchestration layer: they read the store, derive E, and
feed pure serializers ([`tether.io.deeplasi.write_deeplasi_txt`][], the stdlib
`csv`) — mirroring how [`tether.project.handoff`][] drives the SMD
[`tether.idealize.write_smd`][] primitive. They are read-only on the store. The
subset export is a filtered store-to-store copy (`h5py`), also read-only on
the source.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

import numpy as np

from tether.fret.efficiency import apparent_fret
from tether.imaging.extract import read_molecules, read_traces
from tether.io.deeplasi import write_deeplasi_txt
from tether.project.core import Project
from tether.project.labels import CurationLabel
from tether.project.trace_layers import INTENSITY_QUANTITY_LAYERS

__all__ = [
    "USER_NAME_FIELDS",
    "ExportResult",
    "export_deeplasi_txt",
    "export_molecule_table_csv",
    "export_subset_tether",
    "refuse_source_destination",
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
    """Resolve a project reference (an open [`Project`][] or a path) to its path."""
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
        [`tether.project.handoff`][] selection).
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
    [`tether.io.deeplasi.write_deeplasi_txt`][], plus a provenance sidecar.

    Raises
    ------
    KeyError
        If a requested ``molecule_key`` matches no store row (a typo — fail loudly).
    ValueError
        If ``intensity_quantity`` is unknown; the selection is empty (nothing to
        write — [`read_deeplasi_txt`][tether.io.deeplasi.read_deeplasi_txt] rejects a zero-column
        table); or the
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

    Columns are `MOLECULE_TABLE_COLUMNS`. Each row carries the stored
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
#: top-hat, `tether.imaging.aperture`), so keeping background alongside
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
#: `tether.project.idealize.write_idealization_model`). Every other model
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
    same key): that is the identity [`tether.project.idealize.stale_molecule_keys`][]
    itself joins on. Filtering a model's rows by ``molecule_key`` instead would, when a
    key is duplicated and the selection splits it (e.g. one namesake dropped as
    ``REJECT``), copy the un-exported row's idealization as an orphan whose
    ``molecule_id`` has no ``/molecules`` row — which the staleness recompute then reads
    as stale, silently dropping the *exported* molecule's live idealization.
    """
    return {_to_str(molecules["molecule_id"][i]) for i in rows}


def _copy_molecule_rows(src: object, dst: object, rows: list[int]) -> None:
    """Append the selected ``/molecules`` rows into the fresh subset (store order).

    ``rows`` is strictly increasing (`_selected_rows`), so h5py reads only the
    selected rows directly — it never materializes the whole source table.
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    selected = src["molecules"][TABLE][rows]
    table = dst["molecules"][TABLE]
    table.resize((selected.shape[0],))
    table[:] = selected
    _copy_acceptor_detected(src, dst, rows)


def _copy_acceptor_detected(src: object, dst: object, rows: list[int]) -> None:
    """Carry the per-molecule acceptor-detection flag into the subset (#920, ADR-0127).

    The flag follows the rule of
    [`read_acceptor_detected`][tether.imaging.extract.read_acceptor_detected]: rows past
    the end of a shorter source column were never recorded. It is written only when the
    source has the column, so a subset of a project extracted before #920 gains no
    column, as its source has none.
    """
    from tether.imaging.extract import ACCEPTOR_DETECTED_NOT_RECORDED  # noqa: PLC0415

    flags = src["molecules"].get("acceptor_detected")
    if flags is None:
        return
    column = np.full(len(rows), ACCEPTOR_DETECTED_NOT_RECORDED, dtype=np.int8)
    n_flags = int(flags.shape[0])
    stored = [i for i, row in enumerate(rows) if row < n_flags]
    if stored:
        column[stored] = flags[[rows[i] for i in stored]]
    dst["molecules"].create_dataset(
        "acceptor_detected",
        data=column,
        maxshape=(None,),
        chunks=(4096,),
        fillvalue=ACCEPTOR_DETECTED_NOT_RECORDED,
    )


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
        block = ds[rows]  # h5py reads only the selected rows (rows is strictly increasing)
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
        block = ds[rows]  # (N, w, w) -> (n_sel, w, w); reads only the selected rows
        dst_patches.create_dataset(
            channel,
            data=block,
            dtype=ds.dtype,
            chunks=True,
            compression="gzip",
            maxshape=(None,) + ds.shape[1:],
        )


def _copy_idealization(
    src: object, dst: object, selected_ids: set[str], available_layers: set[str]
) -> tuple[list[str], list[str]]:
    """Copy each ``/idealization`` model, filtering its per-molecule rows to the subset.

    Global model arrays (levels/transition matrix/rates/priors) are copied verbatim;
    the row-aligned per-molecule datasets (`_IDEALIZATION_PER_MOLECULE`) are
    subset to the exported molecules **by ``molecule_id``** (the unique per-row identity
    the staleness join uses — see `_selected_ids`), never by the non-unique
    ``molecule_key``.

    A model is **skipped** (and recorded) when either it covers no exported molecule (it
    would be an empty, meaningless model) or the ``/traces`` layers its
    ``intensity_quantity`` was fit over are not in ``available_layers`` — e.g. a
    ``raw``-fit model when ``include_raw=False``: without its input layers the subset
    could never re-stale it, so it would read back permanently stale, breaking the
    live/stale contract (§5.1). Returns ``(written, skipped)`` model names.
    """
    src_ideal = src["idealization"]
    dst_ideal = dst["idealization"]
    written: list[str] = []
    skipped: list[str] = []
    for name in src_ideal:
        group = src_ideal[name]
        quantity = _to_str(group.attrs.get("intensity_quantity", "corrected"))
        required = INTENSITY_QUANTITY_LAYERS.get(quantity)
        if required is not None and not set(required) <= available_layers:
            skipped.append(name)  # its input trace layers aren't in the subset
            continue
        model_ids = [_to_str(x) for x in group["molecule_id"][()]]
        keep = [j for j, mid in enumerate(model_ids) if mid in selected_ids]
        if not keep:
            skipped.append(name)  # none of this model's molecules are in the subset
            continue
        dst_group = dst_ideal.create_group(name)
        for attr_key, attr_val in group.attrs.items():
            dst_group.attrs[attr_key] = attr_val
        dst_group.attrs["n_molecules"] = len(keep)
        for member in group:
            item = group[member]
            if member in _IDEALIZATION_PER_MOLECULE:
                subset = item[keep]  # keep is strictly increasing (direct-row read)
                create_kw = {"compression": "gzip"} if member in ("idealized", "state_path") else {}
                dst_group.create_dataset(member, data=subset, dtype=item.dtype, **create_kw)
            else:
                # A global model array (or the priors group) — copy verbatim, attrs
                # and all. Any future model member not in the per-molecule set is
                # preserved rather than silently dropped.
                src.copy(item, dst_group, name=member)
        written.append(name)
    return written, skipped


#: Compound-dataset fields that always store a **person's name** (#620). The curator who
#: labeled a molecule (``/labels`` ``labeler``), who re-keyed or merged a condition
#: (``/settings/condition_audit`` ``labeler``), and who took over or lost a project lock
#: (``/settings/lock_takeovers`` ``holder_user`` and ``ousted_user``, #626) are the stores
#: today. A correction-transition ``actor`` holds a name only when ``by`` is ``person``.
USER_NAME_FIELDS: frozenset[str] = frozenset({"labeler", "holder_user", "ousted_user"})

#: Attributes that store a **full file path** (#620). A path can name the user (a home
#: folder), so with ``curator_alias`` the copy keeps only the file name. Today this is the
#: model file of each ``/models/<condition_id>`` reference (ADR-0112); an attribute added
#: later that stores a path must be added here, and
#: ``tests/test_export_subset_aliases.py`` fails until it is.
PATH_ATTRS: frozenset[str] = frozenset({"artifact_path"})

#: Compound-dataset fields that store a **JSON object** with a person's name under one
#: key (#620), as ``{field: key}``. Today this is the ``owner`` of each
#: ``/settings/jobs`` row, the writer that last ran the job, whose ``user`` is the OS
#: login (ADR-0126). With ``curator_alias`` only that key is aliased, so the record keeps
#: its shape; a JSON field added later that records a person must be added here, and
#: ``tests/test_export_subset_aliases.py`` fails until it is.
JSON_USER_KEYS: dict[str, str] = {"owner": "user"}


def _name_fields(dtype: np.dtype) -> list[str]:
    """The fields in ``dtype`` that can store a user name."""
    fields = dtype.names or ()
    return [
        field
        for field in fields
        if field in USER_NAME_FIELDS or (field == "actor" and "by" in fields)
    ]


def _stores_user_name(row: np.void, field: str) -> bool:
    """Whether this name field holds a person identity in ``row``."""
    return field != "actor" or _to_str(row["by"]) == "person"


def _json_name_fields(dtype: np.dtype) -> list[str]:
    """The [`JSON_USER_KEYS`][] fields a compound ``dtype`` carries (``[]`` if none)."""
    return [name for name in (dtype.names or ()) if name in JSON_USER_KEYS]


def _has_names(dtype: np.dtype) -> bool:
    """Whether a compound ``dtype`` stores a user name, plainly or inside JSON."""
    return bool(_name_fields(dtype) or _json_name_fields(dtype))


def _json_object(text: str) -> dict[str, object] | None:
    """``text`` parsed as a JSON object, or ``None`` (empty, not JSON, or not an object)."""
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _row_names(rows: np.ndarray) -> list[str]:
    """Every user name in compound ``rows``, row by row in field order."""
    names: list[str] = []
    fields = _name_fields(rows.dtype)
    json_fields = _json_name_fields(rows.dtype)
    for row in rows:
        names += [_to_str(row[field]) for field in fields if _stores_user_name(row, field)]
        for field in json_fields:
            obj = _json_object(_to_str(row[field]))
            if obj is not None and isinstance(obj.get(JSON_USER_KEYS[field]), str):
                names.append(obj[JSON_USER_KEYS[field]])  # type: ignore[arg-type]
    return names


class _CuratorAliases:
    """Map each distinct source user name to a stable ``<prefix>-<n>`` alias (#620).

    The names are numbered in the order they are first met, so two labels by one
    person keep one alias. An alias that would **contain** a source name is skipped
    (re-sharing a copy whose names are ``curator-1`` … must not write ``curator-10``,
    whose bytes hold ``curator-1``), so no source name is in the copy's bytes. ``""``
    (no name recorded) stays ``""``: it names nobody, and an alias would invent a
    curator.

    Raises
    ------
    ValueError
        If ``<prefix>-`` itself contains a source name, or no alias free of every
        source name is found within a bounded search; another prefix is needed.
    """

    def __init__(self, prefix: str, names: list[str]) -> None:
        self._map: dict[str, str] = {}
        taken = {name for name in names if name}
        if any(name in f"{prefix}-" for name in taken):
            raise ValueError(
                "curator_alias contains a user name stored in the source; choose another prefix"
            )
        limit = 10 * (len(taken) + 1) + 1000
        n = 0
        for name in names:
            if name == "" or name in self._map:
                continue
            n += 1
            while any(other in f"{prefix}-{n}" for other in taken):
                n += 1
                if n > limit:
                    raise ValueError(
                        "cannot make aliases that contain no user name of the source; "
                        "choose another curator_alias"
                    )
            self._map[name] = f"{prefix}-{n}"

    @property
    def count(self) -> int:
        """The number of distinct names replaced."""
        return len(self._map)

    def apply(self, rows: np.ndarray) -> np.ndarray:
        """Return a copy of compound ``rows`` with every name field aliased.

        A [`JSON_USER_KEYS`][] field keeps its object and only the name under its key
        changes (written back with sorted keys, as the store writes it).
        """
        out = rows.copy()
        for field in _name_fields(rows.dtype):
            out[field] = [
                self._map.get(_to_str(row[field]), _to_str(row[field]))
                if _stores_user_name(row, field)
                else row[field]
                for row in rows
            ]
        for field in _json_name_fields(rows.dtype):
            out[field] = [self._json(_to_str(v), JSON_USER_KEYS[field]) for v in rows[field]]
        return out

    def _json(self, text: str, key: str) -> str:
        obj = _json_object(text)
        if obj is None or not isinstance(obj.get(key), str):
            return text
        obj[key] = self._map.get(obj[key], obj[key])  # type: ignore[arg-type]
        return json.dumps(obj, sort_keys=True)


def _iter_name_datasets(group: object) -> list[object]:
    """Every compound dataset under ``group`` that carries a name field."""
    import h5py  # noqa: PLC0415

    found: list[object] = []

    def visit(_name: str, obj: object) -> None:
        if isinstance(obj, h5py.Dataset) and _has_names(obj.dtype):
            found.append(obj)

    group.visititems(visit)  # type: ignore[attr-defined]
    return found


def _copied_attr(key: str, value: object, aliases: _CuratorAliases | None) -> object:
    """The value the copy stores for attribute ``key``: with ``aliases``, a path in
    [`PATH_ATTRS`][] becomes its file name (Windows or POSIX separators)."""
    if aliases is None or key not in PATH_ATTRS:
        return value
    return PureWindowsPath(_to_str(value)).name


def _source_user_names(
    src: object, label_rows: np.ndarray, reject_rows: np.ndarray | None
) -> list[str]:
    """Every user name the copy would carry, in the order the copy writes them:
    the exported label and reject-log rows first, then the verbatim metadata groups."""
    names = _row_names(label_rows)
    if reject_rows is not None:
        names += _row_names(reject_rows)
    for grp in _SUBSET_VERBATIM_GROUPS:
        if grp not in src:  # type: ignore[operator]
            continue
        for ds in _iter_name_datasets(src[grp]):  # type: ignore[index]
            names += _row_names(ds[()])
    return names


def _copy_group_aliased(src_group: object, dst_parent: object, aliases: _CuratorAliases) -> None:
    """Copy ``src_group`` under ``dst_parent``, writing name-bearing datasets aliased.

    A name-bearing dataset is built from its aliased rows (never copied and then
    overwritten, because HDF5 does not reclaim the old string bytes), and a path in
    [`PATH_ATTRS`][] is written as its file name only; everything else is copied
    verbatim with [`h5py.Group.copy`][].
    """
    import h5py  # noqa: PLC0415

    name = src_group.name.rsplit("/", 1)[-1]  # type: ignore[attr-defined]
    dst_group = dst_parent.create_group(name)  # type: ignore[attr-defined]
    for key, val in src_group.attrs.items():  # type: ignore[attr-defined]
        dst_group.attrs[key] = _copied_attr(key, val, aliases)
    for member in src_group:  # type: ignore[attr-defined]
        item = src_group[member]  # type: ignore[index]
        if isinstance(item, h5py.Group):
            _copy_group_aliased(item, dst_group, aliases)
        elif isinstance(item, h5py.Dataset) and (
            _has_names(item.dtype) or PATH_ATTRS.intersection(item.attrs)
        ):
            data = item[()]
            ds = dst_group.create_dataset(
                member,
                data=aliases.apply(data) if _has_names(item.dtype) else data,
                dtype=item.dtype,
                maxshape=item.maxshape,
                chunks=item.chunks,
                compression=item.compression,
            )
            for key, val in item.attrs.items():
                ds.attrs[key] = _copied_attr(key, val, aliases)
        else:
            src_group.file.copy(item, dst_group, name=member)  # type: ignore[attr-defined]


def _subset_label_rows(rows: np.ndarray, selected_keys: set[str]) -> tuple[np.ndarray, np.ndarray]:
    """The source positions and rows of selected labels or reasons in store order."""
    mask = np.array([_to_str(k) in selected_keys for k in rows["molecule_key"]], dtype=bool)
    return np.flatnonzero(mask), rows[mask]


def _copy_labels(dst: object, kept: np.ndarray, aliases: _CuratorAliases | None) -> int:
    """Write the subset's selected ``/labels`` provenance rows.

    ``molecule_key`` travels so a labeled subset row always resolves to its canonical
    molecule on merge-back (§5.1/§7.10). With ``aliases`` the ``labeler`` names are
    replaced before the write. Returns the number of label rows copied.
    """
    from tether.io.schema import TABLE  # noqa: PLC0415

    if kept.shape[0] == 0:
        return 0
    if aliases is not None:
        kept = aliases.apply(kept)
    table = dst["labels"][TABLE]  # type: ignore[index]
    table.resize((kept.shape[0],))
    table[:] = kept
    return int(kept.shape[0])


def _kept_reject_log(src: object, selected_keys: set[str]) -> np.ndarray | None:
    """Read selected reject-log rows, or return ``None`` when the source has no log."""
    from tether.project.labels import REJECT_LOG  # noqa: PLC0415

    log = src["labels"].get(REJECT_LOG)  # type: ignore[index]
    return None if log is None else _subset_label_rows(log[()], selected_keys)[1]


def _copy_reject_log(dst: object, kept: np.ndarray | None, aliases: _CuratorAliases | None) -> int:
    """Write the subset's selected ``/labels/reject_log`` rows (#1051).

    The reason of each reject travels with its ``/labels`` row, so
    [`tether.project.labels.reject_record_of`][] reads the same reason in the subset as in
    the source. The log is created lazily, as [`tether.project.labels.reject`][] does: a
    source without one, or a selection none of whose molecules has a row, leaves the subset
    without the dataset. When aliasing, the log uses the same labeler names as the
    labels table. Returns the number of rows copied.
    """
    from tether.project.labels import REJECT_LOG  # noqa: PLC0415

    if kept is None or kept.shape[0] == 0:
        return 0
    if aliases is not None:
        kept = aliases.apply(kept)
    dst["labels"].create_dataset(  # type: ignore[index]
        REJECT_LOG, data=kept, dtype=kept.dtype, maxshape=(None,)
    )
    return int(kept.shape[0])


def _copy_label_reviews(
    src: object, dst: object, selected_ids: set[str], kept_indices: np.ndarray
) -> None:
    """Carry review state with the label indexes of the copied rows."""
    from tether.io import generations  # noqa: PLC0415

    event_indices = {old: new for new, old in enumerate(kept_indices)}
    review_events = {
        mid: event_indices.get(event, generations.PENDING_LABEL_REVIEW)
        for mid, event in generations.label_reviews_in(src).items()
        if mid in selected_ids
    }
    generations.append_label_reviews(dst, review_events)


def _copy_verbatim_groups(src: object, dst: object, aliases: _CuratorAliases | None) -> None:
    """Replace the fresh subset's empty metadata groups with the source's verbatim.

    Preserves ``/conditions`` (+ its per-condition category lists and re-key audit),
    ``/settings`` (extraction provenance), ``/calibration``, and ``/models`` intact —
    small metadata that keeps the subset self-describing and referentially complete.
    With ``aliases`` the copy replaces the user names in those metadata groups.
    """
    for grp in _SUBSET_VERBATIM_GROUPS:
        if grp not in src:
            continue
        if grp in dst:
            del dst[grp]
        if aliases is None:
            src.copy(grp, dst, name=grp)
        else:
            _copy_group_aliased(src[grp], dst, aliases)


def _copy_sources(
    src: object, dst: object, aliases: _CuratorAliases | None, src_path: Path, dst_path: Path
) -> int:
    """Copy the source registry (#912) into the subset; return the number of records.

    Each record of the source is kept, including one made from a ``/movies`` row the subset
    does not carry, so the subset still says which files its molecules came from. A copy
    for sharing (``aliases`` set) keeps only the file name of a path outside the project's
    folder ([`shareable_path`][tether.project.sources.shareable_path]), as for
    ``PATH_ATTRS``. Otherwise each relative path is read against the source project's folder
    and stored relative to the subset's, so it still names the same file. A movie record
    with no checksum of its own takes the checksum of its ``/movies`` row.
    """
    from dataclasses import replace  # noqa: PLC0415

    from tether.project.sources import (  # noqa: PLC0415
        _records_in,  # noqa: PLC2701
        _row,  # noqa: PLC2701
        _stored_path,  # noqa: PLC2701
        _text,  # noqa: PLC2701
        shareable_path,
    )

    records = _records_in(src, src_path)
    # The subset carries no ``/movies`` rows, so a movie record with no checksum of its own
    # takes the extraction's with it: otherwise the subset could not link the file again.
    known = {}
    movies = src.get("movies")
    for row in movies["table"][()] if movies is not None else ():
        known[_text(row["movie_id"])] = _text(row["sha256"]).lower()
    records = [
        replace(r, sha256=known.get(r.movie_id, ""))
        if r.role == "movie" and r.movie_id and not r.sha256
        else r
        for r in records
    ]
    if aliases is not None:
        records = [replace(r, relative_path=shareable_path(r.relative_path)) for r in records]
    else:
        records = [
            replace(r, relative_path=_stored_path(dst_path, r.resolve(src_path))) for r in records
        ]
    if records:
        table = dst["sources"]["table"]
        table.resize((len(records),))
        for i, record in enumerate(records):
            table[i] = _row(record)[0]
    return len(records)


def _preflight_source(src: object, n_molecules: int, include_raw: bool) -> None:
    """Reject a malformed/incomplete source **before** any output is written.

    A subset promises the corrected traces + both patch channels for every exported
    molecule; validate they exist and are row-aligned with ``/molecules`` up front so a
    corrupt store fails loudly (and, with staging, without touching an existing output)
    rather than silently producing a valid-looking subset missing promised data. When
    ``include_raw`` is set the raw+background layers must be **all present** (they are
    embedded and dropped as a set — §5.4).

    Raises
    ------
    ValueError
        If a required corrected layer or patch channel is missing, a molecule-aligned
        array's row count disagrees with ``/molecules``, or ``include_raw`` is set but
        the source lacks a raw/background layer.
    """
    traces = src["traces"]
    patches = src["patches"]
    for layer in _SUBSET_CORRECTED_LAYERS:
        if layer not in traces:
            raise ValueError(
                f"source /traces is missing the required corrected layer {layer!r}; "
                "cannot export a subset .tether"
            )
        if traces[layer].shape[0] != n_molecules:
            raise ValueError(
                f"source /traces/{layer} has {traces[layer].shape[0]} rows != "
                f"{n_molecules} molecules (corrupt store)"
            )
    if include_raw:
        for layer in _SUBSET_RAW_LAYERS:
            if layer not in traces:
                raise ValueError(
                    f"include_raw=True but source /traces is missing {layer!r}; the "
                    "raw + background layers are embedded as a set"
                )
    for channel in ("donor", "acceptor"):
        if channel not in patches:
            raise ValueError(
                f"source /patches is missing the {channel!r} channel; cannot export a "
                "curatable movie-less subset"
            )
        if patches[channel].shape[0] != n_molecules:
            raise ValueError(
                f"source /patches/{channel} has {patches[channel].shape[0]} rows != "
                f"{n_molecules} molecules (corrupt store)"
            )


def refuse_source_destination(
    project: Project | str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    what: str = "subset out_path",
) -> None:
    """Raise when ``out_path`` is the source project itself.

    The check covers a hard-linked alias (the same inode), which ``resolve()`` does not
    catch but an atomic ``os.replace`` onto it would truncate. ``what`` names the
    destination in the message; [`export_subset_tether`][] uses the default.

    Raises
    ------
    ValueError
        If ``out_path`` is the source project.
    """
    path = _project_path(project)
    out_path = Path(out_path)
    same_file = out_path.resolve() == path.resolve()
    if out_path.exists():
        same_file = same_file or out_path.samefile(path)
    if same_file:
        raise ValueError(f"{what} {out_path} is the source project; refusing to overwrite")


def export_subset_tether(
    project: Project | str | os.PathLike[str],
    out_path: str | os.PathLike[str],
    *,
    molecule_keys: list[str] | None = None,
    include_rejected: bool = False,
    include_raw: bool = False,
    overwrite: bool = False,
    curator_alias: str | None = None,
) -> ExportResult:
    """Export a **movie-less subset ``.tether``** (PRD §7.9, §5.3, §5.4).

    Builds a self-contained new ``.tether`` embedding, for the selected molecules:
    their ``/molecules`` rows (coordinates + identity + all scalar fields), the two
    ``/patches`` channel stacks, the corrected ``/traces`` layers, every
    ``/idealization`` model (filtered to the exported molecules), the ``/labels``
    provenance rows and their ``/labels/reject_log`` reasons, and the
    ``/conditions``/``/settings``/``/calibration``/``/models`` metadata verbatim. The
    source **raw** traces are embedded only when ``include_raw=True``.

    Movie-less & raw-reconstructability (§5.4). The subset carries **no** ``/movies``
    rows, so it is definitionally movie-less — there is no source movie to resolve or
    relink (the movie file is not part of the export; per-molecule origin provenance
    still travels via ``molecule_key``, ``source_filename``, and the coordinates).
    Because ``corrected = raw − background`` exactly, ``include_raw`` controls the raw
    **and** background layers together: with it ``False`` the subset holds only
    corrected traces and raw is genuinely **not reconstructable** from the file (no
    movie, no raw, no background to back it out), satisfying the §5.4 invariant; with
    it ``True`` the full six-layer trace record travels.

    Selection mirrors [`export_deeplasi_txt`][]: molecules are taken in **store
    order**; the §7.5 curation filter drops ``REJECT`` unless ``include_rejected``; an
    optional ``molecule_keys`` list subselects by ``molecule_key`` (a duplicate key,
    §7.10, matches every store row that carries it).

    Idealization models are copied filtered per molecule, but a model is **skipped**
    (and recorded in the provenance) when the ``/traces`` layers it was fit over are not
    in the subset — e.g. a ``raw``-fit model when ``include_raw=False`` — since without
    its input layers it could never be re-staled and would read back permanently stale.

    Schema freeze & atomicity. The subset is written through
    [`tether.io.schema.create_project`][] and populated with **additive data only**;
    it adds no structure to the M0-frozen skeleton and never mutates the source
    (``schema-guard`` neutral). It is built under a temporary sibling path and
    **atomically moved** into place, so a mid-export failure leaves any existing
    ``out_path`` untouched; the source is preflighted first (so a malformed store fails
    before staging). A ``<out_path>.provenance.json`` sidecar is written and the same
    provenance is stamped into the subset's root attributes.

    Copy for sharing (#620). With ``curator_alias`` set, the copy replaces user names
    in the labels, reject log, condition audit, lock takeover, job owner and correction
    transition records. It replaces the actor of a person-authored transition and preserves the
    policy text of a policy-authored transition. Each distinct user name becomes
    ``<curator_alias>-1``, ``<curator_alias>-2``, … in the order first met, so one
    person keeps one alias and multi-curator analysis still works. A full path in
    ``PATH_ATTRS`` can include the user name. Only its file name is kept, such as for
    the model file of each ``/models`` reference. The provenance records that aliasing
    was used and the number of names replaced. The source registry (#912) is copied, and a
    registry path outside the project's folder keeps only its file name, so the copy holds
    no absolute private path from it. The option does not remove
    ``source_filename`` (a file name, not a path): a file name can still identify a sample.

    Parameters
    ----------
    project:
        A [`Project`][tether.project.core.Project] or a path to the source ``.tether``.
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
        Replace an existing ``out_path`` (default refuses to clobber).
    curator_alias:
        Replace each stored user name with ``<curator_alias>-<n>`` (default ``None``
        keeps the names, and the output is the same as without the option).

    Returns
    -------
    ExportResult
        The subset path, its provenance sidecar, and the molecule count exported.

    Raises
    ------
    KeyError
        If a requested ``molecule_key`` matches no store row (a typo — fail loudly).
    FileExistsError
        If ``out_path`` already exists and ``overwrite`` is ``False``.
    ValueError
        If the source is not a valid or complete ``.tether`` (missing corrupt/incomplete
        corrected traces or patches); the selection is empty (nothing to export);
        ``out_path`` is the source project (same path or a hard-linked alias); or
        ``curator_alias`` is given but empty or blank, contains a user name stored
        in the source, or leaves no alias free of every source user name.
    """
    import h5py  # noqa: PLC0415

    if curator_alias is not None and not str(curator_alias).strip():
        raise ValueError("curator_alias must be a non-empty prefix such as 'curator'")

    from tether.io.schema import (  # noqa: PLC0415
        TABLE,
        assert_is_compatible_project,
        create_project,
    )

    path = _project_path(project)
    assert_is_compatible_project(path)
    out_path = Path(out_path)
    refuse_source_destination(path, out_path)
    if out_path.exists() and not overwrite:
        # Enforced here (not via create_project's w- mode) because the subset is staged
        # to a temp sibling and atomically moved into place — os.replace always clobbers.
        raise FileExistsError(f"{out_path} already exists; pass overwrite=True to replace it")

    created_utc = datetime.now(UTC).isoformat()
    with h5py.File(path, "r") as src:
        # Select rows and copy their review state from one open file. A
        # generation can replace the canonical path between separate opens.
        molecules = src["molecules"][TABLE][()]
        rows = _selected_rows(molecules, molecule_keys, include_rejected)
        if not rows:
            raise ValueError(
                "no molecules selected to export (empty selection, or every match rejected "
                "with include_rejected=False)"
            )
        selected_keys = _selected_keys(molecules, rows)
        selected_ids = _selected_ids(molecules, rows)
        _preflight_source(src, int(molecules.shape[0]), include_raw)
        kept_indices, kept_labels = _subset_label_rows(src["labels"][TABLE][()], selected_keys)
        kept_reject_log = _kept_reject_log(src, selected_keys)
        aliases = (
            None
            if curator_alias is None
            else _CuratorAliases(
                str(curator_alias), _source_user_names(src, kept_labels, kept_reject_log)
            )
        )
        # Stage into a temp sibling, then atomically replace, so a mid-copy HDF5/IO
        # failure never truncates an existing subset (writing straight to out_path with
        # create_project(overwrite=True) would destroy it before the copy could finish).
        fd, tmp_name = tempfile.mkstemp(
            dir=out_path.parent, prefix=out_path.name + ".", suffix=".tmp"
        )
        os.close(fd)
        staged = Path(tmp_name)
        try:
            create_project(staged, overwrite=True)
            with h5py.File(staged, "r+") as dst:
                _copy_molecule_rows(src, dst, rows)
                layers = _copy_trace_layers(src, dst, rows, include_raw)
                _copy_patches(src, dst, rows)
                # Idealization joins per-molecule on molecule_id (unique) and needs its
                # input trace layers present; labels join on molecule_key (the cross-file
                # merge-back key, §5.1/§7.10) — deliberately different keys.
                models, skipped_models = _copy_idealization(src, dst, selected_ids, set(layers))
                n_labels = _copy_labels(dst, kept_labels, aliases)
                n_reject_log = _copy_reject_log(dst, kept_reject_log, aliases)
                _copy_label_reviews(src, dst, selected_ids, kept_indices)
                _copy_verbatim_groups(src, dst, aliases)
                _copy_sources(src, dst, aliases, path, out_path)
                # Embedded provenance (§7.9): stamp the subset's own root attributes so
                # the file is self-describing even detached from its sidecar.
                dst.attrs["tether_subset_of"] = path.name
                dst.attrs["tether_subset_created_utc"] = created_utc
                dst.attrs["tether_subset_include_raw"] = int(bool(include_raw))
                dst.attrs["tether_subset_n_molecules"] = int(len(rows))
                if aliases is not None:
                    dst.attrs["tether_subset_names_aliased"] = 1
                    dst.attrs["tether_subset_n_aliased_names"] = aliases.count
            os.replace(staged, out_path)  # atomic within out_path's directory
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

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
            "skipped_idealization_models": skipped_models,
            "n_label_rows": n_labels,
            "n_reject_log_rows": n_reject_log,
            "molecule_keys": None if molecule_keys is None else [str(k) for k in molecule_keys],
            **(
                {}
                if aliases is None
                else {
                    "curator_alias": str(curator_alias),
                    "names_aliased": True,
                    "n_aliased_names": aliases.count,
                }
            ),
        },
    )
    return ExportResult(path=out_path, provenance_path=provenance, n_molecules=len(rows))
