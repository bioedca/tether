# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aperture geometry + Sum integration (PRD Appendix E Stages 5, 11-15; §11.2; M0.5 S5).

Locks the integration half of the M0.5(b) extraction preview, feeding off the
:mod:`tether.imaging.detect` coordinates landed in PR #22:

* the ``21x21`` aperture geometry (disk r=3 -> 29 px, ring 6<d<=8 -> 84 px, with
  the ``3 < d <= 6`` dead-zone) is exact and matches §11.2;
* the Sum integration ``I = TOT - bg*N_psf`` (10-frame temporal-MA ring
  background) is verified analytically on synthetic top-hats; and
* the extracted **donor** traces correlate with the Deep-LASI raw ``don`` oracle
  on a committed crop of the real UCKOPSB movie. The acceptor channel needs the
  ``.tmap`` registration apply and rides the M0.5 S6 ``.tdat``/``.tmap`` decode.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from tether.imaging.aperture import (  # noqa: E402
    IntegratedTraces,
    aperture_in_frame,
    aperture_masks,
    integrate_traces,
)

ORACLE = Path(__file__).resolve().parent / "fixtures" / "aperture_oracle.npz"


# --- aperture geometry (Stage 5) --------------------------------------------


def test_aperture_default_pixel_counts() -> None:
    disk, ring = aperture_masks()
    assert disk.shape == (21, 21) and ring.shape == (21, 21)
    assert disk.sum() == 29  # PRD §11.2: PSF disk r=3 -> 29 px
    assert ring.sum() == 84  # PRD §11.2: ring inner 6 / outer 8 -> 84 px


def test_aperture_disk_ring_disjoint_with_deadzone() -> None:
    disk, ring = aperture_masks()
    assert not np.any(disk & ring)  # never overlap
    centre = 21 // 2
    rows, cols = np.mgrid[0:21, 0:21]
    dist = np.hypot(rows - centre, cols - centre)
    dead = (dist > 3) & (dist <= 6)  # the deliberate dead-zone gap
    assert np.any(dead)
    assert not np.any(dead & disk) and not np.any(dead & ring)
    assert disk[centre, centre]  # centre pixel is in the disk


def test_aperture_radii_configurable() -> None:
    disk, ring = aperture_masks(21, disk_radius=2, ring_inner=5, ring_outer=7)
    rows, cols = np.mgrid[-10:11, -10:11]
    dist = np.hypot(rows, cols)
    assert disk.sum() == int((dist <= 2).sum()) == 13  # d<=2 -> 13 px
    assert ring.sum() == int(((dist > 5) & (dist <= 7)).sum())


@pytest.mark.parametrize("window", [20, 0, -1])
def test_aperture_rejects_non_odd_window(window: int) -> None:
    with pytest.raises(ValueError, match="positive odd integer"):
        aperture_masks(window)


def test_aperture_rejects_bad_radii() -> None:
    with pytest.raises(ValueError, match="radii must satisfy"):
        aperture_masks(21, disk_radius=6, ring_inner=4, ring_outer=8)
    with pytest.raises(ValueError, match="does not fit"):
        aperture_masks(11, disk_radius=3, ring_inner=5, ring_outer=8)


def test_aperture_rejects_empty_ring() -> None:
    # (10.0, 10.04] brackets no achievable pixel distance (next is sqrt(101)=10.05).
    with pytest.raises(ValueError, match="ring is empty"):
        aperture_masks(21, disk_radius=3, ring_inner=10.0, ring_outer=10.04)


# --- Sum integration math (Stages 11-15) ------------------------------------


def _const_movie(value: np.ndarray, n_frames: int = 30) -> np.ndarray:
    """Stack a single (H, W) frame ``n_frames`` times (constant in time)."""
    return np.broadcast_to(value, (n_frames, *value.shape)).copy()


def test_integration_top_hat_recovers_amplitude_times_npsf() -> None:
    disk, ring = aperture_masks()
    bg_level, amp = 100.0, 50.0
    frame = np.full((21, 21), bg_level)
    frame[disk] += amp  # a top-hat exactly on the PSF disk
    res = integrate_traces(_const_movie(frame), [[10, 10]])
    n_psf = disk.sum()
    np.testing.assert_allclose(res.total[0], n_psf * (bg_level + amp))
    np.testing.assert_allclose(res.background[0], bg_level * n_psf)
    np.testing.assert_allclose(res.intensity[0], amp * n_psf)  # I = N_psf * amp, exact
    assert res.valid[0]


def test_integration_flat_field_is_zero() -> None:
    frame = np.full((21, 21), 250.0)
    res = integrate_traces(_const_movie(frame), [[10, 10]])
    np.testing.assert_allclose(res.intensity[0], 0.0, atol=1e-9)


def test_integration_deadzone_pixel_does_not_affect_intensity() -> None:
    frame = np.full((21, 21), 80.0)
    frame[disk_marked := aperture_masks()[0]] += 40.0
    base = integrate_traces(_const_movie(frame), [[10, 10]]).intensity[0].copy()
    # A bright pixel placed in the dead-zone (d=5 from centre, here (10, 15)).
    frame[10, 15] = 5000.0
    perturbed = integrate_traces(_const_movie(frame), [[10, 10]]).intensity[0]
    assert not disk_marked[10, 15]  # confirm it is outside the disk
    np.testing.assert_allclose(perturbed, base)  # neither disk nor ring -> no effect


def test_integration_xy_convention_and_offcentre() -> None:
    # Blob centred at (row=15, col=18) -> coord [x=18, y=15] (aperture fits a 40x40).
    disk, _ = aperture_masks()
    frame = np.full((40, 40), 10.0)
    rows, cols = np.mgrid[0:40, 0:40]
    frame[np.hypot(rows - 15, cols - 18) <= 3] += 100.0
    res = integrate_traces(_const_movie(frame), [[18, 15]])
    np.testing.assert_allclose(res.intensity[0], 100.0 * disk.sum())
    assert res.valid[0]


def test_integration_uses_temporal_moving_average_background() -> None:
    # The background is the *10-frame temporal moving average* of the ring, not its
    # instantaneous value. For a spatially flat background ramping 5/frame in time,
    # TOT tracks the instantaneous disk level but BG lags by the MA window's half-
    # width (0.5 frame), so the corrected intensity converges to a constant nonzero
    # offset = N_psf * slope * 0.5 = 29 * 5 * 0.5 = 72.5. (An instantaneous-ring
    # background would give exactly 0 here -> this pins the temporal smoothing.)
    base = np.full((21, 21), 100.0)
    frames = np.stack([base + 5.0 * t for t in range(40)])  # linear temporal ramp
    res = integrate_traces(frames, [[10, 10]])
    n_psf = aperture_masks()[0].sum()
    np.testing.assert_allclose(res.intensity[0, 5:35], n_psf * 5.0 * 0.5)  # steady-state lag
    assert not np.allclose(res.intensity[0, 5:35], 0.0)  # not instantaneous


def test_integration_out_of_bounds_is_zero_and_invalid() -> None:
    frame = np.full((21, 21), 100.0)
    movie = _const_movie(frame)
    res = integrate_traces(movie, [[10, 10], [2, 10], [10, 19]])  # 2nd, 3rd too close to edge
    assert res.valid.tolist() == [True, False, False]
    np.testing.assert_array_equal(res.intensity[1], 0.0)
    np.testing.assert_array_equal(res.intensity[2], 0.0)


def test_integration_empty_coords() -> None:
    res = integrate_traces(_const_movie(np.zeros((21, 21))), np.empty((0, 2)))
    assert res.intensity.shape == (0, 30)
    assert res.valid.shape == (0,)


def test_integration_validates_shapes() -> None:
    with pytest.raises(ValueError, match="3-D"):
        integrate_traces(np.zeros((21, 21)), [[10, 10]])
    with pytest.raises(ValueError, match=r"\(N, 2\)"):
        integrate_traces(np.zeros((5, 21, 21)), np.zeros((3, 3)))


def test_integrated_traces_is_frozen_dataclass() -> None:
    res = integrate_traces(_const_movie(np.full((21, 21), 1.0)), [[10, 10]])
    assert isinstance(res, IntegratedTraces)
    with pytest.raises(AttributeError):
        res.intensity = np.zeros(1)  # type: ignore[misc]


# --- real-data oracle: donor traces vs Deep-LASI raw `don` ------------------


def test_integration_matches_deeplasi_donor_oracle() -> None:
    data = np.load(ORACLE)
    crops = data["crops"]  # (N, T, 21, 21) uint16, big-endian movie pixels
    assert crops.dtype == np.dtype(">u2")  # source byte order preserved (not byte-swapped)
    don_ref = data["don_ref"]  # (N, T) Deep-LASI raw donor intensity
    n_mol, n_frames = don_ref.shape
    centre = tuple(int(v) for v in data["local_center"])  # (row, col) of the spot
    assert centre == (10, 10)

    # Compare on interior frames whose 10-frame temporal-MA window is fully
    # inside the crop (so my background matches the oracle's bookkeeping).
    interior = slice(10, n_frames - 10)
    corrs = []
    for m in range(n_mol):
        res = integrate_traces(crops[m], [[centre[1], centre[0]]])  # coord [x=col, y=row]
        assert res.valid[0]
        mine = res.intensity[0, interior]
        ref = don_ref[m, interior]
        corrs.append(float(np.corrcoef(mine, ref)[0, 1]))

    corrs = np.array(corrs)
    # Curated high-SNR donors; faithful aperture+integration tracks the oracle
    # tightly (loose threshold here per §9 M0.5(b); tightened to the M1 bar later).
    assert np.all(corrs >= 0.95), f"per-molecule donor corr below 0.95: {corrs}"
    assert np.median(corrs) >= 0.97


# --- aperture_in_frame: the shared crop-box predicate (Stage 13) -------------


def test_aperture_in_frame_central_and_edge() -> None:
    # window=21 -> half=10; in a 64x64 frame the in-frame box is [10, 53].
    coords = np.array([[30.0, 30.0], [10.0, 10.0], [53.0, 53.0], [9.0, 30.0], [30.0, 54.0]])
    fits = aperture_in_frame(coords, shape=(64, 64))
    np.testing.assert_array_equal(fits, [True, True, True, False, False])


def test_aperture_in_frame_matches_integrate_valid() -> None:
    # The guardrail predicate must equal the integrator's own `valid` mask, so a
    # colocalize()-kept molecule is exactly an integrate_traces()-valid one.
    movie = _const_movie(np.full((21, 21), 100.0))
    coords = np.array([[10, 10], [2, 10], [10, 19], [10, 2], [19, 10]], dtype=float)
    fits = aperture_in_frame(coords, shape=movie.shape[1:])
    valid = integrate_traces(movie, coords).valid
    np.testing.assert_array_equal(fits, valid)


def test_aperture_in_frame_rounds_away_from_zero() -> None:
    # 10.5 rounds to 11 (away from zero), so its window [1, 21] just fits a 22-tall
    # frame; 10.4 rounds to 10 and also fits -- both pin the rounding rule.
    fits = aperture_in_frame(np.array([[10.5, 10.5], [10.4, 10.4]]), shape=(22, 22))
    np.testing.assert_array_equal(fits, [True, True])
    # In a 21-tall frame, 10.5 -> 11 needs row 21 (out); 10.4 -> 10 fits.
    fits = aperture_in_frame(np.array([[10.5, 10.5], [10.4, 10.4]]), shape=(21, 21))
    np.testing.assert_array_equal(fits, [False, True])


def test_aperture_in_frame_single_xy_and_empty() -> None:
    assert aperture_in_frame(np.array([30.0, 30.0]), shape=(64, 64)).tolist() == [True]
    assert aperture_in_frame(np.empty((0, 2)), shape=(64, 64)).shape == (0,)
    assert aperture_in_frame(np.array([]), shape=(64, 64)).shape == (0,)


def test_aperture_in_frame_window_validation() -> None:
    for window in (20, 0, -1):
        with pytest.raises(ValueError, match="positive odd integer"):
            aperture_in_frame(np.array([[10.0, 10.0]]), shape=(64, 64), window=window)


def test_aperture_in_frame_smaller_window_widens_inframe() -> None:
    # A 7px window (half=3) keeps a spot a 21px window would reject.
    coord = np.array([[5.0, 5.0]])
    assert not aperture_in_frame(coord, shape=(64, 64), window=21)[0]
    assert aperture_in_frame(coord, shape=(64, 64), window=7)[0]
