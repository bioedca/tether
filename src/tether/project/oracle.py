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
        if arr.size and arr.shape[1] != 2:
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

    def summary(self) -> dict[str, float | int | bool | str]:
        """A compact JSON-friendly dict of the headline metrics (for logs/reports)."""
        return {
            "n_ground_truth": self.n_ground_truth,
            "n_extracted": self.n_extracted,
            "n_matched": self.n_matched,
            "recall": round(self.recall, 4),
            "match_tol_px": self.match_tol_px,
            "coord_rms_px": round(self.coord_rms_px, 4),
            "intensity": self.intensity,
            "donor_pearson_median": round(self.donor_pearson_median, 5),
            "acceptor_pearson_median": round(self.acceptor_pearson_median, 5),
            "donor_pearson_pooled": round(self.donor_pearson_pooled, 5),
            "acceptor_pearson_pooled": round(self.acceptor_pearson_pooled, 5),
            "registration_rms_px": (
                round(self.registration_rms_px, 4)
                if np.isfinite(self.registration_rms_px)
                else float("nan")
            ),
            "meets_recall": self.meets_recall,
            "meets_pearson": self.meets_pearson,
            "meets_rms": self.meets_rms,
            "meets_acceptance": self.meets_acceptance(),
        }


def _median_finite(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if finite.size else float("nan")


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
) -> OracleResult:
    """Score one native extraction against a Deep-LASI export (PRD §9 M1).

    Parameters
    ----------
    ground_truth:
        Parsed Deep-LASI ``.mat`` export (donor coordinates + per-frame traces).
    extracted_donor_xy:
        ``(n_ext, 2)`` Tether donor ``[x, y]`` coordinates.
    extracted_donor_trace, extracted_acceptor_trace:
        ``(n_ext, n_frames)`` Tether per-frame integrated intensities matching
        ``intensity`` (e.g. ``donor_raw`` / ``acceptor_raw``).
    tol_px:
        Match tolerance (default 1 px, the §9 M1 number).
    intensity:
        Label recorded on the result (``"raw"`` or ``"corrected"``) — which
        Deep-LASI trace the caller paired against (``ground_truth.donor_<intensity>``).
    valid_lengths:
        Optional ``(n_ext,)`` native frame counts per extracted molecule (the
        zero-pad excluded); falls back to the full trace width.
    registration_rms_px:
        Native bead-fit residual to carry onto the result (``nan`` if N/A, e.g.
        the imported-``.tmap`` path).
    """
    if intensity not in ("raw", "corrected"):
        raise ValueError(f"intensity must be 'raw' or 'corrected', got {intensity!r}")
    gt_donor = getattr(ground_truth, f"donor_{intensity}")
    gt_acceptor = getattr(ground_truth, f"acceptor_{intensity}")
    gt_xy = ground_truth.donor_xy

    ext_donor_xy = np.atleast_2d(np.asarray(extracted_donor_xy, dtype=np.float64))
    n_ext = 0 if ext_donor_xy.size == 0 else ext_donor_xy.shape[0]
    n_gt = int(gt_xy.shape[0])

    matches = match_coordinates(gt_xy, ext_donor_xy, tol_px=tol_px)
    recall = (len(matches) / n_gt) if n_gt else float("nan")
    rms = coordinate_rms(gt_xy, ext_donor_xy, matches)

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
        donor_pearson_median=_median_finite(donor_r),
        acceptor_pearson_median=_median_finite(acceptor_r),
        donor_pearson_pooled=donor_pooled,
        acceptor_pearson_pooled=acceptor_pooled,
        matches=tuple(matches),
        registration_rms_px=float(registration_rms_px),
    )


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
    )
