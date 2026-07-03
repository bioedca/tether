# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Detection-correction factor γ across the acceptor-bleach step (PRD §7.4, Appendix B.2).

The **γ factor** is the fourth and last step of the accurate-FRET procedure
(``background → leakage α → direct-excitation δ(=0) → γ``; PRD §7.4, Appendix B.2).
It normalises the imbalance between the donor and acceptor dyes in their quantum
yield and detection efficiency, turning the leakage-corrected proximity ratio into
an absolute efficiency ``E = I_A,corr / (I_A,corr + γ · I_D,corr)``
[McCann2010][Hellenkamp2018].

The most effective, per-molecule route to γ is single-molecule **photobleaching**
[McCann2010]: at the frame the **acceptor** photobleaches (but before the donor
does), the acceptor intensity **drops** (its FRET-driven signal vanishes) while the
donor intensity **rises** (it is no longer quenched by energy transfer). γ is the
ratio of those two jumps across that one step,

    γ = (I_A,spFRET − I_A,after) / (I_D,after − I_D,spFRET) = ΔI_A / ΔI_D,

measured on **leakage-corrected** intensities over a short tolerance window each
side of the step (PRD Appendix B.2 step 4;
``deeplasi/functions/deeplearning/deep_autocorrect_2color.m:118-130``). Tether drops
Deep-LASI's ALEX ``de·(da+dd)`` direct-excitation term (δ = 0, no ALEX).

Donor convention (why bare ``I_D``, not Deep-LASI's ``I_D·(1+α)``)
-----------------------------------------------------------------
Deep-LASI scales the donor by ``(1 + ct)`` to add the leaked photons back to the
donor budget. Tether's additive scheme (PRD Appendix B.2) instead defines the
corrected donor as the *bare* background-subtracted donor ``I_D,corr = I_D`` — the
leakage subtraction only removes donor-leaked photons *from the acceptor*
(``I_A,corr = I_A − α·I_D``) and does not add them back to the donor. For the final
``E`` to be correct, γ must be defined consistently with that ``I_D,corr``, so γ
here divides by the **bare** ``ΔI_D`` (PRD line 1364 writes ``I_D`` with no
``(1+α)`` factor). Each convention is internally self-consistent — the ``(1+α)``
cancels between a group's γ definition and its ``E`` — so Tether's γ is systematically
``≈ (1 + α)`` times Deep-LASI's on the same step (α ≈ 0.09 ⇒ ~9 %). A comparison to
the Deep-LASI median must control for this convention difference, not just the frame
selection (ADR-0028).

Definitions and gates (PRD §11.2)
---------------------------------
* **Per-trace γ** = ``ΔI_A / ΔI_D`` with the intensity **levels** each side of the
  step averaged over a **3-frame half-window** (``half_window``, §11.2 row 814):
  the pre-step level over ``[step − half_window, step)`` and the post-step level over
  ``[step, step + half_window)``. Averaging localises the estimate to the step, where
  the donor/acceptor jumps are cleanest [McCann2010].
* **Segment gate** ``min_window_frames`` (default 20, §11.2 row 819): **both** the
  pre-step FRET segment ``[0, step)`` and the post-step donor-only segment
  ``[step, donor_bleach)`` must be **strictly longer** than ``min_window_frames`` —
  a well-separated step with enough stable signal on each side (Deep-LASI's
  ``length(spFRET_frames) > min_frames && length(da_acc_bleached) > min_frames``,
  ``deep_autocorrect_2color.m:129``). This is the *same* §11.2 quantity the leakage
  tail uses, kept as one named parameter (:mod:`tether.fret.leakage`).
* **Acceptance ceiling** ``GAMMA_CEILING`` (= 5, §11.2 row 818): a per-trace γ
  outside ``(0, GAMMA_CEILING]`` is non-physical (a negative or zero ratio, or an
  implausibly large one) and is dropped (Deep-LASI ``gamma > 0 && gamma <= γ_lim``).
* **Dataset aggregate** = the **median** of the qualifying per-trace γ (a robust
  population factor, [McCann2010]), withheld (``None``) below
  ``min_qualifying_traces`` (default 10, §11.2 row 820) so a factor is never emitted
  from too little data (PRD §7.2 total-failure path).

Unlike leakage α (one per-condition factor applied to every molecule), γ is a
**per-molecule** quantity with a **population-median fallback**: a qualifying
molecule keeps its own γ; a molecule that fails the gates takes the dataset median
(Deep-LASI ``isnan_corr(gamma(i), median(gamma))``, ``:144``). That per-molecule vs
fallback split is what the later staleness scope keys on — a γ-median shift re-stales
only the fallback molecules (PRD §5.1, §7.2). :func:`estimate_gamma` reports both the
per-trace estimates and the dataset median so the store writer can apply that split.

References
----------
[McCann2010] McCann, Choi, Zheng, Bahlke, Zhu, Nienhaus, Schuler & Weiss.
    "Optimizing methods to recover absolute FRET efficiency from immobilized single
    molecules." Biophysical Journal (2010).
[Hellenkamp2018] Hellenkamp et al. "Precision and accuracy of single-molecule FRET
    measurements — a multi-laboratory benchmark study." Nature Methods (2018).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tether.fret.leakage import (
    DEFAULT_MIN_QUALIFYING_TRACES,
    DEFAULT_MIN_WINDOW_FRAMES,
    apply_leakage,
)

__all__ = [
    "DEFAULT_GAMMA_HALF_WINDOW",
    "DEFAULT_MIN_QUALIFYING_TRACES",
    "DEFAULT_MIN_WINDOW_FRAMES",
    "GAMMA_CEILING",
    "GammaEstimate",
    "TraceGamma",
    "estimate_gamma",
    "gamma_windows",
    "trace_gamma",
]

#: γ acceptance ceiling (PRD §11.2 row 818): a per-trace γ outside ``(0, GAMMA_CEILING]``
#: is non-physical and rejected (Deep-LASI ``γ_lim`` from ``createTracesPlotLayout.m:172``).
GAMMA_CEILING: float = 5.0

#: Correction tolerance half-window (PRD §11.2 row 814): the intensity level each side
#: of the acceptor-bleach step is the mean over this many frames (3 each side, McCann2010).
DEFAULT_GAMMA_HALF_WINDOW: int = 3


@dataclass(frozen=True)
class TraceGamma:
    """A single molecule's γ estimate across its acceptor-bleach step.

    Attributes
    ----------
    gamma
        The per-trace factor ``ΔI_A / ΔI_D``, or ``None`` when no valid estimate
        exists (see ``reason``).
    step
        The acceptor first-bleach frame (0-based within the supplied trace) — the
        step γ is measured across.
    pre_window, post_window
        The half-open ``[start, stop)`` frame ranges the pre-step (FRET) and
        post-step (donor-only) intensity **levels** are averaged over — the 3-frame
        tolerance windows straddling ``step``.
    n_pre, n_post
        Lengths of the pre-step FRET segment ``[0, step)`` and the post-step
        donor-only segment ``[step, donor_bleach)`` — the segments the
        ``min_window_frames`` gate is applied to (not the 3-frame level windows).
    reason
        Why the estimate is/ isn't valid: ``"ok"``; ``"no-step"`` (acceptor does not
        bleach before the donor); ``"short-pre"`` / ``"short-post"`` (a segment is
        not longer than ``min_window_frames``); ``"degenerate-donor"`` (donor does
        not rise across the step, ``ΔI_D <= 0``); ``"out-of-range"`` (γ outside
        ``(0, ceiling]``).
    """

    gamma: float | None
    step: int
    pre_window: tuple[int, int]
    post_window: tuple[int, int]
    n_pre: int
    n_post: int
    reason: str

    @property
    def qualifies(self) -> bool:
        """``True`` iff this trace contributes a valid γ to the dataset aggregate."""
        return self.gamma is not None


@dataclass(frozen=True)
class GammaEstimate:
    """Dataset-level γ aggregated from per-trace acceptor-bleach-step estimates.

    Attributes
    ----------
    gamma
        The dataset factor = median of the qualifying per-trace γ, or ``None`` when
        fewer than ``min_qualifying_traces`` qualified (withheld, never fabricated).
        This median is also the **fallback** value for molecules that did not qualify.
    n_qualifying
        How many traces yielded a valid per-trace estimate.
    n_traces
        How many traces were examined.
    per_trace
        The per-trace :class:`TraceGamma` for every examined trace, in input order
        (qualifying and rejected alike).
    """

    gamma: float | None
    n_qualifying: int
    n_traces: int
    per_trace: tuple[TraceGamma, ...]

    def effective_gamma(self, index: int) -> float | None:
        """Return molecule ``index``'s applied γ: its own value, or the median fallback.

        A qualifying molecule keeps its per-trace γ; a molecule that failed the gates
        takes the dataset median (the population-median fallback, PRD §5.1 / §7.2).
        Returns ``None`` when the dataset γ was withheld (nothing to apply).
        """
        if self.gamma is None:
            return None
        own = self.per_trace[index].gamma
        return own if own is not None else self.gamma

    def is_fallback(self, index: int) -> bool:
        """``True`` iff molecule ``index`` took the median fallback (did not qualify).

        Meaningful only when the dataset γ was applied (``gamma is not None``); the
        later staleness scope re-stales exactly these molecules on a γ-median shift.
        """
        return self.gamma is not None and self.per_trace[index].gamma is None


def gamma_windows(
    step: int, half_window: int, n_frames: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Half-open 3-frame level windows straddling the acceptor-bleach ``step``.

    Returns ``((pre_start, pre_stop), (post_start, post_stop))`` — the pre-step level
    over ``[step − half_window, step)`` and the post-step level over
    ``[step, step + half_window)``, each clamped to ``[0, n_frames]``.
    """
    step = int(step)
    hw = int(half_window)
    pre = (max(step - hw, 0), max(min(step, n_frames), 0))
    post = (min(step, n_frames), min(step + hw, n_frames))
    return pre, post


def trace_gamma(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    alpha: float,
    acceptor_pb: int,
    donor_pb: int,
    half_window: int = DEFAULT_GAMMA_HALF_WINDOW,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = GAMMA_CEILING,
) -> TraceGamma:
    """Estimate one trace's γ across its acceptor-bleach step.

    Parameters
    ----------
    donor, acceptor
        Matching 1-D background-subtracted donor and acceptor channel intensities.
    alpha
        The applied leakage factor; the acceptor is leakage-corrected
        ``I_A,corr = I_A − alpha·I_D`` before the step jumps are measured.
    acceptor_pb, donor_pb
        Per-channel first-bleach frames (0-based within these arrays;
        ``== donor.size`` when a channel does not bleach), e.g. from
        :func:`tether.fret.photobleach.detect_photobleach`. γ needs the acceptor to
        bleach **before** the donor.
    half_window
        Level tolerance half-window each side of the step (PRD §11.2, default 3).
    min_window_frames
        Reject unless both the pre-step FRET segment and the post-step donor-only
        segment are longer than this (PRD §11.2, default 20).
    ceiling
        Reject a per-trace γ outside ``(0, ceiling]`` (PRD §11.2, default 5).

    Returns
    -------
    TraceGamma
        The estimate and its ``reason``; ``gamma is None`` when no valid step.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    if donor.shape != acceptor.shape:
        raise ValueError("donor and acceptor traces must have the same shape")
    if donor.ndim != 1:
        raise ValueError("trace_gamma expects 1-D donor/acceptor traces")

    n = int(donor.size)
    step = int(acceptor_pb)
    donor_stop = min(int(donor_pb), n)
    pre, post = gamma_windows(step, half_window, n)
    # Keep the post-level window inside the donor-active region [step, donor_stop): a
    # large (configurable, §11.2) half_window must never average post-donor-bleach
    # frames — where the donor is dark — into the level and corrupt γ. (The max guard
    # keeps start <= stop for the gate-failure diagnostic returns where donor_stop <= step.)
    post = (post[0], max(post[0], min(post[1], donor_stop)))
    n_pre = step  # length of the pre-step FRET segment [0, step)
    n_post = max(donor_stop - step, 0)  # length of the post-step donor-only segment

    # Segment gates (Deep-LASI deep_autocorrect_2color.m:129: strictly `> min_frames`
    # on both the spFRET and the acceptor-bleached segment). A "step" that isn't a
    # real acceptor-before-donor bleach, or that lacks a long stable side, yields no γ.
    if step <= 0 or step >= donor_stop:
        return TraceGamma(None, step, pre, post, n_pre, n_post, "no-step")
    if n_pre <= int(min_window_frames):
        return TraceGamma(None, step, pre, post, n_pre, n_post, "short-pre")
    if n_post <= int(min_window_frames):
        return TraceGamma(None, step, pre, post, n_pre, n_post, "short-post")

    corrected_acceptor = apply_leakage(donor, acceptor, alpha)
    ia_pre = float(np.mean(corrected_acceptor[pre[0] : pre[1]]))
    ia_post = float(np.mean(corrected_acceptor[post[0] : post[1]]))
    id_pre = float(np.mean(donor[pre[0] : pre[1]]))
    id_post = float(np.mean(donor[post[0] : post[1]]))

    da_delta = ia_pre - ia_post  # acceptor drops across the step
    dd_delta = id_post - id_pre  # donor rises (dequenched) across the step
    if dd_delta <= 0.0:
        # The donor did not rise — no measurable dequenching, so γ = ΔI_A/ΔI_D is
        # undefined (division by a non-positive jump). Gated out rather than emitting
        # a negative/infinite factor.
        return TraceGamma(None, step, pre, post, n_pre, n_post, "degenerate-donor")

    gamma = da_delta / dd_delta
    if not (0.0 < gamma <= float(ceiling)):
        return TraceGamma(None, step, pre, post, n_pre, n_post, "out-of-range")
    return TraceGamma(gamma, step, pre, post, n_pre, n_post, "ok")


def estimate_gamma(
    donor_traces: list[np.ndarray],
    acceptor_traces: list[np.ndarray],
    alphas: list[float],
    acceptor_pbs: list[int],
    donor_pbs: list[int],
    *,
    half_window: int = DEFAULT_GAMMA_HALF_WINDOW,
    min_window_frames: int = DEFAULT_MIN_WINDOW_FRAMES,
    ceiling: float = GAMMA_CEILING,
    min_qualifying_traces: int = DEFAULT_MIN_QUALIFYING_TRACES,
) -> GammaEstimate:
    """Aggregate a dataset's γ from every trace's acceptor-bleach step.

    Computes a per-trace :func:`trace_gamma` for each molecule, then the dataset
    factor as the **median** of the qualifying values. The median is withheld
    (``gamma is None``) below ``min_qualifying_traces`` — a population γ is only as
    trustworthy as the number of clean acceptor-bleach steps behind it (PRD §7.2),
    and a fabricated one would silently bias every corrected E. Qualifying molecules
    keep their own γ; the rest take this median as a fallback (see
    :meth:`GammaEstimate.effective_gamma`).

    All five per-trace sequences must be the same length (one entry per molecule).
    """
    n = len(donor_traces)
    if not (len(acceptor_traces) == len(alphas) == len(acceptor_pbs) == len(donor_pbs) == n):
        raise ValueError(
            "donor_traces, acceptor_traces, alphas, acceptor_pbs, donor_pbs must be the same length"
        )

    per_trace = tuple(
        trace_gamma(
            donor_traces[i],
            acceptor_traces[i],
            alpha=alphas[i],
            acceptor_pb=acceptor_pbs[i],
            donor_pb=donor_pbs[i],
            half_window=half_window,
            min_window_frames=min_window_frames,
            ceiling=ceiling,
        )
        for i in range(n)
    )
    qualifying = [t.gamma for t in per_trace if t.gamma is not None]
    n_qualifying = len(qualifying)
    # ``qualifying and`` guards ``np.median([])`` (→ nan + RuntimeWarning) when the
    # set is empty — including a degenerate ``min_qualifying_traces <= 0`` a caller
    # could pass, where ``n_qualifying >= min`` alone would be vacuously true.
    gamma = (
        float(np.median(qualifying))
        if qualifying and n_qualifying >= int(min_qualifying_traces)
        else None
    )
    return GammaEstimate(
        gamma=gamma,
        n_qualifying=n_qualifying,
        n_traces=n,
        per_trace=per_trace,
    )
