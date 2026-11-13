# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether against smfret-analysis2: the FRET histogram and the cross-correlation (#709, ADR-0084).

PRD §1 makes two model-free lenses first-class, and both come from the laboratory's MATLAB scripts,
smfret-analysis2. The public repository at the commit that ``scripts/reference/matlab/pins.toml``
pins carries 76 trace files and the two outputs that its ``create_histogram.m`` wrote from them,
``total_FRET.csv`` and ``total_XC.csv``. They are committed verbatim under
``tests/fixtures/smfret_analysis2/`` (manifest ids ``sa2-*``, ``tests/fixtures/PROVENANCE.md``).

Tether reproduces both files to the precision of the file once two documented differences of
convention are converted exactly. Neither is a tolerance: each test applies the conversion and then
asks for agreement to 1e-14, float64 round-off (see ``ATOL``).

1. **Histogram bin edges.** ``hist(fret, -0.2:0.01:1.2)`` puts the edges at the midpoints of the
   centres and closes each bin on the right, ``(a, b]``. Tether's :func:`apparent_e_histogram`
   (``np.histogram``) closes each bin on the left, ``[a, b)``. Only a sample that sits exactly on an
   inner edge is binned differently; in these 76 traces there is one, at 0.375. The test moves each
   such sample one bin down, and counts them, so a second tie or a lost one also fails.
2. **Cross-correlation normalization and lag sign.** ``calculate_cross_corr.m`` divides the sum at
   lag ``k`` by ``N - k`` and by MATLAB's ``std(donor) * std(acceptor)``, and ``std`` divides by
   ``N - 1``. Tether divides every lag by ``N`` and by the population σ (PRD §7.7, the biased
   Pearson curve, kept by the maintainer: #709 Q13 A). The exact factor from Tether's value to
   the script's is therefore ``(N - 1) / (N - k)``, not ``N / (N - k)`` alone, which leaves a 1/N
   error. The script sums ``donor(t) * acceptor(t + k)``; Tether's convention is
   ``donor[t] * acceptor[t - k]``, so the script's lag ``k`` is Tether's lag ``-k``.

The script also writes a second cross-correlation normalization in other versions, a smoothed
histogram and a successive-frame cloud; they are out of scope here (#709, #710).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import pytest

from _sa2_traces import read_output, read_trace
from reference_data import fetch
from tether.analysis.crosscorr import cross_correlation
from tether.analysis.histogram import apparent_e_histogram
from tether.fret.efficiency import apparent_fret

REPO = Path(__file__).resolve().parents[1]
#: ``create_histogram.m``: ``bin_size``, the centres ``-0.2:bin_size:1.2``, ``max_cc_lag`` and the
#: default ``timeunit`` that its ``delta-time`` column is written in.
BIN_SIZE = 0.01
FIRST_CENTRE, LAST_CENTRE = -0.2, 1.2
MAX_CC_LAG = 60
TIME_UNIT = 0.03
#: The files write 15 significant digits and every value is below 0.15 in magnitude, so their own
#: rounding is below 1e-16. Float64 round-off adds to that: the FFT over up to 2,491 frames and the
#: order of the weighted sums (measured 2026-11-12 on Linux: 4.4e-16 for the histogram, 3.1e-16 for
#: the cross-correlation). 1e-14 leaves room for another platform's FFT and summation order, and is
#: far below what a real change makes: one sample of one trace moving one bin changes that bin by
#: more than 4e-6, and the 1/N error of the wrong factor is more than 1e-4.
ATOL = 1e-14
#: Inner bin edges that one sample of the 76 traces sits on exactly (see the module docstring).
EXPECTED_EDGE_TIES = 1


def _entries() -> dict[str, fetch.Dataset]:
    return {k: d for k, d in fetch.load_manifest().items() if k.startswith("sa2-")}


def _traces() -> list[tuple[str, np.ndarray, np.ndarray]]:
    traces = []
    for key in sorted(k for k in _entries() if k.startswith("sa2-trace-")):
        path = fetch.path_for(key)
        donor, acceptor = read_trace(path)
        traces.append((path.name, donor, acceptor))
    return traces


@pytest.fixture(scope="module")
def traces() -> list[tuple[str, np.ndarray, np.ndarray]]:
    return _traces()


def test_the_fixtures_are_the_pinned_commit_of_smfret_analysis2() -> None:
    pins = tomllib.loads((REPO / "scripts/reference/matlab/pins.toml").read_text(encoding="utf-8"))
    commit = pins["smfret-analysis2"]["commit"]
    entries = _entries()
    assert sum(k.startswith("sa2-trace-") for k in entries) == 76
    assert {"sa2-total-fret", "sa2-total-xc"} <= set(entries)
    for d in entries.values():
        assert d.source == f"accession:github:vafabakhsh-lab/smfret-analysis2@{commit}", d.id
        assert commit in d.record_url, d.id
        assert (d.tier, d.license) == ("git", "GPL-3.0-only"), d.id
        assert d.file.startswith("tests/fixtures/smfret_analysis2/"), d.id


def test_tether_histogram_equals_total_fret_raw_column(traces) -> None:
    expected = read_output(fetch.path_for("sa2-total-fret"))
    centres = expected[:, 0]
    n_bins = round((LAST_CENTRE - FIRST_CENTRE) / BIN_SIZE) + 1
    np.testing.assert_allclose(centres, FIRST_CENTRE + BIN_SIZE * np.arange(n_bins), atol=1e-12)

    # The script normalizes each trace's histogram to sum 1 and averages over the traces: that is
    # Tether's per-molecule equal weight (1/m for each of a molecule's m finite frames), as counts.
    values, weights = [], []
    for _, donor, acceptor in traces:
        e = apparent_fret(donor, acceptor)
        e = e[np.isfinite(e)]
        values.append(e)
        weights.append(np.full(e.shape, 1.0 / e.size))
    low, high = centres[0] - BIN_SIZE / 2, centres[-1] + BIN_SIZE / 2
    hist = apparent_e_histogram(
        np.concatenate(values),
        bins=n_bins,
        value_range=(low, high),
        density=False,
        weights=np.concatenate(weights),
    )
    # The script's first and last bins are open (-inf and +inf edges); every sample here is inside
    # Tether's range, so no sample is dropped that the script would have counted.
    assert all(((v >= low) & (v < high)).all() for v in values)

    # The script's inner edges are the midpoints of its centres, each bin closed on the right; with
    # open first and last bins, a sample's bin is the count of edges below it. Tether's edges agree
    # with them to one ulp and close each bin on the left. Move every sample that the two rules
    # bin differently from Tether's bin to the script's, and count them.
    script_edges = (centres[:-1] + centres[1:]) / 2
    np.testing.assert_allclose(hist.bin_edges[1:-1], script_edges, rtol=0, atol=1e-15)
    counts = hist.counts.copy()
    moved = 0
    for e, w in zip(values, weights, strict=True):
        tether_bin = np.searchsorted(hist.bin_edges, e, side="right") - 1
        script_bin = np.searchsorted(script_edges, e, side="left")
        for i in np.flatnonzero(tether_bin != script_bin):
            assert e[i] in script_edges, "only a sample on an edge may be binned differently"
            assert tether_bin[i] - script_bin[i] == 1, "a tie moves exactly one bin down"
            counts[tether_bin[i]] -= w[i]
            counts[script_bin[i]] += w[i]
            moved += 1
    assert moved == EXPECTED_EDGE_TIES

    np.testing.assert_allclose(counts / len(traces), expected[:, 1], rtol=0, atol=ATOL)


def _script_xc(traces, factor) -> np.ndarray:
    total = np.zeros(MAX_CC_LAG)
    k = np.arange(MAX_CC_LAG)
    for _, donor, acceptor in traces:
        n = donor.size
        # create_histogram.m computes a trace's curve only when it is longer than max_cc_lag and
        # otherwise adds the previous trace's curve again; every trace here is longer.
        assert n > MAX_CC_LAG
        cc = cross_correlation(donor, acceptor, max_lag=MAX_CC_LAG - 1)
        at = {int(lag): value for lag, value in zip(cc.lags, cc.values, strict=True)}
        tether = np.array([at[-int(i)] for i in k])  # the script's lag k is Tether's lag -k
        total += tether * factor(n, k)
    return total / len(traces)


def test_tether_cross_correlation_equals_total_xc(traces) -> None:
    expected = read_output(fetch.path_for("sa2-total-xc"))
    assert expected.shape == (MAX_CC_LAG, 2)
    np.testing.assert_allclose(expected[:, 0], TIME_UNIT * np.arange(MAX_CC_LAG), atol=1e-12)

    # Tether: sum / (N σd σa), σ with ddof 0. The script: sum / (N - k) / (sd sa), s with ddof 1,
    # and s² = σ² N / (N - 1). So the script's value = Tether's x N / (N - k) x (N - 1) / N.
    got = _script_xc(traces, lambda n, k: (n - 1) / (n - k))
    np.testing.assert_allclose(got, expected[:, 1], rtol=0, atol=ATOL)


def test_the_lag_factor_alone_does_not_reach_the_file(traces) -> None:
    """``N / (N - k)`` without the sample-std part misses by the 1/N of the shortest traces."""
    expected = read_output(fetch.path_for("sa2-total-xc"))[:, 1]
    got = _script_xc(traces, lambda n, k: n / (n - k))
    assert np.abs(got - expected).max() > 1e-4
