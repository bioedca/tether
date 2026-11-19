# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The conjunctive leakage-α validation band of PRD §7.2 and §11.2 (#546, ADR-0133).

Tether has two estimators of the leakage factor α. The **tail** estimator
(:func:`tether.fret.leakage.estimate_leakage_alpha`, ADR-0027) reads each FRET trace after its
acceptor bleaches and before its donor does. The **donor-only** estimator reads a sample with no
acceptor: there every frame before the donor bleaches is a donor-only frame, so it is the same
estimator with the tail starting at frame 0. Both use the same gates (``min_window_frames``,
``LEAKAGE_CEILING``, ``min_qualifying_traces``) and the same ratio of window means, which is
what the PRD calls *matched gates*.

The oracle compares the two population medians. It passes only when **both** hold:

* the relative difference ``|α_donor-only − α_tail| / mean(α_donor-only, α_tail)`` is at most
  :data:`MAX_RELATIVE_DIFFERENCE`, and
* each median lies in :data:`PLAUSIBLE_BAND`.

The band is a plausibility check and never a pass on its own (PRD §7.2): two estimates in the
band that disagree by more than the limit fail.

The data (ADR-0133). The donor-only sample is the maintainer's Cy3-only calibration acquisition,
``donly-01`` (ADR-0027 describes it). Its label names the construct, replicate and concentration of
the FRET series ``lab-03`` to ``lab-08``, recorded on the same setup, so the tail α is the median
over the pooled molecules of :data:`SERIES_IDS` (α is one factor per condition, PRD §7.2). Both
are read from the saved Deep-LASI state with the test-only reader of
:mod:`_deeplasi_public` (PRD §2.2 rule 2), and the photobleaching frames are Tether's own
(:func:`tether.fret.photobleach.detect_photobleach`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = [
    "DONOR_ONLY_ID",
    "DONOR_ONLY_ROLE",
    "FILES",
    "MAX_RELATIVE_DIFFERENCE",
    "PLAUSIBLE_BAND",
    "SERIES_IDS",
    "AlphaAgreement",
    "alpha_agreement",
    "donor_only_alpha",
    "donor_only_file",
    "tail_alpha",
]

#: PRD §11.2 row "Leakage-α validation band": relative-median difference at most 20 %.
MAX_RELATIVE_DIFFERENCE = 0.20
#: PRD §11.2 row "Leakage-α validation band": both medians within 0.05–0.2 (inclusive).
PLAUSIBLE_BAND = (0.05, 0.2)

#: The neutral id of the donor-only acquisition and the role that names its saved state.
DONOR_ONLY_ID = "donly-01"
DONOR_ONLY_ROLE = "donor_only"
#: The FRET series that the donor-only label matches (maintainer's choice, 2026-11-18).
SERIES_IDS: tuple[str, ...] = ("lab-03", "lab-04", "lab-05", "lab-06", "lab-07", "lab-08")

#: SHA-256 and size of the donor-only state, measured on the workstation on 2026-11-18. The
#: series states are pinned in ``tests/_deeplasi_agreement.py``.
FILES: dict[str, dict[str, tuple[str, int]]] = {
    DONOR_ONLY_ID: {
        DONOR_ONLY_ROLE: (
            "0e9664324bc90e7162f9f99610607b8a20feff8bbfd0598e05e47dfb900f3d2e",
            18_624_213,
        ),
    },
}


def donor_only_file(private_data) -> Path:
    """The donor-only state of the workstation, verified against :data:`FILES`."""
    path = private_data.path(DONOR_ONLY_ID, DONOR_ONLY_ROLE)
    sha256, size = FILES[DONOR_ONLY_ID][DONOR_ONLY_ROLE]
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            digest.update(block)
    assert path.stat().st_size == size and digest.hexdigest() == sha256, (
        f"{DONOR_ONLY_ID}/{DONOR_ONLY_ROLE}: the file is not the one the band was recorded on "
        f"(SHA-256 {sha256[:16]}…, {size} B); see tests/_leakage_band.py"
    )
    return path


@dataclass(frozen=True)
class AlphaAgreement:
    """The two medians, their relative difference and the verdict of the conjunctive band."""

    alpha_donor_only: float
    alpha_tail: float
    relative_difference: float
    donor_only_in_band: bool
    tail_in_band: bool
    agrees: bool

    @property
    def passes(self) -> bool:
        """True only when the medians agree **and** both lie in the band (never an OR)."""
        return self.agrees and self.donor_only_in_band and self.tail_in_band


def _in_band(value: float) -> bool:
    low, high = PLAUSIBLE_BAND
    return bool(np.isfinite(value) and low <= value <= high)


def alpha_agreement(alpha_donor_only: float, alpha_tail: float) -> AlphaAgreement:
    """Apply the conjunctive §11.2 band to two population medians.

    A missing median (``None`` or NaN, which is what a withheld estimate gives) fails every
    part: there is nothing to agree with.
    """
    a = float("nan") if alpha_donor_only is None else float(alpha_donor_only)
    b = float("nan") if alpha_tail is None else float(alpha_tail)
    mean = (a + b) / 2.0
    known = np.isfinite(a) and np.isfinite(b) and mean > 0.0
    rel = abs(a - b) / mean if known else float("nan")
    return AlphaAgreement(
        alpha_donor_only=a,
        alpha_tail=b,
        relative_difference=rel,
        donor_only_in_band=_in_band(a),
        tail_in_band=_in_band(b),
        agrees=bool(np.isfinite(rel) and rel <= MAX_RELATIVE_DIFFERENCE),
    )


def _rows(traces) -> list[np.ndarray]:
    """One 1-D float array per molecule; the molecules of a pooled series may differ in length."""
    return [np.asarray(row, dtype=np.float64).reshape(-1) for row in traces]


def donor_only_alpha(donor, acceptor):
    """The donor-only α of a sample with no acceptor, under the tail estimator's gates.

    Every frame before the donor's first bleach is a donor-only frame, so the window of each
    molecule is ``[0, donor_pb)``: :func:`tether.fret.leakage.estimate_leakage_alpha` with every
    acceptor bleach at frame 0. ``donor`` and ``acceptor`` hold one trace per molecule. Returns
    its :class:`~tether.fret.leakage.LeakageEstimate`.
    """
    from tether.fret.leakage import estimate_leakage_alpha  # noqa: PLC0415
    from tether.fret.photobleach import detect_photobleach  # noqa: PLC0415

    donor, acceptor = _rows(donor), _rows(acceptor)
    donor_pb = [
        int(detect_photobleach(d, a).donor_pb) for d, a in zip(donor, acceptor, strict=True)
    ]
    return estimate_leakage_alpha(donor, acceptor, [0] * len(donor), donor_pb)


def tail_alpha(donor, acceptor):
    """The tail α of FRET traces: Tether's bleach frames, then ADR-0027's estimator.

    ``donor`` and ``acceptor`` hold one trace per molecule, of any length, so the molecules of
    several acquisitions pool into one population median.
    """
    from tether.fret.leakage import estimate_leakage_alpha  # noqa: PLC0415
    from tether.fret.photobleach import detect_photobleach  # noqa: PLC0415

    donor, acceptor = _rows(donor), _rows(acceptor)
    bleach = [detect_photobleach(d, a) for d, a in zip(donor, acceptor, strict=True)]
    return estimate_leakage_alpha(
        donor,
        acceptor,
        [int(b.acceptor_pb) for b in bleach],
        [int(b.donor_pb) for b in bleach],
    )
