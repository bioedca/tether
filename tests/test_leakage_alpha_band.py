# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The conjunctive leakage-α validation band on real data (#546, ADR-0133).

The band itself is :func:`_leakage_band.alpha_agreement`. Its arithmetic is checked here in the
required matrix on plain numbers, and its two limits are checked against the PRD row they come
from, so the code and the specification cannot drift apart.

The check on data is a ``workstation`` test (ADR-0071): the donor-only sample and the FRET series
are the maintainer's laboratory data. It reads Tether's donor-only α from ``donly-01`` and the
tail α from the pooled molecules of ``lab-03`` to ``lab-08``, and asserts the band. The runner
records summary numbers only, under the neutral ids. A failure is a finding to report on the
issue, never a reason to change a limit.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

import _leakage_band as band

_PRD = Path(__file__).resolve().parents[1] / "docs" / "PRD.md"


# --- required matrix: the band -----------------------------------------------------------


def test_the_limits_are_the_prd_band_row() -> None:
    rows = [
        line
        for line in _PRD.read_text(encoding="utf-8").splitlines()
        if "Leakage-α validation band" in line
    ]
    assert len(rows) == 1, "PRD §11.2 must have exactly one leakage-α validation band row"
    row = rows[0]
    assert re.search(r"≤\s*20\s*%", row), row
    assert "0.05–0.2" in row, row
    assert band.MAX_RELATIVE_DIFFERENCE == 0.20
    assert band.PLAUSIBLE_BAND == (0.05, 0.2)


def test_agreeing_medians_inside_the_band_pass() -> None:
    result = band.alpha_agreement(0.10, 0.12)
    assert result.relative_difference == pytest.approx(0.02 / 0.11)
    assert result.passes


def test_the_band_is_never_a_pass_on_its_own() -> None:
    # Both medians are plausible, but they disagree by about 104 %: an OR would pass this.
    result = band.alpha_agreement(0.06, 0.19)
    assert result.donor_only_in_band and result.tail_in_band
    assert not result.agrees
    assert not result.passes


def test_agreeing_medians_outside_the_band_fail() -> None:
    result = band.alpha_agreement(0.25, 0.26)
    assert result.agrees
    assert not result.passes


def test_a_difference_over_the_limit_fails() -> None:
    result = band.alpha_agreement(0.100, 0.125)  # 0.025 / 0.1125 = 22 %
    assert not result.agrees and not result.passes


@pytest.mark.parametrize("missing", [None, float("nan")])
def test_a_withheld_median_fails(missing) -> None:
    for result in (band.alpha_agreement(missing, 0.1), band.alpha_agreement(0.1, missing)):
        assert math.isnan(result.relative_difference)
        assert not result.passes


# --- workstation: the band on the donor-only sample and its FRET series ------------------


def _finite(numbers: dict) -> dict:
    return {
        k: v
        for k, v in numbers.items()
        if v is not None and (isinstance(v, int) or math.isfinite(v))
    }


@pytest.mark.workstation
def test_donor_only_alpha_agrees_with_the_tail_alpha_of_its_series(private_data) -> None:
    pytest.importorskip("h5py")
    import _deeplasi_agreement as agree  # noqa: PLC0415
    from _deeplasi_public import read_donor_excitation_traces  # noqa: PLC0415

    sample = read_donor_excitation_traces(band.donor_only_file(private_data))
    donor_only = band.donor_only_alpha(sample.donor, sample.acceptor)

    donor, acceptor = [], []
    for neutral_id in band.SERIES_IDS:
        traces = read_donor_excitation_traces(
            agree.agreement_file(private_data, neutral_id, agree.STATE)
        )
        one = band.tail_alpha(traces.donor, traces.acceptor)
        private_data.record(
            neutral_id,
            **_finite(
                {
                    "tail_alpha": one.alpha,
                    "tail_n_qualifying": one.n_qualifying,
                    "tail_n_molecules": one.n_traces,
                }
            ),
        )
        donor.extend(traces.donor)
        acceptor.extend(traces.acceptor)
    tail = band.tail_alpha(donor, acceptor)
    result = band.alpha_agreement(donor_only.alpha, tail.alpha)
    private_data.record(
        band.DONOR_ONLY_ID,
        **_finite(
            {
                "alpha_donor_only": donor_only.alpha,
                "donor_only_n_qualifying": donor_only.n_qualifying,
                "donor_only_n_molecules": donor_only.n_traces,
                "alpha_tail_series": tail.alpha,
                "tail_series_n_qualifying": tail.n_qualifying,
                "tail_series_n_molecules": tail.n_traces,
                "relative_difference": result.relative_difference,
            }
        ),
    )
    assert donor_only.alpha is not None, (
        f"{band.DONOR_ONLY_ID}: the donor-only α is withheld ({donor_only.n_qualifying} qualifying)"
    )
    assert tail.alpha is not None, f"the series tail α is withheld ({tail.n_qualifying} qualifying)"
    # PRD §7.2 / §11.2, conjunctive (ADR-0133): agreement within 20 % AND both medians in 0.05–0.2.
    assert result.passes, (
        f"{band.DONOR_ONLY_ID} against {band.SERIES_IDS[0]}–{band.SERIES_IDS[-1]}: donor-only α "
        f"{result.alpha_donor_only:.4f}, tail α {result.alpha_tail:.4f}, relative difference "
        f"{result.relative_difference:.3f} (limit {band.MAX_RELATIVE_DIFFERENCE}), in band "
        f"{result.donor_only_in_band}/{result.tail_in_band}"
    )
