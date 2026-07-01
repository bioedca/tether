# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction-vs-Deep-LASI acceptance oracle (PRD §9 M1, §8 NFR-VALID (a)).

The M1 milestone closes only when Tether's *native* extraction reproduces a
Deep-LASI export of the same movie to tolerance. PRD §9 M1 fixes three numbers
on the UCKOPSB pair:

* **matched-molecule recall ≥ 95 %** within **1 px** (nearest-neighbour pairing
  of donor coordinates; Deep-LASI molecules are the denominator);
* **per-frame integrated-intensity Pearson r ≥ 0.99** on matched molecules;
* **registration RMS ≤ 0.5 px** (the native bead-fit residual, validated in the
  registration module; carried here for reporting when available).

This module is the pure, reusable scorer. :func:`evaluate_extraction` takes plain
arrays (extracted donor coordinates + per-frame intensity traces) and a parsed
:class:`~tether.io.deeplasi.DeepLasiExport` ground truth and returns an
:class:`OracleResult`. :func:`evaluate_project` is the on-disk convenience that
reads a written ``.tether`` (via :func:`tether.imaging.extract.read_molecules` /
:func:`~tether.imaging.extract.read_traces`) and a ``.mat`` export, then delegates.

Matching is a deterministic **greedy unique nearest-neighbour** within the
tolerance (no Hungarian assignment is required by §9 M1; greedy global-min keeps
the result order-independent and stops one extracted spot claiming two truths).
Correlation uses Pearson's r (settled textbook linear-agreement measure); both a
robust **per-molecule** distribution (the gated metric) and a single **pooled** r
are reported, since pooling across molecules inflates r with between-molecule
variance and is the weaker statistic.

Beyond the three gated numbers, the result carries two **reporting-only** metrics
(never part of :meth:`OracleResult.meets_acceptance` — the frozen gate is not
touched): the donor-channel **precision** (matched / extracted), so a recall met by
an over-detecting flood is visible rather than mistaken for a faithful match; and,
when acceptor coordinates are supplied, the per-channel **acceptor** coordinate
recall vs ``ground_truth.acceptor_xy`` — validating detection in *both* channels,
not donor-only. (Empirically the acceptor channel recalls far fewer of the curated
molecules than the donor: the dark / low-FRET acceptor population Tether's
donor-anchored extraction deliberately keeps — Vogel 2012, Wanninger 2023 — so a
*bidirectional* colocalization filter would collapse recall; the honest apples-to-
apples framing scores the donor-anchored set and reports precision as the caveat.)

Coordinates everywhere are ``[x, y] = [col, row]`` 0-based pixels, matching the
rest of :mod:`tether.imaging` and :class:`~tether.io.deeplasi.DeepLasiExport`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import os

    from tether.io.deeplasi import DeepLasiExport

__all__ = [
    "OracleResult",
    "coordinate_rms",
    "evaluate_extraction",
    "evaluate_project",
    "match_coordinates",
    "pooled_pearson",
]

#: §9 M1 acceptance thresholds (PRD §9 M1, §11.2).
RECALL_THRESHOLD = 0.95
PEARSON_THRESHOLD = 0.99
RMS_THRESHOLD_PX = 0.5
MATCH_TOL_PX = 1.0


# --- coordinate matching -----------------------------------------------------


def match_coordinates(
    ground_truth_xy: np.ndarray,
    extracted_xy: np.ndarray,
    *,
    tol_px: float = MATCH_TOL_PX,
) -> list[tuple[int, int, float]]:
    """Greedy unique nearest-neighbour pairing within ``tol_px``.

    Every candidate pair within ``tol_px`` is ranked by Euclidean distance and
    assigned greedily, so each ground-truth and each extracted point is used at
    most once and the result is independent of input order (ties broken by index).

    Parameters
    ----------
    ground_truth_xy, extracted_xy:
        ``(n, 2)`` arrays of ``[x, y]`` pixel coordinates.
    tol_px:
        Maximum match distance in pixels (default :data:`MATCH_TOL_PX`).

    Returns
    -------
    list of ``(gt_index, ext_index, distance)``
        Sorted by ascending distance.
    """
    if tol_px <= 0:
        raise ValueError(f"tol_px must be positive, got {tol_px!r}")
    gt = np.atleast_2d(np.asarray(ground_truth_xy, dtype=np.float64))
    ext = np.atleast_2d(np.asarray(extracted_xy, dtype=np.float64))
    for name, arr in (("ground_truth_xy", gt), ("extracted_xy", ext)):
        if arr.size and (arr.ndim != 2 or arr.shape[1] != 2):
            raise ValueError(f"{name} must be (n, 2), got shape {arr.shape}")
    if gt.size == 0 or ext.size == 0:
        return []

    # Pairwise distances (n_gt, n_ext); N is small (≈hundreds), so dense is fine.
    diff = gt[:, None, :] - ext[None, :, :]
    dist = np.hypot(diff[..., 0], diff[..., 1])
    gi, ei = np.nonzero(dist <= tol_px)
    if gi.size == 0:
        return []
    order = np.argsort(dist[gi, ei], kind="stable")
    used_gt: set[int] = set()
    used_ext: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for k in order:
        g, e = int(gi[k]), int(ei[k])
        if g in used_gt or e in used_ext:
            continue
        used_gt.add(g)
        used_ext.add(e)
        matches.append((g, e, float(dist[g, e])))
    matches.sort(key=lambda m: m[2])
    return matches


def coordinate_rms(
    ground_truth_xy: np.ndarray,
    extracted_xy: np.ndarray,
    matches: list[tuple[int, int, float]],
) -> float:
    """RMS of the matched-pair coordinate residual (px); ``nan`` if no matches."""
    if not matches:
        return float("nan")
    gt = np.asarray(ground_truth_xy, dtype=np.float64)
    ext = np.asarray(extracted_xy, dtype=np.float64)
    res = np.array([gt[g] - ext[e] for g, e, _ in matches], dtype=np.float64)
    return float(np.sqrt(np.mean(np.sum(res**2, axis=1))))


# --- intensity correlation ---------------------------------------------------


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson r of two equal-length 1-D series; ``nan`` if either is constant."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or a.size != b.size:
        return float("nan")
    finite = np.isfinite(a) & np.isfinite(b)
    if finite.sum() < 2:
        return float("nan")
    a, b = a[finite], b[finite]
    if np.std(a) == 0.0 or np.std(b) == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _aligned(
    gt_series: np.ndarray, ext_series: np.ndarray, n_valid: int | None
) -> tuple[np.ndarray, np.ndarray]:
    """Trim a ground-truth and extracted per-frame series to a common length.

    Both are assumed to start at frame 0 of the same movie. The comparison length
    is the shortest of (ground-truth frames, extracted frames, the molecule's
    valid native extent ``n_valid`` when supplied — Tether zero-pads to the
    experiment max, so the pad must be excluded).
    """
    length = min(gt_series.shape[-1], ext_series.shape[-1])
    if n_valid is not None:
        length = min(length, int(n_valid))
    return gt_series[..., :length], ext_series[..., :length]


def pooled_pearson(
    gt_traces: np.ndarray,
    ext_traces: np.ndarray,
    matches: list[tuple[int, int, float]],
    *,
    valid_lengths: np.ndarray | None = None,
) -> float:
    """Single Pearson r over the matched molecules' concatenated per-frame series.

    The weaker, pooling statistic (between-molecule variance inflates it); reported
    alongside the robust per-molecule distribution, never used as the sole gate.
    """
    pooled_gt: list[np.ndarray] = []
    pooled_ext: list[np.ndarray] = []
    for g, e, _ in matches:
        n_valid = None if valid_lengths is None else valid_lengths[e]
        a, b = _aligned(gt_traces[g], ext_traces[e], n_valid)
        pooled_gt.append(a.ravel())
        pooled_ext.append(b.ravel())
    if not pooled_gt:
        return float("nan")
    return _pearson(np.concatenate(pooled_gt), np.concatenate(pooled_ext))


def _per_molecule_pearson(
    gt_traces: np.ndarray,
    ext_traces: np.ndarray,
    matches: list[tuple[int, int, float]],
    valid_lengths: np.ndarray | None,
) -> np.ndarray:
    out = np.empty(len(matches), dtype=np.float64)
    for i, (g, e, _) in enumerate(matches):
        n_valid = None if valid_lengths is None else valid_lengths[e]
        a, b = _aligned(gt_traces[g], ext_traces[e], n_valid)
        out[i] = _pearson(a, b)
    return out


# --- the oracle result -------------------------------------------------------


@dataclass(frozen=True)
class OracleResult:
    """Scored extraction-vs-Deep-LASI comparison (PRD §9 M1).

    The three §9 M1 numbers are :attr:`recall`, the per-molecule Pearson medians
    (:attr:`donor_pearson_median` / :attr:`acceptor_pearson_median`), and
    :attr:`registration_rms_px` (populated when the native bead-fit residual is
    known; the imported-``.tmap`` path trusts Deep-LASI's registration and leaves
    it ``nan``). :meth:`meets_acceptance` evaluates the conjunction.
    """

    n_ground_truth: int
    n_extracted: int
    n_matched: int
    recall: float
    match_tol_px: float
    coord_rms_px: float
    intensity: str
    donor_pearson: np.ndarray
    acceptor_pearson: np.ndarray
    donor_pearson_median: float
    acceptor_pearson_median: float
    donor_pearson_pooled: float
    acceptor_pearson_pooled: float
    matches: tuple[tuple[int, int, float], ...]
    #: Donor-channel precision: matched / extracted — the fraction of extracted
    #: molecules that correspond to a curated Deep-LASI molecule. **Reporting only,
    #: never a §9 M1 gate** (the frozen gate is recall + Pearson + RMS), but it makes
    #: a false-positive flood visible so a recall met by over-detection is not read as
    #: a faithful match (the C3d precision honesty concern). ``nan`` when nothing was
    #: extracted.
    precision: float = float("nan")
    #: Per-channel **acceptor** coordinate recall vs ``ground_truth.acceptor_xy`` —
    #: populated only when ``extracted_acceptor_xy`` is supplied (else ``nan`` / 0).
    #: Validates that detection is faithful in *both* channels (USER CORRECTION #1:
    #: "Tether donor det vs DL ``donor_xy``, acceptor vs ``acceptor_xy``"), a
    #: diagnostic that does not enter :meth:`meets_acceptance`.
    n_acceptor_extracted: int = 0
    n_acceptor_matched: int = 0
    acceptor_recall: float = float("nan")
    acceptor_precision: float = float("nan")
    acceptor_coord_rms_px: float = float("nan")
    registration_rms_px: float = float("nan")
    recall_threshold: float = RECALL_THRESHOLD
    pearson_threshold: float = PEARSON_THRESHOLD
    rms_threshold_px: float = RMS_THRESHOLD_PX
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def meets_recall(self) -> bool:
        return self.recall >= self.recall_threshold

    @property
    def meets_pearson(self) -> bool:
        """Both channels' median per-molecule Pearson r clear the threshold."""
        med = (self.donor_pearson_median, self.acceptor_pearson_median)
        return all(np.isfinite(m) and m >= self.pearson_threshold for m in med)

    @property
    def meets_rms(self) -> bool:
        """``True`` when a finite native residual is within gate (``nan`` ⇒ N/A → True)."""
        return (not np.isfinite(self.registration_rms_px)) or (
            self.registration_rms_px <= self.rms_threshold_px
        )

    def meets_acceptance(self) -> bool:
        """The §9 M1 conjunction: recall AND Pearson (AND RMS when measured)."""
        return self.meets_recall and self.meets_pearson and self.meets_rms

    def summary(self) -> dict[str, float | int | bool | str | None]:
        """A compact, strict-JSON-safe dict of the headline metrics (logs/reports).

        Non-finite floats (an unmeasured RMS, or a degenerate metric) are emitted
        as ``None`` rather than ``nan`` so ``json.dumps`` produces valid JSON.
        """

        def _f(value: float, ndigits: int) -> float | None:
            return round(float(value), ndigits) if np.isfinite(value) else None

        return {
            "n_ground_truth": self.n_ground_truth,
            "n_extracted": self.n_extracted,
            "n_matched": self.n_matched,
            "recall": _f(self.recall, 4),
            "precision": _f(self.precision, 4),
            "match_tol_px": self.match_tol_px,
            "coord_rms_px": _f(self.coord_rms_px, 4),
            "intensity": self.intensity,
            "n_acceptor_extracted": self.n_acceptor_extracted,
            "n_acceptor_matched": self.n_acceptor_matched,
            "acceptor_recall": _f(self.acceptor_recall, 4),
            "acceptor_precision": _f(self.acceptor_precision, 4),
            "acceptor_coord_rms_px": _f(self.acceptor_coord_rms_px, 4),
            "donor_pearson_median": _f(self.donor_pearson_median, 5),
            "acceptor_pearson_median": _f(self.acceptor_pearson_median, 5),
            "donor_pearson_pooled": _f(self.donor_pearson_pooled, 5),
            "acceptor_pearson_pooled": _f(self.acceptor_pearson_pooled, 5),
            "registration_rms_px": _f(self.registration_rms_px, 4),
            "meets_recall": self.meets_recall,
            "meets_pearson": self.meets_pearson,
            "meets_rms": self.meets_rms,
            "meets_acceptance": self.meets_acceptance(),
        }


def _acceptance_median(values: np.ndarray) -> float:
    """Median per-molecule Pearson r for the acceptance gate.

    A non-finite r (a constant/degenerate matched trace) is a *failure to agree*,
    not a value to drop: it counts as ``0.0`` (no linear agreement) before the
    median, so a run of mostly-invalid traces cannot pass the gate on a few good
    ones (the masking CodeRabbit flagged on PR #52). Empty input -> ``nan``.
    """
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return float("nan")
    return float(np.median(np.where(np.isfinite(values), values, 0.0)))


# --- the pure scorer ---------------------------------------------------------


def evaluate_extraction(
    ground_truth: DeepLasiExport,
    extracted_donor_xy: np.ndarray,
    extracted_donor_trace: np.ndarray,
    extracted_acceptor_trace: np.ndarray,
    *,
    tol_px: float = MATCH_TOL_PX,
    intensity: str = "raw",
    valid_lengths: np.ndarray | None = None,
    registration_rms_px: float = float("nan"),
    extracted_acceptor_xy: np.ndarray | None = None,
) -> OracleResult:
    """Score one native extraction against a Deep-LASI export (PRD §9 M1).

    The recall / Pearson / RMS conjunction is the frozen §9 M1 gate. Two further
    metrics are computed for **reporting** (never gates): the donor-channel
    :attr:`~OracleResult.precision` (matched / extracted), so a recall met by an
    over-detecting flood is visible rather than mistaken for a faithful match; and,
    when ``extracted_acceptor_xy`` is supplied, the per-channel **acceptor**
    coordinate recall vs ``ground_truth.acceptor_xy`` (USER CORRECTION #1 — validate
    detection in *both* channels, not donor-only).

    Parameters
    ----------
    ground_truth:
        Parsed Deep-LASI ``.mat`` export (donor + acceptor coordinates + per-frame
        traces).
    extracted_donor_xy:
        ``(n_ext, 2)`` Tether donor ``[x, y]`` coordinates.
    extracted_donor_trace, extracted_acceptor_trace:
        ``(n_ext, n_frames)`` Tether per-frame integrated intensities matching
        ``intensity`` (e.g. ``donor_raw`` / ``acceptor_raw``).
    tol_px:
        Match tolerance (default 1 px, the §9 M1 number); applied to both channels.
    intensity:
        Label recorded on the result (``"raw"`` or ``"corrected"``) — which
        Deep-LASI trace the caller paired against (``ground_truth.donor_<intensity>``).
    valid_lengths:
        Optional ``(n_ext,)`` native frame counts per extracted molecule (the
        zero-pad excluded); falls back to the full trace width.
    registration_rms_px:
        Native bead-fit residual to carry onto the result (``nan`` if N/A, e.g.
        the imported-``.tmap`` path).
    extracted_acceptor_xy:
        Optional ``(n_acc, 2)`` acceptor ``[x, y]`` coordinates to score per-channel
        against ``ground_truth.acceptor_xy`` — either the coordinate-domain mapped
        acceptor read positions (a registration check) or an independent acceptor
        detection (a detector check). ``None`` (default) leaves the acceptor-channel
        fields unmeasured (``nan`` / 0). Does **not** enter :meth:`meets_acceptance`.
    """
    if intensity not in ("raw", "corrected"):
        raise ValueError(f"intensity must be 'raw' or 'corrected', got {intensity!r}")
    gt_donor = getattr(ground_truth, f"donor_{intensity}")
    gt_acceptor = getattr(ground_truth, f"acceptor_{intensity}")
    gt_xy = ground_truth.donor_xy

    n_gt = int(gt_xy.shape[0])

    # Donor channel: recall / precision / RMS via the shared per-channel scorer.
    # Precision is reporting only — the fraction of extracted molecules that land on
    # a curated Deep-LASI molecule. A donor-anchored, sensitive detector keeps genuine
    # low-FRET molecules Deep-LASI's curation dropped *and* false positives, so a high
    # recall can coexist with a low precision — surfacing it keeps the §9 recall honest
    # (the C3d over-detection concern).
    n_ext, matches, recall, precision, rms = _score_channel(gt_xy, extracted_donor_xy, n_gt, tol_px)

    n_acc_ext, acc_matches, acc_recall, acc_precision, acc_rms = _score_acceptor_channel(
        ground_truth.acceptor_xy, extracted_acceptor_xy, n_gt, tol_px
    )

    donor_r = _per_molecule_pearson(gt_donor, extracted_donor_trace, matches, valid_lengths)
    acceptor_r = _per_molecule_pearson(
        gt_acceptor, extracted_acceptor_trace, matches, valid_lengths
    )
    donor_pooled = pooled_pearson(
        gt_donor, extracted_donor_trace, matches, valid_lengths=valid_lengths
    )
    acceptor_pooled = pooled_pearson(
        gt_acceptor, extracted_acceptor_trace, matches, valid_lengths=valid_lengths
    )

    return OracleResult(
        n_ground_truth=n_gt,
        n_extracted=n_ext,
        n_matched=len(matches),
        recall=recall,
        match_tol_px=float(tol_px),
        coord_rms_px=rms,
        intensity=intensity,
        donor_pearson=donor_r,
        acceptor_pearson=acceptor_r,
        donor_pearson_median=_acceptance_median(donor_r),
        acceptor_pearson_median=_acceptance_median(acceptor_r),
        donor_pearson_pooled=donor_pooled,
        acceptor_pearson_pooled=acceptor_pooled,
        matches=tuple(matches),
        precision=precision,
        n_acceptor_extracted=n_acc_ext,
        n_acceptor_matched=len(acc_matches),
        acceptor_recall=acc_recall,
        acceptor_precision=acc_precision,
        acceptor_coord_rms_px=acc_rms,
        registration_rms_px=float(registration_rms_px),
    )


def _score_channel(
    gt_xy: np.ndarray,
    extracted_xy: np.ndarray,
    n_gt: int,
    tol_px: float,
) -> tuple[int, list[tuple[int, int, float]], float, float, float]:
    """Coordinate recall + precision + RMS of one channel vs curated coordinates.

    Returns ``(n_extracted, matches, recall, precision, coord_rms)``. ``recall`` uses
    the curated ``n_gt`` denominator (so donor and acceptor recall are directly
    comparable); ``precision`` uses this channel's own extracted count. Shared by the
    donor and acceptor paths so the two channels' scoring cannot drift apart.
    """
    ext_xy = np.atleast_2d(np.asarray(extracted_xy, dtype=np.float64))
    n_ext = 0 if ext_xy.size == 0 else ext_xy.shape[0]
    matches = match_coordinates(gt_xy, ext_xy, tol_px=tol_px)
    recall = (len(matches) / n_gt) if n_gt else float("nan")
    precision = (len(matches) / n_ext) if n_ext else float("nan")
    rms = coordinate_rms(gt_xy, ext_xy, matches)
    return n_ext, matches, recall, precision, rms


def _score_acceptor_channel(
    gt_acceptor_xy: np.ndarray,
    extracted_acceptor_xy: np.ndarray | None,
    n_gt: int,
    tol_px: float,
) -> tuple[int, list[tuple[int, int, float]], float, float, float]:
    """Per-channel acceptor coordinate recall + precision vs the curated coordinates.

    Delegates to :func:`_score_channel`; returns all-unmeasured
    (``0``/``[]``/``nan``…) when ``extracted_acceptor_xy`` is ``None`` (the optional
    acceptor path), else the acceptor channel's ``(n_extracted, matches, recall,
    precision, coord_rms)``.
    """
    if extracted_acceptor_xy is None:
        return 0, [], float("nan"), float("nan"), float("nan")
    return _score_channel(gt_acceptor_xy, extracted_acceptor_xy, n_gt, tol_px)


# --- on-disk convenience -----------------------------------------------------


def evaluate_project(
    project_path: str | os.PathLike[str],
    deeplasi_mat_path: str | os.PathLike[str],
    *,
    tol_px: float = MATCH_TOL_PX,
    intensity: str = "raw",
    registration_rms_px: float = float("nan"),
) -> OracleResult:
    """Read a written ``.tether`` + a Deep-LASI ``.mat`` and score them (§9 M1).

    Reads ``/molecules`` (donor coordinates + per-molecule ``frame_range``) and
    ``/traces`` from the project, the export from the ``.mat``, then delegates to
    :func:`evaluate_extraction` using the chosen ``intensity`` trace pair.
    """
    from tether.imaging.extract import read_molecules, read_traces  # noqa: PLC0415
    from tether.io.deeplasi import read_deeplasi_mat  # noqa: PLC0415

    gt = read_deeplasi_mat(Path(deeplasi_mat_path))
    mols = read_molecules(Path(project_path))
    traces = read_traces(Path(project_path))

    donor_xy = np.asarray(mols["donor_xy"], dtype=np.float64)
    # The stored acceptor coordinates are the donor-anchored, coordinate-domain mapped
    # read positions (§ M1 S7); scored per-channel against the curated acceptor_xy they
    # give a registration/coordinate-domain acceptor check alongside the donor recall.
    acceptor_xy = np.asarray(mols["acceptor_xy"], dtype=np.float64)
    frame_range = np.asarray(mols["frame_range"])
    valid_lengths = (frame_range[:, 1] - frame_range[:, 0]).astype(np.int64)

    donor_trace = traces[f"donor_{intensity}"]
    acceptor_trace = traces[f"acceptor_{intensity}"]

    return evaluate_extraction(
        gt,
        donor_xy,
        donor_trace,
        acceptor_trace,
        tol_px=tol_px,
        intensity=intensity,
        valid_lengths=valid_lengths,
        registration_rms_px=registration_rms_px,
        extracted_acceptor_xy=acceptor_xy,
    )
