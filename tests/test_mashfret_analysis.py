# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tether's FRET histogram against MASH-FRET's own Histogram analysis (#712, ADR-0101).

The MASH-FRET project files of Zenodo doi:10.5281/zenodo.24104528 (CC-BY-4.0) store, for each
molecule tag, the FRET histogram that MASH-FRET's Histogram analysis built (``prmThm``), next to
the traces it was built from. ``getHist.m`` takes the γ-corrected FRET of the ``denoise``
intensities over the included frames and bins it with MATLAB's ``hist`` at the bin centres
``low:bin:high``; with "overflow" off it then removes the first and the last bin, which collect
everything beyond the centres. The bins that remain are therefore ``bin`` wide and centred on the
stored centres, and Tether's :func:`~tether.analysis.histogram.apparent_e_histogram` with that
count over ``(first centre − bin/2, last centre + bin/2)`` is the same binning. Fed Tether's
:func:`~tether.fret.efficiency.corrected_fret` with MASH-FRET's γ, it must give MASH-FRET's counts
**exactly**: no limit (ADR-0101).

The required matrix reads the committed extract of one tag, "Path A 0.2 +- 0.3" of the smallest
project (``scripts/make_mashfret_fixture.py``); the weekly workstation run checks every FRET
histogram that the eight readable files of the archive store (37). The state, population and rate
comparisons need an idealization and run in the sidecar job
(``tests/test_mashfret_analysis_sidecar.py``); their limits are in
``schema/mashfret_analysis_limits.json``, which the last test here keeps tied to its measurements.
"""

from __future__ import annotations

import importlib.util
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest

from _mashfret import TagAnalysis, UnsupportedProject, read_tag_analysis, stored_histogram_tags
from reference_data.fetch import path_for
from tether.analysis.histogram import apparent_e_histogram
from tether.fret.efficiency import corrected_fret

REPO = Path(__file__).resolve().parents[1]
LIMITS = REPO / "schema" / "mashfret_analysis_limits.json"
PATH_A = "mashfret-ed135e-30mm-path-a"
ARCHIVE = "mashfret-group-iib-intron-archive"
#: Tag 12 of the smallest project: "Path A 0.2 +- 0.3".
PATH_A_TAG = 12


@pytest.fixture(scope="module")
def path_a() -> TagAnalysis:
    return read_tag_analysis(path_for(PATH_A), PATH_A_TAG)


def tether_counts(analysis: TagAnalysis, *, gamma: np.ndarray | None = None) -> np.ndarray:
    """Tether's histogram of Tether's corrected FRET on MASH-FRET's bins."""
    gammas = analysis.gamma if gamma is None else gamma
    fret = np.concatenate(
        [
            corrected_fret(d, a, alpha=0.0, gamma=g)
            for d, a, g in zip(analysis.donor, analysis.acceptor, gammas, strict=True)
        ]
    )
    half = analysis.bin_width / 2
    hist = apparent_e_histogram(
        fret,
        bins=analysis.centres.size,
        value_range=(analysis.centres[0] - half, analysis.centres[-1] + half),
        density=False,
    )
    return hist.counts


def test_the_path_a_extract_holds_mashfret_s_analysis(path_a: TagAnalysis) -> None:
    assert path_a.molecules.size == 17
    assert np.all(path_a.gamma == 0.45)
    assert path_a.frame_time == pytest.approx(0.09084)
    # 49 bins of 0.024 from -0.176; the uniform spacing is what the range mapping assumes.
    assert path_a.centres.size == 49 and path_a.bin_width == pytest.approx(0.024)
    assert np.allclose(np.diff(path_a.centres), path_a.bin_width, rtol=0, atol=1e-12)
    assert path_a.counts.sum() == 10086  # every included frame lies inside the bins but one
    assert path_a.gaussians is not None and path_a.gaussians.shape == (3, 8)
    assert path_a.dwells is not None and path_a.dwells.shape[1] == 8


def test_tether_gives_mashfret_s_fret_histogram_exactly(path_a: TagAnalysis) -> None:
    assert np.array_equal(tether_counts(path_a), path_a.counts)


def test_without_gamma_the_histogram_is_not_mashfret_s(path_a: TagAnalysis) -> None:
    # Teeth: apparent FRET (γ = 1) moves frames to other bins.
    wrong = tether_counts(path_a, gamma=np.ones_like(path_a.gamma))
    assert np.abs(wrong - path_a.counts).sum() > 1000


def test_the_reader_refuses_a_stored_tag_without_a_histogram(path_a: TagAnalysis) -> None:
    with pytest.raises(ValueError, match="no FRET histogram"):
        read_tag_analysis(path_for(PATH_A), 0)  # the extract keeps only the Path A row


def _round_2sig(value: float, up: bool) -> float:
    if value == 0:
        return 0.0
    exp = math.floor(math.log10(abs(value)))
    scaled = round(value / 10 ** (exp - 1), 9)
    return float(f"{math.ceil(scaled) if up else math.floor(scaled)}e{exp - 1}")


def test_every_frozen_limit_is_its_measurement_rounded_outward() -> None:
    doc = json.loads(LIMITS.read_text("utf-8"))
    gates = doc["gates"]
    assert set(gates) == {"frame_agreement", "state_count_mean_abs_difference"}
    for name, entry in gates.items():
        up = entry["direction"] == "at_most"
        assert entry["direction"] in {"at_most", "at_least"}, name
        assert entry["limit"] == _round_2sig(entry["measured"], up=up), name


def test_rounding_rule() -> None:
    assert _round_2sig(0.8481213, up=False) == 0.84
    assert _round_2sig(0.3529411, up=True) == 0.36
    assert _round_2sig(0.0, up=True) == 0.0


@pytest.mark.workstation
def test_every_stored_fret_histogram_of_the_archive_is_tether_s(tmp_path) -> None:
    archive = path_for(ARCHIVE)
    script = REPO / "scripts" / "make_mashfret_fixture.py"
    spec = importlib.util.spec_from_file_location("make_mashfret_fixture", script)
    maker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    assert maker.extract_path_a(maker.source_bytes(archive)) == path_for(PATH_A).read_bytes()

    checked = skipped = 0
    with zipfile.ZipFile(archive) as zf:
        for name in sorted(n for n in zf.namelist() if n.endswith(".mash")):
            target = tmp_path / Path(name).name
            target.write_bytes(zf.read(name))
            try:
                tags = stored_histogram_tags(target)
            except UnsupportedProject:
                skipped += 1  # the MASH-FRET 1.3.4 file keeps its settings in a TP structure
                continue
            for tag in tags:
                analysis = read_tag_analysis(target, tag)
                assert np.array_equal(tether_counts(analysis), analysis.counts), (name, tag)
                checked += 1
            target.unlink()
    assert (checked, skipped) == (37, 1)
