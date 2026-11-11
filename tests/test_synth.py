# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The test-only synthetic data generator ``tests/_synth.py`` (#701).

Four promises, one test group each:

* the generated traces carry the answer they were generated from (state paths, switch
  probabilities, noise, bleaching), to statistical limits stated per test;
* the same seed gives the same bytes: pinned SHA-256 of one trace set and one movie, run
  on Linux, macOS and Windows by the required CI matrix;
* :func:`tether.project.extract.extract_movie` run on a generated movie recovers the known
  spot count, positions, channel mapping and per-frame intensities;
* nothing under ``src/tether`` imports the generator (it is not a product simulator,
  PRD N2 / #550).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from _synth import (
    PSF_KERNEL,
    PSF_SCALE,
    fret_traces,
    standard_noise,
    two_channel_movie,
    write_movie_tiff,
)

_REPO = Path(__file__).resolve().parents[1]

# --- the known answer ---------------------------------------------------------


def _mean_dwell(states: np.ndarray, state: int) -> float:
    """Mean length of the interior runs of ``state`` (first and last run of a row dropped)."""
    lengths: list[int] = []
    for row in states:
        edges = np.flatnonzero(np.diff(row)) + 1
        runs = np.split(row, edges)[1:-1]
        lengths.extend(len(r) for r in runs if r[0] == state)
    return float(np.mean(lengths))


def test_two_state_traces_carry_their_rates_and_noise() -> None:
    p = np.array([[0.96, 0.04], [0.10, 0.90]])
    tr = fret_traces(200, 400, seed=11, efficiency=(0.2, 0.8), switch_probability=p)
    assert tr.donor.shape == tr.acceptor.shape == tr.states.shape == (200, 400)
    assert set(np.unique(tr.states)) == {0, 1}
    assert np.all(tr.bleach_frame == 400)
    # Geometric dwells: mean 1 / (1 - P[i, i]) frames (25 and 10). Each mean pools
    # >= 1000 dwells, so its relative standard error is about 3 %; the limit is 10 %.
    assert _mean_dwell(tr.states, 0) == pytest.approx(25.0, rel=0.10)
    assert _mean_dwell(tr.states, 1) == pytest.approx(10.0, rel=0.10)
    # Emissions: D = I (1 - E_s) + noise, A = I E_s + noise, I = 1000, sigma = 30.
    e = tr.efficiency[tr.states]
    for resid in (tr.donor - 1000.0 * (1.0 - e), tr.acceptor - 1000.0 * e):
        assert abs(float(resid.mean())) < 1.0  # standard error 30 / sqrt(80000) ~ 0.1
        assert float(resid.std()) == pytest.approx(30.0, rel=0.02)


def test_three_state_transition_counts_match_the_matrix() -> None:
    p = np.array([[0.90, 0.06, 0.04], [0.05, 0.90, 0.05], [0.02, 0.08, 0.90]])
    tr = fret_traces(100, 600, seed=5, efficiency=(0.1, 0.5, 0.9), switch_probability=p)
    counts = np.zeros((3, 3))
    for row in tr.states:
        np.add.at(counts, (row[:-1], row[1:]), 1)
    observed = counts / counts.sum(axis=1, keepdims=True)
    # About 20000 departures per row: the standard error of each entry is < 0.003.
    np.testing.assert_allclose(observed, p, atol=0.015)


def test_bleaching_is_geometric_and_leaves_noise_only() -> None:
    tr = fret_traces(400, 300, seed=3, bleach_probability=0.01, noise_sigma=20.0)
    bleached = tr.bleach_frame < 300
    # Geometric bleach frame: P(no bleach in 300 frames) = 0.99 ** 300 ~ 0.049.
    assert 0.02 < 1.0 - bleached.mean() < 0.09
    # Mean bleach frame of the bleached ones: the geometric law truncated at 300 frames.
    f = np.arange(300)
    w = 0.01 * 0.99**f
    expected = float((f * w).sum() / w.sum())
    assert float(tr.bleach_frame[bleached].mean()) == pytest.approx(expected, rel=0.10)
    for m in np.flatnonzero(bleached)[:50]:
        f = tr.bleach_frame[m]
        assert np.all(tr.states[m, f:] == -1)
        assert np.all(tr.states[m, :f] >= 0)
        assert np.all(np.abs(tr.donor[m, f:]) <= 6 * 20.0)  # Irwin-Hall bound
        assert np.all(np.abs(tr.acceptor[m, f:]) <= 6 * 20.0)


def test_standard_noise_has_unit_variance_and_a_bound() -> None:
    x = standard_noise(np.random.default_rng(0), (200_000,))
    assert abs(float(x.mean())) < 0.01
    assert float(x.var()) == pytest.approx(1.0, rel=0.01)
    assert float(np.abs(x).max()) <= 6.0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"efficiency": (1.2,)}, "efficiency"),
        ({"switch_probability": np.array([[0.5, 0.4], [0.5, 0.5]])}, "row-stochastic"),
        ({"switch_probability": np.eye(3)}, "row-stochastic"),
        ({"bleach_probability": 1.5}, "bleach_probability"),
    ],
)
def test_bad_trace_parameters_are_refused(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        fret_traces(2, 10, seed=0, **kwargs)


def test_movie_has_known_spots_mapping_and_counts() -> None:
    mv = two_channel_movie(seed=1, n_frames=8)
    assert mv.frames.shape == (8, 64, 128) and mv.frames.dtype == np.uint16
    assert mv.donor_xy.shape == (9, 2)
    np.testing.assert_array_equal(mv.acceptor_xy - mv.donor_xy, [[1, -1]] * 9)
    # A noise-free movie holds exactly background + the floor-divided PSF patches.
    signal = mv.frames.astype(np.int64) - mv.background
    donor_half, acceptor_half = signal[:, :, :64], signal[:, :, 64:]
    expected_d = ((mv.donor_amplitude[:, :, None, None] * PSF_KERNEL) // PSF_SCALE).sum(axis=(2, 3))
    np.testing.assert_array_equal(donor_half.sum(axis=(1, 2)), expected_d.sum(axis=0))
    assert int(acceptor_half.sum()) == int(
        ((mv.acceptor_amplitude[:, :, None, None] * PSF_KERNEL) // PSF_SCALE).sum()
    )
    # The amplitudes are D + A = spot_intensity at one of the two FRET levels.
    np.testing.assert_array_equal(mv.donor_amplitude + mv.acceptor_amplitude, 6000)
    assert set(np.unique(mv.acceptor_amplitude)) <= {2100, 3900}
    with pytest.raises(ValueError, match="leaves its half"):
        two_channel_movie(seed=1, n_frames=2, margin=2)


# --- same seed, same bytes ----------------------------------------------------


def _traces_digest(seed: int) -> str:
    tr = fret_traces(20, 100, seed=seed, efficiency=(0.2, 0.5, 0.8), bleach_probability=0.01)
    h = hashlib.sha256()
    for arr, dtype in ((tr.donor, "<f8"), (tr.acceptor, "<f8"), (tr.states, "<i8")):
        h.update(np.ascontiguousarray(arr, dtype=dtype).tobytes())
    h.update(np.ascontiguousarray(tr.bleach_frame, dtype="<i8").tobytes())
    return h.hexdigest()


def _movie_digest(seed: int) -> str:
    mv = two_channel_movie(seed=seed, n_frames=20, noise_sigma=3.0)
    return hashlib.sha256(np.ascontiguousarray(mv.frames, dtype=">u2").tobytes()).hexdigest()


#: Pinned on Linux; the required matrix re-derives both on macOS and Windows. A change
#: here is a change of the generator's output, which every test that uses it inherits.
_PINNED_TRACES = "3f24b6cf64620eba56a743180225ffe02af8495adfe90d71a27b8bb1a378fd75"
_PINNED_MOVIE = "f67e3659b092f89cce92d0ee0bfc5990f529eda312a6cfbb811d960cb978bd5f"


def test_same_seed_gives_the_same_bytes() -> None:
    assert _traces_digest(2026) == _traces_digest(2026)
    assert _traces_digest(2026) != _traces_digest(2027)
    assert _movie_digest(2026) != _movie_digest(2027)


def test_pinned_checksums_hold_on_this_os() -> None:
    assert _traces_digest(2026) == _PINNED_TRACES
    assert _movie_digest(2026) == _PINNED_MOVIE


# --- end to end through the extractor ----------------------------------------


@pytest.mark.parametrize("sigma", [0.0, 3.0])
def test_extraction_recovers_the_known_spots_and_intensities(tmp_path: Path, sigma) -> None:
    pytest.importorskip("h5py")
    pytest.importorskip("skimage")
    pytest.importorskip("tifffile")
    from tether.imaging.extract import read_molecules, read_traces
    from tether.project.extract import extract_movie

    mv = two_channel_movie(seed=7, noise_sigma=sigma)
    movie = write_movie_tiff(mv, tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif")
    out = tmp_path / "synthetic.tether"
    summary = extract_movie(movie, out)

    # Spot count, positions and the channel mapping (a +1 / -1 px translation).
    assert summary.n_molecules == len(mv.donor_xy) == 9
    assert summary.low_confidence_registration is False
    mols = read_molecules(out)
    order = [int(np.argmin(np.abs(mv.donor_xy - xy).sum(axis=1))) for xy in mols["donor_xy"]]
    assert sorted(order) == list(range(9))
    np.testing.assert_allclose(mols["donor_xy"], mv.donor_xy[order], atol=0.5)
    np.testing.assert_allclose(mols["acceptor_xy"] - mols["donor_xy"], [mv.offset] * 9, atol=0.05)

    # Per-frame intensities. The known answer is the spot's counts inside the default
    # radius-3 disk (29 px), background excluded. Without noise the ring background is
    # exactly the constant offset, so the recovery is exact. With noise each frame differs
    # by the disk noise (sigma * sqrt(29) ~ 16 counts) plus the error of the ring
    # estimate. Limits: every frame within 5 * sigma * sqrt(29) * 1.1 (~ 89 counts,
    # < 4.3 % of the dimmest level) and a mean error of at most 5 counts over each
    # channel's 360 samples (standard error ~ 1; 0.25 % of the dimmest level).
    traces = read_traces(out)
    limit = 5.0 * sigma * np.sqrt(29.0) * 1.1 + 1e-6
    for channel in ("donor", "acceptor"):
        known = mv.disk_intensity(channel)[order].astype(np.float64)
        error = traces[f"{channel}_corrected"] - known
        assert float(np.abs(error).max()) <= limit, channel
        assert abs(float(error.mean())) <= (5.0 if sigma else 1e-6), channel


# --- isolation from the product ----------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_product_module_imports_the_generator() -> None:
    sources = sorted((_REPO / "src" / "tether").rglob("*.py"))
    assert sources, "src/tether not found"
    offenders = {
        str(p.relative_to(_REPO)): sorted(
            m for m in _imported_modules(p) if m.split(".")[0] in {"_synth", "tests"}
        )
        for p in sources
    }
    assert {k: v for k, v in offenders.items() if v} == {}
    assert importlib.util.find_spec("tether._synth") is None


def test_the_isolation_scan_sees_an_import(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("import os\nfrom _synth import fret_traces\n", encoding="utf-8")
    assert {"os", "_synth"} <= _imported_modules(probe)
