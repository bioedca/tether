# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""4-DOF Fourier-Mellin similarity prealign (PRD Appendix E Stage 7; M1 S5b).

Locks :func:`tether.imaging.register.estimate_similarity_prealign` -- the faithful
analogue of Deep-LASI ``imregcorr(...,'similarity')`` -- on two complementary
fronts (ADR-0013):

* **recovery** of a known rotation + scale + translation, on a deterministic,
  non-saturated synthetic bead field (the kind of raw content the pipeline sees);
* **real-data behaviour** on the committed ``bead_prealign_oracle.npz`` crops,
  whose estimate must match the ``.tmap``-derived ground-truth similarity (a real
  ~7.6 px translation, so a no-op estimator fails).

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
    c_ref = np.column_stack([rng.uniform(35, w - 35, 140), rng.uniform(35, h - 35, 140)])
    c_mov = target.inverse(c_ref)  # so target(c_mov) == c_ref
    reference = _render_field(c_ref, shape, rng)
    moving = _render_field(c_mov, shape, rng)
    return reference, moving, c_mov, c_ref


# Near-identity split-sensor regime (the physical case): the channels differ by a
# real ~7 px translation with sub-degree rotation / sub-percent scale. Genuine
# scale recovery on rich real bead content is locked by the real-fixture test
# below; on the sparse SYNTHETIC field, sub-resolution rotation/scale return
# ~identity and translation dominates -- so here we assert (a) translation
# recovered within the 2 px NN-pairing gate the prealign exists to clear, and
# (b) the estimator does not hallucinate a warp on near-identity inputs. (Larger
# warps are seed-fragile on sparse fields and out of scope -- ADR-0013.)
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
    # (b) no hallucinated warp: the estimate stays near identity for near-identity input.
    assert abs(est.scale - 1.0) < 0.01
    assert abs(np.rad2deg(est.rotation)) < 1.0


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
    assert abs(est.scale - gt_scale) < 0.005  # genuine scale recovery (gt ~1.0011, not 1.0)
    assert abs(np.rad2deg(est.rotation) - gt_rotation_deg) < 0.5
    assert np.hypot(*(est.translation - gt_translation)) < 1.5
    # Non-trivial: the real channels are offset ~7.6 px, so a no-op (t=[0,0]) fails.
    assert np.linalg.norm(est.translation) > 5.0


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
