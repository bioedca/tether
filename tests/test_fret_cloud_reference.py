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
  normalized to ``f / sum(f) / (dx * dy)``: Tether's product-Gaussian KDE with the same defaults.
* **Contour levels.** The cells sorted by density, ``cumsum * dx * dy`` and, for each coverage
  ``p`` of 10 % to 90 %, the first cell where it reaches ``p``: Tether's HDR level rule.
* **Centroids.** ``kmeans(pairs, 4, 'Replicates', 5)``: k-means++ starts, Lloyd iterations, the run
  of least total squared distance. The starts are random and the two programs draw them from
  different generators, so the test does not ask for the same run. It asks that MATLAB's
  centroids are a fixed point of Tether's Lloyd step, and that Tether's best run has the same total
  squared distance and the same centroids to the measured limits below.
"""

from __future__ import annotations

import csv
import json
import tomllib
from pathlib import Path

import h5py
import numpy as np
import pytest

from _sa2_traces import read_trace
from reference_data import fetch
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

#: The measured limits (see the pull request and ADR-0105); each is the measured value rounded up
#: to the next power of ten, and each is far below what a real change makes.
#: Grid nodes: ``linspace`` and ``numpy.linspace`` place a node at most one ulp apart.
GRID_ATOL = 1e-15
#: Density, relative to its largest value: two sums of 3800 Gaussians in another order.
DENSITY_REL_LIMIT = 1e-12
#: Contour levels, relative: each is one grid value, so they agree as the density does.
LEVEL_REL_LIMIT = 1e-12
#: MATLAB's centroids under one Lloyd step of Tether: the mean of each cluster, in another order.
FIXED_POINT_ATOL = 1e-12
#: Tether's best k-means run against MATLAB's: relative total squared distance, and the largest
#: centroid difference in E. Two k-means runs can end in two local minima this close (seed 4 of
#: Tether's own generator ends 1e-4 from seeds 0 to 3), so these are not round-off limits.
SSE_REL_LIMIT = 1e-5
CENTROID_ATOL = 1e-3


def _read_csv(path: Path, header: tuple[str, ...]) -> np.ndarray:
    with path.open(encoding="ascii", newline="") as handle:
        assert tuple(handle.readline().rstrip("\r\n").split(",")) == header, path.name
    return np.loadtxt(path, dtype=np.float64, delimiter=",", skiprows=1, ndmin=2)


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
    assert record["matlab"]["release"].startswith("R20")
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


def test_the_density_is_the_scripts_density(cloud) -> None:
    with h5py.File(RECORD_DIR / "density.h5", "r") as h5:
        x = h5["x"][()].ravel()
        y = h5["y"][()].ravel()
        density = h5["density"][()]  # MATLAB column-major: density[i, j] is at (x[i], y[j])
    np.testing.assert_allclose(cloud.grid, x, rtol=0, atol=GRID_ATOL)
    np.testing.assert_allclose(cloud.grid, y, rtol=0, atol=GRID_ATOL)
    assert cloud.density is not None and cloud.density.shape == density.shape
    error = np.max(np.abs(cloud.density - density)) / np.max(density)
    assert error <= DENSITY_REL_LIMIT, error
    # The orientation is checked, not assumed: the cloud is not symmetric about E(i) = E(i+1) to
    # the limit above, so a transposed density fails.
    assert np.max(np.abs(cloud.density - density.T)) / np.max(density) > 1e3 * DENSITY_REL_LIMIT


def test_the_contour_levels_are_the_scripts_levels(cloud) -> None:
    expected = _read_csv(RECORD_DIR / "thresholds.csv", ("coverage", "threshold"))
    # MATLAB's ``0.1:0.1:0.9`` and Tether's literals may differ by one ulp; the levels do not.
    np.testing.assert_allclose(cloud.hdr_coverages, expected[:, 0], rtol=0, atol=1e-15)
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
