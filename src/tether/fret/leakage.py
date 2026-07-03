# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Donor→acceptor leakage α from the post-acceptor-bleach tail (PRD §7.2, Appendix B.2).

The leakage (bleedthrough) factor **α** corrects the acceptor channel for donor
emission that spills into it through the emission filters — the first
photophysical correction in the accurate-FRET procedure
(``background → leakage α → direct-excitation δ(=0) → γ``; PRD §7.4, Appendix B).
It is applied additively,

    I_A,corr = I_A* − α · I_D*,

on the background-subtracted (``corrected``) intensities [Lee2005][Hellenkamp2018].

Estimating α needs a *donor-only* condition — frames where the donor emits but the
acceptor does not, so any acceptor-channel signal is pure leakage. A dedicated
Cy3-only sample is one source; the other, always available from the FRET data
itself, is the **post-acceptor-bleach tail**: once a molecule's acceptor
photobleaches (but before its donor bleaches), the trace *is* a per-molecule
donor-only measurement. This module implements that tail estimator, which needs no
separate calibration acquisition and rides directly on the per-channel bleach
frames from :mod:`tether.fret.photobleach`.

Definitions and gates (PRD §11.2)
---------------------------------
* **Per-trace α** over the tail follows Deep-LASI's crosstalk definition
  ``ct = mean(I_DA) / mean(I_DD)`` (PRD §11.1 / ``deeplasi …/manualCorrectionFactors.m``):
  the ratio of the mean acceptor-channel (leakage) to mean donor-channel (emission)
  intensity across the tail. A ratio of window means, not a mean of per-frame
  ratios, so a near-zero donor frame cannot blow the estimate up.
* **Window-length gate** ``min_window_frames`` (default 20): a tail shorter than
  this is rejected — too few frames to average leakage reliably.
* **Acceptance ceiling** ``LEAKAGE_CEILING`` (≈ 0.3): a per-trace α outside
  ``[0, ceiling]`` is non-physical (leakage cannot be negative) or implausibly high
  (Cy3→Cy5 leakage is typically 0.05–0.2, empirical median ≈ 0.09) and is dropped —
  a tightening of Deep-LASI's loose ``ct_lim = 1``.
* **Dataset aggregate** = the **median** of the qualifying per-trace α values — a
  single per-condition leakage factor (leakage is an instrument/dye property shared
  across molecules), robust to per-trace outliers. Withheld (``None``) when fewer
  than ``min_qualifying_traces`` (default ≈ 10) molecules yield a valid estimate,
  rather than emitting a factor from too little data (PRD §7.2 total-failure path).

``min_window_frames`` (a per-trace bleach-window minimum) and
``min_qualifying_traces`` (a per-dataset minimum) are distinct quantities and are
kept as separate named parameters (PRD §11.2).

References
----------
[Lee2005] Lee, Kapanidis, Wang, Michalet, Mukhopadhyay, Ebright & Weiss.
    "Accurate FRET measurements within single diffusing biomolecules using
    alternating-laser excitation." Biophysical Journal (2005).
[Hellenkamp2018] Hellenkamp et al. "Precision and accuracy of single-molecule
    FRET measurements — a multi-laboratory benchmark study." Nature Methods (2018).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "DEFAULT_MIN_QUALIFYING_TRACES",
    "DEFAULT_MIN_WINDOW_FRAMES",
    "LEAKAGE_CEILING",
    "LeakageEstimate",
    "TailAlpha",
    "apply_leakage",
    "estimate_leakage_alpha",
    "tail_alpha",
    "tail_window",
]

#: Leakage acceptance ceiling (PRD §11.2): Cy3→Cy5 leakage is typically 0.05–0.2
#: (empirical median ≈ 0.09); a per-trace α outside ``[0, LEAKAGE_CEILING]`` is
#: rejected. Tether tightening of Deep-LASI's loose ``ct_lim = 1``.
LEAKAGE_CEILING: float = 0.3

#: Per-trace tail-window minimum (PRD §11.2): a post-acceptor-bleach tail shorter
#: than this many frames is too short to average leakage from and is rejected.
DEFAULT_MIN_WINDOW_FRAMES: int = 20

#: Per-dataset minimum (PRD §11.2): fewer than this many qualifying per-trace
#: estimates → withhold the dataset α rather than emit one from too little data.
DEFAULT_MIN_QUALIFYING_TRACES: int = 10


@dataclass(frozen=True)
class TailAlpha:
    """A single molecule's post-acceptor-bleach-tail leakage estimate.

    Attributes
    ----------
    alpha
        The per-trace leakage factor ``mean(I_DA) / mean(I_DD)`` over the tail, or
        ``None`` when no valid estimate exists (see ``reason``).
    start, stop
        The half-open tail frame range ``[start, stop)`` (0-based within the
        supplied trace) — the donor-only window between acceptor and donor bleach.
    n_frames
        Tail length ``stop - start``.
    reason
        Why the estimate is/ isn't valid: ``"ok"``; ``"no-tail"`` (acceptor does
        not bleach before the donor); ``"short-tail"`` (tail < ``min_window_frames``);
        ``"degenerate-donor"`` (non-positive mean donor emission in the tail);
        ``"out-of-range"`` (α outside ``[0, ceiling]``).
    """

    alpha: float | None
    start: int
    stop: int
    n_frames: int
    reason: str

    @property
    def qualifies(self) -> bool:
        """``True`` iff this trace contributes a valid α to the dataset aggregate."""
        return self.alpha is not None


@dataclass(frozen=True)
class LeakageEstimate:
    """Dataset-level leakage α aggregated from per-trace tail estimates.

    Attributes
    ----------
    alpha
        The per-condition leakage factor = median of the qualifying per-trace α, or
        ``None`` when fewer than ``min_qualifying_traces`` traces qualified (the
        factor is withheld, never fabricated).
    n_qualifying
        How many traces yielded a valid per-trace estimate.
    n_traces
        How many traces were examined.
    per_trace
        The per-trace :class:`TailAlpha` for every examined trace, in input order
        (diagnostics; qualifying and rejected alike).
    """

    alpha: float | None
    n_qualifying: int
    n_traces: int
    per_trace: tuple[TailAlpha, ...]


def tail_window(acceptor_pb: int, donor_pb: int, n_frames: int) -> tuple[int, int]:
    """Half-open donor-only tail ``[acceptor_pb, min(donor_pb, n_frames))``.

    The post-acceptor-bleach tail is the run of frames where the acceptor has
    bleached (``>= acceptor_pb``) but the donor is still emitting (``< donor_pb``).
    Returns ``(start, stop)`` with ``stop <= start`` when there is no such run
    (acceptor bleaches at/after the donor, or does not bleach within the trace).
    """
    start = int(acceptor_pb)
    stop = min(int(donor_pb), int(n_frames))
    return start, stop


def tail_alpha(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    acceptor_pb: int,
    donor_pb: int,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = LEAKAGE_CEILING,
) -> TailAlpha:
    """Estimate one trace's leakage α from its post-acceptor-bleach tail.

    Parameters
    ----------
    donor, acceptor
        Matching 1-D background-subtracted donor and acceptor channel intensities.
    acceptor_pb, donor_pb
        Per-channel first-bleach frames (0-based within these arrays;
        ``== donor.size`` when a channel does not bleach), e.g. from
        :func:`tether.fret.photobleach.detect_photobleach`.
    min_window_frames
        Reject a tail shorter than this (PRD §11.2, default 20).
    ceiling
        Reject a per-trace α outside ``[0, ceiling]`` (PRD §11.2, default 0.3).

    Returns
    -------
    TailAlpha
        The estimate and its ``reason``; ``alpha is None`` when no valid tail.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    if donor.shape != acceptor.shape:
        raise ValueError("donor and acceptor traces must have the same shape")
    if donor.ndim != 1:
        raise ValueError("tail_alpha expects 1-D donor/acceptor traces")

    n = int(donor.size)
    start, stop = tail_window(acceptor_pb, donor_pb, n)
    n_tail = stop - start
    if n_tail <= 0:
        return TailAlpha(None, start, max(stop, start), 0, "no-tail")
    if n_tail < int(min_window_frames):
        return TailAlpha(None, start, stop, n_tail, "short-tail")

    mean_donor = float(np.mean(donor[start:stop]))
    if mean_donor <= 0.0:
        return TailAlpha(None, start, stop, n_tail, "degenerate-donor")
    mean_acceptor = float(np.mean(acceptor[start:stop]))
    alpha = mean_acceptor / mean_donor
    if not (0.0 <= alpha <= float(ceiling)):
        return TailAlpha(None, start, stop, n_tail, "out-of-range")
    return TailAlpha(alpha, start, stop, n_tail, "ok")


def estimate_leakage_alpha(
    donor_traces: list[np.ndarray],
    acceptor_traces: list[np.ndarray],
    acceptor_pbs: list[int],
    donor_pbs: list[int],
    *,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = LEAKAGE_CEILING,
    min_qualifying_traces: int = DEFAULT_MIN_QUALIFYING_TRACES,
) -> LeakageEstimate:
    """Aggregate a dataset's leakage α from every trace's post-bleach tail.

    Computes a per-trace :func:`tail_alpha` for each molecule, then the dataset
    factor as the **median** of the qualifying values. The factor is **withheld**
    (``alpha is None``) when fewer than ``min_qualifying_traces`` qualify — a
    per-condition leakage α is only as trustworthy as the number of donor-only tails
    behind it (PRD §7.2), and a fabricated one would silently bias every corrected E.

    All four per-trace sequences must be the same length (one entry per molecule).
    """
    n = len(donor_traces)
    if not (len(acceptor_traces) == len(acceptor_pbs) == len(donor_pbs) == n):
        raise ValueError(
            "donor_traces, acceptor_traces, acceptor_pbs, donor_pbs must be the same length"
        )

    per_trace = tuple(
        tail_alpha(
            donor_traces[i],
            acceptor_traces[i],
            acceptor_pb=acceptor_pbs[i],
            donor_pb=donor_pbs[i],
            min_window_frames=min_window_frames,
            ceiling=ceiling,
        )
        for i in range(n)
    )
    qualifying = [t.alpha for t in per_trace if t.alpha is not None]
    n_qualifying = len(qualifying)
    alpha = float(np.median(qualifying)) if n_qualifying >= int(min_qualifying_traces) else None
    return LeakageEstimate(
        alpha=alpha,
        n_qualifying=n_qualifying,
        n_traces=n,
        per_trace=per_trace,
    )


def apply_leakage(donor: np.ndarray, acceptor: np.ndarray, alpha: float) -> np.ndarray:
    """Return the leakage-corrected acceptor intensity ``I_A − α · I_D``.

    The additive donor-leakage correction (PRD Appendix B.2 step 2). ``donor`` and
    ``acceptor`` broadcast against each other; the result is ``float64``. No
    clipping is applied — a slightly negative corrected value on a noisy frame is a
    real fluctuation, and hiding it would distort downstream E.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    return acceptor - float(alpha) * donor
