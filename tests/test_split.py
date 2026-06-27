# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Channel split geometry (PRD Appendix E Stage 1; ``processImage.m`` port; M1 S1).

Locks the Stage-1 ``rotate -> flip -> crop`` transform: the clockwise rotation
direction (``imrotate(I, -rot)``), ``flipud``/``fliplr`` semantics, the load-bearing
operation order, and the MATLAB 1-based-inclusive ``Crop`` -> 0-based half-open
slice conversion. The detection image (Stage 2) is locked in ``test_detect.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from tether.imaging import ChannelGeometry, process_image, split_channels  # noqa: E402
from tether.imaging.detect import detection_image  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"


# --- identity ----------------------------------------------------------------


def test_process_image_identity_is_unchanged() -> None:
    a = np.arange(12, dtype=np.int32).reshape(3, 4)
    np.testing.assert_array_equal(process_image(a), a)


# --- rotation (imrotate(I, -rot) == clockwise by rot) ------------------------


def test_process_image_rotation_90_is_clockwise() -> None:
    # Hand-computed clockwise 90: anchors the DIRECTION, independent of the
    # implementation (a [1 2 / 3 4] rotated CW puts 3 at top-left).
    a = np.array([[1, 2], [3, 4]])
    np.testing.assert_array_equal(process_image(a, rotation_deg=90), [[3, 1], [4, 2]])


@pytest.mark.parametrize(
    ("deg", "expected"),
    [
        (0, [[1, 2], [3, 4]]),
        (90, [[3, 1], [4, 2]]),  # clockwise 90
        (180, [[4, 3], [2, 1]]),
        (270, [[2, 4], [1, 3]]),  # clockwise 270 == counter-clockwise 90
    ],
)
def test_process_image_rotation_table(deg: int, expected: list[list[int]]) -> None:
    a = np.array([[1, 2], [3, 4]])
    out = process_image(a, rotation_deg=deg)
    np.testing.assert_array_equal(out, expected)
    # Equivalence to imrotate(I, -deg) via the lossless rot90 identity.
    np.testing.assert_array_equal(out, np.rot90(a, (-deg // 90) % 4))


def test_process_image_rotation_90_swaps_nonsquare_shape() -> None:
    a = np.arange(6).reshape(2, 3)  # (H=2, W=3)
    assert process_image(a, rotation_deg=90).shape == (3, 2)


def test_process_image_rejects_non_quadrant_rotation() -> None:
    with pytest.raises(ValueError, match="rotation_deg must be one of"):
        process_image(np.zeros((4, 4)), rotation_deg=45)


# --- flip ([v, h] -> flipud / fliplr) ----------------------------------------


@pytest.mark.parametrize(
    ("flip", "expected"),
    [
        ((0, 0), [[1, 2, 3], [4, 5, 6]]),
        ((1, 0), [[4, 5, 6], [1, 2, 3]]),  # flipud (rows)
        ((0, 1), [[3, 2, 1], [6, 5, 4]]),  # fliplr (cols)
        ((1, 1), [[6, 5, 4], [3, 2, 1]]),
    ],
)
def test_process_image_flip(flip: tuple[int, int], expected: list[list[int]]) -> None:
    a = np.array([[1, 2, 3], [4, 5, 6]])
    np.testing.assert_array_equal(process_image(a, flip=flip), expected)


def test_process_image_rejects_bad_flip_shape() -> None:
    with pytest.raises(ValueError, match="flip must be a length-2"):
        process_image(np.zeros((4, 4)), flip=(1, 0, 1))


# --- crop (MATLAB 1-based inclusive -> 0-based half-open) ---------------------


def test_process_image_crop_1based_inclusive_to_0based_halfopen() -> None:
    a = np.arange(36).reshape(6, 6)
    # Crop [y1, x1, y2, x2] = [2, 3, 4, 5] -> rows 2..4, cols 3..5 (1-based incl.)
    # -> a[1:4, 2:5] (0-based half-open).
    out = process_image(a, crop=[2, 3, 4, 5])
    assert out.shape == (3, 3)
    np.testing.assert_array_equal(out, a[1:4, 2:5])


def test_process_image_crop_top_left_corner() -> None:
    a = np.arange(36).reshape(6, 6)
    np.testing.assert_array_equal(process_image(a, crop=[1, 1, 2, 2]), a[0:2, 0:2])


def test_process_image_crop_full_frame_is_identity() -> None:
    a = np.arange(36).reshape(6, 6)
    np.testing.assert_array_equal(process_image(a, crop=[1, 1, 6, 6]), a)


@pytest.mark.parametrize("crop", [[1, 1, 2], [1, 1, 2, 2, 2]])
def test_process_image_rejects_wrong_crop_size(crop: list[int]) -> None:
    with pytest.raises(ValueError, match="crop must have 4 elements"):
        process_image(np.zeros((6, 6)), crop=crop)


def test_process_image_rejects_inverted_crop() -> None:
    with pytest.raises(ValueError, match="must be 1-based"):
        process_image(np.zeros((6, 6)), crop=[4, 1, 2, 3])  # y2 < y1


def test_process_image_rejects_zero_based_crop() -> None:
    with pytest.raises(ValueError, match="must be 1-based"):
        process_image(np.zeros((6, 6)), crop=[0, 1, 3, 3])  # y1 < 1


def test_process_image_rejects_out_of_bounds_crop() -> None:
    with pytest.raises(ValueError, match="exceeds the rotated frame"):
        process_image(np.zeros((6, 6)), crop=[1, 1, 6, 7])  # x2 > width


def test_process_image_crop_bounds_apply_in_rotated_frame() -> None:
    # After a 90-degree rotation a (2, 4) frame becomes (4, 2); a crop valid only
    # in the rotated frame (y2=4 > original H=2) must succeed, proving the order.
    a = np.arange(8).reshape(2, 4)
    out = process_image(a, rotation_deg=90, crop=[1, 1, 4, 2])
    np.testing.assert_array_equal(out, np.rot90(a, 3))


# --- operation order: rotate -> flip -> crop ---------------------------------


def test_process_image_order_is_rotate_then_flip_then_crop() -> None:
    a = np.arange(20).reshape(4, 5)
    # Reference: apply the three steps explicitly, in order.
    step = np.rot90(a, (-90 // 90) % 4)  # rotate (clockwise 90) -> (5, 4)
    step = np.flipud(step)  # flip v
    step = np.fliplr(step)  # flip h
    expected = step[1:4, 0:2]  # crop [y1,x1,y2,x2]=[2,1,4,2] -> rows 1:4, cols 0:2
    out = process_image(a, rotation_deg=90, flip=(1, 1), crop=[2, 1, 4, 2])
    np.testing.assert_array_equal(out, expected)


# --- 3-D stack (T, H, W): frame-wise on the spatial plane --------------------


def test_process_image_stack_is_framewise() -> None:
    movie = np.arange(2 * 2 * 3).reshape(2, 2, 3)  # (T=2, H=2, W=3)
    out = process_image(movie, rotation_deg=90)
    assert out.shape == (2, 3, 2)
    for t in range(movie.shape[0]):
        np.testing.assert_array_equal(out[t], process_image(movie[t], rotation_deg=90))


def test_process_image_rejects_bad_ndim() -> None:
    with pytest.raises(ValueError, match="must be 2-D .* or 3-D"):
        process_image(np.zeros(5))
    with pytest.raises(ValueError, match="must be 2-D .* or 3-D"):
        process_image(np.zeros((2, 2, 2, 2)))


# --- ChannelGeometry + split_channels ----------------------------------------


def test_channel_geometry_apply_matches_process_image() -> None:
    a = np.arange(36).reshape(6, 6)
    geom = ChannelGeometry(crop=np.array([2, 2, 5, 5]), rotation_deg=90, flip=(0, 1))
    np.testing.assert_array_equal(
        geom.apply(a),
        process_image(a, rotation_deg=90, crop=[2, 2, 5, 5], flip=(0, 1)),
    )


def test_split_channels_left_right_tiles_back_to_frame() -> None:
    # Default donor = Left, acceptor = Right of a frame split down the middle
    # (PRD Appendix E Stage 1). The two halves must tile back to the original,
    # proving the 1-based-inclusive crop bounds are exact and complementary.
    movie = np.arange(4 * 6 * 8).reshape(4, 6, 8)  # (T=4, H=6, W=8)
    donor = ChannelGeometry(crop=[1, 1, 6, 4])  # left  4 cols
    acceptor = ChannelGeometry(crop=[1, 5, 6, 8])  # right 4 cols
    left, right = split_channels(movie, donor, acceptor)
    assert left.shape == (4, 6, 4)
    assert right.shape == (4, 6, 4)
    np.testing.assert_array_equal(np.concatenate([left, right], axis=2), movie)


# --- Stage 1 + Stage 2 on the committed real fixture -------------------------


def test_split_then_detection_image_on_fixture() -> None:
    tifffile = pytest.importorskip("tifffile")
    movie = tifffile.imread(FIXTURE)
    assert movie.shape == (50, 64, 64)
    # Split the 64-wide frame into two 32-wide halves and tile back exactly.
    donor = ChannelGeometry(crop=[1, 1, 64, 32])
    acceptor = ChannelGeometry(crop=[1, 33, 64, 64])
    left, right = split_channels(movie, donor, acceptor)
    assert left.shape == (50, 64, 32)
    assert right.shape == (50, 64, 32)
    np.testing.assert_array_equal(np.concatenate([left, right], axis=2), movie)
    # Each half feeds the Stage-2 detection image: normalized to [0, 1].
    for half in (left, right):
        det = detection_image(half)
        assert det.shape == (64, 32)
        assert det.min() >= 0.0 and det.max() == pytest.approx(1.0)
