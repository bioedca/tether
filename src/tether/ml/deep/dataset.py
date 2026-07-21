# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Framework-agnostic deep-classifier training-dataset substrate (PRD §7.5/§9 M8; FR-ML).

The dependency-free (pure NumPy, **no** deep-learning framework) core behind the M8 deep
trace classifier (PRD §4.1 "PyTorch (deep, GPU) later"; ADR-0047 Option A). It turns
per-molecule windowed donor/acceptor intensity traces + their shared-store accept/reject
labels + cold-start weights into fixed-length, per-trace-normalized tensors ready for a
1-D CNN/LSTM ``DataLoader`` — **without** importing torch, so it lives in the base env and is
covered by the default 3-OS test matrix. The torch ``Dataset``/``DataLoader`` wrapper, the
model, and the training loop are the follow-up PR-1b in the isolated, optional ``deep/`` stack.

Design (ADR-0047):

* **Channels** default to the *measured* ``(donor, acceptor)`` background-corrected
  intensities — the DeepFRET non-ALEX input (DD + DA) [Thomsen2020]; a derived FRET-efficiency
  channel is a deliberate PR-1b extension (it is undefined where D + A ≈ 0, so it is not baked
  into the substrate to avoid emitting a fabricated value). Deep-LASI classifies the same
  intensity traces [Wanninger2023].
* **Normalization** default ``"per_trace_total"`` divides donor **and** acceptor by a single
  per-trace scale (the max total intensity D + A over the valid frames), so their relative
  magnitude — and hence the apparent-FRET ratio ``E = A/(D + A)`` — is preserved; an independent
  per-channel standardization would rescale the two channels by different factors and destroy
  that ratio (the Pearson donor–acceptor correlation is scale-free and survives either scheme, so
  it is *not* the distinguishing property). ``"none"`` leaves the raw intensities. Both are the
  PRD §11.2 "Deep-dataset preprocessing" tunable.
* **Fixed length** ``window_length``: a trace longer than the window is cropped to its leading
  frames (the pre-bleach, information-rich region), a shorter one is zero-padded; a boolean
  ``mask`` marks the real observed frames so a downstream model never treats padding as data
  (the never-fabricate discipline — padding is masked, not zero-filled-as-real).
* **Labels** are the shared-store binary accept(1)/reject(0) curation labels (``CurationLabel``,
  ADR-0023); the six-way DeepFRET taxonomy [Thomsen2020] needs the M4 category codec, which
  does not exist yet (ADR-0023 defers it) — so the substrate is binary, extensible later.

Pure and store-free: the store wrapper that reads a ``.tether`` is
:func:`tether.project.deep_dataset.build_deep_dataset`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DEFAULT_DEEP_CHANNELS",
    "DEFAULT_NORMALIZATION",
    "DEFAULT_SPLIT_SEED",
    "DEFAULT_VAL_FRACTION",
    "DEFAULT_WINDOW_LENGTH",
    "NORMALIZATIONS",
    "SUPPORTED_CHANNELS",
    "DeepTraceDataset",
    "assemble_dataset",
    "normalize_pair",
    "train_val_split",
]

#: The measured intensity channels the substrate can stack, in canonical order. The default is
#: both — the DeepFRET non-ALEX (DD + DA) input [Thomsen2020].
SUPPORTED_CHANNELS: tuple[str, ...] = ("donor", "acceptor")

#: Default stacked channels (PRD §11.2 "Deep-dataset preprocessing").
DEFAULT_DEEP_CHANNELS: tuple[str, ...] = ("donor", "acceptor")

#: Per-trace normalization methods (PRD §11.2). ``"per_trace_total"`` shares one scale across
#: donor + acceptor to preserve the apparent-FRET ratio ``E = A/(D + A)``; ``"none"`` is identity.
NORMALIZATIONS: tuple[str, ...] = ("per_trace_total", "none")
DEFAULT_NORMALIZATION = "per_trace_total"

#: Fixed model-input length (frames). Longer traces crop to the leading window, shorter ones
#: zero-pad + mask. ~500 covers typical TIRF smFRET trace lengths; a PRD §11.2 tunable, retuned
#: to the trained model in PR-1b.
DEFAULT_WINDOW_LENGTH = 500

#: Reproducible train/val split defaults (PRD §11.2; mirrors the ranker's ``random_state=0``).
DEFAULT_VAL_FRACTION = 0.2
DEFAULT_SPLIT_SEED = 0


# ``eq=False`` -> identity equality/hash (the RankingDataset / WeightedTrainingSet precedent):
# this value object holds numpy arrays a dataclass-generated ``__eq__``/``__hash__`` could not
# compare/hash.
@dataclass(frozen=True, eq=False)
class DeepTraceDataset:
    """Fixed-length, per-trace-normalized deep-classifier tensors + labels/weights.

    Every array is aligned on axis 0 (row ``i`` = ``molecule_ids[i]``).

    Attributes
    ----------
    molecule_ids:
        The unique ``molecule_id`` of each sample (the correct join key — a ``molecule_key`` can
        name several ids, §7.10).
    channels:
        The stacked channels in order — axis 1 of ``X`` (default ``("donor", "acceptor")``).
    X:
        ``(n_samples, n_channels, window_length)`` ``float32`` tensor. Padded frames are ``0.0``
        with ``mask`` ``False`` (a masked placeholder, never a fabricated observation).
    mask:
        ``(n_samples, window_length)`` ``bool`` — ``True`` on the real observed frames, ``False``
        on the padding a downstream model must ignore.
    lengths:
        ``(n_samples,)`` ``int64`` valid frame count of each sample (``min(native, window)``).
    y:
        ``(n_samples,)`` ``int8`` binary label — ``1`` = accept (good), ``0`` = reject
        (``CurationLabel`` mapped ``+1 -> 1``, ``-1 -> 0``; ADR-0023).
    sample_weight:
        ``(n_samples,)`` ``float64`` per-sample training weight — ``1.0`` for a human label, the
        decayed ``w₀/(1 + n_human)`` for a provisional prior (:mod:`tether.ml.weighting`).
    window_length, normalization, intensity_quantity:
        The self-describing build provenance (NFR-REPRO).
    """

    molecule_ids: list[str]
    channels: tuple[str, ...]
    X: np.ndarray
    mask: np.ndarray
    lengths: np.ndarray
    y: np.ndarray
    sample_weight: np.ndarray
    window_length: int
    normalization: str
    intensity_quantity: str

    @property
    def n_samples(self) -> int:
        """Number of molecules in the set — the shared axis-0 length of every array."""
        return len(self.molecule_ids)

    @property
    def n_channels(self) -> int:
        """Number of stacked intensity channels — axis 1 of ``X``."""
        return len(self.channels)

    @property
    def n_good(self) -> int:
        """Number of accept (good) samples."""
        return int(np.count_nonzero(self.y == 1))

    @property
    def n_bad(self) -> int:
        """Number of reject (bad) samples."""
        return int(np.count_nonzero(self.y == 0))

    def split(
        self,
        *,
        val_fraction: float = DEFAULT_VAL_FRACTION,
        seed: int = DEFAULT_SPLIT_SEED,
        stratify: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Reproducible ``(train_idx, val_idx)`` over these rows — see :func:`train_val_split`."""
        return train_val_split(self.y, val_fraction=val_fraction, seed=seed, stratify=stratify)


def normalize_pair(
    donor: np.ndarray, acceptor: np.ndarray, method: str = DEFAULT_NORMALIZATION
) -> tuple[np.ndarray, np.ndarray]:
    """Per-trace normalize a ``(donor, acceptor)`` pair, preserving the apparent-FRET ratio.

    ``"per_trace_total"`` divides **both** channels by one shared scale — the max of the total
    intensity ``donor + acceptor`` over the trace — so their relative magnitude, and hence the
    apparent FRET ``E = A/(D + A)``, is unchanged; an independent per-channel scaling would
    rescale the two channels by different factors and destroy that ratio. A degenerate trace whose
    finite total max is ``<= 0`` (or absent) is left unscaled (scale ``1.0``), never divided by
    zero. ``"none"`` is identity. Returns two ``float64`` copies (never mutates the inputs).
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    if method == "none":
        return donor.copy(), acceptor.copy()
    if method != "per_trace_total":
        raise ValueError(f"unknown normalization {method!r}; expected one of {NORMALIZATIONS}")
    if donor.size == 0:
        return donor.copy(), acceptor.copy()
    total = donor + acceptor
    finite = total[np.isfinite(total)]
    scale = float(finite.max()) if finite.size else 0.0
    if not np.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    return donor / scale, acceptor / scale


def _fit_length(series: np.ndarray, window_length: int) -> tuple[np.ndarray, int]:
    """Crop/zero-pad a 1-D series to ``window_length``; return ``(fitted float32, valid_len)``."""
    series = np.asarray(series, dtype=np.float64)
    n = int(series.shape[0])
    valid = min(n, window_length)
    out = np.zeros(window_length, dtype=np.float32)
    if valid > 0:
        out[:valid] = series[:valid].astype(np.float32)
    return out, valid


def assemble_dataset(
    molecule_ids: Sequence[str],
    donors: Sequence[np.ndarray],
    acceptors: Sequence[np.ndarray],
    y: np.ndarray,
    sample_weight: np.ndarray,
    *,
    window_length: int = DEFAULT_WINDOW_LENGTH,
    normalization: str = DEFAULT_NORMALIZATION,
    channels: Sequence[str] = DEFAULT_DEEP_CHANNELS,
    intensity_quantity: str = "corrected",
) -> DeepTraceDataset:
    """Build a :class:`DeepTraceDataset` from aligned per-molecule windowed traces + labels.

    All inputs are positionally aligned (row ``i`` everywhere = one molecule). ``donors`` /
    ``acceptors`` are variable-length 1-D arrays already sliced to each molecule's analysis
    window; each pair is per-trace normalized (shared scale, :func:`normalize_pair`), the
    requested ``channels`` stacked (axis 1), then cropped/zero-padded to ``window_length`` with a
    validity ``mask``. ``y`` is mapped to binary ``int8`` by ``accept = (label > 0)`` (a
    ``CurationLabel`` ``+1``/boolean ``True`` accept -> ``1``; ``-1``/``0``/``False`` -> ``0``).

    Raises
    ------
    ValueError
        Empty set, misaligned input lengths, a per-molecule donor/acceptor length mismatch, an
        unknown/duplicate channel, an unknown normalization, or a non-positive ``window_length``.
    """
    channels = tuple(channels)
    if not channels:
        raise ValueError(f"channels must be a non-empty subset of {SUPPORTED_CHANNELS}")
    unknown = [c for c in channels if c not in SUPPORTED_CHANNELS]
    if unknown:
        raise ValueError(f"unsupported channel(s) {unknown}; expected from {SUPPORTED_CHANNELS}")
    if len(set(channels)) != len(channels):
        raise ValueError(f"duplicate channel in {channels}")
    if normalization not in NORMALIZATIONS:
        raise ValueError(
            f"unknown normalization {normalization!r}; expected one of {NORMALIZATIONS}"
        )
    window_length = int(window_length)
    if window_length <= 0:
        raise ValueError(f"window_length must be positive, got {window_length}")

    ids = [str(m) for m in molecule_ids]
    donors = list(donors)
    acceptors = list(acceptors)
    y_arr = np.asarray(y)
    w_arr = np.asarray(sample_weight, dtype=np.float64)
    n = len(ids)
    if not (len(donors) == len(acceptors) == int(y_arr.shape[0]) == int(w_arr.shape[0]) == n):
        raise ValueError(
            "molecule_ids, donors, acceptors, y and sample_weight must be the same length"
        )
    if n == 0:
        raise ValueError("cannot assemble a deep dataset from zero molecules")

    # accept = (label > 0): CurationLabel +1 / bool True -> 1; -1 / 0 / False -> 0.
    y_int = (y_arr > 0).astype(np.int8)

    n_channels = len(channels)
    x = np.zeros((n, n_channels, window_length), dtype=np.float32)
    mask = np.zeros((n, window_length), dtype=bool)
    lengths = np.zeros(n, dtype=np.int64)
    for i in range(n):
        donor_i = np.asarray(donors[i], dtype=np.float64)
        acceptor_i = np.asarray(acceptors[i], dtype=np.float64)
        if donor_i.shape[0] != acceptor_i.shape[0]:
            raise ValueError(
                f"molecule {ids[i]!r}: donor length {donor_i.shape[0]} != acceptor length "
                f"{acceptor_i.shape[0]}"
            )
        donor_n, acceptor_n = normalize_pair(donor_i, acceptor_i, normalization)
        valid = 0
        for c, name in enumerate(channels):
            series = donor_n if name == "donor" else acceptor_n
            fitted, valid = _fit_length(series, window_length)
            x[i, c] = fitted
        lengths[i] = valid
        mask[i, :valid] = True

    return DeepTraceDataset(
        molecule_ids=ids,
        channels=channels,
        X=x,
        mask=mask,
        lengths=lengths,
        y=y_int,
        sample_weight=w_arr,
        window_length=window_length,
        normalization=normalization,
        intensity_quantity=str(intensity_quantity),
    )


def train_val_split(
    y: np.ndarray,
    *,
    val_fraction: float = DEFAULT_VAL_FRACTION,
    seed: int = DEFAULT_SPLIT_SEED,
    stratify: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Reproducible ``(train_idx, val_idx)`` row-index split, stratified by label by default.

    Deterministic for a given ``seed`` (``np.random.default_rng``). With ``stratify`` the split
    is taken **within** each class so the accept/reject ratio is preserved on both sides (each
    class contributes ``round(n_class * val_fraction)`` rows to validation, clamped so a class of
    ``>= 2`` rows never fully empties from either side; a singleton class stays in train). Returns
    two sorted ``int64`` index arrays that partition ``range(len(y))`` — disjoint and covering.

    Raises
    ------
    ValueError
        Empty ``y``, or ``val_fraction`` not in the open interval ``(0, 1)``.
    """
    y_arr = np.asarray(y)
    n = int(y_arr.shape[0])
    if n == 0:
        raise ValueError("cannot split an empty label array")
    if not (0.0 < float(val_fraction) < 1.0):
        raise ValueError(f"val_fraction must be in (0, 1), got {val_fraction}")

    rng = np.random.default_rng(seed)
    groups = (
        [np.nonzero(y_arr == cls)[0] for cls in np.unique(y_arr)] if stratify else [np.arange(n)]
    )
    val_parts: list[np.ndarray] = []
    for group in groups:
        shuffled = group.copy()
        rng.shuffle(shuffled)
        m = int(shuffled.shape[0])
        n_val = int(round(m * float(val_fraction)))
        n_val = max(1, min(n_val, m - 1)) if m >= 2 else 0
        val_parts.append(shuffled[:n_val])

    val_idx = np.sort(np.concatenate(val_parts)) if val_parts else np.array([], dtype=np.int64)
    val_mask = np.zeros(n, dtype=bool)
    val_mask[val_idx] = True
    train_idx = np.nonzero(~val_mask)[0]
    return train_idx.astype(np.int64), val_idx.astype(np.int64)
