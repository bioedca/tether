# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Donor–acceptor cross-correlation (M2 S8, FR-ANALYZE; PRD §7.7).

Locks the Pearson-normalized FFT cross-correlation: the pure-array core (zero-lag
Pearson coefficient, lag-1 magnitude, FFT == direct, undefined -> NaN) and the
store-level population aggregate (curation-filtered, skips undefined). Headless ->
base CI matrix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

from tether.analysis import cross_correlation, population_cross_correlation  # noqa: E402
from tether.imaging.aperture import IntegratedTraces  # noqa: E402
from tether.imaging.calibrate import RegistrationMap  # noqa: E402
from tether.imaging.coloc import ColocalizedMolecules  # noqa: E402
from tether.imaging.extract import (  # noqa: E402
    MoleculeTraces,
    MovieMetadata,
    read_molecules,
    write_extraction,
)
from tether.imaging.register import PolyTransform2D  # noqa: E402
from tether.imaging.split import ChannelGeometry  # noqa: E402
from tether.io.filename import parse_filename  # noqa: E402
from tether.io.schema import create_project  # noqa: E402
from tether.project.core import Project  # noqa: E402

_PARSED = parse_filename("Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
_WINDOW = 21


def _distinct_coords(n: int) -> np.ndarray:
    return np.array([[12.0 + 1.7 * i, 14.0 + 2.3 * (i % 7)] for i in range(n)], dtype="float64")


def _reg_map() -> RegistrationMap:
    poly = PolyTransform2D(
        a=np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0]),
        b=np.array([0.0, 0.0, 1.0, 0.0, 0.0, 0.0]),
        norm_xy=np.eye(3),
        norm_uv=np.eye(3),
    )
    return RegistrationMap(
        reference_channel=1,
        moving_channel=2,
        ref_to_moving=poly,
        moving_to_ref=poly,
        rms_residual=0.1,
        n_control_points=100,
    )


def _integrated(intensity: np.ndarray) -> IntegratedTraces:
    intensity = np.asarray(intensity, dtype="float64")
    n = intensity.shape[0]
    background = np.full_like(intensity, 100.0)
    return IntegratedTraces(
        intensity=intensity,
        total=intensity + background,
        background=background,
        valid=np.ones(n, dtype=bool),
    )


def _build_store(path: Path, donor: np.ndarray, acceptor: np.ndarray) -> tuple[Project, list[str]]:
    donor = np.asarray(donor, dtype="float64")
    acceptor = np.asarray(acceptor, dtype="float64")
    n, t = donor.shape
    coords = _distinct_coords(n)
    mols = ColocalizedMolecules(
        donor_xy=coords,
        acceptor_xy=coords,
        acceptor_detected=np.zeros(n, dtype=bool),
        donor_index=np.arange(n, dtype=np.intp),
        acceptor_index=np.full(n, -1, dtype=np.intp),
    )
    traces = MoleculeTraces(
        donor=_integrated(donor),
        acceptor=_integrated(acceptor),
        donor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        acceptor_patches=np.zeros((n, _WINDOW, _WINDOW), dtype="float32"),
        window=_WINDOW,
        disk_radius=3.0,
        ring_inner=6.0,
        ring_outer=8.0,
        bg_window=10,
    )
    movie = MovieMetadata(
        movie_id="mov-1",
        sha256="a" * 64,
        n_frames=t,
        height=64,
        width=64,
        donor_geometry=ChannelGeometry(crop=(1, 1, 64, 64)),
        acceptor_geometry=ChannelGeometry(crop=(1, 65, 64, 128)),
    )
    create_project(path, overwrite=True)
    write_extraction(
        path,
        movie=movie,
        molecules=mols,
        traces=traces,
        parsed=_PARSED,
        registration_map=_reg_map(),
    )
    proj = Project.open(path)
    keys = [
        k.decode() if isinstance(k, bytes) else str(k) for k in read_molecules(path)["molecule_key"]
    ]
    return proj, keys


# --- pure-array core ---------------------------------------------------------


def test_perfect_anticorrelation_gives_lag0_minus_one() -> None:
    donor = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    acceptor = 10.0 - donor  # exactly anti-phase: a0 = -d0
    cc = cross_correlation(donor, acceptor)
    assert cc.lag0 == pytest.approx(-1.0)
    assert cc.n_molecules is None
    assert cc.normalize == "pearson"


def test_perfect_correlation_gives_lag0_plus_one() -> None:
    donor = np.array([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    acceptor = 2.0 * donor + 3.0  # a0 = 2 d0
    cc = cross_correlation(donor, acceptor)
    assert cc.lag0 == pytest.approx(1.0)


def test_fft_matches_direct_normalized() -> None:
    rng = np.random.default_rng(0)
    donor = rng.normal(500.0, 50.0, size=64)
    acceptor = rng.normal(500.0, 50.0, size=64)
    cc = cross_correlation(donor, acceptor)

    d0 = donor - donor.mean()
    a0 = acceptor - acceptor.mean()
    denom = donor.size * d0.std() * a0.std()
    direct = signal.correlate(d0, a0, mode="full", method="direct") / denom
    np.testing.assert_allclose(cc.values, direct, atol=1e-10)
    # zero-lag equals the Pearson coefficient
    assert cc.lag0 == pytest.approx(float(np.corrcoef(donor, acceptor)[0, 1]))


def test_lags_axis_and_lag1_magnitude() -> None:
    n = 8
    donor = np.arange(n, dtype=float) + 1.0
    acceptor = 20.0 - donor
    cc = cross_correlation(donor, acceptor)
    np.testing.assert_array_equal(cc.lags, np.arange(-(n - 1), n))
    assert cc.values.shape == (2 * n - 1,)
    at_plus_one = float(cc.values[cc.lags == 1][0])
    assert cc.lag1_magnitude == pytest.approx(abs(at_plus_one))
    assert cc.lag1_magnitude >= 0.0


def test_constant_channel_is_nan_not_zero() -> None:
    donor = np.full(10, 500.0)  # sigma == 0 -> correlation undefined
    acceptor = np.arange(10, dtype=float)
    cc = cross_correlation(donor, acceptor)
    assert np.isnan(cc.lag0)
    assert np.isnan(cc.lag1_magnitude)
    assert np.all(np.isnan(cc.values))


def test_non_dyadic_constant_channel_is_nan() -> None:
    # A bit-identical non-dyadic constant (0.1) leaves a ~1e-17 residue after
    # mean-subtraction; the correlation is still undefined and must be NaN.
    donor = np.full(16, 0.1)
    acceptor = np.sin(np.arange(16, dtype=float))
    cc = cross_correlation(donor, acceptor)
    assert np.isnan(cc.lag0)
    assert np.isnan(cc.lag1_magnitude)
    assert np.all(np.isnan(cc.values))


def test_lag1_magnitude_matches_independent_asymmetric_value() -> None:
    # Independent donor/acceptor -> asymmetric cross-correlation (r[+1] != r[-1]),
    # so this pins lag1_magnitude to |r[+1]| computed by hand and catches a +1/-1
    # sign or off-by-one error in the lag selection.
    rng = np.random.default_rng(7)
    donor = rng.normal(500.0, 40.0, size=32)
    acceptor = rng.normal(500.0, 40.0, size=32)
    cc = cross_correlation(donor, acceptor)

    d0 = donor - donor.mean()
    a0 = acceptor - acceptor.mean()
    denom = donor.size * d0.std() * a0.std()
    manual_plus1 = float(np.sum(d0[1:] * a0[:-1]) / denom)  # r[+1] = sum_l d0[l] a0[l-1]
    manual_minus1 = float(np.sum(d0[:-1] * a0[1:]) / denom)  # r[-1]
    assert manual_plus1 != pytest.approx(manual_minus1)  # genuinely asymmetric
    assert cc.lag1_magnitude == pytest.approx(abs(manual_plus1))
    assert float(cc.values[cc.lags == 1][0]) == pytest.approx(manual_plus1)
    assert float(cc.values[cc.lags == -1][0]) == pytest.approx(manual_minus1)


def test_max_lag_truncates() -> None:
    donor = np.arange(20, dtype=float)
    acceptor = 30.0 - donor
    cc = cross_correlation(donor, acceptor, max_lag=2)
    np.testing.assert_array_equal(cc.lags, np.array([-2, -1, 0, 1, 2]))
    assert cc.values.shape == (5,)


def test_core_validation() -> None:
    with pytest.raises(ValueError, match="same length"):
        cross_correlation(np.arange(4.0), np.arange(5.0))
    with pytest.raises(ValueError, match="at least 2 frames"):
        cross_correlation(np.array([1.0]), np.array([2.0]))
    with pytest.raises(ValueError, match="finite"):
        cross_correlation(np.array([1.0, np.nan, 3.0]), np.array([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="normalize"):
        cross_correlation(np.arange(4.0), np.arange(4.0), normalize="biased")
    with pytest.raises(ValueError, match="non-negative"):
        cross_correlation(np.arange(4.0), np.arange(4.0), max_lag=-1)


# --- store-level population ---------------------------------------------------


def _varying_pair(n: int, t: int) -> tuple[np.ndarray, np.ndarray]:
    """(n, t) anticorrelated donor/acceptor: each molecule has lag0 = -1."""
    donor = np.empty((n, t), dtype="float64")
    acceptor = np.empty((n, t), dtype="float64")
    for i in range(n):
        donor[i] = np.arange(t, dtype="float64") + 10.0 * i
        acceptor[i] = 1000.0 - donor[i]
    return donor, acceptor


def test_population_averages_and_excludes_rejected(tmp_path) -> None:
    donor, acceptor = _varying_pair(3, 12)
    proj, keys = _build_store(tmp_path / "x.tether", donor, acceptor)

    cc = population_cross_correlation(proj)
    assert cc.n_molecules == 3
    assert cc.lag0 == pytest.approx(-1.0)  # every molecule is perfectly anti-phase
    assert np.isfinite(cc.lag1_magnitude)
    assert 0.0 <= cc.lag1_magnitude <= 1.0
    assert cc.lags[cc.lags == 0].size == 1

    proj.reject(keys[0], labeler="tester")
    cc2 = population_cross_correlation(proj)
    assert cc2.n_molecules == 2
    assert cc2.lag0 == pytest.approx(-1.0)


def test_population_skips_constant_molecule(tmp_path) -> None:
    donor, acceptor = _varying_pair(3, 12)
    donor[2, :] = 700.0  # a constant channel -> undefined correlation
    acceptor[2, :] = 1000.0 - donor[2]  # also constant
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)

    cc = population_cross_correlation(proj)
    assert cc.n_molecules == 2  # the constant molecule is skipped, not fabricated as 0
    assert cc.lag0 == pytest.approx(-1.0)


def test_population_skips_non_dyadic_constant_molecule(tmp_path) -> None:
    donor, acceptor = _varying_pair(3, 12)
    donor[2, :] = 0.1  # bit-identical non-dyadic constant -> undefined correlation
    acceptor[2, :] = 1000.0 - donor[2]
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    cc = population_cross_correlation(proj)
    assert cc.n_molecules == 2  # the flat 0.1 molecule is skipped, not counted
    assert cc.lag0 == pytest.approx(-1.0)


def test_population_respects_max_lag(tmp_path) -> None:
    donor, acceptor = _varying_pair(2, 15)
    proj, _ = _build_store(tmp_path / "x.tether", donor, acceptor)
    cc = population_cross_correlation(proj, max_lag=3)
    np.testing.assert_array_equal(cc.lags, np.arange(-3, 4))
    assert cc.values.shape == (7,)
