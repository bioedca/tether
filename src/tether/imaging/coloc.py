# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Colocalization under one of two rules + apply-map-at-extraction (PRD App. E Stages 11-13).

The bridge between spot detection ([`tether.imaging.detect`][]) and Sum
integration ([`tether.imaging.aperture`][]): given donor (reference) spots, an
acceptor (moving) channel reached through a
[`RegistrationMap`][tether.imaging.calibrate.RegistrationMap],
and the two channels' frame shapes, pair each donor with its acceptor read
position and decide which molecules can actually be extracted.

What this stage does (and deliberately does *not* do)
-----------------------------------------------------
* **Stage 11 -- the colocalization rule (ADR-0127, which replaces ADR-0015).** Every
  in-frame donor spot is a candidate, and the independently detected acceptor spots
  are warped into donor coordinates and matched to it within ``coloc_distance_px``
  (the [`acceptor_detected`][tether.imaging.coloc.ColocalizedMolecules.acceptor_detected]
  flag). The ``policy`` then decides which candidates are molecules:

  - ``partner-in-both`` (the default for a new extraction): only a candidate with a
    detected acceptor partner is kept. This is Deep-LASI's rule -- ``findColoc(T, 3)``
    keeps a molecule only if it has a partner in every channel
    (``mapping/findColoc.m:110``), the "colocalized molecules only" extraction mode
    (Asadiatouei 2024, *Biophys. J.*).
  - ``donor-anchored``: every candidate is kept, detected acceptor or not. The partner
    rule drops the low-FRET and acceptor-dark molecules, whose acceptor is too weak to
    be detected as a spot of its own; dark/non-FRET acceptor states are a real and
    substantial fraction of FRET data (Vogel 2012, *PLoS ONE*), so this rule stays
    available for weak-acceptor and low-FRET samples.

  Under either rule the acceptor intensity is read at the *mapped donor position*,
  never at the detected acceptor spot (Deep-LASI places its apertures the same way,
  Stage 12): the partner only decides whether a molecule is kept. The result carries
  the molecule count each rule would keep
  ([`rule_counts`][tether.imaging.coloc.ColocalizedMolecules.rule_counts]), so a caller
  can show both before it extracts anything.

* **Stage 12 -- apply the map in the coordinate domain.** The donor->acceptor read
  position is [`RegistrationMap.apply_reference_to_moving`][] on the *coordinates*;
  the acceptor->donor warp used for the detection flag is
  [`apply_moving_to_reference`][tether.imaging.calibrate.RegistrationMap.apply_moving_to_reference].
  **The movie is never
  resampled** (``batchExtraction.m:132-143`` rewarps the movie only for a
  ``_warped_to_*_ref.tif`` QA export; the extraction path ``:421-431`` feeds raw
  pixels at coordinate-mapped positions): transforming coordinates avoids the
  interpolation bias a pixel rewarp would inject into the integrated intensities.
  Sub-pixel positions are preserved -- the round-to-pixel happens only at the crop,
  in [`tether.imaging.aperture.integrate_traces`][].

* **Stage 13 -- crop-box guardrail.** A molecule is extractable only if its full
  ``window x window`` (default 21x21) aperture lies inside the frame **in both
  channels** -- the donor aperture at ``donor_xy`` and the acceptor aperture at the
  mapped ``acceptor_xy``. Spots whose window leaves either frame are skipped
  (``extractTraces.m:9-25`` zero-fills out-of-frame crops; Tether drops them from
  the molecule list so no all-zero trace is ever written). The shared in-frame
  predicate is [`tether.imaging.aperture.aperture_in_frame`][], so this guardrail
  and the integrator agree by construction.

**Coordinate convention** (as everywhere in [`tether.imaging`][]): points are
``(N, 2)`` arrays of ``[x, y] = [col, row]`` in 0-based pixels; frame shapes are
``(H, W) = (rows, cols)``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np
from scipy.spatial import cKDTree

from tether.extract_choices import (
    COLOCALIZATION_POLICIES,
    DEFAULT_COLOCALIZATION_POLICY,
    DONOR_ANCHORED,
    PARTNER_IN_BOTH,
)
from tether.imaging.aperture import (
    _validate_frame_shape,
    _validate_window,
    aperture_in_frame,
)
from tether.imaging.calibrate import RegistrationMap

__all__ = [
    "COLOCALIZATION_POLICIES",
    "DEFAULT_COLOCALIZATION_POLICY",
    "DEFAULT_COLOC_DISTANCE_PX",
    "DONOR_ANCHORED",
    "PARTNER_IN_BOTH",
    "ColocalizedMolecules",
    "colocalize",
]

#: Colocalization nearest-neighbour distance gate, in px (PRD §11.2; Deep-LASI
#: ``batchExtraction.m:182`` calls ``findColoc(T, 3)``). It sets the
#: `ColocalizedMolecules.acceptor_detected` flag, which the ``partner-in-both`` rule
#: keeps molecules by; the ``donor-anchored`` molecule list does not depend on it.
DEFAULT_COLOC_DISTANCE_PX = 3.0


@dataclass(frozen=True)
class ColocalizedMolecules:
    """Colocalization result (one row per kept donor molecule).

    Every array field is aligned row-for-row: row ``i`` is one molecule, anchored on
    the donor spot ``donor_index[i]`` of the input, with its acceptor read at
    ``acceptor_xy[i]``. Only molecules whose ``window x window`` aperture fits in
    **both** channels are present (the others are skipped, Stage 13), and under the
    ``partner-in-both`` rule only those with a detected acceptor partner.

    Attributes
    ----------
    donor_xy:
        ``(N, 2)`` ``[x, y]`` donor (reference) coordinates -- the kept subset of
        the input donor spots, sub-pixel, unmodified.
    acceptor_xy:
        ``(N, 2)`` ``[x, y]`` acceptor (moving) coordinates, the donor positions
        warped through the registration map (Stage 12). Sub-pixel; not snapped.
    acceptor_detected:
        ``(N,)`` bool -- whether an independently-detected acceptor spot fell within
        ``coloc_distance_px`` of the donor (the classic findColoc "partner" test,
        evaluated in donor coordinates). Under ``partner-in-both`` it is ``True`` on
        every row; under ``donor-anchored`` a ``False`` row is kept (the dark or
        low-FRET acceptor). All ``False`` when no acceptor spots are supplied.
    donor_index:
        ``(N,)`` int -- the row of the input ``donor_spots`` each molecule came from.
    acceptor_index:
        ``(N,)`` int -- the row of the input ``acceptor_spots`` matched within the
        gate, or ``-1`` where ``acceptor_detected`` is ``False``. The match is
        per-donor nearest-neighbour (not mutual/unique), so two donors within the
        gate of the same acceptor may share an ``acceptor_index`` -- it is not a
        bijection.
    policy:
        The colocalization rule that chose these rows, one of
        [`COLOCALIZATION_POLICIES`][tether.extract_choices.COLOCALIZATION_POLICIES].
        Defaults to ``donor-anchored`` so a result built by hand from coordinates
        keeps every row it was given.
    rule_counts:
        ``{policy: n_molecules}`` -- how many molecules each rule keeps from the same
        spots, so the counts can be shown before the run (#920). Empty for a result
        not built by [`colocalize`][].
    detection_recorded:
        Whether ``acceptor_detected`` was measured here from independently detected
        acceptor spots, so a project may store it (ADR-0127). ``False`` by default,
        for a result built by hand from coordinates -- a recovered Deep-LASI set, say --
        whose flag is a placeholder; [`colocalize`][] sets ``True``.
    """

    donor_xy: np.ndarray
    acceptor_xy: np.ndarray
    acceptor_detected: np.ndarray
    donor_index: np.ndarray
    acceptor_index: np.ndarray
    policy: str = DONOR_ANCHORED
    rule_counts: Mapping[str, int] = field(default_factory=dict)
    detection_recorded: bool = False

    def __post_init__(self) -> None:
        """Check the policy and freeze ``rule_counts`` so a recorded count cannot change."""
        if self.policy not in COLOCALIZATION_POLICIES:
            raise ValueError(
                f"policy must be one of {COLOCALIZATION_POLICIES}, got {self.policy!r}"
            )
        counts = {str(k): int(v) for k, v in dict(self.rule_counts).items()}
        object.__setattr__(self, "rule_counts", MappingProxyType(counts))

    def __len__(self) -> int:
        """Number of kept molecules; makes an empty result falsy (``if result:``)."""
        return int(self.donor_xy.shape[0])

    @property
    def n_molecules(self) -> int:
        """Number of kept (extractable) molecules under ``policy``."""
        return len(self)


def _as_xy(points: np.ndarray, name: str) -> np.ndarray:
    """Coerce ``points`` to a ``(N, 2)`` float64 ``[x, y]`` array (empty allowed)."""
    arr = np.asarray(points, dtype=np.float64)
    if arr.size == 0:
        return np.empty((0, 2), dtype=np.float64)
    arr = np.atleast_2d(arr)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"{name} must be (N, 2) [x, y], got shape {arr.shape}")
    if not np.isfinite(arr).all():
        # A non-finite coordinate would warp to a non-finite read position and
        # silently corrupt the in-frame test (NaN comparisons are always False).
        raise ValueError(f"{name} must contain only finite values")
    return arr


def colocalize(
    donor_spots: np.ndarray,
    registration_map: RegistrationMap,
    *,
    donor_shape: tuple[int, int],
    acceptor_shape: tuple[int, int],
    acceptor_spots: np.ndarray | None = None,
    window: int = 21,
    coloc_distance_px: float = DEFAULT_COLOC_DISTANCE_PX,
    policy: str = DEFAULT_COLOCALIZATION_POLICY,
) -> ColocalizedMolecules:
    """Pair donor spots with their mapped acceptor positions under ``policy`` (Stages 11-13).

    Parameters
    ----------
    donor_spots:
        ``(Nd, 2)`` ``[x, y]`` spot coordinates in the donor (reference) channel,
        e.g. [`tether.imaging.detect.detect_spots`][] on the donor half.
    registration_map:
        The donor(reference)<->acceptor(moving)
        [`RegistrationMap`][tether.imaging.calibrate.RegistrationMap]
        (built once upstream by
        [`fit_registration_map`][tether.imaging.calibrate.fit_registration_map]
        or [`registration_map_from_tmap`][tether.imaging.calibrate.registration_map_from_tmap]). Its
        forward map warps donor coordinates into the acceptor frame.
    donor_shape, acceptor_shape:
        ``(H, W)`` pixel shapes of the donor and acceptor channel sub-images (e.g.
        each half's ``.shape[-2:]``), used for the per-channel crop-box guardrail.
    acceptor_spots:
        Optional ``(Na, 2)`` ``[x, y]`` independently-detected acceptor-channel
        spots. When given they annotate
        [`acceptor_detected`][tether.imaging.coloc.ColocalizedMolecules.acceptor_detected]
        (warped into donor coordinates and nearest-neighbour matched within
        ``coloc_distance_px``); when ``None`` every molecule is flagged undetected.
        Under ``partner-in-both`` they decide which molecules are kept, so that rule
        requires them; under ``donor-anchored`` they never filter the list.
    window:
        Odd aperture side length in px (default 21); a molecule is kept only if its
        ``window x window`` crop fits inside both frames (Stage 13).
    coloc_distance_px:
        Nearest-neighbour gate for the detection flag (PRD §11.2 default 3 px).
        Strict (``< gate``, matching ``findColoc.m:58``, applied explicitly in NumPy
        so it does not depend on cKDTree's boundary convention): an acceptor at
        exactly the gate distance is not a match.
    policy:
        The colocalization rule, one of
        [`COLOCALIZATION_POLICIES`][tether.extract_choices.COLOCALIZATION_POLICIES]
        (default ``partner-in-both``, ADR-0127).

    Returns
    -------
    ColocalizedMolecules
        One row per kept molecule, with the count of both rules in
        [`rule_counts`][tether.imaging.coloc.ColocalizedMolecules.rule_counts].

    Raises
    ------
    ValueError
        For an unknown ``policy``, or ``partner-in-both`` without ``acceptor_spots``
        (with no acceptor spots that rule would keep no molecule, silently).
    """
    if not isinstance(registration_map, RegistrationMap):
        raise TypeError(
            f"registration_map must be a RegistrationMap, got {type(registration_map).__name__}"
        )
    if not (coloc_distance_px > 0):
        raise ValueError(f"coloc_distance_px must be > 0, got {coloc_distance_px}")
    if policy not in COLOCALIZATION_POLICIES:
        raise ValueError(f"policy must be one of {COLOCALIZATION_POLICIES}, got {policy!r}")
    if policy == PARTNER_IN_BOTH and acceptor_spots is None:
        raise ValueError(
            "the partner-in-both rule needs acceptor_spots (it keeps only molecules with a "
            "detected acceptor); pass them, or choose policy='donor-anchored'"
        )
    # Validate window + shapes up front via the shared aperture validators (not only
    # inside aperture_in_frame) so the contract holds even when donor_spots is empty
    # and the guardrail never runs -- and matches integrate_traces exactly.
    _validate_window(window)
    _validate_frame_shape(donor_shape, "donor_shape")
    _validate_frame_shape(acceptor_shape, "acceptor_shape")

    donor_xy = _as_xy(donor_spots, "donor_spots")
    if donor_xy.shape[0] == 0:
        return _empty_result(policy)

    # Stage 12: apply the map in the coordinate domain (no movie rewarp). The
    # donor-anchored acceptor read position is the donor warped forward
    # (PolyTransform2D.apply already returns a float64 (N, 2)).
    acceptor_xy = np.asarray(registration_map.apply_reference_to_moving(donor_xy), dtype=np.float64)

    # Stage 13: keep only molecules whose window fits in BOTH channels.
    keep = aperture_in_frame(donor_xy, shape=donor_shape, window=window) & aperture_in_frame(
        acceptor_xy, shape=acceptor_shape, window=window
    )
    donor_index = np.nonzero(keep)[0]
    donor_xy = donor_xy[keep]
    acceptor_xy = acceptor_xy[keep]

    acceptor_detected, acceptor_index = _annotate_detection(
        donor_xy, acceptor_spots, registration_map, coloc_distance_px
    )
    counts = {
        PARTNER_IN_BOTH: int(np.count_nonzero(acceptor_detected)),
        DONOR_ANCHORED: int(donor_xy.shape[0]),
    }

    # Stage 11: the rule. Partner-in-both keeps a candidate only with a detected
    # acceptor partner; the acceptor is still read at the mapped donor position.
    rows = acceptor_detected if policy == PARTNER_IN_BOTH else np.ones(len(donor_xy), bool)
    return ColocalizedMolecules(
        donor_xy=donor_xy[rows],
        acceptor_xy=acceptor_xy[rows],
        acceptor_detected=acceptor_detected[rows],
        donor_index=donor_index[rows].astype(np.intp),
        acceptor_index=acceptor_index[rows],
        policy=policy,
        rule_counts=counts,
        detection_recorded=acceptor_spots is not None,
    )


def _annotate_detection(
    donor_xy: np.ndarray,
    acceptor_spots: np.ndarray | None,
    registration_map: RegistrationMap,
    coloc_distance_px: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Flag which kept donors have an independently-detected acceptor partner.

    Warps the acceptor spots into donor coordinates (Stage 11, "warp R spots into G
    coords") and nearest-neighbour matches each kept donor within
    ``coloc_distance_px`` (``cKDTree``, strict ``< gate`` per ``findColoc.m:58``).
    The match is per-donor nearest-acceptor (not mutual or one-to-one), as in
    findColoc, which takes for each reference spot an other-channel spot within the
    gate (``findColoc.m:58-60``): whether a donor *has* a partner -- the only thing the
    ``partner-in-both`` rule reads -- is the same either way, since the nearest acceptor
    is within the gate exactly when any acceptor is. Returns
    ``(acceptor_detected, acceptor_index)`` aligned with ``donor_xy``; an unmatched donor
    gets ``acceptor_index == -1``.
    """
    n = donor_xy.shape[0]
    detected = np.zeros(n, dtype=bool)
    acceptor_index = np.full(n, -1, dtype=np.intp)
    if acceptor_spots is None:
        return detected, acceptor_index
    acceptor_xy = _as_xy(acceptor_spots, "acceptor_spots")
    if acceptor_xy.shape[0] == 0 or n == 0:
        return detected, acceptor_index

    acceptor_in_donor = np.asarray(
        registration_map.apply_moving_to_reference(acceptor_xy), dtype=np.float64
    )
    tree = cKDTree(acceptor_in_donor)
    # Query the true nearest neighbour (no distance_upper_bound) and gate in NumPy,
    # so the boundary is exactly findColoc's strict `< dist` (mapping/findColoc.m:58)
    # and independent of cKDTree's distance_upper_bound convention (which is
    # inclusive in current scipy, contrary to a strict-bound assumption).
    dist, idx = tree.query(donor_xy, k=1)
    dist = np.atleast_1d(dist)
    idx = np.atleast_1d(idx)
    hit = dist < float(coloc_distance_px)
    detected[hit] = True
    acceptor_index[hit] = idx[hit].astype(np.intp)
    return detected, acceptor_index


def _empty_result(policy: str) -> ColocalizedMolecules:
    """An all-empty result with the right shapes/dtypes (no donor molecules)."""
    return ColocalizedMolecules(
        donor_xy=np.empty((0, 2), dtype=np.float64),
        acceptor_xy=np.empty((0, 2), dtype=np.float64),
        acceptor_detected=np.empty(0, dtype=bool),
        donor_index=np.empty(0, dtype=np.intp),
        acceptor_index=np.empty(0, dtype=np.intp),
        policy=policy,
        rule_counts={PARTNER_IN_BOTH: 0, DONOR_ANCHORED: 0},
        detection_recorded=True,
    )
