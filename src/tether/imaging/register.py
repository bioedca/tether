# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dual-view channel registration (PRD Appendix E Stages 6-10; §11.1/§11.2; M0.5 S6).

Tether splits one camera frame into a donor (reference) and an acceptor half and
must map a coordinate from one half onto the other. Deep-LASI persists that map in
a ``.tmap`` file as MATLAB ``images.geotrans.PolynomialTransformation2D`` objects;
Tether validates a *native* polynomial fit against it.

This module builds that pipeline incrementally (the native bead-detection ->
phase-correlation prealign -> nearest-neighbour pairing -> degree-2 fit chain of
M1 S5/S6):

* :class:`SimilarityTransform2D` + :func:`estimate_translation_prealign` /
  :func:`estimate_similarity_prealign` -- the coarse phase-correlation prealign
  that seeds pairing (Appendix E Stage 7). M1 S5a lands the translation DOF; M1
  S5b lands the full 4-DOF rotation+scale estimate -- a Fourier-Mellin log-polar
  pass, the faithful analogue of Deep-LASI ``imregcorr(..., 'similarity')``
  (``createMapPhaseCorr.m:11``) -- reusing the same transform type (ADR-0012,
  ADR-0013);
* :func:`pair_control_points` -- mutual nearest-neighbour pairing within a px
  tolerance (Appendix E Stage 8), matched in the prealigned frame but returning
  the original un-prealigned moving coords for the fit (ADR-0012);
* :class:`PolyTransform2D` -- a degree-2 2-D polynomial warp in the exact MATLAB
  form (per-output coefficient vectors ``A`` (x) and ``B`` (y) in the basis
  ``[1, x, y, x*y, x**2, y**2]`` with input/output normalisation affines), shared
  by both the decoded ``.tmap`` transform and the native fit so a residual is a
  like-for-like point comparison;
* :func:`fit_polynomial_transform` -- a normalised least-squares degree-2 fit from
  matched control points (mirrors ``fitgeotrans(...,'polynomial',2)``);
* :func:`read_tmap` -- decode a Deep-LASI ``.tmap`` (a classic MATLAB v5 MAT-file
  whose transform coefficients live in the MCOS ``__function_workspace__`` blob);
* :func:`point_rms` -- the RMS of per-point Euclidean residuals, the registration
  quality number (§9 M0.5(b): native RMS <= 0.5 px vs the ``.tmap``).

Coordinate convention follows the rest of :mod:`tether.imaging`: points are
``(N, 2)`` arrays of ``[x, y] = [col, row]``. ``PolyTransform2D`` evaluates the
raw polynomial in whatever frame it was built; the decoded ``.tmap`` transforms
are in MATLAB 1-based pixel coordinates, so :class:`TmapChannel` exposes
``reference_to_channel``/``channel_to_reference`` helpers that convert Tether's
0-based ``[x, y]`` across that boundary (PRD §11.1).

Reference: Deep-LASI ``mapping/createMap.m``, ``mapping/createMapPhaseCorr.m``,
``mapping/findColoc.m``, ``classes/TIRFdata.m`` (read-only sibling).
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import scipy.io as sio
from scipy.spatial import cKDTree

__all__ = [
    "PairedControlPoints",
    "PolyTransform2D",
    "SimilarityTransform2D",
    "TmapChannel",
    "estimate_similarity_prealign",
    "estimate_translation_prealign",
    "fit_polynomial_transform",
    "pair_control_points",
    "point_rms",
    "poly_basis_deg2",
    "read_tmap",
]


def poly_basis_deg2(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Degree-2 polynomial design matrix in MATLAB's term order.

    Returns the ``(N, 6)`` basis ``[1, x, y, x*y, x**2, y**2]`` used by
    ``images.geotrans.PolynomialTransformation2D`` (and reproduced by the native
    fit). The term order is load-bearing: it is the order the decoded ``.tmap``
    ``A``/``B`` coefficient vectors are stored in.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    return np.stack([np.ones_like(x), x, y, x * y, x * x, y * y], axis=1)


@dataclass(frozen=True)
class PolyTransform2D:
    """A degree-2 2-D polynomial geometric transform.

    Evaluates ``out = denorm(P(norm(pt)))`` where ``norm`` applies
    ``inv(norm_xy)`` to the input point, ``P`` is the degree-2 polynomial with
    per-output coefficient vectors ``A`` (output x) and ``B`` (output y) in the
    :func:`poly_basis_deg2` basis, and ``denorm`` applies ``norm_uv`` to the
    polynomial output. ``norm_xy``/``norm_uv`` are ``3x3`` affines in MATLAB's
    post-multiply convention (``[x, y, 1] @ M``); identity normalisation reduces
    the transform to a plain polynomial in the input frame.
    """

    a: np.ndarray  # (6,) output-x coefficients
    b: np.ndarray  # (6,) output-y coefficients
    norm_xy: np.ndarray  # (3, 3) input normalisation affine
    norm_uv: np.ndarray  # (3, 3) output denormalisation affine

    def __post_init__(self) -> None:
        for name, arr, shape in (
            ("a", self.a, (6,)),
            ("b", self.b, (6,)),
            ("norm_xy", self.norm_xy, (3, 3)),
            ("norm_uv", self.norm_uv, (3, 3)),
        ):
            if np.asarray(arr).shape != shape:
                raise ValueError(f"PolyTransform2D.{name} must have shape {shape}")

    def apply(self, points: np.ndarray) -> np.ndarray:
        """Map ``(N, 2)`` ``[x, y]`` points through the transform (returns ``(N, 2)``)."""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise ValueError("points must be an (N, 2) array of [x, y]")
        homog = np.hstack([pts, np.ones((len(pts), 1))])
        xn = (homog @ np.linalg.inv(self.norm_xy))[:, :2]  # pixel -> normalised input
        terms = poly_basis_deg2(xn[:, 0], xn[:, 1])
        uv = np.stack([terms @ self.a, terms @ self.b], axis=1)  # normalised output
        homog_uv = np.hstack([uv, np.ones((len(uv), 1))])
        return (homog_uv @ self.norm_uv)[:, :2]  # normalised -> pixel


def fit_polynomial_transform(
    src: np.ndarray, dst: np.ndarray, *, normalize: bool = True
) -> PolyTransform2D:
    """Least-squares degree-2 polynomial fit mapping ``src`` -> ``dst``.

    ``src`` and ``dst`` are matched ``(N, 2)`` ``[x, y]`` control points (e.g. the
    colocalized molecule pairs from a ``.tdat``). Mirrors MATLAB
    ``fitgeotrans(src, dst, 'polynomial', 2)``: by default the input points are
    centred/scaled before the fit (``normalize=True``) for conditioning, exactly
    as ``images.geotrans.PolynomialTransformation2D`` does, with the normalisation
    folded into ``norm_xy`` so :meth:`PolyTransform2D.apply` consumes raw pixels.
    The output stays in pixel coordinates (``norm_uv`` is identity), so the fit
    lives in whatever frame ``src``/``dst`` are given in.

    A degree-2 fit needs at least 6 non-degenerate control points.
    """
    src = np.atleast_2d(np.asarray(src, dtype=np.float64))
    dst = np.atleast_2d(np.asarray(dst, dtype=np.float64))
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2:
        raise ValueError("src and dst must be matching (N, 2) arrays of [x, y]")
    if len(src) < 6:
        raise ValueError(f"degree-2 fit needs >= 6 control points, got {len(src)}")

    if normalize:
        mean = src.mean(axis=0)
        scale = src.std(axis=0)
        scale = np.where(scale > 0, scale, 1.0)  # guard a degenerate (collinear) axis
        xn = (src - mean) / scale
        # norm_xy maps normalised -> pixel ([x, y, 1] @ norm_xy); its inverse, used
        # in apply(), maps pixel -> normalised: (pixel - mean) / scale.
        norm_xy = np.array([[scale[0], 0.0, 0.0], [0.0, scale[1], 0.0], [mean[0], mean[1], 1.0]])
    else:
        xn = src
        norm_xy = np.eye(3)

    terms = poly_basis_deg2(xn[:, 0], xn[:, 1])
    if np.linalg.matrix_rank(terms) < 6:
        # Enough points but degenerate (duplicate/collinear) -> rank-deficient
        # design matrix; lstsq would silently return an underdetermined fit.
        raise ValueError("degree-2 fit needs 6 non-degenerate control points")
    a, *_ = np.linalg.lstsq(terms, dst[:, 0], rcond=None)
    b, *_ = np.linalg.lstsq(terms, dst[:, 1], rcond=None)
    return PolyTransform2D(a=a, b=b, norm_xy=norm_xy, norm_uv=np.eye(3))


def point_rms(a: np.ndarray, b: np.ndarray) -> float:
    """RMS of per-point Euclidean residuals between two ``(N, 2)`` point sets."""
    a = np.atleast_2d(np.asarray(a, dtype=np.float64))
    b = np.atleast_2d(np.asarray(b, dtype=np.float64))
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2:
        raise ValueError("a and b must be matching (N, 2) arrays of [x, y]")
    if len(a) == 0:
        raise ValueError("point_rms is undefined for zero points")
    return float(np.sqrt(np.mean(np.sum((a - b) ** 2, axis=1))))


# --- prealign + nearest-neighbour pairing (Appendix E Stages 7-8) ------------


@dataclass(frozen=True)
class SimilarityTransform2D:
    """A 4-DOF similarity transform: isotropic scale + rotation + translation.

    Evaluates ``out = scale * R(rotation) @ [x, y] + translation`` with ``R`` the
    standard counter-clockwise rotation, in Tether's ``[x, y] = [col, row]``
    convention. This is the *registration prealign* (PRD Appendix E Stage 7;
    ``deeplasi/functions/mapping/createMapPhaseCorr.m:11`` ``imregcorr(...,
    'similarity')``): a coarse map whose only job is to bring corresponding
    control points close enough for nearest-neighbour pairing. It is **never**
    composed into the stored polynomial calibration — the map is always fit on
    the original, un-prealigned coordinates (Stage 8; see
    :func:`pair_control_points`).

    M1 S5a populates this from a translation-only phase correlation
    (:func:`estimate_translation_prealign`, the scale-1/rotation-0 special case);
    M1 S5b populates the full 4-DOF rotation+scale estimate from a Fourier-Mellin
    log-polar pass (:func:`estimate_similarity_prealign`). The scale/rotation
    fields are first-class here so both estimators share one type (ADR-0012,
    ADR-0013).
    """

    scale: float
    rotation: float  # radians, counter-clockwise
    translation: np.ndarray  # (2,) [tx, ty]

    def __post_init__(self) -> None:
        translation = np.asarray(self.translation, dtype=np.float64)
        if translation.shape != (2,):
            raise ValueError("SimilarityTransform2D.translation must have shape (2,)")
        if not np.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("SimilarityTransform2D.scale must be finite and > 0")
        if not np.isfinite(self.rotation):
            raise ValueError("SimilarityTransform2D.rotation must be finite")
        if not np.all(np.isfinite(translation)):
            raise ValueError("SimilarityTransform2D.translation must be finite")
        object.__setattr__(self, "translation", translation)

    def apply(self, points: np.ndarray) -> np.ndarray:
        """Map ``(N, 2)`` ``[x, y]`` points through the transform (returns ``(N, 2)``)."""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise ValueError("points must be an (N, 2) array of [x, y]")
        cos_t, sin_t = np.cos(self.rotation), np.sin(self.rotation)
        rot = self.scale * np.array([[cos_t, -sin_t], [sin_t, cos_t]])
        return pts @ rot.T + np.asarray(self.translation, dtype=np.float64)


def estimate_translation_prealign(
    reference: np.ndarray, moving: np.ndarray, *, upsample_factor: int = 10
) -> SimilarityTransform2D:
    """Estimate the translation that seeds pairing, via phase correlation (Stage 7).

    The translation-DOF analogue of Deep-LASI's ``imregcorr`` prealign:
    ``skimage.registration.phase_cross_correlation`` finds the whole-image shift
    aligning ``moving`` onto ``reference`` to ``1 / upsample_factor`` px. The
    returned :class:`SimilarityTransform2D` (scale 1, rotation 0) maps a moving
    ``[x, y]`` point into the reference frame; pass it to
    :func:`pair_control_points` as ``prealign``.

    Parameters
    ----------
    reference, moving:
        2-D single-channel images of the **same shape** (e.g. the per-half bead
        detection images from :func:`tether.imaging.detect.detection_image`).
    upsample_factor:
        Sub-pixel registration upsampling (PRD §11.2 default 10): the shift is
        resolved to ``1 / upsample_factor`` px.
    """
    reference = np.asarray(reference, dtype=np.float64)
    moving = np.asarray(moving, dtype=np.float64)
    if reference.ndim != 2 or moving.ndim != 2:
        raise ValueError("reference and moving must be 2-D images")
    if reference.shape != moving.shape:
        raise ValueError(
            f"reference {reference.shape} and moving {moving.shape} must have the same shape"
        )
    if upsample_factor < 1:
        raise ValueError(f"upsample_factor must be >= 1, got {upsample_factor}")

    from skimage.registration import phase_cross_correlation  # noqa: PLC0415 (heavy, isolated)

    shift, _error, _phasediff = phase_cross_correlation(
        reference, moving, upsample_factor=upsample_factor
    )
    # phase_cross_correlation returns the shift in numpy [row, col] order that
    # registers `moving` onto `reference`: a feature at p_moving maps to
    # p_reference = p_moving + [shift_col, shift_row]. Convert to [x, y].
    translation = np.array([shift[1], shift[0]], dtype=np.float64)
    return SimilarityTransform2D(scale=1.0, rotation=0.0, translation=translation)


def _masked_ncc(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    """Normalised cross-correlation of ``a`` and ``b`` over ``mask`` (-1 if too small)."""
    if int(mask.sum()) < 64:
        return -1.0
    av = a[mask] - a[mask].mean()
    bv = b[mask] - b[mask].mean()
    denom = float(np.sqrt((av * av).sum() * (bv * bv).sum()))
    return float((av * bv).sum() / denom) if denom > 0 else -1.0


def estimate_similarity_prealign(
    reference: np.ndarray,
    moving: np.ndarray,
    *,
    upsample_factor: int = 10,
    low_sigma: float = 3.0,
    high_sigma: float = 20.0,
) -> SimilarityTransform2D:
    """Estimate a 4-DOF similarity prealign (rotation + scale + translation), Stage 7.

    The faithful Python analogue of Deep-LASI's ``imregcorr(...,'similarity')``
    (``deeplasi/functions/mapping/createMapPhaseCorr.m:11``), which recovers
    translation **and** rotation **and** isotropic scale by frequency-domain
    (Fourier-Mellin) registration. Where :func:`estimate_translation_prealign`
    covers only the translation DOF (M1 S5a), this adds rotation + scale (M1 S5b,
    ADR-0013). The returned :class:`SimilarityTransform2D` maps a ``moving``
    ``[x, y]`` point into the ``reference`` frame; pass it to
    :func:`pair_control_points` as ``prealign``. ``reference`` defaults to the
    donor half (PRD §11.2).

    Method (the canonical skimage log-polar recipe, version-matched to the base
    ``conda-lock`` scikit-image 0.26):

    1. band-pass each image (``difference_of_gaussians``) to suppress DC/background
       and high-frequency noise, then apply a Hann window (``filters.window``);
    2. take the centred FFT magnitude (``scipy.fft.fftshift(fft2(...))``) -- which
       is translation-invariant, so rotation+scale live here alone;
    3. log-polar resample the magnitude (``transform.warp_polar(scaling='log')``)
       and phase-correlate the two (``phase_cross_correlation``,
       ``normalization=None``): the angular shift is rotation, the radial shift is
       log-scale;
    4. the FFT magnitude is centro-symmetric, so rotation is ambiguous mod 180°
       and the scale direction is order-dependent -- so the four candidates
       (rotation ``{θ, θ-180}`` × scale ``{s, 1/s}``) are each materialised, the
       residual translation found by a windowed real-space phase correlation, and
       the candidate with the highest masked-overlap NCC is chosen (no fragile
       hand-derived sign convention).

    **Reliable regime.** Validated for the physical split-sensor case -- the
    near-identity regime of sub-degree rotation and sub-percent scale, which is all
    dual-view channel registration ever needs (the committed calibration crop's
    ``.tmap`` similarity is ≈ 0.04°, ≈ 0.1 % off unity). Larger warps are not
    validated and grow unreliable on sparse fields; rotations approaching ±90° are
    inherently ambiguous from a magnitude spectrum and are out of scope (ADR-0013).

    Parameters
    ----------
    reference, moving:
        2-D single-channel images of the **same shape** (the per-half bead
        detection / calibration images).
    upsample_factor:
        Sub-pixel upsampling of the final translation phase correlation
        (PRD §11.2 default 10): translation resolved to ``1 / upsample_factor`` px.
    low_sigma, high_sigma:
        Band-pass Gaussian std-devs in px (PRD §11.2 prealign row; defaults 3 / 20
        suppress saturated background + pixel noise while keeping bead-scale
        structure).
    """
    reference = np.asarray(reference, dtype=np.float64)
    moving = np.asarray(moving, dtype=np.float64)
    if reference.ndim != 2 or moving.ndim != 2:
        raise ValueError("reference and moving must be 2-D images")
    if reference.shape != moving.shape:
        raise ValueError(
            f"reference {reference.shape} and moving {moving.shape} must have the same shape"
        )
    if upsample_factor < 1:
        raise ValueError(f"upsample_factor must be >= 1, got {upsample_factor}")
    if not (0 < low_sigma < high_sigma):
        raise ValueError(f"require 0 < low_sigma < high_sigma, got {low_sigma}, {high_sigma}")
    # The low-frequency log-polar radius is shape[0]//8; require >= 16 px per axis so
    # radius >= 2 keeps np.log(radius) > 0 (real bead detection images are >= 256 px).
    if min(reference.shape) < 16:
        raise ValueError(f"images must be >= 16 px per axis, got {reference.shape}")
    if not (np.all(np.isfinite(reference)) and np.all(np.isfinite(moving))):
        raise ValueError("reference and moving must be finite (no NaN/inf)")

    # Heavy, GUI-stack-adjacent imports kept local (mirrors estimate_translation_prealign).
    from scipy.fft import fft2, fftshift  # noqa: PLC0415
    from skimage.filters import difference_of_gaussians, window  # noqa: PLC0415
    from skimage.registration import phase_cross_correlation  # noqa: PLC0415
    from skimage.transform import SimilarityTransform, warp, warp_polar  # noqa: PLC0415

    han = window("hann", reference.shape)
    ref_fs = np.abs(fftshift(fft2(difference_of_gaussians(reference, low_sigma, high_sigma) * han)))
    mov_fs = np.abs(fftshift(fft2(difference_of_gaussians(moving, low_sigma, high_sigma) * han)))

    shape = ref_fs.shape
    radius = shape[0] // 8  # restrict to low frequencies (where the bead structure lives)
    warped_ref = warp_polar(ref_fs, radius=radius, output_shape=shape, scaling="log", order=0)
    warped_mov = warp_polar(mov_fs, radius=radius, output_shape=shape, scaling="log", order=0)
    # The magnitude is centro-symmetric: only the [0, 180) angular half is unique.
    half = shape[0] // 2
    (shift_r, shift_c), _err, _pd = phase_cross_correlation(
        warped_ref[:half], warped_mov[:half], upsample_factor=10, normalization=None
    )
    raw_angle = (360.0 / shape[0]) * shift_r
    raw_scale = float(np.exp(shift_c / (shape[1] / np.log(radius))))

    cx, cy = moving.shape[1] / 2.0, moving.shape[0] / 2.0

    def _centred(scale: float, rot_deg: float) -> SimilarityTransform:
        # rotation + isotropic scale about the image centre (translation resolved next).
        return (
            SimilarityTransform(translation=(-cx, -cy))
            + SimilarityTransform(scale=scale, rotation=np.deg2rad(rot_deg))
            + SimilarityTransform(translation=(cx, cy))
        )

    ones = np.ones_like(moving)
    best_score = -np.inf
    best_full = SimilarityTransform()  # identity seed; always overwritten (score >= -1 > -inf)
    for rot_c in (raw_angle, raw_angle - 180.0):
        for scale_c in (raw_scale, 1.0 / raw_scale):
            rs = _centred(scale_c, rot_c)
            moving_rs = warp(moving, rs.inverse, order=1, preserve_range=True)
            # Window before the translation phase correlation to suppress warp-border
            # artefacts that otherwise corrupt the recovered shift.
            (t_r, t_c), _e, _p = phase_cross_correlation(
                reference * han, moving_rs * han, upsample_factor=upsample_factor
            )
            full = rs + SimilarityTransform(translation=(float(t_c), float(t_r)))
            aligned = warp(moving, full.inverse, order=1, preserve_range=True)
            cover = warp(ones, full.inverse, order=0, preserve_range=True) > 0.5
            score = _masked_ncc(reference, aligned, cover)
            if score > best_score:
                best_score, best_full = score, full

    sim = SimilarityTransform(matrix=best_full.params)
    return SimilarityTransform2D(
        scale=float(sim.scale),
        rotation=float(sim.rotation),
        translation=np.asarray(sim.translation, dtype=np.float64),
    )


@dataclass(frozen=True)
class PairedControlPoints:
    """Matched control points from :func:`pair_control_points`.

    Attributes
    ----------
    reference:
        ``(K, 2)`` ``[x, y]`` reference-channel points (one per pair).
    moving:
        ``(K, 2)`` ``[x, y]`` moving-channel points — the **original**,
        un-prealigned coordinates (the prealign only seeds matching; the
        polynomial map is fit on these, PRD Appendix E Stage 8).
    reference_index, moving_index:
        ``(K,)`` indices back into the input ``reference`` / ``moving`` arrays.
    """

    reference: np.ndarray
    moving: np.ndarray
    reference_index: np.ndarray
    moving_index: np.ndarray


def pair_control_points(
    reference: np.ndarray,
    moving: np.ndarray,
    *,
    tol: float = 2.0,
    prealign: SimilarityTransform2D | None = None,
) -> PairedControlPoints:
    """Mutual nearest-neighbour pairing of control points within ``tol`` px (Stage 8).

    Ports ``deeplasi/functions/mapping/findPairs.m`` with a deliberate correctness
    fix: matches must be **mutual** — each kept reference point's nearest moving
    point is the same point that chose it, and vice versa — so no point is
    assigned to two partners. Deep-LASI's ``pdist2(...,'Smallest',1)`` is a
    one-directional greedy gate that can map two moving points onto one reference
    point; Tether enforces a unique one-to-one matching instead (ADR-0012).

    Matching runs in the **prealigned** frame when ``prealign`` is given
    (``prealign.apply`` is applied to ``moving`` before the nearest-neighbour
    search), but the returned ``moving`` points are the **original**,
    un-prealigned coordinates — the prealign exists only to make matching
    reliable, never to bake the coarse transform into the fitted map.

    Parameters
    ----------
    reference, moving:
        ``(N, 2)`` / ``(M, 2)`` ``[x, y]`` point sets (e.g. bead centroids from
        :func:`tether.imaging.detect.detect_spots` on each half).
    tol:
        Maximum Euclidean pairing distance in px (PRD §11.2 default 2; up to ~4).
    prealign:
        Optional coarse transform mapping ``moving`` into the reference frame
        (e.g. from :func:`estimate_translation_prealign`).

    Returns
    -------
    PairedControlPoints
        The mutual matches; ``reference``/``moving`` are ready for
        :func:`fit_polynomial_transform`. Empty (``K = 0``) when nothing pairs.
    """
    reference = np.atleast_2d(np.asarray(reference, dtype=np.float64))
    moving = np.atleast_2d(np.asarray(moving, dtype=np.float64))
    for name, arr in (("reference", reference), ("moving", moving)):
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError(f"{name} must be an (N, 2) array of [x, y]")
    if tol <= 0:
        raise ValueError(f"tol must be > 0, got {tol}")

    if len(reference) == 0 or len(moving) == 0:
        empty_i = np.empty(0, dtype=np.intp)
        empty_xy = np.empty((0, 2), dtype=np.float64)
        return PairedControlPoints(empty_xy, empty_xy.copy(), empty_i, empty_i.copy())

    moving_search = prealign.apply(moving) if prealign is not None else moving

    ref_tree = cKDTree(reference)
    moving_tree = cKDTree(moving_search)
    # cKDTree's distance_upper_bound is a strict bound (a point at exactly the
    # bound is a miss), so nudge it up one ULP to make the gate inclusive --
    # faithful to Deep-LASI's `D <= tol` (findPairs.m:22). A cKDTree miss is
    # marked with an infinite distance and index == n (the queried tree's size).
    gate = float(np.nextafter(tol, np.inf))
    # For each moving point: its nearest reference within tol.
    dist_m, nn_ref = ref_tree.query(moving_search, k=1, distance_upper_bound=gate)
    # For each reference point: its nearest moving within tol.
    _dist_r, nn_moving = moving_tree.query(reference, k=1, distance_upper_bound=gate)

    n_ref = len(reference)
    ref_idx: list[int] = []
    mov_idx: list[int] = []
    for m, (d, r) in enumerate(zip(dist_m, nn_ref, strict=True)):
        if not np.isfinite(d) or r >= n_ref:  # no reference within tol
            continue
        if nn_moving[r] == m:  # mutual nearest neighbours -> a unique pair
            ref_idx.append(int(r))
            mov_idx.append(int(m))

    ref_arr = np.asarray(ref_idx, dtype=np.intp)
    mov_arr = np.asarray(mov_idx, dtype=np.intp)
    return PairedControlPoints(
        reference=reference[ref_arr],
        moving=moving[mov_arr],  # ORIGINAL (un-prealigned) coords
        reference_index=ref_arr,
        moving_index=mov_arr,
    )


@dataclass(frozen=True)
class TmapChannel:
    """One channel's decoded ``.tmap`` registration (MATLAB 1-based pixel frame).

    MATLAB's ``images.geotrans.PolynomialTransformation2D`` only implements
    ``transformPointsInverse``, so the stored coefficients evaluate in the
    *inverse* direction relative to the field name (deeplasi
    ``classes/TIRFdata.m``): the decoded ``MapToReference`` actually maps
    reference -> this channel (:attr:`ref_to_channel`) and ``MapFromReference``
    maps this channel -> reference (:attr:`channel_to_ref`). The two are
    independently fitted, not algebraic inverses. Transforms are in MATLAB
    1-based pixel coordinates; the :meth:`reference_to_channel` /
    :meth:`channel_to_reference` helpers accept and return Tether 0-based
    ``[x, y]`` and handle that boundary (PRD §11.1).

    The registration is **channel-local**: Deep-LASI extracts each channel as a
    sub-image via ``processImage(I, Rotation, Crop, Flip)`` (``tools/processImage.m``)
    and registers those crops, so :meth:`reference_to_channel` maps a *reference-
    local* coordinate to a *this-channel-local* coordinate. To place a point in
    the raw, un-split movie use :meth:`reference_to_channel_image`, which folds in
    the per-channel crop :attr:`origin`.
    """

    channel_id: int
    crop: np.ndarray  # the channel's crop rect, as stored in the .tmap
    map_particles: np.ndarray  # (M, 2) bead control points, as stored
    ref_to_channel: PolyTransform2D  # decoded MapToReference; apply: reference -> channel
    channel_to_ref: PolyTransform2D  # decoded MapFromReference; apply: channel -> reference

    @property
    def origin(self) -> np.ndarray:
        """0-based ``[x, y]`` pixel origin of this channel's crop in the full frame.

        Deep-LASI's ``processImage`` crops the channel sub-image as
        ``I(y1:y2, x1:x2)`` from the rect ``Crop = [[y1, x1], [y2, x2]]`` (1-based,
        inclusive; ``tools/processImage.m:23-30``). The sub-image's top-left pixel
        is therefore full-frame ``(x1, y1)``; returned 0-based as ``[x1-1, y1-1]``,
        so a channel-local ``[x, y]`` plus this origin is the full-frame position.
        """
        flat = np.asarray(self.crop, dtype=np.float64).ravel()
        if flat.size != 4:
            raise ValueError(
                f"channel {self.channel_id} crop must have 4 elements [y1, x1, y2, x2], "
                f"got {flat.size}"
            )
        y1, x1 = flat[0], flat[1]
        return np.array([x1 - 1.0, y1 - 1.0])

    def reference_to_channel(self, points0: np.ndarray) -> np.ndarray:
        """Map 0-based ``[x, y]`` from the reference channel into this channel (0-based)."""
        return self.ref_to_channel.apply(np.asarray(points0, dtype=np.float64) + 1.0) - 1.0

    def channel_to_reference(self, points0: np.ndarray) -> np.ndarray:
        """Map 0-based ``[x, y]`` from this channel into the reference channel (0-based)."""
        return self.channel_to_ref.apply(np.asarray(points0, dtype=np.float64) + 1.0) - 1.0

    def reference_to_channel_image(
        self,
        points0: np.ndarray,
        *,
        reference_origin: np.ndarray | tuple[float, float] = (0.0, 0.0),
    ) -> np.ndarray:
        """Map full-frame reference ``[x, y]`` to this channel's full-frame position.

        Composes the channel-local registration with the crop geometry: a
        full-frame reference point is rebased to the reference channel's local
        frame (subtract ``reference_origin``), warped into this channel's local
        frame (:meth:`reference_to_channel`), then offset by this channel's crop
        :attr:`origin` back to full-frame pixels. Use this to read the acceptor
        signal in the raw (un-split) movie at a donor coordinate's mapped position
        — the donor-anchored colocalization read (PRD Appendix E Stages 11-13).

        ``reference_origin`` defaults to ``(0, 0)`` (the donor/reference channel
        is conventionally cropped from the frame origin); pass the reference
        channel's :attr:`origin` if it is not.
        """
        pts = np.atleast_2d(np.asarray(points0, dtype=np.float64))
        ref_origin = np.asarray(reference_origin, dtype=np.float64)
        return self.reference_to_channel(pts - ref_origin) + self.origin


# --- .tmap MCOS decode -------------------------------------------------------
#
# A ``.tmap`` is a classic MATLAB v5 MAT-file: variable ``m`` is a 1xN cell of
# per-channel structs whose MapToReference/MapFromReference fields are MCOS
# ``images.geotrans.PolynomialTransformation2D`` objects. scipy surfaces those as
# opaque handles; the actual coefficients live in the file's
# ``__function_workspace__`` (the MCOS subsystem). We re-parse that blob and
# resolve each handle to its property struct.
#
# NB: this leans on a scipy private reader (``scipy.io.matlab._mio5``) to re-read
# the embedded MAT stream -- the only practical path to MCOS coefficients. It runs
# in the fixture-derivation script and a data-present (non-CI) test, never in the
# default required matrix, so a scipy-version drift cannot redden ``main``.


def _opaque_payload(void_record: np.void) -> np.ndarray:
    """Return the trailing payload field of a scipy ``MatlabOpaque`` void record.

    scipy renamed the opaque fields across versions (older: ``s0,s1,s2,arr``;
    >=1.x: ``_TypeSystem,_Class,_ObjectMetadata``); the payload (object cell array
    for FileWrapper, or the handle vector for a class instance) is always last.
    """
    return np.asarray(void_record[void_record.dtype.names[-1]])


def _read_filewrapper(func_ws_bytes: bytes) -> np.ndarray:
    """Re-parse the ``__function_workspace__`` MCOS blob into its object cell array.

    The blob is an 8-byte sub-header followed by a standard MAT-5 element stream;
    we prepend a synthetic 128-byte v5 header and re-read it. ``read_file_header``
    must run before ``read_var_header`` (``get_variables`` does this internally),
    otherwise scipy raises "Expecting miMATRIX".
    """
    from scipy.io.matlab._mio5 import MatFile5Reader  # noqa: PLC0415 (private, isolated)

    header = b"MATLAB 5.0 MAT-file" + b" " * (116 - 19) + b"\x00" * 8 + b"\x00\x01" + b"IM"
    stream = header + func_ws_bytes[8:]
    reader = MatFile5Reader(BytesIO(stream), struct_as_record=True, squeeze_me=False)
    reader.mat_stream.seek(0)
    reader.initialize_read()
    reader.read_file_header()  # consume the 128-byte header
    var_header, _ = reader.read_var_header()
    file_wrapper = reader.read_var_array(var_header, process=False)
    # The opaque record's payload is the FileWrapper object cell array.
    return _opaque_payload(file_wrapper[0, 0]["MCOS"][0]).ravel()


def _is_poly_cell(cell: np.ndarray) -> bool:
    """True if an object cell is a PolynomialTransformation2D property struct."""
    try:
        names = cell[0, 0].dtype.names
    except (AttributeError, IndexError, TypeError):
        return False
    return bool(names) and {"A", "B", "normTransformXY", "normTransformUV"} <= set(names)


def _affine_matrix(arr: np.ndarray, objid: int) -> np.ndarray:
    """Resolve an affine2d/imref2d normalisation handle to its 3x3 matrix."""
    cell = arr[objid][0, 0]
    return np.asarray(cell["TransformationMatrix"], dtype=np.float64)


def _read_poly(arr: np.ndarray, cell_index: int) -> PolyTransform2D:
    struct = arr[cell_index][0, 0]
    a = np.asarray(struct["A"], dtype=np.float64).ravel()
    b = np.asarray(struct["B"], dtype=np.float64).ravel()
    if a.size != 6 or b.size != 6:
        raise ValueError(f"expected 6 degree-2 coefficients, got A={a.size} B={b.size}")
    norm_xy_id = int(np.asarray(struct["normTransformXY"]).ravel()[4])
    norm_uv_id = int(np.asarray(struct["normTransformUV"]).ravel()[4])
    return PolyTransform2D(
        a=a,
        b=b,
        norm_xy=_affine_matrix(arr, norm_xy_id),
        norm_uv=_affine_matrix(arr, norm_uv_id),
    )


def read_tmap(path: str | Path) -> dict[int, TmapChannel]:
    """Decode a Deep-LASI ``.tmap`` into ``{channel_id: TmapChannel}``.

    Resolves the MCOS ``PolynomialTransformation2D`` coefficients without MATLAB.
    The object-id -> property-cell mapping is derived generically (not hardcoded):
    the four ``Map{To,From}Reference`` handles give four polynomial object ids,
    which rank-zip onto the four polynomial property cells (MCOS stores objects of
    one class consecutively in creation/id order). Decoded transforms are in the
    file's native MATLAB 1-based pixel frame.
    """
    data = sio.loadmat(str(path))
    if "m" not in data:
        raise ValueError(f"{path}: not a Deep-LASI .tmap (no 'm' cell)")
    cells = data["m"]
    arr = _read_filewrapper(data["__function_workspace__"].tobytes())

    # Polynomial property cells in arr order; their ranks map to sorted object ids.
    poly_cell_indices = sorted(i for i in range(1, len(arr)) if _is_poly_cell(arr[i]))

    handles: dict[int, tuple[int, int]] = {}  # channel_id -> (to_objid, from_objid)
    for idx in range(cells.shape[1]):
        struct = cells[0, idx][0, 0]
        channel_id = int(np.asarray(struct["ChannelID"]).ravel()[0])
        # The opaque handle's 5th element (index 4) is the polynomial object id.
        to_objid = int(_opaque_payload(struct["MapToReference"][0]).ravel()[4])
        from_objid = int(_opaque_payload(struct["MapFromReference"][0]).ravel()[4])
        handles[channel_id] = (to_objid, from_objid)

    referenced = sorted({oid for pair in handles.values() for oid in pair})
    if len(referenced) != len(poly_cell_indices):
        raise ValueError(
            f"{path}: {len(referenced)} polynomial handles but "
            f"{len(poly_cell_indices)} polynomial cells (unexpected .tmap layout)"
        )
    objid_to_cell = dict(zip(referenced, poly_cell_indices, strict=True))

    out: dict[int, TmapChannel] = {}
    for idx in range(cells.shape[1]):
        struct = cells[0, idx][0, 0]
        channel_id = int(np.asarray(struct["ChannelID"]).ravel()[0])
        to_objid, from_objid = handles[channel_id]
        out[channel_id] = TmapChannel(
            channel_id=channel_id,
            crop=np.asarray(struct["Crop"]),
            map_particles=np.asarray(struct["MapParticles"], dtype=np.float64),
            ref_to_channel=_read_poly(arr, objid_to_cell[to_objid]),
            channel_to_ref=_read_poly(arr, objid_to_cell[from_objid]),
        )
    return out
