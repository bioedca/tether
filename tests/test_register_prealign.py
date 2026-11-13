# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""4-DOF Fourier-Mellin similarity prealign (PRD Appendix E Stage 7; M1 S5b).

Locks :func:`tether.imaging.register.estimate_similarity_prealign` -- the faithful
analogue of Deep-LASI ``imregcorr(...,'similarity')`` -- on two complementary
fronts (ADR-0013):

* **recovery** on a deterministic, non-saturated synthetic bead field (the kind of
  raw content the pipeline sees): the scale, rotation and translation of
  near-identity warps on a square image and on the non-square channel-half shape
  that ``extract_movie`` passes (#780, ADR-0089), and the rotation of a 10 degree
  warp;
* **real-data behaviour** on the committed ``bead_prealign_oracle.npz`` crops,
  whose estimate must match the ``.tmap``-derived ground-truth similarity (a real
  ~7.6 px translation, so a no-op estimator fails). The crop holds too few beads
  to resolve the scale below about 0.002, so its scale limit is the coarse one
  and the scale recovery is proven on the synthetic field (ADR-0089).

The saturated ``map.tif`` display export can only validate the near-identity real
relationship, not large-warp recovery (see ``tests/fixtures/PROVENANCE.md``);
hence recovery is proven on synthetic and real-behaviour on the fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")  # estimate_similarity_prealign uses scikit-image

import numpy as np  # noqa: E402
from skimage.transform import SimilarityTransform  # noqa: E402

from tether.imaging.register import (  # noqa: E402
    SimilarityTransform2D,
    estimate_similarity_prealign,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bead_prealign_oracle.npz"


def _render_field(
    centers: np.ndarray, shape: tuple[int, int], rng: np.random.Generator, sigma: float = 1.6
) -> np.ndarray:
    """Non-saturated synthetic bead field: Gaussian spots on a flat background."""
    h, w = shape
    img = np.full((h, w), 100.0)
    yy, xx = np.mgrid[0:h, 0:w]
    for x, y in centers:
        amp = rng.uniform(250, 800)
        img += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    return img


def _synthetic_pair(
    scale: float,
    rot_deg: float,
    tx: float,
    ty: float,
    rng: np.random.Generator,
    shape: tuple[int, int] = (512, 256),
    n_beads: int = 140,
):
    """Build (reference, moving) bead images related by a known moving->reference T."""
    h, w = shape
    cx, cy = w / 2.0, h / 2.0
    target = (  # the moving->reference similarity the estimator must recover
        SimilarityTransform(translation=(-cx, -cy))
        + SimilarityTransform(scale=scale, rotation=np.deg2rad(rot_deg))
        + SimilarityTransform(translation=(cx, cy))
        + SimilarityTransform(translation=(tx, ty))
    )
    c_ref = np.column_stack([rng.uniform(35, w - 35, n_beads), rng.uniform(35, h - 35, n_beads)])
    c_mov = target.inverse(c_ref)  # so target(c_mov) == c_ref
    reference = _render_field(c_ref, shape, rng)
    moving = _render_field(c_mov, shape, rng)
    return reference, moving, c_mov, c_ref


# Near-identity split-sensor regime (the physical case): the channels differ by a
# real ~7 px translation with sub-degree rotation / sub-percent scale. Here we
# assert (a) translation recovered within the 2 px NN-pairing gate the prealign
# exists to clear, and (b) the scale and rotation themselves, to a few estimator
# steps (one step is 0.07 deg and 0.0011 in scale at 256 px, ADR-0089). The
# identity case also shows that no warp is hallucinated.
_NEAR_IDENTITY_CASES = [
    (1.0, 0.0, 0.0, 0.0),
    (1.002, 0.3, 6.0, -3.0),
    (0.999, -0.3, -5.0, 2.0),
]


@pytest.mark.parametrize(("scale", "rot_deg", "tx", "ty"), _NEAR_IDENTITY_CASES)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_near_identity_prealign_robust_across_seeds(
    scale: float, rot_deg: float, tx: float, ty: float, seed: int
) -> None:
    rng = np.random.default_rng(seed)
    reference, moving, c_mov, c_ref = _synthetic_pair(scale, rot_deg, tx, ty, rng)
    est = estimate_similarity_prealign(reference, moving)
    assert isinstance(est, SimilarityTransform2D)
    # (a) translation recovered: moving control points land within the NN-pairing gate.
    point_err = float(np.sqrt(((est.apply(c_mov) - c_ref) ** 2).sum(axis=1)).mean())
    assert point_err < 2.0, f"{point_err:.3f} px (s={scale}, rot={rot_deg}, seed={seed})"
    # (b) the warp itself is recovered (#780); for the identity case, none is invented.
    assert abs(est.scale - scale) < 0.003, f"scale {est.scale:.4f} vs {scale}"
    assert abs(np.rad2deg(est.rotation) - rot_deg) < 0.3, f"rot {np.rad2deg(est.rotation):.3f}"


def test_real_bead_pair_matches_tmap_ground_truth() -> None:
    """Genuine similarity recovery on real bead content: the estimate on the real
    crops matches the .tmap-derived ground truth, including the sub-percent scale a
    translation-only prealign (S5a) cannot capture."""
    data = np.load(FIXTURE)
    donor = data["donor"].astype(np.float64)
    acceptor = data["acceptor"].astype(np.float64)
    gt_scale = float(data["gt_scale"])
    gt_rotation_deg = float(data["gt_rotation_deg"])
    gt_translation = data["gt_translation"]

    # reference = donor, moving = acceptor -> returns the acceptor->donor map.
    est = estimate_similarity_prealign(donor, acceptor)
    # Scale: the crop cannot resolve it below about 0.002. Its 14 bead pairs fit a
    # similarity with a bootstrap scale SD of 0.0023, and correct variants of the
    # estimator give 0.997 to 1.012 on it (ADR-0089). #734 tightened this limit to
    # 0.0005 because the old estimator gave 1.00135; that was one scale step
    # (0.0014) above 1.0, not a measurement. So the limit is the pre-#734 0.005, and
    # scale recovery is proven on the synthetic field (#780).
    assert abs(est.scale - gt_scale) < 0.005
    # Rotation: the ground truth (0.04 deg) is about one 0.07 deg angular step, so
    # this crop cannot prove rotation recovery; the synthetic tests do.
    assert abs(np.rad2deg(est.rotation) - gt_rotation_deg) < 0.5
    assert np.hypot(*(est.translation - gt_translation)) < 1.5
    # Non-trivial: the real channels are offset ~7.6 px, so a no-op (t=[0,0]) fails.
    assert np.linalg.norm(est.translation) > 5.0


@pytest.mark.parametrize("rot_deg", [10.0, -10.0])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_rotation_reaches_the_estimate(rot_deg: float, seed: int) -> None:
    """The log-polar angular shift becomes the returned rotation (#734 IMG-F9).

    10 degrees is outside the validated near-identity regime (ADR-0013, ADR-0089).
    It proves that a larger angle also reaches the result with its sign; an
    estimator that drops the angle returns about 0 degrees and fails by 10.
    """
    rng = np.random.default_rng(seed)
    reference, moving, c_mov, c_ref = _synthetic_pair(1.0, rot_deg, 6.0, -3.0, rng, (256, 256))
    est = estimate_similarity_prealign(reference, moving)
    assert abs(np.rad2deg(est.rotation) - rot_deg) < 1.5
    point_err = float(np.sqrt(((est.apply(c_mov) - c_ref) ** 2).sum(axis=1)).mean())
    assert point_err < 2.0  # within the pairing gate the prealign exists to clear


# (rows, cols): a square field, and the channel-half shape extract_movie passes the
# estimator for a 512 x 512 side-by-side movie (#780 acceptance item 2).
_RECOVERY_SHAPES = [(256, 256), (512, 256)]


@pytest.mark.parametrize("shape", _RECOVERY_SHAPES, ids=["square", "channel-half"])
@pytest.mark.parametrize(("scale", "rot_deg"), [(1.01, 1.0), (0.99, -1.0), (1.0, 1.0), (1.01, 0.0)])
@pytest.mark.parametrize("n_beads", [140, 400])
def test_near_identity_rotation_and_scale_are_recovered(
    shape: tuple[int, int], scale: float, rot_deg: float, n_beads: int
) -> None:
    """Recovery of scale 1.01 and rotation 1 degree on a non-saturated field (#780).

    This is the recovery ADR-0013 names. Before #780 the estimate was scale 1.0
    and rotation 0 or 0.14 degree: the spectrum grid was not square, too coarse,
    and the band-pass removed the fine structure (ADR-0089). The sign variants
    and the pure-rotation and pure-scale cases show the two axes are independent.
    """
    rng = np.random.default_rng(0)
    reference, moving, c_mov, c_ref = _synthetic_pair(
        scale, rot_deg, 6.0, -3.0, rng, shape, n_beads=n_beads
    )
    est = estimate_similarity_prealign(reference, moving)
    assert abs(est.scale - scale) < 0.003, f"scale {est.scale:.4f} vs {scale}"
    assert abs(np.rad2deg(est.rotation) - rot_deg) < 0.3, f"rot {np.rad2deg(est.rotation):.3f}"
    point_err = float(np.sqrt(((est.apply(c_mov) - c_ref) ** 2).sum(axis=1)).mean())
    assert point_err < 0.5, f"{point_err:.3f} px"


def test_prealign_seeds_pairing_end_to_end() -> None:
    """The documented pipeline use: the similarity prealign feeds pair_control_points,
    recovering the donor<->acceptor correspondence (Appendix E Stages 7-8)."""
    from tether.imaging.register import pair_control_points

    rng = np.random.default_rng(0)
    # near-identity pair with the real ~7 px offset; c_mov[i] corresponds to c_ref[i].
    reference, moving, c_mov, c_ref = _synthetic_pair(1.001, 0.2, 7.0, -2.0, rng)
    est = estimate_similarity_prealign(reference, moving)
    paired = pair_control_points(c_ref, c_mov, tol=2.0, prealign=est)
    # Without the prealign the ~7 px offset exceeds the 2 px gate -> few/no pairs;
    # with it, the mutual-NN pairing recovers the identity correspondence.
    assert len(paired.reference_index) >= int(0.9 * len(c_ref))
    assert np.array_equal(paired.reference_index, paired.moving_index)


def test_same_shape_required() -> None:
    rng = np.random.default_rng(0)
    a = rng.standard_normal((64, 64))
    b = rng.standard_normal((64, 48))
    with pytest.raises(ValueError, match="same shape"):
        estimate_similarity_prealign(a, b)


def test_rejects_non_2d() -> None:
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="2-D"):
        estimate_similarity_prealign(rng.standard_normal((4, 4, 4)), rng.standard_normal((4, 4, 4)))


def test_rejects_too_small() -> None:
    a = np.zeros((12, 12))
    with pytest.raises(ValueError, match="16 px"):
        estimate_similarity_prealign(a, a)


def test_rejects_non_finite() -> None:
    a = np.zeros((32, 32))
    bad = a.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        estimate_similarity_prealign(a, bad)


@pytest.mark.parametrize("upsample", [0, -1])
def test_rejects_bad_upsample(upsample: int) -> None:
    a = np.zeros((32, 32))
    with pytest.raises(ValueError, match="upsample_factor"):
        estimate_similarity_prealign(a, a, upsample_factor=upsample)


@pytest.mark.parametrize(("low", "high"), [(0.0, 20.0), (20.0, 5.0), (5.0, 5.0)])
def test_rejects_bad_bandpass(low: float, high: float) -> None:
    a = np.zeros((32, 32))
    with pytest.raises(ValueError, match="low_sigma"):
        estimate_similarity_prealign(a, a, low_sigma=low, high_sigma=high)


def test_public_imaging_surface_reexports_similarity_prealign() -> None:
    import tether.imaging as imaging

    assert imaging.estimate_similarity_prealign is estimate_similarity_prealign
    assert "estimate_similarity_prealign" in imaging.__all__
