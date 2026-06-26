# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""à trous wavelet spot detection (PRD Appendix E Stages 2-4; §11.2; M0.5 S5).

Locks the detection half of the M0.5(b) extraction preview: the moving-average
max-projection detection image, the B3-spline à trous transform, the
AND-of-scales-1&4 multiscale detection mask, and the Stage-4 guardrails
(border margin, 8 px min-separation NMS, max-pixel snap). The aperture +
integration + ``.tmap`` apply + Deep-LASI intensity comparison are a follow-up
PR on the same issue (#16).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.imaging.detect import (  # noqa: E402
    atrous_wavelet_planes,
    b3_spline_kernel,
    detect_spots,
    detection_image,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"


def _gaussian_blob(
    image: np.ndarray, row: float, col: float, amp: float, sigma: float = 1.6
) -> None:
    rows, cols = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
    image += amp * np.exp(-((rows - row) ** 2 + (cols - col) ** 2) / (2 * sigma**2))


def _count_near(spots: np.ndarray, x: float, y: float, tol: float = 2.0) -> int:
    """Number of detected ``[x, y]`` spots within ``tol`` px of ``(x, y)``."""
    if spots.shape[0] == 0:
        return 0
    return int(np.sum(np.hypot(spots[:, 0] - x, spots[:, 1] - y) <= tol))


# --- B3-spline à trous kernel ------------------------------------------------


@pytest.mark.parametrize("step", [1, 2, 4, 8])
def test_b3_kernel_normalized_and_dilated(step: int) -> None:
    kernel = b3_spline_kernel(step)
    assert kernel.shape == (4 * step + 1,)
    assert kernel.sum() == pytest.approx(1.0)  # low-pass scaling filter
    nonzero = np.nonzero(kernel)[0].tolist()
    assert nonzero == [0, step, 2 * step, 3 * step, 4 * step]
    np.testing.assert_allclose(kernel[nonzero], [1 / 16, 1 / 4, 3 / 8, 1 / 4, 1 / 16])


def test_b3_kernel_rejects_bad_step() -> None:
    with pytest.raises(ValueError, match="step must be >= 1"):
        b3_spline_kernel(0)


# --- detection image (Stage 2) ----------------------------------------------


def test_detection_image_single_block_is_normalized_mean() -> None:
    rng = np.random.default_rng(0)
    movie = rng.uniform(0, 1000, size=(50, 16, 16))
    image = detection_image(movie, block=50)  # exactly one block
    expected = movie.mean(axis=0)
    expected = expected / expected.max()
    np.testing.assert_allclose(image, expected)


def test_detection_image_range_and_shape() -> None:
    rng = np.random.default_rng(1)
    movie = rng.uniform(0, 500, size=(120, 32, 24))
    image = detection_image(movie, block=50)
    assert image.shape == (32, 24)
    assert image.min() >= 0.0
    assert image.max() == pytest.approx(1.0)


def test_detection_image_too_few_frames_sum_fallback() -> None:
    rng = np.random.default_rng(2)
    movie = rng.uniform(0, 500, size=(10, 8, 8))  # T < block -> sum projection
    image = detection_image(movie, block=50)
    expected = movie.sum(axis=0)
    np.testing.assert_allclose(image, expected / expected.max())


def test_detection_image_all_zero_movie_is_zero() -> None:
    image = detection_image(np.zeros((60, 8, 8)), block=50)
    assert np.all(image == 0.0)


def test_detection_image_validates_ndim() -> None:
    with pytest.raises(ValueError, match="3-D"):
        detection_image(np.zeros((8, 8)))


# --- à trous planes (Stage 3) -----------------------------------------------


def test_atrous_planes_count_shape_and_one_sided() -> None:
    rng = np.random.default_rng(3)
    image = rng.uniform(0, 1, size=(40, 40))
    planes = atrous_wavelet_planes(image, n_scales=6)
    assert len(planes) == 5  # J - 1 wavelet planes
    for plane in planes:
        assert plane.shape == image.shape
        assert np.all(plane >= 0.0)  # one-sided threshold keeps only bright coeffs


def test_atrous_validates_scales_and_ndim() -> None:
    with pytest.raises(ValueError, match="n_scales must be >= 2"):
        atrous_wavelet_planes(np.zeros((8, 8)), n_scales=1)
    with pytest.raises(ValueError, match="2-D"):
        atrous_wavelet_planes(np.zeros((4, 4, 4)))


# --- spot detection + guardrails (Stages 3-4) -------------------------------


def test_detect_spots_recovers_synthetic_truth() -> None:
    rng = np.random.default_rng(0)
    image = rng.normal(100, 5, size=(64, 64))
    truth = [(12, 20), (40, 45), (30, 30)]  # (row, col)
    for row, col in truth:
        _gaussian_blob(image, row, col, amp=400)
    spots = detect_spots(image / image.max())  # default scale_pair=(1, 4)
    assert spots.shape == (3, 2)
    for row, col in truth:
        assert _count_near(spots, x=col, y=row, tol=1.0) == 1


def test_detect_spots_returns_xy_descending_brightness() -> None:
    rng = np.random.default_rng(0)
    image = rng.normal(100, 5, size=(64, 64))
    _gaussian_blob(image, 30, 30, amp=900)  # brightest
    _gaussian_blob(image, 12, 50, amp=300)
    spots = detect_spots(image / image.max())
    assert spots.shape[1] == 2
    # First row is the brightest spot -> near (x=30, y=30).
    assert spots[0, 0] == pytest.approx(30, abs=1)
    assert spots[0, 1] == pytest.approx(30, abs=1)


def test_detect_spots_min_separation_keeps_brightest() -> None:
    rng = np.random.default_rng(1)
    image = rng.normal(100, 4, size=(64, 64))
    _gaussian_blob(image, 30, 30, amp=600)  # brighter
    _gaussian_blob(image, 30, 42, amp=300)  # 12 px away
    det = image / image.max()
    kept_8 = detect_spots(det, min_separation=8)
    assert _count_near(kept_8, x=30, y=30) == 1
    assert _count_near(kept_8, x=42, y=30) == 1  # 12 px > 8 px -> both kept
    kept_15 = detect_spots(det, min_separation=15)
    assert _count_near(kept_15, x=30, y=30) == 1
    assert _count_near(kept_15, x=42, y=30) == 0  # suppressed -> brightest only


def test_detect_spots_border_removal() -> None:
    rng = np.random.default_rng(1)
    image = rng.normal(100, 4, size=(64, 64))
    _gaussian_blob(image, 3, 30, amp=600)  # near top edge
    _gaussian_blob(image, 40, 40, amp=500)
    det = image / image.max()
    assert _count_near(detect_spots(det, border_margin=1), x=30, y=3) == 1
    assert _count_near(detect_spots(det, border_margin=6), x=30, y=3) == 0
    assert _count_near(detect_spots(det, border_margin=6), x=40, y=40) == 1


def test_detect_spots_output_respects_guardrails_after_refine() -> None:
    # The Stage-4 max-pixel snap must not push a spot back across the border or
    # within min_separation: guardrails run AFTER refinement, so the returned
    # coordinates always satisfy the border + separation contract (PR #22 review).
    rng = np.random.default_rng(7)
    image = rng.normal(100, 5, size=(64, 64))
    for row, col in [(10, 10), (10, 16), (50, 50), (32, 8)]:
        _gaussian_blob(image, row, col, amp=500)
    det = image / image.max()
    margin, min_sep = 6, 10.0
    spots = detect_spots(det, border_margin=margin, min_separation=min_sep, refine=True)
    assert spots.shape[0] >= 1
    # x = col, y = row; every spot within [margin, size-1-margin] on both axes.
    assert np.all(spots[:, 0] >= margin) and np.all(spots[:, 0] <= 63 - margin)
    assert np.all(spots[:, 1] >= margin) and np.all(spots[:, 1] <= 63 - margin)
    # Pairwise separation >= min_sep holds on the refined output.
    for i in range(spots.shape[0]):
        for j in range(i + 1, spots.shape[0]):
            assert np.hypot(*(spots[i] - spots[j])) >= min_sep


def test_detect_spots_empty_on_flat_image() -> None:
    spots = detect_spots(np.full((32, 32), 0.5))
    assert spots.shape == (0, 2)


def test_detect_spots_rejects_bad_scale_pair() -> None:
    with pytest.raises(ValueError, match="out of range"):
        detect_spots(np.zeros((32, 32)), n_scales=4, scale_pair=(1, 4))


def test_detect_spots_validates_ndim() -> None:
    with pytest.raises(ValueError, match="2-D"):
        detect_spots(np.zeros((4, 4, 4)))


# --- end-to-end on the committed real fixture -------------------------------


def test_detect_spots_on_fixture_movie() -> None:
    tifffile = pytest.importorskip("tifffile")
    movie = tifffile.imread(FIXTURE)
    assert movie.shape == (50, 64, 64)
    image = detection_image(movie)  # default block=50 -> one block
    assert image.shape == (64, 64)
    assert image.min() >= 0.0 and image.max() == pytest.approx(1.0)
    spots = detect_spots(image)  # faithful default (1, 4)
    assert spots.ndim == 2 and spots.shape[1] == 2
    assert spots.dtype == np.float64
    assert spots.shape[0] >= 3  # the cropped UCKOPSB slice has several real spots
    assert np.all(np.isfinite(spots))
    assert np.all(spots[:, 0] >= 0) and np.all(spots[:, 0] <= 63)  # x in bounds
    assert np.all(spots[:, 1] >= 0) and np.all(spots[:, 1] <= 63)  # y in bounds
