# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Donor-anchored colocalization + apply-map-at-extraction (PRD App. E Stages 11-13; M1 S7).

Locks the coordinate-domain colocalization layer:

* the acceptor read position is the donor warped through the registration map
  (Stage 12), sub-pixel, never resampled;
* the donor-anchored relaxation keeps every in-frame donor -- including ones with
  no independently-detected acceptor (the dark/low-FRET population findColoc's
  "partner in every channel" rule would drop);
* the ``acceptor_detected`` flag is the strict ``< 3 px`` NN test in donor coords;
* the 21x21 crop-box guardrail skips molecules whose window leaves *either* frame.

The transforms are synthetic (known translations/affines) so every warp is exactly
predictable; the real-data extraction-vs-Deep-LASI oracle is M1 S9.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import (  # noqa: E402
    DEFAULT_COLOC_DISTANCE_PX,
    ColocalizedMolecules,
    colocalize,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402

_IDENTITY_A = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0])  # x_out = x
_IDENTITY_B = np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0])  # y_out = y


def _poly(a: np.ndarray, b: np.ndarray) -> PolyTransform2D:
    return PolyTransform2D(a=a, b=b, norm_xy=np.eye(3), norm_uv=np.eye(3))


def _identity_poly() -> PolyTransform2D:
    return _poly(_IDENTITY_A.copy(), _IDENTITY_B.copy())


def _translation_poly(tx: float, ty: float) -> PolyTransform2D:
    """A pure translation ``[x, y] -> [x + tx, y + ty]`` as a degree-2 poly."""
    return _poly(
        np.array([tx, 1.0, 0.0, 0.0, 0.0, 0.0]),
        np.array([ty, 0.0, 1.0, 0.0, 0.0, 0.0]),
    )


def _make_map(
    ref_to_moving: PolyTransform2D | None = None,
    moving_to_ref: PolyTransform2D | None = None,
    *,
    rms: float = 0.1,
    n: int = 12,
    gate: float = 0.5,
) -> RegistrationMap:
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=_identity_poly() if ref_to_moving is None else ref_to_moving,
        moving_to_ref=_identity_poly() if moving_to_ref is None else moving_to_ref,
        rms_residual=rms,
        n_control_points=n,
        gate_px=gate,
    )


# --- Stage 12: donor-anchored read position (coordinate domain) --------------


def test_acceptor_read_position_is_mapped_donor() -> None:
    # ref_to_moving translates donor -> acceptor by (+5, -3).
    reg = _make_map(ref_to_moving=_translation_poly(5.0, -3.0))
    donor = np.array([[30.0, 25.0], [40.0, 50.0]])
    res = colocalize(donor, reg, donor_shape=(96, 96), acceptor_shape=(96, 96))
    assert res.n_molecules == 2
    np.testing.assert_allclose(res.acceptor_xy, donor + np.array([5.0, -3.0]))
    np.testing.assert_allclose(res.donor_xy, donor)  # donor coords untouched


def test_coordinate_domain_preserves_subpixel() -> None:
    # Identity map: the acceptor read keeps the donor's sub-pixel coordinate
    # exactly -- a coordinate transform, not a pixel-snapped movie rewarp.
    reg = _make_map()
    donor = np.array([[30.37, 25.62]])
    res = colocalize(donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64))
    np.testing.assert_allclose(res.acceptor_xy, donor)
    assert not np.allclose(res.acceptor_xy, np.round(res.acceptor_xy))  # not snapped


# --- Stage 11: donor-anchored relaxation (keep dark/low-FRET acceptors) -------


def test_retains_donor_with_no_acceptor_partner() -> None:
    # The key scientific contract: a donor whose acceptor was never independently
    # detected is STILL extracted (findColoc would drop it). acceptor_spots=None.
    reg = _make_map()
    donor = np.array([[30.0, 30.0], [45.0, 20.0]])
    res = colocalize(donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64))
    assert res.n_molecules == 2  # none dropped for lack of a partner
    assert not res.acceptor_detected.any()
    assert np.all(res.acceptor_index == -1)


def test_dark_acceptor_kept_while_partnered_one_is_flagged() -> None:
    reg = _make_map()  # identity both ways
    donor = np.array([[30.0, 30.0], [45.0, 20.0]])
    # Only the first donor has an acceptor within the gate (identity moving_to_ref).
    acceptor = np.array([[30.5, 30.0]])
    res = colocalize(
        donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64), acceptor_spots=acceptor
    )
    assert res.n_molecules == 2  # the dark-acceptor donor is retained
    np.testing.assert_array_equal(res.acceptor_detected, [True, False])
    np.testing.assert_array_equal(res.acceptor_index, [0, -1])


# --- Stage 11 detection flag: strict 3 px NN in donor coords -----------------


def test_acceptor_detected_gate() -> None:
    reg = _make_map()  # identity moving_to_ref -> acceptor compared in its own coords
    donor = np.array([[20.0, 20.0], [40.0, 40.0]])
    # donor0: acceptor 2.99 px away -> inside the 3 px gate (hit);
    # donor1: acceptor 3.01 px away -> outside (miss). Well clear of the exact
    # boundary, which is float-fragile and measure-zero on real sub-pixel data.
    acceptor = np.array([[22.99, 20.0], [43.01, 40.0]])
    res = colocalize(
        donor,
        reg,
        donor_shape=(64, 64),
        acceptor_shape=(64, 64),
        acceptor_spots=acceptor,
        coloc_distance_px=3.0,
    )
    np.testing.assert_array_equal(res.acceptor_detected, [True, False])
    np.testing.assert_array_equal(res.acceptor_index, [0, -1])


def test_detection_warps_acceptor_into_donor_coords() -> None:
    # A non-identity moving_to_ref must be applied before the NN test: the acceptor
    # spot only colocates with the donor AFTER the warp, not in raw coords.
    reg = _make_map(moving_to_ref=_translation_poly(-10.0, 0.0))
    donor = np.array([[30.0, 30.0]])
    acceptor = np.array([[40.0, 30.0]])  # warps to (30, 30) in donor coords
    res = colocalize(
        donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64), acceptor_spots=acceptor
    )
    np.testing.assert_array_equal(res.acceptor_detected, [True])
    np.testing.assert_array_equal(res.acceptor_index, [0])


def test_default_coloc_distance_is_three_px() -> None:
    assert DEFAULT_COLOC_DISTANCE_PX == 3.0


# --- Stage 13: crop-box guardrail (skip out-of-frame in either channel) ------


def test_skips_donor_whose_window_leaves_the_donor_frame() -> None:
    reg = _make_map()
    # window=21 -> half=10; in a 64x64 frame only [10, 53] is in-frame.
    donor = np.array([[30.0, 30.0], [5.0, 30.0], [30.0, 60.0]])
    res = colocalize(donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64))
    assert res.n_molecules == 1
    np.testing.assert_array_equal(res.donor_index, [0])
    np.testing.assert_allclose(res.donor_xy, [[30.0, 30.0]])


def test_skips_donor_whose_mapped_acceptor_leaves_the_acceptor_frame() -> None:
    # Donor is comfortably in-frame, but the mapped acceptor lands off the (small)
    # acceptor frame -> the molecule is not extractable, so it is skipped.
    reg = _make_map(ref_to_moving=_translation_poly(100.0, 0.0))
    donor = np.array([[100.0, 100.0], [30.0, 100.0]])
    res = colocalize(donor, reg, donor_shape=(200, 200), acceptor_shape=(64, 64))
    # donor[0] -> acceptor (200,100) out of 64x64; donor[1] -> (130,100) also out.
    assert res.n_molecules == 0


def test_donor_index_tracks_survivors() -> None:
    reg = _make_map()
    donor = np.array([[5.0, 5.0], [30.0, 30.0], [60.0, 60.0], [40.0, 40.0]])
    res = colocalize(donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64))
    # rows 1 and 3 are in-frame (10..53); rows 0 and 2 are not.
    np.testing.assert_array_equal(res.donor_index, [1, 3])
    np.testing.assert_allclose(res.donor_xy, donor[[1, 3]])


# --- empties + validation ----------------------------------------------------


def test_empty_donor_spots_returns_empty() -> None:
    reg = _make_map()
    res = colocalize(np.empty((0, 2)), reg, donor_shape=(64, 64), acceptor_shape=(64, 64))
    assert isinstance(res, ColocalizedMolecules)
    assert res.n_molecules == 0
    assert res.donor_xy.shape == (0, 2)
    assert res.acceptor_xy.shape == (0, 2)
    assert res.acceptor_detected.shape == (0,)
    assert res.donor_index.shape == (0,)
    assert res.acceptor_index.shape == (0,)


def test_empty_acceptor_spots_all_undetected() -> None:
    reg = _make_map()
    donor = np.array([[30.0, 30.0]])
    res = colocalize(
        donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64), acceptor_spots=np.empty((0, 2))
    )
    assert res.n_molecules == 1
    assert not res.acceptor_detected.any()
    np.testing.assert_array_equal(res.acceptor_index, [-1])


def test_rejects_non_registration_map() -> None:
    with pytest.raises(TypeError, match="RegistrationMap"):
        colocalize(
            np.array([[30.0, 30.0]]), object(), donor_shape=(64, 64), acceptor_shape=(64, 64)
        )


def test_rejects_bad_donor_shape() -> None:
    reg = _make_map()
    with pytest.raises(ValueError, match=r"donor_spots must be \(N, 2\)"):
        colocalize(np.zeros((3, 3)), reg, donor_shape=(64, 64), acceptor_shape=(64, 64))


def test_rejects_nonfinite_donor() -> None:
    reg = _make_map()
    with pytest.raises(ValueError, match="finite"):
        colocalize(np.array([[np.nan, 30.0]]), reg, donor_shape=(64, 64), acceptor_shape=(64, 64))


def test_rejects_nonpositive_coloc_distance() -> None:
    reg = _make_map()
    with pytest.raises(ValueError, match="coloc_distance_px"):
        colocalize(
            np.array([[30.0, 30.0]]),
            reg,
            donor_shape=(64, 64),
            acceptor_shape=(64, 64),
            coloc_distance_px=0.0,
        )


@pytest.mark.parametrize(
    "bad", [(64,), (64, 64, 3), (64.5, 64.0), (0, 64), (-1, 64), (float("inf"), 64)]
)
def test_rejects_bad_frame_shape(bad: tuple[int, ...]) -> None:
    reg = _make_map()
    with pytest.raises(ValueError, match="H, W"):
        colocalize(np.array([[30.0, 30.0]]), reg, donor_shape=bad, acceptor_shape=(64, 64))


@pytest.mark.parametrize("window", [20, 0, -1])
def test_rejects_non_odd_window(window: int) -> None:
    # Validated up front, so even an empty donor set rejects a bad window.
    reg = _make_map()
    for donor in (np.array([[30.0, 30.0]]), np.empty((0, 2))):
        with pytest.raises(ValueError, match="positive odd integer"):
            colocalize(donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64), window=window)


def test_read_and_detection_use_distinct_transform_directions() -> None:
    # With a non-symmetric map (moving_to_ref is NOT the inverse of ref_to_moving),
    # the acceptor READ position must come from ref_to_moving while the detection
    # flag must come from moving_to_ref -- proving the two directions are wired to
    # the right transforms, not accidentally swapped or shared.
    reg = _make_map(
        ref_to_moving=_translation_poly(5.0, -3.0),  # donor -> acceptor read
        moving_to_ref=_translation_poly(2.0, 1.0),  # acceptor -> donor (not the inverse)
    )
    donor = np.array([[30.0, 30.0]])
    # This acceptor warps via moving_to_ref to (30, 30) == donor -> detected.
    acceptor = np.array([[28.0, 29.0]])
    res = colocalize(
        donor, reg, donor_shape=(64, 64), acceptor_shape=(64, 64), acceptor_spots=acceptor
    )
    np.testing.assert_allclose(res.acceptor_xy, [[35.0, 27.0]])  # ref_to_moving, not the inverse
    np.testing.assert_array_equal(res.acceptor_detected, [True])  # moving_to_ref warp colocates
    np.testing.assert_array_equal(res.acceptor_index, [0])
