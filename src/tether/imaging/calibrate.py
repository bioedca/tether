# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registration calibration: polynomial map + RMS gate + persistence (M1 S6).

This is the home for PRD Appendix E **Stages 9-10** and the FR-EXTRACT §7.1
registration contract ("support both a native bead/grid fit *and* an imported
``.tmap``", with a numeric RMS-residual gate). It sits one level above the raw
fit/decode primitives in :mod:`tether.imaging.register` (which M0.5 S5/S6 landed:
:func:`~tether.imaging.register.fit_polynomial_transform`,
:func:`~tether.imaging.register.point_rms`,
:func:`~tether.imaging.register.read_tmap`,
:class:`~tether.imaging.register.PolyTransform2D`) and turns them into a single,
persistable calibration object shared by the native and imported paths.

What S6 adds on top of the M0.5 primitives
-------------------------------------------
* :class:`RegistrationMap` -- a frozen, serialisable per-movie donor<->acceptor
  calibration: the degree-2 polynomial coefficients **in both directions**, the
  numeric **RMS residual**, the control-point count, the gate, the per-channel
  split geometry, and provenance (source = native | imported, bead/map file, app
  version). It carries the **over-gate verdict** (:attr:`~RegistrationMap.low_confidence`)
  and the molecule tag that verdict implies (:attr:`~RegistrationMap.molecule_tags`).
* :func:`fit_registration_map` -- the native fit orchestration (Stage 9): a
  degree-2 polynomial map (with a 4-DOF *similarity fallback* when fewer than six
  control points are available), a numeric per-point RMS residual, and the
  **over-gate branch** (§7.1): a fit ``<=`` the gate (default 0.5 px, §11.2)
  proceeds; a fit ``>`` the gate marks the calibration *low-confidence* and tags
  every molecule it produces ``low-confidence-registration`` -- **never** silently
  dropped. The action is mode-aware; this headless seam implements the batch
  policy (``"warn"`` accept-with-flag-and-warn, the default, vs ``"fail"``
  fail-the-movie). The blocking GUI confirm-dialog is a later (M2) layer over this
  same verdict.
* :func:`registration_map_from_tmap` -- the imported path (§7.1, §9 M1's
  conjunctive "native calibration **and** apply imported ``.tmap``"): build the
  *same* :class:`RegistrationMap` from a decoded ``.tmap`` (skip the native fit),
  stamping ``source = "imported"``. Because both paths yield one type, an imported
  map and a native fit are directly comparable (the apply-both parity check, §7.1).
* :func:`save_map` / :func:`load_map` -- Stage-10 map-file persistence to ``.npz``
  (explicit coefficients both directions + geometry + provenance; **no raw images,
  no pickled transform objects**), kept separate from the session ``.tether``
  (mirroring Deep-LASI's ``.tmap`` vs ``.tdat`` split).
* :func:`write_calibration` / :func:`read_calibration` -- persist the calibration
  *into* a project's frozen ``/calibration`` container group as **additive data**
  (the M0 schema freeze allows data, not structure; the group is forward-declared
  empty in :mod:`tether.io.schema`).

Deferred to a follow-up (logged in ADR-0014): the degree-3 polynomial *retry* rung
of the fit-failure ladder (it needs :class:`PolyTransform2D` to become
degree-aware) and decoding the imported ``.tmap``'s per-channel rotation/flip (the
transform coefficients and crop suffice for the S6 map + parity; rotation/flip
matter for S7 apply-map-at-extraction).

Coordinate convention follows the rest of :mod:`tether.imaging`: points are
``(N, 2)`` arrays of ``[x, y] = [col, row]`` in 0-based pixels.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from tether.imaging.register import (
    PolyTransform2D,
    TmapChannel,
    fit_polynomial_transform,
    point_rms,
)
from tether.imaging.split import ChannelGeometry

__all__ = [
    "DEFAULT_RMS_GATE_PX",
    "LOW_CONFIDENCE_TAG",
    "OverGateRegistrationWarning",
    "RegistrationMap",
    "RegistrationOverGateError",
    "fit_registration_map",
    "load_map",
    "read_calibration",
    "registration_map_from_tmap",
    "save_map",
    "write_calibration",
]

#: Registration RMS-residual gate (PRD §11.2; Tether's numeric improvement over
#: Deep-LASI's visual-only QA). A fit with RMS residual ``> gate`` is flagged
#: low-confidence; the molecules it produces are tagged, never dropped (§7.1).
DEFAULT_RMS_GATE_PX = 0.5

#: The molecule tag a low-confidence (over-gate) calibration imprints on every
#: molecule it extracts (§7.1; applied at integration, M1 S8).
LOW_CONFIDENCE_TAG = "low-confidence-registration"

#: Minimum control points for a degree-2 polynomial fit; below this a 4-DOF
#: similarity is fitted instead (Stage 9 "similarity fallback if < ~6 points").
_MIN_POLY_POINTS = 6

#: The frozen empty container group calibrations are written into (PRD §5.1).
_CALIBRATION_GROUP = "calibration"

_DEGREE_POLYNOMIAL = 2  #: degree-2 native/imported polynomial map.
_DEGREE_SIMILARITY = 1  #: a 4-DOF similarity, stored as a degree-1 polynomial.


class RegistrationOverGateError(RuntimeError):
    """Raised by :func:`fit_registration_map` when a fit exceeds the RMS gate.

    Only raised under the ``on_over_gate="fail"`` batch policy (§7.1 fail-movie);
    the default ``"warn"`` policy returns the (flagged) map instead.
    """


class OverGateRegistrationWarning(UserWarning):
    """Warned by :func:`fit_registration_map` when a fit exceeds the RMS gate.

    The structured per-movie warning of the headless ``on_over_gate="warn"``
    (accept-with-flag) policy (§7.1). Its own category so callers/tests can target
    it with :func:`warnings.catch_warnings` / :func:`pytest.warns`.
    """


# --- the calibration object --------------------------------------------------


@dataclass(frozen=True, eq=False)
class RegistrationMap:
    """A persistable donor<->acceptor registration calibration (Stages 9-10).

    One movie's channel-to-channel map: the degree-2 polynomial coefficients in
    **both** directions (``reference -> moving`` and ``moving -> reference``,
    independently fitted, mirroring the decoded ``.tmap``), the numeric RMS
    residual and the gate it was judged against, the control-point count, optional
    per-channel split geometry, and provenance. Built by :func:`fit_registration_map`
    (native) or :func:`registration_map_from_tmap` (imported); both yield this one
    type so the two are directly comparable (§7.1 apply-both parity).

    "reference" is the donor half (the registration anchor); "moving" is the
    acceptor half mapped onto it. :meth:`apply_reference_to_moving` warps a 0-based
    reference ``[x, y]`` into acceptor coordinates (the donor-anchored read of
    Stage 11), :meth:`apply_moving_to_reference` the inverse.
    """

    reference_channel: int
    moving_channel: int
    ref_to_moving: PolyTransform2D
    moving_to_ref: PolyTransform2D
    rms_residual: float
    n_control_points: int
    gate_px: float = DEFAULT_RMS_GATE_PX
    degree: int = _DEGREE_POLYNOMIAL
    source: Literal["native", "imported"] = "native"
    reference_geometry: ChannelGeometry | None = None
    moving_geometry: ChannelGeometry | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.reference_channel == self.moving_channel:
            raise ValueError(
                f"reference and moving channel must differ (both {self.reference_channel})"
            )
        for name, tf in (
            ("ref_to_moving", self.ref_to_moving),
            ("moving_to_ref", self.moving_to_ref),
        ):
            if not isinstance(tf, PolyTransform2D):
                raise TypeError(f"{name} must be a PolyTransform2D, got {type(tf).__name__}")
        if self.n_control_points < 0:
            raise ValueError(f"n_control_points must be >= 0, got {self.n_control_points}")
        if not (self.gate_px > 0):
            raise ValueError(f"gate_px must be > 0, got {self.gate_px}")
        if self.source not in ("native", "imported"):
            raise ValueError(f"source must be 'native' or 'imported', got {self.source!r}")

    @property
    def low_confidence(self) -> bool:
        """Whether the residual exceeds the gate (the §7.1 over-gate verdict).

        A non-finite residual (e.g. an imported map with no control points to
        measure against) is **not** treated as low-confidence -- the gate only
        fires on a finite residual strictly above it.
        """
        return bool(np.isfinite(self.rms_residual) and self.rms_residual > self.gate_px)

    @property
    def molecule_tags(self) -> tuple[str, ...]:
        """Tags every molecule this calibration extracts must carry (§7.1).

        ``(LOW_CONFIDENCE_TAG,)`` for an over-gate calibration, else empty. The
        integration step (M1 S8) imprints these onto ``/molecules.tags`` so a
        low-confidence registration is visible downstream and **never** silently
        drops a molecule.
        """
        return (LOW_CONFIDENCE_TAG,) if self.low_confidence else ()

    def apply_reference_to_moving(self, points: np.ndarray) -> np.ndarray:
        """Warp 0-based reference (donor) ``[x, y]`` into moving (acceptor) coords."""
        return self.ref_to_moving.apply(points)

    def apply_moving_to_reference(self, points: np.ndarray) -> np.ndarray:
        """Warp 0-based moving (acceptor) ``[x, y]`` into reference (donor) coords."""
        return self.moving_to_ref.apply(points)


# --- native fit (Stage 9) ----------------------------------------------------


def _fit_similarity(src: np.ndarray, dst: np.ndarray) -> PolyTransform2D:
    """Least-squares 4-DOF similarity (scale + rotation + translation) as a poly.

    The Stage-9 "similarity fallback" for fewer than six control points, solved in
    closed form (Umeyama, reflection excluded) and expressed as a degree-2
    :class:`PolyTransform2D` with zero quadratic terms, so the calibration stays a
    single uniform type regardless of which rung produced it. Settled linear
    algebra (no FRET/biophysics fact -> no Consensus gate).
    """
    n = len(src)
    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst
    var_src = float((src_c**2).sum() / n)
    if var_src <= 0:  # all source points coincide -> rotation/scale undefined
        raise ValueError("similarity fallback needs non-coincident control points")
    cov = (dst_c.T @ src_c) / n  # 2x2 cross-covariance
    u, d, vt = np.linalg.svd(cov)
    s_corr = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:  # exclude a reflection
        s_corr[-1, -1] = -1.0
    rot = u @ s_corr @ vt  # 2x2 rotation
    scale = float((d * np.diag(s_corr)).sum() / var_src)
    linear = scale * rot  # maps src -> dst as out = linear @ pt + offset
    offset = mu_dst - linear @ mu_src
    # Polynomial basis is [1, x, y, x*y, x**2, y**2]; the affine part fills the
    # first three coefficients (constant, x, y), the quadratic terms are zero.
    a = np.array([offset[0], linear[0, 0], linear[0, 1], 0.0, 0.0, 0.0])
    b = np.array([offset[1], linear[1, 0], linear[1, 1], 0.0, 0.0, 0.0])
    return PolyTransform2D(a=a, b=b, norm_xy=np.eye(3), norm_uv=np.eye(3))


def _fit_both(src: np.ndarray, dst: np.ndarray) -> tuple[PolyTransform2D, PolyTransform2D, int]:
    """Fit ``src->dst`` and ``dst->src``; return (forward, inverse, degree)."""
    if len(src) >= _MIN_POLY_POINTS:
        return (
            fit_polynomial_transform(src, dst),
            fit_polynomial_transform(dst, src),
            _DEGREE_POLYNOMIAL,
        )
    if len(src) < 2:
        raise ValueError(
            f"registration needs >= 2 control points for a similarity fallback, got {len(src)}"
        )
    return _fit_similarity(src, dst), _fit_similarity(dst, src), _DEGREE_SIMILARITY


def fit_registration_map(
    reference_points: np.ndarray,
    moving_points: np.ndarray,
    *,
    reference_channel: int,
    moving_channel: int,
    reference_geometry: ChannelGeometry | None = None,
    moving_geometry: ChannelGeometry | None = None,
    gate_px: float = DEFAULT_RMS_GATE_PX,
    on_over_gate: Literal["warn", "fail"] = "warn",
    app_version: str | None = None,
    bead_file: str | None = None,
) -> RegistrationMap:
    """Fit a native degree-2 registration map with the RMS gate (Stages 9, §7.1).

    Fits the donor(reference)<->acceptor(moving) polynomial map in both directions
    from matched control points (e.g. the mutual-NN pairs of
    :func:`tether.imaging.register.pair_control_points`), computes the numeric
    per-point RMS residual of the forward map, and applies the over-gate branch.

    Parameters
    ----------
    reference_points, moving_points:
        Matched ``(N, 2)`` ``[x, y]`` control points in the reference (donor) and
        moving (acceptor) frames. ``N >= 6`` fits a degree-2 polynomial; ``2 <= N
        < 6`` falls back to a 4-DOF similarity (Stage 9).
    reference_channel, moving_channel:
        The two distinct channel ids (e.g. donor 1, acceptor 2).
    reference_geometry, moving_geometry:
        Optional per-channel split geometry (crop/rotation/flip), persisted with
        the map (Stage 10). Not required to fit the transform.
    gate_px:
        RMS-residual gate in px (PRD §11.2 default 0.5).
    on_over_gate:
        Batch policy when the residual exceeds the gate (§7.1): ``"warn"`` (the
        default headless accept-with-flag-and-warn -- returns the map flagged
        :attr:`~RegistrationMap.low_confidence` and emits an
        :class:`OverGateRegistrationWarning`) or ``"fail"`` (raise
        :class:`RegistrationOverGateError`, the fail-the-movie policy). A fit
        within the gate proceeds identically under either policy.
    app_version, bead_file:
        Provenance stamps (the Tether version, the bead/grid source file).

    Returns
    -------
    RegistrationMap
        ``source="native"``. A within-gate fit has
        :attr:`~RegistrationMap.low_confidence` ``False``; an over-gate fit (under
        ``"warn"``) is returned flagged, **never** dropped.
    """
    reference_points = np.atleast_2d(np.asarray(reference_points, dtype=np.float64))
    moving_points = np.atleast_2d(np.asarray(moving_points, dtype=np.float64))
    if (
        reference_points.shape != moving_points.shape
        or reference_points.ndim != 2
        or reference_points.shape[1] != 2
    ):
        raise ValueError(
            "reference_points and moving_points must be matching (N, 2) arrays of [x, y]"
        )
    if on_over_gate not in ("warn", "fail"):
        raise ValueError(f"on_over_gate must be 'warn' or 'fail', got {on_over_gate!r}")

    ref_to_moving, moving_to_ref, degree = _fit_both(reference_points, moving_points)
    rms = point_rms(ref_to_moving.apply(reference_points), moving_points)

    reg_map = RegistrationMap(
        reference_channel=int(reference_channel),
        moving_channel=int(moving_channel),
        ref_to_moving=ref_to_moving,
        moving_to_ref=moving_to_ref,
        rms_residual=float(rms),
        n_control_points=int(len(reference_points)),
        gate_px=float(gate_px),
        degree=degree,
        source="native",
        reference_geometry=reference_geometry,
        moving_geometry=moving_geometry,
        provenance={
            "app_version": app_version,
            "bead_file": bead_file,
            "fit": "polynomial-deg2" if degree == _DEGREE_POLYNOMIAL else "similarity",
        },
    )

    _emit_over_gate(reg_map, on_over_gate)
    return reg_map


def _emit_over_gate(reg_map: RegistrationMap, on_over_gate: Literal["warn", "fail"]) -> None:
    """Apply the §7.1 over-gate batch policy to an already-built calibration.

    A no-op for a within-gate (or unknown-residual) calibration. For a
    low-confidence one, ``"fail"`` raises :class:`RegistrationOverGateError`
    (fail-the-movie) and ``"warn"`` emits a structured
    :class:`OverGateRegistrationWarning` (accept-with-flag); the calibration is
    returned flagged either way by the caller -- this never drops a molecule.
    Shared by the native (:func:`fit_registration_map`) and imported
    (:func:`registration_map_from_tmap`) paths so both behave identically in a
    batch run.
    """
    if not reg_map.low_confidence:
        return
    message = (
        f"registration RMS residual {reg_map.rms_residual:.3f} px exceeds the "
        f"{reg_map.gate_px:.3f} px gate (channels {reg_map.reference_channel}->"
        f"{reg_map.moving_channel}, {reg_map.n_control_points} control points, "
        f"source {reg_map.source}): calibration flagged low-confidence; molecules "
        f"will be tagged {LOW_CONFIDENCE_TAG!r}."
    )
    if on_over_gate == "fail":
        raise RegistrationOverGateError(message)
    warnings.warn(message, OverGateRegistrationWarning, stacklevel=3)


# --- imported path (§7.1; §9 M1 conjunctive deliverable) ---------------------


def registration_map_from_tmap(
    channels: dict[int, TmapChannel],
    *,
    moving_channel: int | None = None,
    reference_channel: int | None = None,
    reference_points: np.ndarray | None = None,
    moving_points: np.ndarray | None = None,
    gate_px: float = DEFAULT_RMS_GATE_PX,
    on_over_gate: Literal["warn", "fail"] = "warn",
    app_version: str | None = None,
    source_file: str | None = None,
) -> RegistrationMap:
    """Build a :class:`RegistrationMap` from a decoded ``.tmap`` (the imported path).

    Reuses Deep-LASI's stored polynomial transforms instead of fitting natively
    (§7.1 "support both ... and an imported ``.tmap``"; §9 M1). ``channels`` is the
    output of :func:`tether.imaging.register.read_tmap`. The reference channel
    defaults to the lowest id (the donor half, whose own map is identity); the
    moving channel defaults to the single other channel when there is exactly one.

    The imported transforms are the ``.tmap``'s ``reference -> channel`` and
    ``channel -> reference`` maps (0-based via the :class:`TmapChannel` helpers).
    The crop origin is taken from the ``.tmap``; per-channel rotation/flip are not
    yet decoded (S7) and are left unset. When matched control points
    (``reference_points`` / ``moving_points``, e.g. colocalized molecule pairs) are
    supplied, the imported map's RMS residual is measured at them and the same
    over-gate policy as the native path applies (``on_over_gate``: ``"warn"`` flags
    + emits :class:`OverGateRegistrationWarning`, ``"fail"`` raises
    :class:`RegistrationOverGateError`); otherwise the residual is non-finite
    ("unknown", which never trips the over-gate flag).

    Returns
    -------
    RegistrationMap
        ``source="imported"``, directly comparable to a native fit of the same
        movie (§7.1 apply-both parity).
    """
    if not channels:
        raise ValueError("channels is empty (decode a .tmap with read_tmap first)")
    if on_over_gate not in ("warn", "fail"):
        raise ValueError(f"on_over_gate must be 'warn' or 'fail', got {on_over_gate!r}")
    if reference_channel is None:
        reference_channel = min(channels)
    if moving_channel is None:
        others = [cid for cid in channels if cid != reference_channel]
        if len(others) != 1:
            raise ValueError(
                f"moving_channel is ambiguous ({len(others)} non-reference "
                f"channels); pass it explicitly"
            )
        moving_channel = others[0]
    if moving_channel not in channels:
        raise ValueError(f"moving_channel {moving_channel} not in decoded .tmap {sorted(channels)}")
    if reference_channel == moving_channel:
        raise ValueError(f"reference and moving channel must differ (both {reference_channel})")

    moving = channels[moving_channel]
    # The TmapChannel transforms warp channel-local 1-based coords; wrap them in
    # 0-based PolyTransform2Ds (forward = reference -> moving) so the RegistrationMap
    # API is identical to the native path. The +-1 boundary is folded into norm_xy.
    ref_to_moving = _shift_poly(moving.ref_to_channel, shift_in=1.0, shift_out=-1.0)
    moving_to_ref = _shift_poly(moving.channel_to_ref, shift_in=1.0, shift_out=-1.0)

    n_pts = 0
    rms = float("nan")
    if reference_points is not None and moving_points is not None:
        reference_points = np.atleast_2d(np.asarray(reference_points, dtype=np.float64))
        moving_points = np.atleast_2d(np.asarray(moving_points, dtype=np.float64))
        if reference_points.shape != moving_points.shape:
            raise ValueError("reference_points and moving_points must have matching shape")
        n_pts = int(len(reference_points))
        rms = float(point_rms(ref_to_moving.apply(reference_points), moving_points))

    moving_geometry = ChannelGeometry(
        crop=tuple(np.asarray(moving.crop, dtype=int).ravel().tolist())
    )

    reg_map = RegistrationMap(
        reference_channel=int(reference_channel),
        moving_channel=int(moving_channel),
        ref_to_moving=ref_to_moving,
        moving_to_ref=moving_to_ref,
        rms_residual=rms,
        n_control_points=n_pts,
        gate_px=float(gate_px),
        degree=_DEGREE_POLYNOMIAL,
        source="imported",
        reference_geometry=None,
        moving_geometry=moving_geometry,
        provenance={"app_version": app_version, "source_file": source_file, "fit": "imported-tmap"},
    )
    _emit_over_gate(reg_map, on_over_gate)
    return reg_map


def _shift_poly(poly: PolyTransform2D, *, shift_in: float, shift_out: float) -> PolyTransform2D:
    """Pre/post-shift a polynomial's pixel frame by folding offsets into the norms.

    ``TmapChannel`` maps are in MATLAB 1-based pixels; ``reference_to_channel``
    composes ``+1`` (Tether 0-based -> MATLAB 1-based) before and ``-1`` after the
    raw transform. To expose a transform that consumes/produces 0-based pixels
    directly, fold a ``+shift_in`` translation into ``norm_xy`` (applied to the
    input before the polynomial) and a ``+shift_out`` into ``norm_uv`` (applied to
    the output after it). With ``shift_in=+1, shift_out=-1`` this is exactly the
    0-based wrapper the :class:`TmapChannel` helpers apply at call time, baked into
    the coefficients so the resulting :class:`PolyTransform2D` is self-contained.
    """
    pre = np.eye(3)
    pre[2, 0], pre[2, 1] = shift_in, shift_in  # [x, y, 1] @ pre = [x + shift_in, y + shift_in, 1]
    post = np.eye(3)
    post[2, 0], post[2, 1] = shift_out, shift_out
    # apply(): xn = ([pt, 1] @ inv(norm_xy))[:2]; out = [P(xn), 1] @ norm_uv.
    # Prepending +shift_in to the input means inv(new_norm_xy) = pre @ inv(norm_xy),
    # i.e. new_norm_xy = norm_xy @ inv(pre); appending +shift_out means new_norm_uv
    # = norm_uv @ post.
    return PolyTransform2D(
        a=poly.a,
        b=poly.b,
        norm_xy=poly.norm_xy @ np.linalg.inv(pre),
        norm_uv=poly.norm_uv @ post,
    )


# --- map-file persistence (Stage 10) -----------------------------------------


def _geometry_to_arrays(prefix: str, geom: ChannelGeometry | None) -> dict[str, np.ndarray]:
    """Flatten an optional ChannelGeometry to npz-safe arrays (no pickling)."""
    if geom is None:
        return {f"{prefix}_present": np.array(0, dtype=np.int64)}
    crop = (
        np.array([], dtype=np.int64) if geom.crop is None else np.asarray(geom.crop, dtype=np.int64)
    )
    return {
        f"{prefix}_present": np.array(1, dtype=np.int64),
        f"{prefix}_crop": crop,
        f"{prefix}_rotation_deg": np.array(geom.rotation_deg, dtype=np.int64),
        f"{prefix}_flip": np.asarray(geom.flip, dtype=np.int64),
    }


def _geometry_from_arrays(prefix: str, data: Any) -> ChannelGeometry | None:
    if int(data[f"{prefix}_present"]) == 0:
        return None
    crop_arr = np.asarray(data[f"{prefix}_crop"], dtype=np.int64)
    crop = None if crop_arr.size == 0 else tuple(crop_arr.tolist())
    return ChannelGeometry(
        crop=crop,
        rotation_deg=int(data[f"{prefix}_rotation_deg"]),
        flip=tuple(np.asarray(data[f"{prefix}_flip"], dtype=np.int64).tolist()),
    )


def _poly_arrays(prefix: str, poly: PolyTransform2D) -> dict[str, np.ndarray]:
    return {
        f"{prefix}_a": np.asarray(poly.a, dtype=np.float64),
        f"{prefix}_b": np.asarray(poly.b, dtype=np.float64),
        f"{prefix}_norm_xy": np.asarray(poly.norm_xy, dtype=np.float64),
        f"{prefix}_norm_uv": np.asarray(poly.norm_uv, dtype=np.float64),
    }


def _poly_from_arrays(prefix: str, data: Any) -> PolyTransform2D:
    return PolyTransform2D(
        a=np.asarray(data[f"{prefix}_a"], dtype=np.float64),
        b=np.asarray(data[f"{prefix}_b"], dtype=np.float64),
        norm_xy=np.asarray(data[f"{prefix}_norm_xy"], dtype=np.float64),
        norm_uv=np.asarray(data[f"{prefix}_norm_uv"], dtype=np.float64),
    )


def save_map(reg_map: RegistrationMap, path: str | Path) -> Path:
    """Persist a :class:`RegistrationMap` to a ``.npz`` map file (Stage 10).

    Stores explicit coefficients (both directions), geometry, the RMS residual and
    gate, the control-point count, source, degree, and provenance. **No raw images
    and no pickled objects** -- everything is a plain array or a 0-d string, so the
    file reloads with ``allow_pickle=False`` (the default). Map files are kept
    separate from session ``.tether`` files (mirroring ``.tmap`` vs ``.tdat``).
    """
    path = Path(path)
    arrays: dict[str, np.ndarray] = {
        "reference_channel": np.array(reg_map.reference_channel, dtype=np.int64),
        "moving_channel": np.array(reg_map.moving_channel, dtype=np.int64),
        "rms_residual": np.array(reg_map.rms_residual, dtype=np.float64),
        "n_control_points": np.array(reg_map.n_control_points, dtype=np.int64),
        "gate_px": np.array(reg_map.gate_px, dtype=np.float64),
        "degree": np.array(reg_map.degree, dtype=np.int64),
        "source": np.array(reg_map.source),
        "provenance_json": np.array(_provenance_to_json(reg_map.provenance)),
    }
    arrays.update(_poly_arrays("ref_to_moving", reg_map.ref_to_moving))
    arrays.update(_poly_arrays("moving_to_ref", reg_map.moving_to_ref))
    arrays.update(_geometry_to_arrays("reference_geometry", reg_map.reference_geometry))
    arrays.update(_geometry_to_arrays("moving_geometry", reg_map.moving_geometry))
    np.savez(path, **arrays)
    # np.savez appends ".npz" unless the suffix is *exactly* ".npz" -- mirror that
    # so the returned path is the file actually written (e.g. "m.dat" -> "m.dat.npz").
    return path if path.suffix == ".npz" else path.with_suffix(path.suffix + ".npz")


def load_map(path: str | Path) -> RegistrationMap:
    """Load a :class:`RegistrationMap` from a ``.npz`` map file (:func:`save_map`)."""
    with np.load(Path(path), allow_pickle=False) as data:
        return RegistrationMap(
            reference_channel=int(data["reference_channel"]),
            moving_channel=int(data["moving_channel"]),
            ref_to_moving=_poly_from_arrays("ref_to_moving", data),
            moving_to_ref=_poly_from_arrays("moving_to_ref", data),
            rms_residual=float(data["rms_residual"]),
            n_control_points=int(data["n_control_points"]),
            gate_px=float(data["gate_px"]),
            degree=int(data["degree"]),
            source=str(data["source"]),
            reference_geometry=_geometry_from_arrays("reference_geometry", data),
            moving_geometry=_geometry_from_arrays("moving_geometry", data),
            provenance=_provenance_from_json(str(data["provenance_json"])),
        )


def _provenance_to_json(provenance: dict[str, Any]) -> str:
    import json  # noqa: PLC0415

    return json.dumps(provenance, sort_keys=True)


def _provenance_from_json(blob: str) -> dict[str, Any]:
    import json  # noqa: PLC0415

    return json.loads(blob)


# --- project /calibration persistence (additive data, M0 freeze) -------------


def write_calibration(
    project_path: str | Path, reg_map: RegistrationMap, *, calibration_id: str
) -> str:
    """Write a calibration into a project's ``/calibration`` group as additive data.

    Persists ``reg_map`` under ``/calibration/<calibration_id>`` of an existing
    ``.tether`` store: a subgroup per direction holding the coefficient arrays, plus
    group attributes for the scalar provenance (RMS residual, gate, control-point
    count, degree, source, channels, ``low_confidence`` verdict, and the geometry).
    This is **additive data** -- the frozen ``/calibration`` container group itself
    is untouched, so the M0 schema freeze (``schema-guard``) stays green.

    Returns the ``/calibration/<calibration_id>`` path written.
    """
    import h5py  # noqa: PLC0415

    project_path = Path(project_path)
    str_dt = h5py.string_dtype(encoding="utf-8")
    with h5py.File(project_path, "a") as f:
        cal = f.require_group(_CALIBRATION_GROUP)
        if calibration_id in cal:
            raise ValueError(
                f"calibration '{calibration_id}' already exists in {project_path} "
                f"(calibrations are write-once; use a fresh id)"
            )
        grp = cal.create_group(calibration_id, track_order=True)
        for name, poly in (
            ("ref_to_moving", reg_map.ref_to_moving),
            ("moving_to_ref", reg_map.moving_to_ref),
        ):
            sub = grp.create_group(name, track_order=True)
            sub.create_dataset("a", data=np.asarray(poly.a, dtype=np.float64))
            sub.create_dataset("b", data=np.asarray(poly.b, dtype=np.float64))
            sub.create_dataset("norm_xy", data=np.asarray(poly.norm_xy, dtype=np.float64))
            sub.create_dataset("norm_uv", data=np.asarray(poly.norm_uv, dtype=np.float64))
        grp.attrs["reference_channel"] = reg_map.reference_channel
        grp.attrs["moving_channel"] = reg_map.moving_channel
        grp.attrs["rms_residual"] = reg_map.rms_residual
        grp.attrs["n_control_points"] = reg_map.n_control_points
        grp.attrs["gate_px"] = reg_map.gate_px
        grp.attrs["degree"] = reg_map.degree
        grp.attrs["low_confidence"] = bool(reg_map.low_confidence)
        grp.attrs["source"] = np.array(reg_map.source, dtype=str_dt)
        grp.attrs["provenance_json"] = np.array(
            _provenance_to_json(reg_map.provenance), dtype=str_dt
        )
        _write_geometry_attrs(grp, "reference_geometry", reg_map.reference_geometry)
        _write_geometry_attrs(grp, "moving_geometry", reg_map.moving_geometry)
    return f"/{_CALIBRATION_GROUP}/{calibration_id}"


def read_calibration(project_path: str | Path, calibration_id: str) -> RegistrationMap:
    """Read a calibration from ``/calibration/<calibration_id>`` (see :func:`write_calibration`)."""
    import h5py  # noqa: PLC0415

    project_path = Path(project_path)
    with h5py.File(project_path, "r") as f:
        try:
            grp = f[f"{_CALIBRATION_GROUP}/{calibration_id}"]
        except KeyError as exc:
            raise KeyError(f"no calibration '{calibration_id}' in {project_path}") from exc
        polys = {}
        for name in ("ref_to_moving", "moving_to_ref"):
            sub = grp[name]
            polys[name] = PolyTransform2D(
                a=np.asarray(sub["a"], dtype=np.float64),
                b=np.asarray(sub["b"], dtype=np.float64),
                norm_xy=np.asarray(sub["norm_xy"], dtype=np.float64),
                norm_uv=np.asarray(sub["norm_uv"], dtype=np.float64),
            )
        return RegistrationMap(
            reference_channel=int(grp.attrs["reference_channel"]),
            moving_channel=int(grp.attrs["moving_channel"]),
            ref_to_moving=polys["ref_to_moving"],
            moving_to_ref=polys["moving_to_ref"],
            rms_residual=float(grp.attrs["rms_residual"]),
            n_control_points=int(grp.attrs["n_control_points"]),
            gate_px=float(grp.attrs["gate_px"]),
            degree=int(grp.attrs["degree"]),
            source=_attr_str(grp.attrs["source"]),
            reference_geometry=_read_geometry_attrs(grp, "reference_geometry"),
            moving_geometry=_read_geometry_attrs(grp, "moving_geometry"),
            provenance=_provenance_from_json(_attr_str(grp.attrs["provenance_json"])),
        )


def _attr_str(value: Any) -> str:
    """Normalise an HDF5 string attribute (str or bytes) to ``str``."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _write_geometry_attrs(grp: Any, prefix: str, geom: ChannelGeometry | None) -> None:
    if geom is None:
        grp.attrs[f"{prefix}_present"] = False
        return
    grp.attrs[f"{prefix}_present"] = True
    grp.attrs[f"{prefix}_has_crop"] = geom.crop is not None
    if geom.crop is not None:
        grp.attrs[f"{prefix}_crop"] = np.asarray(geom.crop, dtype=np.int64)
    grp.attrs[f"{prefix}_rotation_deg"] = int(geom.rotation_deg)
    grp.attrs[f"{prefix}_flip"] = np.asarray(geom.flip, dtype=np.int64)


def _read_geometry_attrs(grp: Any, prefix: str) -> ChannelGeometry | None:
    if not bool(grp.attrs[f"{prefix}_present"]):
        return None
    crop = None
    if bool(grp.attrs[f"{prefix}_has_crop"]):
        crop = tuple(np.asarray(grp.attrs[f"{prefix}_crop"], dtype=np.int64).tolist())
    return ChannelGeometry(
        crop=crop,
        rotation_deg=int(grp.attrs[f"{prefix}_rotation_deg"]),
        flip=tuple(np.asarray(grp.attrs[f"{prefix}_flip"], dtype=np.int64).tolist()),
    )
