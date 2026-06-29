# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Deep-LASI ``.mat`` / ``.txt`` reader for extraction validation (PRD §9 M1, Appendix A).

Tether re-extracts traces natively (Appendix E); the M1 acceptance gate
(§8 NFR-VALID (a), §9 M1) checks that native extraction reproduces Deep-LASI's
result on the same movie — recall ≥ 95 % @ 1 px, per-frame integrated-intensity
Pearson r ≥ 0.99, registration RMS ≤ 0.5 px. That comparison needs Deep-LASI's
*own* output as the oracle. This module reads the two Deep-LASI export artifacts
that carry it (PRD Appendix A):

* ``DeepLASI_MAT_export_*.mat`` — a MATLAB **v5** ``.mat`` (≈ 9 MB). For each of N
  molecules over T frames it holds ``fret_pairs`` (N×4 donor/acceptor **pixel
  coordinates**) plus raw / corrected / background donor + acceptor integrated
  traces (``don`` / ``donc`` / ``bdon``, ``acc`` / ``accc`` / ``bacc``) and movie
  provenance (the ``movie_name`` *filename* + the ``movie_path`` *directory* — two
  distinct fields — plus ``exportedby``). :func:`read_deeplasi_mat` returns the
  coordinates + the six trace arrays — the **coordinate + intensity oracle**.
* ``…-donc-accc-w.txt`` — whitespace text, T rows × 2N columns of *corrected*
  donor/acceptor intensities **interleaved per molecule** (``donc₀ accc₀ donc₁
  accc₁ …``, rounded to 5 decimals); **no coordinates** (PRD Appendix A).
  :func:`read_deeplasi_txt` returns the two corrected-trace arrays. It equals the
  ``.mat`` ``donc`` / ``accc`` to the text rounding (verified across all 250
  molecules of the reference acquisition).

**Scope — deliberately minimal: a validation reader, not a project importer.**
Only the fields the M1 extraction oracle consumes are parsed. The per-molecule
photobleach frames (``pacc`` / ``pdon``) and correction factors (``b`` =
Deep-LASI β → Tether α, ``g`` = γ) are **left for M3**, where their semantics are
verified against the bleach-frame (§9 M3, NFR-VALID (g)) and Appendix-B
correction gates — this reader does not encode an unvalidated interpretation of
them. Full project round-trip from a ``.tdat`` / ``.mat`` is the M7 importer.

Coordinate convention: ``fret_pairs`` columns are ``[x_donor, y_donor, x_acc,
y_acc]`` with ``x`` = column, ``y`` = row, **1-based** (MATLAB); this reader
subtracts 1 to return Tether's 0-based ``[x = col, y = row]`` — matching
:mod:`tether.imaging` and the convention the M0.5 aperture oracle validated to
donor-correlation ≈ 0.99 (``scripts/make_aperture_fixture.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# NOTE: ``scipy`` is imported *lazily* inside ``read_deeplasi_mat`` (not at module
# top) so that importing ``tether.io`` — which re-exports this reader — does not
# require scipy. The ``schema-guard`` CI gate imports ``tether.io.schema`` through
# the package with a deliberately minimal ``h5py`` + numpy env; pulling scipy into
# the package import graph would break it. Keep scipy out of module scope.

__all__ = [
    "DeepLasiExport",
    "DeepLasiTraces",
    "read_deeplasi_mat",
    "read_deeplasi_txt",
]

# The six (N, T) trace arrays and the coordinate array read from the ``.mat``.
# Selective load (``variable_names``) keeps the read off the ~9 MB file's other
# ~25 fields (FRET, direct-excitation, range/select/tags, bleach, β/γ — M3+).
_MAT_TRACE_FIELDS = ("don", "acc", "donc", "accc", "bdon", "bacc")
_MAT_REQUIRED = ("fret_pairs", *_MAT_TRACE_FIELDS)
# Provenance (best-effort): ``movie_name`` is the source-movie *filename*,
# ``movie_path`` its containing *directory* — two distinct Deep-LASI fields
# (PRD §6 / Appendix A); ``exportedby`` is the exporter tool/version.
_MAT_PROVENANCE = ("movie_name", "movie_path", "exportedby")
_MAT_VARIABLES = (*_MAT_REQUIRED, *_MAT_PROVENANCE)

_MATFILE_V73 = 2  # scipy.io.matlab.matfile_version major code for the HDF5 v7.3 format


@dataclass(frozen=True, eq=False)
class DeepLasiExport:
    """A parsed Deep-LASI ``.mat`` validation export (PRD Appendix A).

    Coordinates are 0-based ``[x = col, y = row]`` ``float64`` ``(n_molecules,
    2)``; traces are ``(n_molecules, n_frames)`` ``float64``. The donor/acceptor
    *integrated* intensities are the oracle for the M1 intensity-Pearson gate;
    the ``*_xy`` seed the recall / registration-RMS gates. ``eq=False`` because
    the ndarray fields make a generated ``__eq__`` ambiguous (cf.
    ``tether.imaging.calibrate.RegistrationMap``).
    """

    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    donor_raw: np.ndarray
    acceptor_raw: np.ndarray
    donor_corrected: np.ndarray
    acceptor_corrected: np.ndarray
    donor_background: np.ndarray
    acceptor_background: np.ndarray
    movie_name: str  # source-movie filename (Deep-LASI ``movie_name``)
    movie_path: str  # its containing directory (Deep-LASI ``movie_path``)
    exported_by: str  # exporter tool/version (Deep-LASI ``exportedby``)

    @property
    def n_molecules(self) -> int:
        return int(self.donor_xy.shape[0])

    @property
    def n_frames(self) -> int:
        return int(self.donor_raw.shape[1])


@dataclass(frozen=True, eq=False)
class DeepLasiTraces:
    """Parsed ``…-donc-accc-w.txt`` corrected traces — **no coordinates** (Appendix A).

    Both arrays are ``(n_molecules, n_frames)`` ``float64``, de-interleaved from
    the per-molecule donor/acceptor column pairs.
    """

    donor_corrected: np.ndarray
    acceptor_corrected: np.ndarray

    @property
    def n_molecules(self) -> int:
        return int(self.donor_corrected.shape[0])

    @property
    def n_frames(self) -> int:
        return int(self.donor_corrected.shape[1])


def _scalar_str(value: object) -> str:
    """Best-effort single string from a MATLAB char / cell field (provenance only).

    Provenance fields are optional and never fail the read — a missing or oddly
    shaped field yields ``""`` rather than raising.
    """
    if value is None:
        return ""
    item: object = np.asarray(value)
    # Unwrap nested char / object cells. ``loadmat(chars_as_strings=True)`` joins
    # char arrays into a single string, but be defensive: a multi-element char
    # array (dtype 'U'/'S') is one string split across cells — join it rather
    # than taking only the first character.
    while isinstance(item, np.ndarray):
        if item.size == 0:
            return ""
        if item.dtype.kind in {"U", "S"} and item.size > 1:
            return "".join(item.astype(str).reshape(-1)).strip()
        item = item.reshape(-1)[0]
    return str(item).strip()


def read_deeplasi_mat(path: str | Path) -> DeepLasiExport:
    """Read a Deep-LASI ``DeepLASI_MAT_export_*.mat`` (MATLAB v5) export.

    Parameters
    ----------
    path
        Path to the ``.mat`` file.

    Returns
    -------
    DeepLasiExport
        0-based donor/acceptor coordinates + the six raw/corrected/background
        ``(N, T)`` trace arrays + movie provenance.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    NotImplementedError
        If ``path`` is a MATLAB v7.3 (HDF5) file — the Deep-LASI export is v5
        (PRD Appendix A); ``scipy.io.loadmat`` cannot read v7.3.
    ValueError
        If ``path`` is not a readable MATLAB ``.mat`` file, or a required field
        is missing or has an inconsistent shape.
    """
    import scipy.io as sio
    from scipy.io.matlab import MatReadError, matfile_version

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Deep-LASI .mat not found: {path}")

    try:
        major, _minor = matfile_version(str(path))
    except (ValueError, MatReadError, IndexError) as exc:
        # Not a MAT file at all (a bare HDF5, garbage, truncated, a .txt renamed
        # to .mat, …). ``matfile_version`` can raise IndexError on short/garbage
        # input, not just ValueError/MatReadError — wrap them all.
        raise ValueError(f"{path.name} is not a readable MATLAB v5 .mat file: {exc}") from exc
    if major == _MATFILE_V73:
        raise NotImplementedError(
            f"{path.name} is a MATLAB v7.3 (HDF5) .mat; the Deep-LASI export is "
            "MATLAB v5 (PRD Appendix A) and scipy.io.loadmat cannot read v7.3."
        )

    mat = sio.loadmat(
        str(path),
        variable_names=list(_MAT_VARIABLES),
        squeeze_me=False,
        struct_as_record=True,
    )
    missing = [key for key in _MAT_REQUIRED if key not in mat]
    if missing:
        raise ValueError(
            f"{path.name} is missing required Deep-LASI field(s): {', '.join(missing)}"
        )

    fret_pairs = np.asarray(mat["fret_pairs"], dtype=np.float64)
    if fret_pairs.ndim != 2 or fret_pairs.shape[1] != 4:
        raise ValueError(f"fret_pairs must be (N, 4); got {fret_pairs.shape} in {path.name}")
    n_molecules = fret_pairs.shape[0]

    traces: dict[str, np.ndarray] = {}
    n_frames: int | None = None
    for key in _MAT_TRACE_FIELDS:
        arr = np.ascontiguousarray(np.asarray(mat[key], dtype=np.float64))
        if arr.ndim != 2 or arr.shape[0] != n_molecules:
            raise ValueError(f"{key} must be (N={n_molecules}, T); got {arr.shape} in {path.name}")
        if n_frames is None:
            n_frames = arr.shape[1]
        elif arr.shape[1] != n_frames:
            raise ValueError(
                f"{path.name}: inconsistent frame count — {key} has {arr.shape[1]} "
                f"frames, expected {n_frames}"
            )
        traces[key] = arr

    # 1-based MATLAB [x=col, y=row] -> Tether 0-based.
    donor_xy = np.ascontiguousarray(fret_pairs[:, 0:2] - 1.0)
    acceptor_xy = np.ascontiguousarray(fret_pairs[:, 2:4] - 1.0)

    return DeepLasiExport(
        donor_xy=donor_xy,
        acceptor_xy=acceptor_xy,
        donor_raw=traces["don"],
        acceptor_raw=traces["acc"],
        donor_corrected=traces["donc"],
        acceptor_corrected=traces["accc"],
        donor_background=traces["bdon"],
        acceptor_background=traces["bacc"],
        # ``movie_name`` is (N, 1) per-molecule; a single-video export (one movie
        # -> one .tether) has one distinct name, so the first entry is it.
        movie_name=_scalar_str(mat.get("movie_name")),
        movie_path=_scalar_str(mat.get("movie_path")),
        exported_by=_scalar_str(mat.get("exportedby")),
    )


def read_deeplasi_txt(path: str | Path) -> DeepLasiTraces:
    """Read a Deep-LASI ``…-donc-accc-w.txt`` corrected-trace export.

    The file is whitespace text, T rows (frames) × 2N columns: each molecule
    contributes an interleaved ``(donor, acceptor)`` corrected-intensity column
    pair. De-interleaves to two ``(N, T)`` arrays.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the column count is zero or odd (not a donor/acceptor pairing).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Deep-LASI .txt not found: {path}")

    try:
        data = np.loadtxt(path, dtype=np.float64, ndmin=2)  # (T, 2N)
    except ValueError as exc:
        # Ragged rows / non-numeric content — re-raise with a reader-level message
        # rather than leaking numpy's internal `usecols` hint.
        raise ValueError(
            f"{path.name}: could not parse as a rectangular numeric trace table: {exc}"
        ) from exc
    if data.shape[1] == 0 or data.shape[1] % 2 != 0:
        raise ValueError(
            f"{path.name}: expected T rows x 2N interleaved donor/acceptor columns "
            f"(an even, non-zero column count); got shape {data.shape}"
        )

    donor = np.ascontiguousarray(data[:, 0::2].T)  # (N, T)
    acceptor = np.ascontiguousarray(data[:, 1::2].T)  # (N, T)
    return DeepLasiTraces(donor_corrected=donor, acceptor_corrected=acceptor)
