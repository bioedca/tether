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
    ParticleDetectionMode,
    _bandpass,
    _refine_snap,
    atrous_wavelet_planes,
    b3_spline_kernel,
    detect_spots,
    detect_spots_by_mode,
    detect_spots_intensity,
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


def test_detection_image_drops_partial_tail_block() -> None:
    # T=120 is not a multiple of block=50: frames 100-119 form a partial tail
    # block, which cumIMG.m drops (keeping only the two whole 50-frame blocks).
    block = 50
    movie = np.zeros((120, 8, 8), dtype=np.float64)
    movie[:, 1, 1] = 100.0  # a steady spot present in every frame (the whole blocks)
    movie[100:, 5, 6] = 1000.0  # a bright spike ONLY in the dropped tail (frames 100-119)
    image = detection_image(movie, block=block)
    # Faithful to cumIMG.m: dropping the remainder makes T=120 identical to T=100.
    np.testing.assert_array_equal(image, detection_image(movie[: 2 * block], block=block))
    # The steady spot is the normalized peak; the dropped-tail spike never appears
    # (had the partial block been kept, [5, 6] would be the global max instead).
    assert image[1, 1] == pytest.approx(1.0)
    assert image[5, 6] == 0.0


def test_detection_image_does_not_mutate_input() -> None:
    # detection_image casts with copy=False, so it must not write through to the
    # caller's array (a float64 input is passed through, not copied).
    movie = np.zeros((120, 8, 8), dtype=np.float64)
    movie[:, 1, 1] = 100.0
    movie[100:, 5, 6] = 1000.0
    before = movie.copy()
    detection_image(movie, block=50)
    np.testing.assert_array_equal(movie, before)


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


# --- MATLAB away-from-zero snap rounding (findPart.m round; #23 follow-up) ---
# `_refine_snap` returns NATIVE (row, col) order (detect_spots flips to [x, y]).
# Snapped outputs are integer-valued, so assert with `==`. Both constructions put
# a single bright pixel so the Gaussian-smoothed argmax is at that pixel, isolating
# the rounding: with banker's (half-to-even) rounding the legacy code returned a
# different pixel (noted per case).


def test_refine_snap_crop_centre_rounds_away_from_zero() -> None:
    # Centroid (10.5, 12.5): away-from-zero crop centre (11, 13) puts the bright
    # pixel at the window centre -> offset 0 -> snap = (round_away(10.5),
    # round_away(12.5)) = (11, 13). Banker's crop centre (10, 12) -> (12, 14).
    img = np.zeros((30, 30))
    img[11, 13] = 1.0
    assert _refine_snap(img, 10.5, 12.5, half=5) == (11.0, 13.0)


def test_refine_snap_locks_both_round_sites() -> None:
    # Centroid (10.5, 13.5), bright pixel (11, 14): full away/away -> (11, 14),
    # which is unique vs the legacy banker/banker path (12, 14). One assertion
    # pins BOTH the crop-centre round (findPart crop) and the final snap round.
    img = np.zeros((30, 30))
    img[11, 14] = 1.0
    assert _refine_snap(img, 10.5, 13.5, half=5) == (11.0, 14.0)


# --- Crocker-Grier band-pass (bpass.m; intensity-detector helper) ------------


def test_bandpass_localizes_blob_and_zeros_border() -> None:
    img = np.zeros((40, 40))
    _gaussian_blob(img, row=20, col=20, amp=1.0)
    band = _bandpass(img, lnoise=1.0, lobject=7)
    assert band.shape == img.shape
    # bpass.m re-pads the 'valid' convolution -> a lobject(=7)-px zero border.
    assert np.all(band[:7, :] == 0) and np.all(band[-7:, :] == 0)
    assert np.all(band[:, :7] == 0) and np.all(band[:, -7:] == 0)
    # The band-pass max sits on the blob (within 2 px).
    assert band.max() > 0
    peak_row, peak_col = np.unravel_index(int(np.argmax(band)), band.shape)
    assert abs(peak_row - 20) <= 2 and abs(peak_col - 20) <= 2


def test_bandpass_too_small_returns_zeros() -> None:
    # Image smaller than the 2*lobject+1 (=15) kernel can't be 'valid'-convolved.
    band = _bandpass(np.ones((10, 10)), lnoise=1.0, lobject=7)
    assert band.shape == (10, 10)
    assert np.all(band == 0)


def test_bandpass_validates_length_scales() -> None:
    img = np.zeros((40, 40))
    with pytest.raises(ValueError, match="lnoise"):
        _bandpass(img, lnoise=0.0)
    with pytest.raises(ValueError, match="lnoise"):
        _bandpass(img, lnoise=-1.0)
    with pytest.raises(ValueError, match="lobject"):
        _bandpass(img, lobject=0)


# --- intensity-threshold detector (findPart.m mode 2) ------------------------


def test_detect_spots_intensity_recovers_synthetic_truth() -> None:
    image = np.zeros((64, 64))
    truth = [(18, 20), (40, 44), (30, 12)]  # (row, col)
    for row, col in truth:
        _gaussian_blob(image, row=row, col=col, amp=1.0)
    image /= image.max()
    spots = detect_spots_intensity(image)
    # every planted spot is recovered (per-channel detection vs known truth)
    for row, col in truth:
        assert _count_near(spots, x=col, y=row, tol=2.0) >= 1
    # a clean synthetic background yields no extra detections
    assert spots.shape[0] == len(truth)


def test_detect_spots_intensity_returns_xy_descending_brightness() -> None:
    image = np.zeros((64, 64))
    _gaussian_blob(image, row=30, col=30, amp=1.0)  # bright
    _gaussian_blob(image, row=12, col=50, amp=0.75)  # dim but above threshold
    image /= image.max()
    spots = detect_spots_intensity(image)
    # both blobs must be detected, AND the brighter one must come first
    bright_idx = np.flatnonzero(np.hypot(spots[:, 0] - 30, spots[:, 1] - 30) <= 2.0)
    dim_idx = np.flatnonzero(np.hypot(spots[:, 0] - 50, spots[:, 1] - 12) <= 2.0)
    assert bright_idx.size == 1
    assert dim_idx.size == 1
    assert bright_idx[0] < dim_idx[0]


def test_detect_spots_intensity_respects_guardrails() -> None:
    image = np.zeros((64, 64))
    for row, col in [(8, 8), (32, 32), (33, 35), (58, 58)]:
        _gaussian_blob(image, row=row, col=col, amp=1.0)
    image /= image.max()
    spots = detect_spots_intensity(image, border_margin=6, min_separation=10.0)
    assert spots.shape[1] == 2
    for x, y in spots:
        assert 6 <= x <= 64 - 1 - 6 and 6 <= y <= 64 - 1 - 6
    for i in range(spots.shape[0]):
        for j in range(i + 1, spots.shape[0]):
            assert np.hypot(*(spots[i] - spots[j])) >= 10.0


def test_detect_spots_intensity_validates_inputs() -> None:
    image = np.zeros((30, 30))
    with pytest.raises(ValueError, match="2-D"):
        detect_spots_intensity(image[np.newaxis])
    with pytest.raises(ValueError, match="threshold"):
        detect_spots_intensity(image, threshold=1.0)
    with pytest.raises(ValueError, match="threshold"):
        detect_spots_intensity(image, threshold=-0.1)
    with pytest.raises(ValueError, match="fine_threshold"):
        detect_spots_intensity(image, fine_threshold=0.0)


def test_detect_spots_intensity_empty_on_flat_image() -> None:
    # Flat-zero detection image -> peak <= 0 -> no candidates.
    assert detect_spots_intensity(np.zeros((30, 30))).shape == (0, 2)


def test_detect_spots_intensity_on_fixture_movie() -> None:
    tifffile = pytest.importorskip("tifffile")
    movie = tifffile.imread(FIXTURE)
    image = detection_image(movie)
    spots = detect_spots_intensity(image)
    assert spots.ndim == 2 and spots.shape[1] == 2
    assert spots.dtype == np.float64
    assert spots.shape[0] >= 1
    assert np.all(np.isfinite(spots))
    assert np.all(spots >= 0) and np.all(spots <= 63)  # in bounds


# --- mode selector -----------------------------------------------------------


def test_particle_detection_mode_values() -> None:
    assert ParticleDetectionMode.WAVELET.value == "wavelet"
    assert ParticleDetectionMode.INTENSITY.value == "intensity"
    assert ParticleDetectionMode("intensity") is ParticleDetectionMode.INTENSITY
    assert ParticleDetectionMode.WAVELET == "wavelet"  # str-enum serializes as value


def test_detect_spots_by_mode_matches_direct_calls() -> None:
    image = np.zeros((64, 64))
    for row, col in [(18, 20), (40, 44)]:
        _gaussian_blob(image, row=row, col=col, amp=1.0)
    image /= image.max()
    # default + explicit "wavelet" -> the à trous detector.
    np.testing.assert_array_equal(detect_spots_by_mode(image), detect_spots(image))
    np.testing.assert_array_equal(detect_spots_by_mode(image, mode="wavelet"), detect_spots(image))
    # "intensity" (enum or str) -> the intensity-threshold detector.
    np.testing.assert_array_equal(
        detect_spots_by_mode(image, mode=ParticleDetectionMode.INTENSITY),
        detect_spots_intensity(image),
    )
    np.testing.assert_array_equal(
        detect_spots_by_mode(image, mode="intensity"), detect_spots_intensity(image)
    )


def test_detect_spots_by_mode_rejects_unknown_mode() -> None:
    image = np.zeros((20, 20))
    with pytest.raises(ValueError):
        detect_spots_by_mode(image, mode="bandpass")  # mode 3 not yet implemented
    with pytest.raises(ValueError):
        detect_spots_by_mode(image, mode="nonsense")
