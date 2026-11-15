# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's successive-frame FRET cloud against the laboratory script (#710, ADR-0105).

The reference is ``FRET_cloud_contours_centroids.m`` of the FRET_cloud scripts (Eduardo Campos
Chavez, 2025, unpublished). The script is not in the repository: ``pins.toml`` pins it by its
SHA-256, and ``scripts/reference/matlab/fret_cloud.py`` ran it unchanged, one time, in MATLAB on
the 76 smfret-analysis2 test traces. The frozen output is
``tests/fixtures/reference/fret-cloud-scripts/sa2-traces/``; its ``provenance.json`` names the
script hash, the citation, the MATLAB release, the input hashes and the seed, and
``tests/test_matlab_reference.py`` checks every output against it.

What the script does, and how the test gives Tether the same input:

* **Pairs.** Of each trace file the script takes column 4 as E and pairs ``E(1:50)`` with
  ``E(2:51)``. Column 4 of these traces is zero, so the MATLAB task gave the script a copy whose
  column 4 is ``acceptor / (acceptor + donor)``, which is Tether's apparent E. The 50-pair cut is a
  choice of the script, not of Tether, so the test gives Tether the first 51 frames of each trace.
* **Density.** ``mvksdensity`` with bandwidth 0.05 on a 200 x 200 grid of ``linspace(0, 1, 200)``,
  normalized to ``f / sum(f) / (dx * dy)``: Tether's product-Gaussian KDE with the same defaults,
  with one difference that the frozen output shows. MATLAB leaves out the part of each pair's
  kernel that is more than 4 bandwidths from the pair on either axis; Tether keeps the whole
  Gaussian. With that cut applied (``_cut_density``), the two densities agree to round-off, and the
  5216 nodes where MATLAB's density is exactly 0 are exactly the nodes that no cut kernel reaches.
  Without it, Tether's density differs by at most 1.1e-4 of the peak (measured), and that is the
  whole difference. The cut is MATLAB's, not the script's, so Tether does not copy it (ADR-0105).
* **Contour levels.** The cells sorted by density, ``cumsum * dx * dy`` and, for each coverage
  ``p`` of 10 % to 90 %, the first cell where it reaches ``p``: Tether's HDR level rule, which on
  the cut density gives MATLAB's nine levels to round-off.
* **Centroids.** ``kmeans(pairs, 4, 'Replicates', 5)``: k-means++ starts, Lloyd iterations, the run
  of least total squared distance. The starts are random and the two programs draw them from
  different generators, so the test does not ask for the same run. It asks that MATLAB's
  centroids are a fixed point of Tether's Lloyd step, and that Tether's best run has the same total
  squared distance and the same centroids to the measured limits below.
"""

from __future__ import annotations

import csv
import json
import re
import tomllib
from pathlib import Path

import h5py
import numpy as np
import pytest

from _sa2_traces import read_trace
from reference_data import fetch
from tether.analysis.cloud import _hdr_levels_from_grid
from tether.analysis.successive import (
    _assign,
    _lloyd,
    successive_frame_cloud,
    successive_frame_pairs,
)
from tether.fret.efficiency import apparent_fret

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "tests/fixtures/reference/fret-cloud-scripts/sa2-traces"
TOOL = "fret-cloud-scripts"
SCRIPT = "FRET_cloud_contours_centroids.m"
CITATION = "FRET_cloud scripts, Eduardo Campos Chavez, 2025, unpublished"
#: ``numFramesToUse`` of the script: pairs ``(i, i + 1)`` for ``i = 1..50`` of each file.
PAIRS_PER_FILE = 50
N_TRACES = 76

#: mvksdensity's cut, in bandwidths on each axis (see the module docstring).
MATLAB_KERNEL_CUT = 4.0

#: The limits. Each is the value measured on 2026-11-15 (Linux, NumPy; MATLAB R2023b on Windows)
#: rounded up to the next power of ten.
#: Grid nodes: ``linspace`` and ``numpy.linspace`` (measured 1.1e-16, one ulp).
GRID_ATOL = 1e-15
#: The cut density against MATLAB's, relative to the peak: two sums of 3800 Gaussians in another
#: order (measured 2.5e-15). The nine levels from it, relative (measured 2.2e-15).
CUT_DENSITY_REL_LIMIT = 1e-14
CUT_LEVEL_REL_LIMIT = 1e-14
#: Tether's own density against MATLAB's, relative to the peak: the kernel cut and nothing else
#: (measured 1.1e-4). Its nine levels, relative (measured 3.2e-4). A bandwidth 1 % off moves the
#: density by 5e-3 of its peak, so this still catches a wrong default; the strict check is the cut
#: comparison above.
DENSITY_REL_LIMIT = 1e-3
LEVEL_REL_LIMIT = 1e-3
#: MATLAB's centroids under one Lloyd step of Tether: the mean of each cluster (measured 0).
FIXED_POINT_ATOL = 1e-12
#: Tether's best k-means run against MATLAB's. They end in two nearby local minima: MATLAB's total
#: squared distance is 4.5e-7 (relative) below Tether's, and the centroids are up to 8.0e-4 apart
#: in E (measured). These are not round-off limits; k-means has such minima (on these pairs,
#: Tether seeds 4, 6 and 8 of 0 to 9 end in three others, up to 0.003 apart in E).
SSE_REL_LIMIT = 1e-6
CENTROID_ATOL = 1e-3


def _read_csv(path: Path, header: tuple[str, ...]) -> np.ndarray:
    with path.open(encoding="ascii", newline="") as handle:
        assert tuple(handle.readline().rstrip("\r\n").split(",")) == header, path.name
    return np.loadtxt(path, dtype=np.float64, delimiter=",", skiprows=1, ndmin=2)


def _read_density() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with h5py.File(RECORD_DIR / "density.h5", "r") as h5:
        x = h5["x"][()].ravel()
        y = h5["y"][()].ravel()
        density = h5["density"][()]  # MATLAB column-major: density[i, j] is at (x[i], y[j])
    return x, y, density


def _cut_density(points: np.ndarray, grid: np.ndarray, bandwidth: float) -> np.ndarray:
    """The normalized product-Gaussian density with mvksdensity's cut at 4 bandwidths an axis."""
    zx = (grid[:, None] - points[None, :, 0]) / bandwidth
    zy = (grid[:, None] - points[None, :, 1]) / bandwidth
    kx = np.exp(-0.5 * zx**2) * (np.abs(zx) <= MATLAB_KERNEL_CUT)
    ky = np.exp(-0.5 * zy**2) * (np.abs(zy) <= MATLAB_KERNEL_CUT)
    raw = kx @ ky.T
    step = float(grid[1] - grid[0])
    return raw / (raw.sum() * step * step)


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads((RECORD_DIR / "provenance.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trace_paths() -> dict[str, Path]:
    keys = sorted(k for k in fetch.load_manifest() if k.startswith("sa2-trace-"))
    paths = {fetch.path_for(k).name: fetch.path_for(k) for k in keys}
    assert len(paths) == N_TRACES
    return paths


@pytest.fixture(scope="module")
def chunks(trace_paths) -> list[np.ndarray]:
    """Tether's apparent E of the first 51 frames of each trace, in the script's file order."""
    with (RECORD_DIR / "files.csv").open(encoding="ascii", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["trace"]
    names = [row[0] for row in rows[1:]]
    # MATLAB's ``sort`` of the names, by character code, which is Python's order for ASCII.
    assert names == sorted(trace_paths)
    out = []
    for name in names:
        donor, acceptor = read_trace(trace_paths[name])
        assert donor.size > PAIRS_PER_FILE, name  # the script cut every file to 50 pairs
        out.append(apparent_fret(donor, acceptor)[: PAIRS_PER_FILE + 1])
    return out


@pytest.fixture(scope="module")
def cloud(chunks):
    return successive_frame_cloud(chunks)


def test_the_record_is_the_pinned_script_on_the_test_traces(record, trace_paths) -> None:
    pins = tomllib.loads((REPO / "scripts/reference/matlab/pins.toml").read_text(encoding="utf-8"))
    assert pins[TOOL]["citation"] == CITATION
    assert record["method"] == "matlab-batch"
    assert record["task"] == "cloud"
    assert record["tool"] == {"name": TOOL, "citation": CITATION, "files": pins[TOOL]["files"]}
    assert SCRIPT in record["tool"]["files"]
    assert record["params"] == {"seed": 0}
    assert re.fullmatch(r"20[0-9]{2}[ab]", record["matlab"]["release"])
    # The inputs are the committed smfret-analysis2 traces, byte for byte.
    entries = fetch.load_manifest()
    manifest = {fetch.path_for(k).name: entries[k].sha256 for k in entries if "sa2-trace-" in k}
    assert record["inputs"]["traces"] == {name: manifest[name] for name in trace_paths}
    with (RECORD_DIR / "toolbox.csv").open(encoding="ascii", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["toolbox", "version"]
    assert rows[1][0] == "Statistics and Machine Learning Toolbox"


def test_the_pairs_are_the_scripts_pairs_exactly(chunks) -> None:
    expected = _read_csv(RECORD_DIR / "pairs.csv", ("x", "y"))
    assert expected.shape == (N_TRACES * PAIRS_PER_FILE, 2)
    got = successive_frame_pairs(chunks)
    # The same IEEE division of the same doubles, written with %.17g: equal, not close.
    assert np.array_equal(got.pairs, expected)
    assert got.n_molecules == N_TRACES
    assert np.array_equal(np.bincount(got.molecule_index), np.full(N_TRACES, PAIRS_PER_FILE))


def test_the_kernel_cut_is_the_whole_density_difference(cloud) -> None:
    x, y, density = _read_density()
    np.testing.assert_allclose(cloud.grid, x, rtol=0, atol=GRID_ATOL)
    np.testing.assert_allclose(cloud.grid, y, rtol=0, atol=GRID_ATOL)
    cut = _cut_density(cloud.pairs.pairs, cloud.grid, cloud.bandwidth)
    assert np.array_equal(cut == 0.0, density == 0.0)
    assert (density == 0.0).sum() == 5216
    error = np.max(np.abs(cut - density)) / np.max(density)
    assert error <= CUT_DENSITY_REL_LIMIT, error


def test_the_density_is_the_scripts_density(cloud) -> None:
    _, _, density = _read_density()
    assert cloud.density is not None and cloud.density.shape == density.shape
    error = np.max(np.abs(cloud.density - density)) / np.max(density)
    assert error <= DENSITY_REL_LIMIT, error
    # The orientation is checked, not assumed: the cloud is not symmetric about E(i) = E(i+1), so a
    # transposed density misses the cut comparison by far (measured 1.2e-2 of the peak).
    cut = _cut_density(cloud.pairs.pairs, cloud.grid, cloud.bandwidth)
    assert np.max(np.abs(cut - density.T)) / np.max(density) > 1e-3


def test_the_contour_levels_are_the_scripts_levels(cloud) -> None:
    expected = _read_csv(RECORD_DIR / "thresholds.csv", ("coverage", "threshold"))
    # MATLAB's ``0.1:0.1:0.9`` and Tether's literals may differ by one ulp; the levels do not.
    np.testing.assert_allclose(cloud.hdr_coverages, expected[:, 0], rtol=0, atol=1e-15)
    # Tether's level rule on the cut density gives the script's levels.
    cut = _cut_density(cloud.pairs.pairs, cloud.grid, cloud.bandwidth)
    levels = _hdr_levels_from_grid(cut, cloud.node_area, cloud.hdr_coverages)
    assert levels is not None
    np.testing.assert_allclose(levels, expected[:, 1], rtol=CUT_LEVEL_REL_LIMIT, atol=0)
    assert cloud.hdr_levels is not None
    np.testing.assert_allclose(cloud.hdr_levels, expected[:, 1], rtol=LEVEL_REL_LIMIT, atol=0)


def test_the_scripts_centroids_are_a_lloyd_fixed_point(cloud) -> None:
    centroids = _read_csv(RECORD_DIR / "centroids.csv", ("x", "y"))
    assert centroids.shape == (4, 2)
    points = cloud.pairs.pairs
    labels, _ = _assign(points, centroids)
    means = np.array([points[labels == c].mean(axis=0) for c in range(centroids.shape[0])])
    np.testing.assert_allclose(means, centroids, rtol=0, atol=FIXED_POINT_ATOL)
    stepped, _, iterations = _lloyd(points, centroids.copy(), max_iter=100)
    np.testing.assert_allclose(stepped, centroids, rtol=0, atol=FIXED_POINT_ATOL)
    assert iterations == 1  # one update, and no point changes cluster


def test_tethers_centroids_are_the_scripts_centroids(cloud) -> None:
    centroids = _read_csv(RECORD_DIR / "centroids.csv", ("x", "y"))
    points = cloud.pairs.pairs
    _, d2 = _assign(points, centroids)
    matlab_sse = float(d2.sum())
    assert cloud.centroid_sse is not None
    assert abs(cloud.centroid_sse - matlab_sse) / matlab_sse <= SSE_REL_LIMIT
    # MATLAB's kmeans orders its clusters by its own start; Tether's are sorted by E(i), E(i+1).
    order = np.lexsort((centroids[:, 1], centroids[:, 0]))
    np.testing.assert_allclose(cloud.centroids, centroids[order], rtol=0, atol=CENTROID_ATOL)
