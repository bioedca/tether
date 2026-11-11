# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Advisory check that the donor and acceptor halves are not swapped (#646, ADR-0070).

``--donor-side`` tells the extractor which half of a split frame is the donor. A wrong
value raises no error and inverts every FRET efficiency. This module reads the answer
back out of the extracted traces, using the one event whose direction tells the two
dyes apart: **the acceptor photobleaching first**.

Under donor excitation, when the acceptor bleaches the donor stops transferring energy,
so the acceptor signal steps **down** to background while the donor signal steps **up**
(the donor is no longer quenched). When the donor bleaches, the acceptor loses its only
excitation and both signals step down together. These are the steps the per-molecule
γ estimate is built on [Roy2008] [McCann2010] [Hellenkamp2018]; :mod:`tether.fret.gamma`
reads the same acceptor-bleach step.

The rule (signed off by the maintainer on #646)
-----------------------------------------------
For each molecule, the native single-step detector (:func:`tether.fret.photobleach.
detect_photobleach`) gives a first-bleach frame per channel. A molecule is an
**event** when one channel bleaches at least ``min_frames`` before the other, so the
other is still emitting after the step, and the other then bleaches too, at least
``min_frames`` before the trace ends. That is the whole bleach sequence the rule names:
a single step in one channel, then both signals at background. Requiring the second
bleach is what keeps a FRET transition out: a transition to a state where one channel is
near zero looks like a bleach step, but it is not followed by both signals reaching
background. The event **votes** when, across the first step,

* the channel that bleached first drops by at least ``step_z`` standard errors, and
* the other channel rises by at least ``step_z`` standard errors,

with each level the mean of up to ``level_frames`` frames on its side of the step and
the per-frame noise estimated robustly from first differences. The channel that drops
while the other rises behaved as the acceptor. A vote is *consistent* when that channel
is the one labelled acceptor and *swapped* when it is the one labelled donor.

Molecules whose two channels step down within ``min_frames`` of each other show the
donor-bleach pattern. Both labellings predict it, so they are counted
(``n_together``) and cast no vote. The count also takes in molecules whose acceptor
bleaches first but whose donor never reaches background in view: the single-step
detector places the donor's step *up* at the same frame. They cast no vote either.

With fewer than ``min_events`` votes the verdict is ``insufficient`` ("not enough
bleach events to check"). Otherwise it is ``swapped`` when at least ``majority`` of the
votes are swapped, ``consistent`` when at least ``majority`` are consistent, and
``mixed`` in between. A sample with low FRET or no acceptor gives few or no votes, so
it reads ``insufficient`` rather than a verdict either way.

The check is advisory. It reads the traces and never changes them, it never fails an
extraction, and the extraction pipeline records its result with the extraction
provenance (``/settings/extraction``). Its thresholds and the false-alarm rates measured
on the repository's fixtures are in ADR-0070 and PRD §11.2.

References
----------
[Roy2008] Roy, Hohng & Ha. "A practical guide to single-molecule FRET." Nature Methods
    5, 507-516 (2008). doi:10.1038/nmeth.1208
[McCann2010] McCann, Choi, Zheng, Weninger & Bowen. "Optimizing methods to recover
    absolute FRET efficiency from immobilized single molecules." Biophysical Journal
    99, 961-970 (2010). doi:10.1016/j.bpj.2010.04.063
[Hellenkamp2018] Hellenkamp et al. "Precision and accuracy of single-molecule FRET
    measurements - a multi-laboratory benchmark study." Nature Methods 15, 669-676
    (2018). doi:10.1038/s41592-018-0085-0
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

import numpy as np

from tether.fret.photobleach import (
    PB_PRIOR_A,
    PB_PRIOR_B,
    PB_PRIOR_BETA,
    PB_PRIOR_MU,
    detect_photobleach,
)

__all__ = [
    "EVENT_CONSISTENT",
    "EVENT_NONE",
    "EVENT_SWAPPED",
    "EVENT_TOGETHER",
    "EVENT_UNCLEAR",
    "SIDE_CHECK_LEVEL_FRAMES",
    "SIDE_CHECK_MAJORITY",
    "SIDE_CHECK_MIN_EVENTS",
    "SIDE_CHECK_MIN_FRAMES",
    "SIDE_CHECK_STEP_Z",
    "VERDICT_CONSISTENT",
    "VERDICT_INSUFFICIENT",
    "VERDICT_MIXED",
    "VERDICT_NOT_RUN",
    "VERDICT_SWAPPED",
    "DonorSideCheck",
    "check_donor_side",
    "classify_bleach_event",
    "side_check_verdict",
    "votes_needed",
]

# --- Tunables (PRD §11.2; measured in ADR-0070) --------------------------------
#: Fewest voting bleach events before the check gives a verdict.
SIDE_CHECK_MIN_EVENTS: int = 15
#: Fraction of the votes that must agree before the check calls the halves swapped
#: (or consistent).
SIDE_CHECK_MAJORITY: float = 0.8
#: Standard errors by which the first channel must drop, and the other rise, across the
#: step for the event to vote.
SIDE_CHECK_STEP_Z: float = 3.0
#: Frames each side of the step averaged for the two levels.
SIDE_CHECK_LEVEL_FRAMES: int = 10
#: Fewest frames before the step, and between the two channels' bleaches, for an event.
SIDE_CHECK_MIN_FRAMES: int = 5

# --- Per-molecule event kinds ----------------------------------------------------
EVENT_CONSISTENT = "consistent"  #: labelled acceptor dropped while the donor rose
EVENT_SWAPPED = "swapped"  #: labelled donor dropped while the acceptor rose
EVENT_TOGETHER = "together"  #: both channels stepped down together (donor bleach)
EVENT_UNCLEAR = "unclear"  #: one channel bleached first but the steps are not clear
EVENT_NONE = "none"  #: no usable bleach step (none within the trace, or too early)

# --- Movie-level verdicts ----------------------------------------------------------
VERDICT_CONSISTENT = "consistent"
VERDICT_SWAPPED = "swapped"
VERDICT_MIXED = "mixed"
VERDICT_INSUFFICIENT = "insufficient"
VERDICT_NOT_RUN = "not-run"


def _noise(segment: np.ndarray) -> float:
    """Robust per-frame noise: the MAD of first differences, scaled to a Gaussian σ.

    Differencing removes the step itself (it costs one large difference, which the
    median ignores), so the estimate is the frame-to-frame scatter on both sides.
    """
    diffs = np.diff(segment)
    if diffs.size < 2:
        return float("nan")
    mad = float(np.median(np.abs(diffs - np.median(diffs))))
    return 1.4826 * mad / np.sqrt(2.0)


def _z(change: float, sigma: float, n_pre: int, n_post: int) -> float:
    """Standard-error units of a change between two level means."""
    se = sigma * np.sqrt(1.0 / n_pre + 1.0 / n_post)
    if not np.isfinite(se):
        return float("nan")
    if se == 0.0:
        # A noiseless trace (synthetic): any positive change is unambiguous.
        return float("inf") if change > 0 else 0.0
    return float(change / se)


def classify_bleach_event(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    step_z: float = SIDE_CHECK_STEP_Z,
    level_frames: int = SIDE_CHECK_LEVEL_FRAMES,
    min_frames: int = SIDE_CHECK_MIN_FRAMES,
    a: float = PB_PRIOR_A,
    b: float = PB_PRIOR_B,
    beta: float = PB_PRIOR_BETA,
    mu: float = PB_PRIOR_MU,
) -> str:
    """Classify one molecule's first bleach step (one of the ``EVENT_*`` kinds).

    ``donor`` and ``acceptor`` are the molecule's background-subtracted traces, as
    labelled by the extraction. The photobleach priors default to the PRD §11.2 values.
    """
    donor = np.asarray(donor, dtype=np.float64)
    acceptor = np.asarray(acceptor, dtype=np.float64)
    if donor.shape != acceptor.shape or donor.ndim != 1:
        raise ValueError("donor and acceptor must be 1-D traces of equal length")
    n = donor.size
    if n < 2 * min_frames or not (np.all(np.isfinite(donor)) and np.all(np.isfinite(acceptor))):
        return EVENT_NONE

    res = detect_photobleach(donor, acceptor, a=a, b=b, beta=beta, mu=mu)
    first = min(res.donor_pb, res.acceptor_pb)
    second = max(res.donor_pb, res.acceptor_pb)
    if first >= n or first < min_frames:
        return EVENT_NONE
    if second - first < min_frames:
        # Both channels reach background together: the donor-bleach pattern.
        return EVENT_TOGETHER if second < n else EVENT_UNCLEAR
    if second > n - min_frames:
        # The channel still emitting never reaches background in view, so the step is not
        # followed by the second bleach that closes the sequence. A FRET transition near
        # the end of a trace looks like this; a bleach sequence does not.
        return EVENT_UNCLEAR

    acceptor_first = res.acceptor_pb < res.donor_pb
    dropped, other = (acceptor, donor) if acceptor_first else (donor, acceptor)
    lo = max(0, first - level_frames)
    hi = min(first + level_frames, second)
    n_pre, n_post = first - lo, hi - first
    z_drop = _z(
        float(dropped[lo:first].mean() - dropped[first:hi].mean()),
        _noise(dropped[lo:hi]),
        n_pre,
        n_post,
    )
    z_rise = _z(
        float(other[first:hi].mean() - other[lo:first].mean()),
        _noise(other[lo:hi]),
        n_pre,
        n_post,
    )
    if z_drop >= step_z and z_rise >= step_z:
        return EVENT_CONSISTENT if acceptor_first else EVENT_SWAPPED
    return EVENT_UNCLEAR


def votes_needed(n_events: int, majority: float) -> int:
    """Votes one side needs out of ``n_events`` to reach ``majority`` (rounded up)."""
    # The small tolerance keeps 0.8 * 20 from rounding up to 17 through float error.
    return math.ceil(majority * n_events - 1e-9)


def side_check_verdict(n_consistent: int, n_swapped: int, min_events: int, majority: float) -> str:
    """The movie-level verdict from the vote counts (the rule's last step)."""
    n_events = n_consistent + n_swapped
    if n_events < min_events:
        return VERDICT_INSUFFICIENT
    need = votes_needed(n_events, majority)
    if n_swapped >= need:
        return VERDICT_SWAPPED
    if n_consistent >= need:
        return VERDICT_CONSISTENT
    return VERDICT_MIXED


@dataclass(frozen=True)
class DonorSideCheck:
    """The outcome of :func:`check_donor_side` for one movie.

    Attributes
    ----------
    verdict
        One of ``consistent``, ``swapped``, ``mixed``, ``insufficient`` or ``not-run``.
    n_molecules
        Molecules examined.
    n_consistent, n_swapped
        Votes for the labels as extracted, and for swapped halves.
    n_together
        Molecules whose two channels stepped down together (no vote).
    min_events, majority, step_z, level_frames, min_frames
        The thresholds the verdict was reached with (recorded for provenance).
    reason
        Why the check did not run, for ``not-run``; empty otherwise.
    """

    verdict: str
    n_molecules: int = 0
    n_consistent: int = 0
    n_swapped: int = 0
    n_together: int = 0
    min_events: int = SIDE_CHECK_MIN_EVENTS
    majority: float = SIDE_CHECK_MAJORITY
    step_z: float = SIDE_CHECK_STEP_Z
    level_frames: int = SIDE_CHECK_LEVEL_FRAMES
    min_frames: int = SIDE_CHECK_MIN_FRAMES
    reason: str = ""

    @property
    def n_events(self) -> int:
        """Voting bleach events (consistent + swapped)."""
        return self.n_consistent + self.n_swapped

    @property
    def swapped(self) -> bool:
        """Whether the check warns that the halves look swapped."""
        return self.verdict == VERDICT_SWAPPED

    def to_dict(self) -> dict[str, object]:
        """A JSON-serialisable record for ``/settings/extraction`` (provenance)."""
        record: dict[str, object] = asdict(self)
        record["n_events"] = self.n_events
        return record

    @classmethod
    def from_dict(cls, record: dict[str, object]) -> DonorSideCheck:
        """Rebuild a check from :meth:`to_dict` output; unknown keys are ignored."""
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in record.items() if k in names})  # type: ignore[arg-type]

    def describe(self, *, donor_side: str = "left", imported_map: bool = False) -> str:
        """One plain sentence for the operator, with the evidence and the remedy.

        ``donor_side`` is the side the movie was extracted with; ``imported_map`` says
        the split came from an imported ``.tmap``, where ``--donor-side`` does not apply.
        """
        if self.verdict == VERDICT_NOT_RUN:
            return f"the donor-side check did not run ({self.reason})"
        if self.verdict == VERDICT_INSUFFICIENT:
            return (
                f"not enough bleach events to check the donor side "
                f"({self.n_events} found, {self.min_events} needed)"
            )
        evidence = f"{self.n_swapped} of {self.n_events} bleach events"
        if self.verdict == VERDICT_SWAPPED:
            if imported_map:
                remedy = (
                    "check the channel order of the .tmap, or extract again without --tmap "
                    "and with the right --donor-side"
                )
            else:
                other = "right" if donor_side == "left" else "left"
                remedy = f"extract this movie again with --donor-side {other}"
            return f"{evidence} are consistent with swapped donor and acceptor halves; {remedy}"
        if self.verdict == VERDICT_MIXED:
            return (
                f"{evidence} are consistent with swapped halves, which is not a clear "
                "majority either way; check one acceptor-bleach trace by eye"
            )
        return (
            f"{self.n_consistent} of {self.n_events} bleach events agree with the donor side "
            "as extracted"
        )


def check_donor_side(
    donor: np.ndarray,
    acceptor: np.ndarray,
    *,
    min_events: int = SIDE_CHECK_MIN_EVENTS,
    majority: float = SIDE_CHECK_MAJORITY,
    step_z: float = SIDE_CHECK_STEP_Z,
    level_frames: int = SIDE_CHECK_LEVEL_FRAMES,
    min_frames: int = SIDE_CHECK_MIN_FRAMES,
    a: float = PB_PRIOR_A,
    b: float = PB_PRIOR_B,
    beta: float = PB_PRIOR_BETA,
    mu: float = PB_PRIOR_MU,
) -> DonorSideCheck:
    """Check one movie's traces for swapped donor and acceptor halves.

    Parameters
    ----------
    donor, acceptor
        ``(n_molecules, n_frames)`` background-subtracted traces, row-aligned, as the
        extraction labelled them. They are read, never modified.
    min_events, majority, step_z, level_frames, min_frames
        The rule's thresholds (PRD §11.2, ADR-0070); see the module docstring.
    a, b, beta, mu
        Priors of the single-step photobleach detector (PRD §11.2 defaults).

    Returns
    -------
    DonorSideCheck
        The verdict and the counts behind it.
    """
    if min_events < 1:
        raise ValueError(f"min_events must be >= 1, got {min_events}")
    if not 0.5 < majority <= 1.0:
        raise ValueError(f"majority must be in (0.5, 1], got {majority}")
    if not step_z > 0:
        raise ValueError(f"step_z must be > 0, got {step_z}")
    if min_frames < 2 or level_frames < min_frames:
        raise ValueError(
            "frames must satisfy 2 <= min_frames <= level_frames, got "
            f"min_frames={min_frames}, level_frames={level_frames}"
        )
    donor = np.atleast_2d(np.asarray(donor, dtype=np.float64))
    acceptor = np.atleast_2d(np.asarray(acceptor, dtype=np.float64))
    if donor.shape != acceptor.shape:
        raise ValueError(f"donor/acceptor shapes differ: {donor.shape} vs {acceptor.shape}")

    counts = {EVENT_CONSISTENT: 0, EVENT_SWAPPED: 0, EVENT_TOGETHER: 0}
    n_molecules = 0 if donor.size == 0 else donor.shape[0]
    for i in range(n_molecules):
        kind = classify_bleach_event(
            donor[i],
            acceptor[i],
            step_z=step_z,
            level_frames=level_frames,
            min_frames=min_frames,
            a=a,
            b=b,
            beta=beta,
            mu=mu,
        )
        if kind in counts:
            counts[kind] += 1

    n_consistent, n_swapped = counts[EVENT_CONSISTENT], counts[EVENT_SWAPPED]
    return DonorSideCheck(
        verdict=side_check_verdict(n_consistent, n_swapped, min_events, majority),
        n_molecules=n_molecules,
        n_consistent=n_consistent,
        n_swapped=n_swapped,
        n_together=counts[EVENT_TOGETHER],
        min_events=min_events,
        majority=majority,
        step_z=step_z,
        level_frames=level_frames,
        min_frames=min_frames,
    )
