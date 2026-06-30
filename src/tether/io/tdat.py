# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode the Deep-LASI ``TIRFdata`` ``.tdat`` — colocalized particle coordinates
and correction factors (PRD §7.8, Appendix A, Appendix B; §11.1).

A ``.tdat`` is a **MATLAB v7.3 MAT-file**, i.e. an HDF5 container. The TIRFdata
object is saved as a struct at the root group ``temp/``; bulk arrays live in the
MATLAB reference group ``#refs#/`` and are reached through HDF5 object references,
while genuine MATLAB *objects* (the per-channel ``Channel`` instances, ``HMMdata``,
``table`` …) live in the ``#subsystem#/MCOS`` ``FileWrapper__`` blob.

This reader recovers the payloads M1 import needs **without** decoding the
MCOS object blob, because each is a plain numeric leaf of ``temp/``:

* **Particle-detection mode** — ``temp/ParticleDetectionMode`` is a plain
  ``double`` scalar holding the Deep-LASI ``findPart`` ``method`` code the movie
  was detected with (``mapping/findPart.m:18-62``: 1 wavelet, 2 intensity, 3
  band-pass; ``classes/TRACERdata.m:62`` defaults it to 1). It maps to the Tether
  :class:`~tether.imaging.detect.ParticleDetectionMode` string so a ``.tether``
  re-extraction can reproduce the method the data was actually detected with
  (PRD §11.2, ADR-0021). The companion per-channel ``DetectionThreshold`` is a
  ``TIRFdata`` MCOS property (``temp/Channel[i]`` -> ``#subsystem#/MCOS``), so it
  is **not** decoded here yet (PR-C3c-decode-B); :attr:`TdatDetectionSettings.threshold`
  stays ``None`` and each detector falls back to its own faithful default.

* **Colocalized coordinates** — ``temp/ParticlesColocalized`` is a MATLAB cell
  (one entry per movie in the stack) whose object reference resolves to the
  ``findColoc`` matrix. Its 17 columns are, 1-based
  (``mapping/findColoc.m:10``)::

      X1 Y1 #1 | X2 Y2 #2 | X3 Y3 #3 | X4 Y4 #4 | bCh1 bCh2 bCh3 bCh4 | nFile

  i.e. per channel an ``(X, Y, detection-index)`` triple, then the four
  per-channel colocalization flags, then the source-file index. ``(X, Y)`` is
  already ``[x, y]`` (Deep-LASI stores mapped particles ``fliplr``'d to ``[x, y]``;
  PRD §11.1), so the only conversion is MATLAB 1-based-inclusive → Tether
  0-based (subtract 1). The per-channel *detection index* and *file index* are
  source bookkeeping and are kept 1-based, as stored.

* **Correction factors** — ``temp/DefaultAlpha`` / ``DefaultBeta`` /
  ``DefaultGamma``. **Deep-LASI's naming is inverted relative to Tether's**
  (``classes/TIRFdata.m:23-25``): Deep-LASI ``Beta`` is donor→acceptor spectral
  *leakage*, ``Alpha`` is acceptor *direct excitation*. :func:`remap_correction_factors`
  applies the PRD Appendix-B remap — Deep-LASI ``Beta`` → Tether ``alpha``
  (leakage, applied), Deep-LASI ``Alpha`` → Tether ``delta`` (direct excitation,
  **inert 0** without ALEX [Lee2005][Hohlbein2014]), Deep-LASI ``Gamma`` → Tether
  ``gamma`` — and retains the Deep-LASI values for provenance. Misattributing
  ``Beta`` would silently drop a real leakage correction (PRD §7.8).

Per-channel split geometry (crop / rotation / flip), which *does* live in the
MCOS ``Channel`` objects, and the native-registration residual check against the
``.tmap`` are out of scope here — they land with the M0.5 S6 follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import h5py
import numpy as np

if TYPE_CHECKING:
    from os import PathLike

__all__ = [
    "Tdat",
    "TdatColocalization",
    "TdatCorrections",
    "TdatDetectionSettings",
    "read_detection_settings",
    "read_tdat",
    "remap_correction_factors",
]

# findColoc.m column layout (0-based here): per channel an (X, Y, index) triple
# at stride 3, then four colocalization flags, then the source-file index.
_N_COLS = 17
_MAX_CHANNELS = 4
_FLAG_START = 12  # bCh1..bCh4 occupy columns 12..15
_NFILE_COL = 16

# Deep-LASI findPart.m ``method`` code -> Tether ParticleDetectionMode string value
# (mapping/findPart.m:18-30). The string values are the frozen
# tether.imaging.detect.ParticleDetectionMode members (kept as literals so io does
# not depend on the imaging layer; a test asserts they stay a subset of the enum).
# Modes 4 (local-variance) and 5 (ZMW intensity) are not ported, so a .tdat saved
# with one of them is refused rather than silently mis-detected.
_DETECTION_MODE_BY_CODE = {1: "wavelet", 2: "intensity", 3: "bandpass"}
# classes/TRACERdata.m:62 — ParticleDetectionMode defaults to 1 (wavelet); a .tdat
# that predates the field (or a minimal one) decodes to that class default.
_DEFAULT_DETECTION_MODE_CODE = 1


@dataclass(frozen=True)
class TdatCorrections:
    """Correction factors from a ``.tdat`` in both Deep-LASI and Tether naming.

    The ``deeplasi_*`` fields are the raw stored values (Deep-LASI's naming, where
    ``beta`` is leakage and ``alpha`` is direct excitation). The ``alpha`` /
    ``delta`` / ``gamma`` fields are the Tether-scheme remap (PRD Appendix B):
    ``alpha`` (leakage) = Deep-LASI ``beta``; ``delta`` (direct excitation) is
    inert ``0`` for single-laser data; ``gamma`` = Deep-LASI ``gamma``.
    """

    deeplasi_alpha: float  # Deep-LASI Alpha — acceptor direct excitation
    deeplasi_beta: float  # Deep-LASI Beta — donor→acceptor spectral leakage
    deeplasi_gamma: float  # Deep-LASI Gamma — relative detection efficiency
    alpha: float  # Tether leakage (= Deep-LASI beta)
    delta: float  # Tether direct excitation (inert 0 — needs ALEX)
    gamma: float  # Tether gamma (= Deep-LASI gamma)


@dataclass(frozen=True)
class TdatColocalization:
    """Colocalized-particle coordinate table decoded from ``ParticlesColocalized``.

    Attributes
    ----------
    coords:
        ``{channel_index: (N, 2) float64}`` of 0-based ``[x, y]`` sub-pixel
        coordinates, keyed by 0-based channel index, only for channels that
        carry colocalized data.
    detection_index:
        ``{channel_index: (N,) int64}`` of the per-channel detection index
        (1-based, as stored — source bookkeeping into each channel's spot list).
    channel_present:
        ``(4,)`` bool — which of the four channels colocalized.
    file_index:
        ``(N,) int64`` source-movie index per molecule (1-based, as stored).
    n_molecules:
        number of colocalized molecules ``N``.
    """

    coords: dict[int, np.ndarray]
    detection_index: dict[int, np.ndarray]
    channel_present: np.ndarray
    file_index: np.ndarray
    n_molecules: int


@dataclass(frozen=True)
class TdatDetectionSettings:
    """Particle-detection config recovered from a ``.tdat`` (PRD §11.2, ADR-0021).

    Attributes
    ----------
    mode:
        the Tether :class:`~tether.imaging.detect.ParticleDetectionMode` string
        (``"wavelet"`` / ``"intensity"`` / ``"bandpass"``) for the Deep-LASI
        ``findPart`` method the movie was detected with.
    threshold:
        the per-channel ``DetectionThreshold`` as a fraction of the
        detection-image max, or ``None`` when it is unknown. It is **always**
        ``None`` here: the value is a ``TIRFdata`` MCOS property that the plain-leaf
        reader does not decode (PR-C3c-decode-B adds the MCOS ``Channel`` decoder).
        ``None`` lets each detector use its own faithful default (PRD §11.2).
    """

    mode: str
    threshold: float | None = None


@dataclass(frozen=True)
class Tdat:
    """The decoded payload of a Deep-LASI ``.tdat`` (coordinates + factors + detection)."""

    colocalization: TdatColocalization
    corrections: TdatCorrections
    detection: TdatDetectionSettings
    channels_with_data: tuple[int, ...]  # 0-based channel indices
    reference_channel: int  # 0-based mapping/trace reference channel


def remap_correction_factors(
    deeplasi_alpha: float, deeplasi_beta: float, deeplasi_gamma: float
) -> TdatCorrections:
    """Apply the PRD Appendix-B Deep-LASI → Tether correction-factor remap.

    Deep-LASI ``Beta`` (leakage) → Tether ``alpha``; Deep-LASI ``Alpha`` (direct
    excitation) → Tether ``delta``, forced **inert 0** because single-laser data
    cannot estimate direct excitation (it needs the acceptor-under-acceptor-
    excitation channel that only ALEX provides [Lee2005][Hohlbein2014]); Deep-LASI
    ``Gamma`` → Tether ``gamma``.
    """
    return TdatCorrections(
        deeplasi_alpha=float(deeplasi_alpha),
        deeplasi_beta=float(deeplasi_beta),
        deeplasi_gamma=float(deeplasi_gamma),
        alpha=float(deeplasi_beta),
        delta=0.0,
        gamma=float(deeplasi_gamma),
    )


def _scalar(group: h5py.Group, name: str) -> float:
    """Read a MATLAB scalar (stored as a 1×1 array) as a Python float."""
    return float(np.asarray(group[name][()]).reshape(-1)[0])


def _detection_mode_code(temp: h5py.Group) -> int:
    """Return the integer ``findPart`` mode code from ``temp/ParticleDetectionMode``.

    A ``.tdat`` without the leaf decodes to the Deep-LASI class default (wavelet).
    A present value must be an exact-integer ``double`` — a fractional or non-finite
    code is corruption and is rejected, not silently truncated into a bogus mode.
    """
    if "ParticleDetectionMode" not in temp:
        return _DEFAULT_DETECTION_MODE_CODE
    value = _scalar(temp, "ParticleDetectionMode")
    if not np.isfinite(value) or not float(value).is_integer():
        raise ValueError(f"ParticleDetectionMode must be a finite integer mode code; got {value!r}")
    return int(value)


def _detection_settings(temp: h5py.Group) -> TdatDetectionSettings:
    """Decode :class:`TdatDetectionSettings` from the ``temp`` struct.

    Maps the Deep-LASI ``findPart`` ``method`` code to the Tether mode string; an
    unported mode (4 local-variance / 5 ZMW, or any out-of-range code) is refused
    so an import can never silently mis-detect with the wrong method. The
    per-channel ``DetectionThreshold`` is an MCOS property and is left ``None``
    (decoded in PR-C3c-decode-B).
    """
    code = _detection_mode_code(temp)
    try:
        mode = _DETECTION_MODE_BY_CODE[code]
    except KeyError:
        supported = sorted(_DETECTION_MODE_BY_CODE)
        raise ValueError(
            f"Deep-LASI ParticleDetectionMode {code} is not supported by Tether "
            f"(only {supported} = wavelet/intensity/bandpass; modes 4 'local-variance' "
            "and 5 'ZMW intensity' are not ported)"
        ) from None
    return TdatDetectionSettings(mode=mode, threshold=None)


def read_detection_settings(path: str | PathLike[str]) -> TdatDetectionSettings:
    """Decode just the particle-detection config from a ``.tdat``.

    A lightweight companion to :func:`read_tdat` for the ``tether extract --tdat``
    auto-apply path: it reads only ``temp/ParticleDetectionMode`` (no coordinate or
    correction-factor decode), so a ``.tdat`` that lacks colocalization data still
    yields its detection mode. Raises :class:`ValueError` for a non-TIRFdata
    container or an unsupported mode.
    """
    path = Path(path)
    with h5py.File(path, "r") as file:
        if "temp" not in file:
            raise ValueError(
                f"{path.name!r} is not a Deep-LASI TIRFdata .tdat "
                f"(no 'temp' struct; root keys: {sorted(file.keys())})"
            )
        return _detection_settings(file["temp"])


def _one_based_channel_index(value: float, name: str) -> int:
    """Validate a MATLAB 1-based channel index and return it 0-based.

    Rejects non-finite, fractional, or out-of-range values so a corrupt header
    can never leak an invalid channel id into the public :class:`Tdat`.
    """
    if not np.isfinite(value):
        raise ValueError(f"{name} must be a finite channel index; got {value!r}")
    if not float(value).is_integer():
        # exact, not tolerant: a channel index is stored as an exact-integer
        # double, so any fractional part (even near-integer) is corruption.
        raise ValueError(f"{name} must be an integer channel index; got {value!r}")
    channel = int(value) - 1
    if not 0 <= channel < _MAX_CHANNELS:
        raise ValueError(f"{name} must be a 1..{_MAX_CHANNELS} channel index; got {value!r}")
    return channel


def _one_based_channels(group: h5py.Group, name: str) -> tuple[int, ...]:
    """Read a MATLAB 1-based channel-index vector as a sorted 0-based tuple."""
    values = np.asarray(group[name][()], dtype=np.float64).reshape(-1)
    return tuple(sorted(_one_based_channel_index(v, name) for v in values))


def _coloc_tables(file: h5py.File, temp: h5py.Group) -> list[np.ndarray]:
    """Resolve every non-empty ``ParticlesColocalized`` cell to an ``(N, 17)`` table.

    Each cell entry holds an HDF5 object reference to the per-file ``findColoc``
    matrix; an empty cell is stored as a non-reference MATLAB ``[]`` marker and is
    skipped. h5py reads the MATLAB ``(N, 17)`` matrix transposed as ``(17, N)``.
    """
    pc = temp["ParticlesColocalized"]
    # An entirely empty ParticlesColocalized is not a reference dataset.
    if h5py.check_dtype(ref=pc.dtype) is not h5py.Reference:
        return []
    tables: list[np.ndarray] = []
    for ref in np.asarray(pc[()]).reshape(-1):
        if not ref:  # null reference (unpopulated cell slot)
            continue
        table = np.asarray(file[ref][()], dtype=np.float64)
        # A MATLAB empty-array cell element (a movie with no colocalized
        # particles) dereferences to a non-2-D dims marker — legitimately skip
        # it. A 2-D array with the wrong column count is corrupt or
        # schema-changed input: fail loudly rather than silently decoding fewer
        # molecules.
        if table.ndim != 2:
            continue
        if table.shape[0] != _N_COLS:
            raise ValueError(
                f"ParticlesColocalized table has shape {table.shape}; "
                f"expected {_N_COLS} columns on the first axis (MATLAB-transposed)"
            )
        tables.append(table.T)  # -> (N, 17): rows = molecules
    return tables


def read_tdat(path: str | PathLike[str]) -> Tdat:
    """Decode a Deep-LASI ``.tdat`` into coordinates + correction factors + detection mode.

    Returns a :class:`Tdat`. Coordinates are 0-based ``[x, y]`` (PRD §11.1);
    correction factors are remapped to the Tether scheme (PRD Appendix B) with the
    Deep-LASI originals retained; :attr:`Tdat.detection` carries the
    :class:`TdatDetectionSettings` (the ``findPart`` mode the movie was detected
    with). Raises :class:`ValueError` if the file is not a recognizable TIRFdata
    container (no ``temp`` struct) or carries an unsupported detection mode.
    """
    path = Path(path)
    with h5py.File(path, "r") as file:
        if "temp" not in file:
            raise ValueError(
                f"{path.name!r} is not a Deep-LASI TIRFdata .tdat "
                f"(no 'temp' struct; root keys: {sorted(file.keys())})"
            )
        temp = file["temp"]
        corrections = remap_correction_factors(
            _scalar(temp, "DefaultAlpha"),
            _scalar(temp, "DefaultBeta"),
            _scalar(temp, "DefaultGamma"),
        )
        detection = _detection_settings(temp)
        channels_with_data = _one_based_channels(temp, "ChannelsWithData")
        reference_channel = _one_based_channel_index(
            _scalar(temp, "MappingReferenceChannel"), "MappingReferenceChannel"
        )
        tables = _coloc_tables(file, temp)

    coloc = _build_colocalization(tables)
    return Tdat(
        colocalization=coloc,
        corrections=corrections,
        detection=detection,
        channels_with_data=channels_with_data,
        reference_channel=reference_channel,
    )


def _build_colocalization(tables: list[np.ndarray]) -> TdatColocalization:
    """Slice stacked ``(N, 17)`` findColoc tables into per-channel 0-based coords."""
    if not tables:
        return TdatColocalization(
            coords={},
            detection_index={},
            channel_present=np.zeros(_MAX_CHANNELS, dtype=bool),
            file_index=np.empty(0, dtype=np.int64),
            n_molecules=0,
        )
    table = np.vstack(tables)
    flags = table[:, _FLAG_START : _FLAG_START + _MAX_CHANNELS].astype(bool)  # (N, 4) per-row bCh
    channel_present = flags.any(axis=0)
    # findColoc keeps only molecules colocalized in *every* data channel; filter
    # defensively to rows present in all participating channels so a row that
    # lacks a channel can never publish that channel's placeholder (post 1-based
    # conversion: negative) coordinates. The kept rows stay aligned across
    # channels — coords[d][i] and coords[a][i] are the same molecule.
    participating = np.flatnonzero(channel_present)
    complete = (
        flags[:, participating].all(axis=1)
        if participating.size
        else np.zeros(len(table), dtype=bool)
    )
    table = table[complete]
    coords: dict[int, np.ndarray] = {}
    detection_index: dict[int, np.ndarray] = {}
    for channel in participating.tolist():
        base = channel * 3
        # findColoc writes its coordinate columns as [row, col]: they come from
        # ParticlesMapped/Particles, which are MATLAB [row, col] straight out of
        # findPart.m (deeplasi mapping/findColoc.m; findPart.m:44 `XY=[ty,tx]`).
        # Flip to Tether's [x, y] = [col, row] convention (PRD §11.1), then
        # convert 1-based inclusive -> 0-based. (The earlier "no flip" assumption
        # put row in x, which the M0.5 S6 registration validation exposed.)
        xy = table[:, base : base + 2][:, ::-1] - 1.0
        coords[channel] = np.ascontiguousarray(xy, dtype=np.float64)
        detection_index[channel] = table[:, base + 2].astype(np.int64)
    return TdatColocalization(
        coords=coords,
        detection_index=detection_index,
        channel_present=channel_present,
        file_index=table[:, _NFILE_COL].astype(np.int64),
        n_molecules=int(table.shape[0]),
    )
