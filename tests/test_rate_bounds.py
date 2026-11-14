# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dirichlet rate bounds against the published files of Campos-Chavez et al. 2024 (#758).

The Source Data of Campos-Chavez et al., Nat Commun 15:6592 (2024),
doi:10.1038/s41467-024-50885-x (CC BY-NC-ND 4.0, committed unchanged by #755, ADR-0083), holds
five transition count matrices (``Kmatrix/*.txt``) and the output that the authors'
``CalculateKbounds.py`` printed for each (``Kmatrix/*.out``). The step has no random part, so
Tether's :func:`~tether.analysis.rate_bounds.dirichlet_rate_bounds` must print the **same digits**
for every matrix of every file (maintainer decision Q7: A). The frame time is the script's own,
0.1 s (its comment: 10 frames per second). ADR-0092 records the method and its source.

The published script does not run as it is committed (one line has a wrong indent), so the test
compares with its saved output and never runs it.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from reference_data import fetch
from tether.analysis.rate_bounds import (
    RATE_BOUNDS_METHOD,
    RateBounds,
    dirichlet_rate_bounds,
    model_rate_bounds,
)

#: The frame time of ``CalculateKbounds.py`` (``k_bounds(TransMatrix, var, .1)``).
PUBLISHED_DT = 0.1
DATASETS = ("DeltaDiscriminator", "DeltaNCCA", "LinkerMutant", "RAGmutant", "Wt-Wt")

#: Each printed section of a ``.out`` file, the attribute of :class:`RateBounds` it shows, and
#: the format the script printed it with.
SECTIONS = {
    "Transition matrix =": ("counts", "{:0.5f}"),
    "Variance matrix =": ("variance", "{:0.12f}"),
    "Normalized transition matrix =": ("norm_tmatrix", "{:0.5f}"),
    "Rates =": ("rates", "{:0.5f}"),
    "Upper bounds": ("upper", "{:0.5f}"),
    "Lower bounds": ("lower", "{:0.5f}"),
    "Upper bounds difference": ("upper_difference", "{:0.5f}"),
    "Lower bounds difference": ("lower_difference", "{:0.5f}"),
}


def _manifest_id(name: str, suffix: str) -> str:
    return f"campos2024-kmatrix-{name.lower()}-{suffix}"


def _published(path: Path) -> dict[str, list[str]]:
    """The printed numbers of each section of a ``.out`` file, as the strings it holds."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        header = line.strip()
        if header in SECTIONS:
            current = sections.setdefault(header, [])
        elif current is not None:
            current.extend(re.findall(r"-?\d+\.\d+", line))
    return sections


@pytest.fixture(scope="module", params=DATASETS)
def published(request) -> tuple[str, RateBounds, dict[str, list[str]]]:
    name = request.param
    counts = np.loadtxt(fetch.path_for(_manifest_id(name, "txt")))
    out = _published(fetch.path_for(_manifest_id(name, "out")))
    return name, dirichlet_rate_bounds(counts, PUBLISHED_DT), out


def test_every_published_section_is_read(published) -> None:
    name, result, out = published
    n = result.counts.shape[0]
    assert set(out) == set(SECTIONS), name
    for header, numbers in out.items():
        assert len(numbers) == n * n, f"{name}: {header} has {len(numbers)} numbers"


@pytest.mark.parametrize("header", list(SECTIONS))
def test_published_digits_agree(published, header: str) -> None:
    """Each matrix prints the digits of the published ``.out``, entry by entry."""
    name, result, out = published
    attribute, fmt = SECTIONS[header]
    ours = [fmt.format(x) for x in np.asarray(getattr(result, attribute)).ravel()]
    assert ours == out[header], f"{name}: {header}"


def test_bounds_are_not_symmetric(published) -> None:
    """The logarithm is not linear, so the upper bound is farther from the rate."""
    name, result, _ = published
    assert np.all(result.lower <= result.rates), name
    assert np.all(result.rates <= result.upper), name
    off = ~np.eye(result.counts.shape[0], dtype=bool)
    assert np.all(result.upper_difference[off] >= -result.lower_difference[off]), name
    assert np.any(result.upper_difference > -result.lower_difference), name


# --- defined results outside the published files ---


def test_a_zero_count_gives_zero_rate_and_bounds() -> None:
    r = dirichlet_rate_bounds([[90.0, 10.0, 0.0], [5.0, 95.0, 0.0], [1.0, 1.0, 98.0]], 0.1)
    for m in (r.norm_tmatrix, r.variance, r.rates, r.lower, r.upper):
        assert m[0, 2] == 0.0
        assert not np.signbit(m[0, 2])
    assert np.all(np.isfinite(r.rates))


def test_a_row_of_zero_sum_is_undefined_and_leaves_the_others() -> None:
    r = dirichlet_rate_bounds([[90.0, 10.0], [0.0, 0.0]], 0.1)
    for m in (r.norm_tmatrix, r.variance, r.rates, r.lower, r.upper):
        assert np.all(np.isnan(m[1]))
        assert np.all(np.isfinite(m[0]))
    np.testing.assert_allclose(r.norm_tmatrix[0], [0.9, 0.1])


def test_a_probability_of_one_gives_infinite_rate_and_bounds() -> None:
    r = dirichlet_rate_bounds([[0.0, 50.0], [10.0, 90.0]], 0.1)
    assert r.norm_tmatrix[0, 1] == 1.0
    assert r.variance[0, 1] == 0.0
    assert r.rates[0, 1] == np.inf
    assert r.lower[0, 1] == np.inf
    assert r.upper[0, 1] == np.inf
    assert r.rates[0, 0] == 0.0


def test_bound_probabilities_are_clipped_to_zero_and_one() -> None:
    """Few counts: ``p + sd`` passes 1 and ``p - sd`` passes 0."""
    r = dirichlet_rate_bounds([[0.5, 0.1], [0.1, 0.5]], 0.1)
    p, sd = r.norm_tmatrix, np.sqrt(r.variance)
    assert p[0, 0] + sd[0, 0] > 1 and p[0, 1] - sd[0, 1] < 0
    assert r.upper[0, 0] == np.inf
    assert r.lower[0, 1] == 0.0
    assert 0 < r.rates[0, 1] < r.upper[0, 1] < np.inf


def test_the_rate_is_the_first_order_rate_of_the_frame_probability() -> None:
    k, dt = 2.0, 0.05
    p = 1 - np.exp(-k * dt)
    r = dirichlet_rate_bounds([[1e6 * (1 - p), 1e6 * p], [1e6 * p, 1e6 * (1 - p)]], dt)
    np.testing.assert_allclose(r.rates[0, 1], k, rtol=1e-12)


# --- refusals ---


def test_a_normalized_matrix_is_refused() -> None:
    with pytest.raises(ValueError, match="normalized transition matrix.*un-normalized"):
        dirichlet_rate_bounds([[0.9, 0.1], [0.2, 0.8]], 0.1)


@pytest.mark.parametrize(
    ("counts", "message"),
    [
        ([[1.0, 2.0, 3.0]], "must be square"),
        ([1.0, 2.0], "must be square"),
        (np.zeros((0, 0)), "not empty"),
        ([[1.0, np.nan], [1.0, 1.0]], "not finite"),
        ([[1.0, np.inf], [1.0, 1.0]], "not finite"),
        ([[5.0, -1.0], [1.0, 1.0]], "negative"),
    ],
)
def test_a_bad_count_matrix_is_refused(counts, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        dirichlet_rate_bounds(counts, 0.1)


@pytest.mark.parametrize("dt", [0.0, -0.1, np.nan, np.inf])
def test_a_bad_frame_time_is_refused(dt: float) -> None:
    with pytest.raises(ValueError, match="dt must be"):
        dirichlet_rate_bounds([[9.0, 1.0], [1.0, 9.0]], dt)


# --- the project entry point ---


@pytest.fixture
def stored_model(tmp_path: Path):
    pytest.importorskip("h5py")
    import h5py

    from _analysis_store import build_store_with_model

    paths = np.array([[0, 0, 1, 1, 0], [1, 1, 0, 0, 1]], dtype="int64")
    project, _ = build_store_with_model(tmp_path, paths, np.array([0.2, 0.8]))
    counts = np.loadtxt(fetch.path_for(_manifest_id("DeltaNCCA", "txt")))
    with h5py.File(project.path, "r+") as f:
        f["idealization"]["vbconhmm"].create_dataset("tmatrix", data=counts)
    return project, counts


def test_model_rate_bounds_reads_the_stored_count_matrix(stored_model) -> None:
    project, counts = stored_model
    r = model_rate_bounds(project, "vbconhmm", dt=PUBLISHED_DT, created_utc="2026-11-14T00:00:00Z")
    expected = dirichlet_rate_bounds(counts, PUBLISHED_DT)
    for attribute in ("counts", "norm_tmatrix", "variance", "rates", "upper", "lower"):
        np.testing.assert_array_equal(getattr(r, attribute), getattr(expected, attribute))
    assert r.provenance is not None
    assert r.provenance["method"] == RATE_BOUNDS_METHOD
    assert r.provenance["parameters"] == {"dt": PUBLISHED_DT}
    assert r.provenance["created_utc"] == "2026-11-14T00:00:00Z"
    assert r.provenance["source_project"] == project.path.name
    assert r.provenance["model_name"] == "vbconhmm"
    assert r.provenance["model_created_utc"] == "2026-01-01T00:00:00Z"
    assert r.provenance["app_version"]
    # The same call on the path gives the same numbers.
    by_path = model_rate_bounds(project.path, "vbconhmm", dt=PUBLISHED_DT)
    np.testing.assert_array_equal(by_path.rates, r.rates)


def test_model_rate_bounds_refuses_a_model_without_counts(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    from _analysis_store import build_store_with_model

    paths = np.array([[0, 1, 1, 0]], dtype="int64")
    project, _ = build_store_with_model(tmp_path, paths, np.array([0.2, 0.8]))
    with pytest.raises(ValueError, match="stores no transition count matrix"):
        model_rate_bounds(project, "vbconhmm", dt=0.1)
    with pytest.raises(KeyError):
        model_rate_bounds(project, "missing", dt=0.1)
