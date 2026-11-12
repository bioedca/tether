# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The measured kinSoftChallenge sets beside the published results of the challenge (#718).

Advisory by construction (ADR-0082): measured data has no ground truth, so no test here fails on
where Tether's rates lie among the published ones. They fail on a wrong copy, a wrong derivation,
a page that no longer shows the file, or a Tether fit that has drifted from its frozen result.

* Required matrix: the published-results file is a complete copy with a source for each number,
  the class exit rate is right on models whose answer is known, the reader reads an archive built
  here, and ``docs/validation.md`` shows the rows this file gives.
* Weekly ``reference-data`` job (``reference_download``, with the tMAVEN sidecar): each set is
  downloaded, fitted, held to the frozen ``tether_measured`` and reported beside the published
  range.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import numpy as np
import pytest

from _kinsoft_measured import (
    CLASSES,
    class_exit_rates,
    fret_classes,
    load_results,
    position,
    published_exit_rates,
    read_measured_set,
    tether_exit_rates,
)
from reference_data import fetch

RESULTS = load_results()
SETS = RESULTS["sets"]
MANIFEST = fetch.load_manifest()
PAGE = Path(__file__).resolve().parents[1] / "docs" / "validation.md"
PAPER_DOI = "doi:10.1038/s41467-022-33023-3"
DATA_DOI = "doi:10.5281/zenodo.5701310"


# --- the published-results file -----------------------------------------------------------------


def test_every_set_names_its_data_and_the_paper_file_its_numbers_come_from() -> None:
    assert RESULTS["source"]["doi"] == PAPER_DOI.removeprefix("doi:")
    assert RESULTS["comparison"]["advisory"] is True
    assert sorted(SETS) == ["fig2-10ms", "fig2-1ms", "fig5-set1", "fig5-set2", "fig5-set3"]
    for key, s in SETS.items():
        data, published = MANIFEST[s["data"]], MANIFEST[s["published"]["data"]]
        assert (data.tier, data.source, data.license) == ("download", DATA_DOI, "CC-BY-4.0"), key
        assert (published.tier, published.source) == ("download", PAPER_DOI), key
        assert published.license == "CC-BY-4.0", key
        assert f"Supplementary Data {s['published']['supplementary_data']} (" in published.citation


def test_every_analysis_is_a_whole_copy_of_one_sheet() -> None:
    # The number of copied sheets, by literal: all submissions for Fig. 2, and for Fig. 5 the
    # two-FRET-state ones the paper's Fig. 5 compares.
    counts = {k: len(s["analyses"]) for k, s in SETS.items()}
    assert counts == {
        "fig2-10ms": 14,
        "fig2-1ms": 10,
        "fig5-set1": 6,
        "fig5-set2": 8,
        "fig5-set3": 6,
    }
    excluded = {(k, a["sheet"]) for k, s in SETS.items() for a in s["analyses"] if "excluded" in a}
    assert excluded == {("fig2-1ms", "7_Correlation_1ms"), ("fig5-set2", "11_STaSI_FS_2")}
    for key, s in SETS.items():
        for a in s["analyses"]:
            assert a["sheet"].split("_", 1)[1].startswith(a["tool"]), a["sheet"]
            if key.startswith("fig5"):
                assert a["sheet"].endswith("_FS_2"), a["sheet"]
            if "excluded" in a:
                continue
            n = len(a["fret_e"])
            for block in ("rates_s_inv", "uncertainty_plus", "uncertainty_minus"):
                if a[block] is not None:
                    assert np.shape(a[block]) == (n, n), (a["sheet"], block)
            assert all(a["rates_s_inv"][i][i] == 0 for i in range(n)), a["sheet"]


def test_two_copied_numbers_are_the_ones_the_paper_shows() -> None:
    # Pomegranate on the 10 ms set (Supplementary Data 6) and MASH-FRET (bootstrap) on Fig. 5
    # set 2, which lists its high-FRET state first (Supplementary Data 11).
    pomegranate = SETS["fig2-10ms"]["analyses"][0]
    assert pomegranate["fret_e"] == [0.268, 0.711]
    assert pomegranate["rates_s_inv"] == [[0.0, 2.91], [3.19, 0.0]]
    (mash,) = [a for a in SETS["fig5-set2"]["analyses"] if a["sheet"].startswith("12_")]
    assert class_exit_rates(mash["fret_e"], mash["rates_s_inv"]) == pytest.approx(
        {"low": 0.66, "high": 0.53}
    )


# --- the derivation -----------------------------------------------------------------------------


def test_a_two_state_model_exits_at_its_own_two_rates() -> None:
    assert class_exit_rates([0.27, 0.71], [[0, 2.5], [3.0, 0]]) == pytest.approx(
        {"low": 2.5, "high": 3.0}
    )
    # The state order of a submission does not matter: classes follow FRET E.
    assert class_exit_rates([0.71, 0.27], [[0, 3.0], [2.5, 0]]) == pytest.approx(
        {"low": 2.5, "high": 3.0}
    )


def test_a_class_of_two_hidden_states_exits_at_its_mean_residence_time() -> None:
    # Low states a and b (no transition between them) each exit to the high state h at k_a and
    # k_b; h enters a at r_a and b at r_b. The low class is entered in the ratio r_a : r_b, so its
    # mean residence is (r_a/k_a + r_b/k_b) / (r_a + r_b); h is left at r_a + r_b.
    k_a, k_b, r_a, r_b = 2.0, 0.25, 0.3, 0.1
    rates = [[0, 0, k_a], [0, 0, k_b], [r_a, r_b, 0]]
    got = class_exit_rates([0.25, 0.25, 0.76], rates)
    assert got["low"] == pytest.approx((r_a + r_b) / (r_a / k_a + r_b / k_b), rel=1e-12)
    assert got["high"] == pytest.approx(r_a + r_b, rel=1e-12)
    # Internal hops inside a class are not exits: a fast a <-> b exchange leaves h unchanged.
    rates[0][1] = rates[1][0] = 50.0
    assert class_exit_rates([0.25, 0.25, 0.76], rates)["high"] == pytest.approx(r_a + r_b)


def test_fret_classes_split_at_the_largest_gap() -> None:
    assert fret_classes([0.24, 0.24, 0.76, 0.76]).tolist() == [0, 0, 1, 1]
    assert fret_classes([0.755, 0.271]).tolist() == [1, 0]
    with pytest.raises(ValueError, match="two or more"):
        fret_classes([0.3, float("nan")])


def test_a_model_whose_class_is_never_entered_is_refused() -> None:
    with pytest.raises(ValueError, match="never entered|no unique stationary"):
        class_exit_rates([0.2, 0.8], [[0, 0], [1.0, 0]])
    with pytest.raises(ValueError, match="2x2 and non-negative"):
        class_exit_rates([0.2, 0.8], [[0, -1.0], [1.0, 0]])


def test_position_counts_the_published_values_below_and_gives_the_range() -> None:
    assert position(2.7, [3.0, 2.5, 2.9, 2.0]) == {
        "n": 4,
        "below": 2,
        "min": 2.0,
        "median": 2.7,
        "max": 3.0,
        "inside_range": True,
    }
    assert position(3.5, [3.0, 2.5])["inside_range"] is False


def test_read_measured_set_skips_macos_copies_and_converts_the_time(tmp_path: Path) -> None:
    archive = tmp_path / "set.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Traces/Readme.txt", "time donor acceptor\n")
        zf.writestr("__MACOSX/Traces/._305.txt", "junk")
        zf.writestr("Traces/305.txt", "13000\t6\t36\n13010\t7\t36\n13020\t14\t26\n")
        zf.writestr("Traces/309.txt", "50\t1\t2\n60\t3\t4\n")
    traces, dt = read_measured_set(archive, "Traces/[0-9]*.txt", 0.001)
    assert dt == 0.01
    assert [t.name for t in traces] == ["Traces/305.txt", "Traces/309.txt"]
    assert traces[0].donor.tolist() == [6, 7, 14] and traces[0].acceptor.tolist() == [36, 36, 26]
    with pytest.raises(ValueError, match="no member"):
        read_measured_set(archive, "expSet1/*.dat", 1.0)
    with zipfile.ZipFile(archive, "a") as zf:
        zf.writestr("Traces/310.txt", "0\t1\t2\n20\t3\t4\n")
    with pytest.raises(ValueError, match="frame times"):
        read_measured_set(archive, "Traces/[0-9]*.txt", 0.001)


# --- the page -----------------------------------------------------------------------------------


def _report_rows() -> list[str]:
    """The rows of the docs table, built from the file: Tether beside the published range."""
    rows = []
    for key, s in SETS.items():
        published = published_exit_rates(s["analyses"]).values()
        for c in CLASSES:
            tether = s["tether_measured"][f"k_{c}"]
            p = position(tether, [v[c] for v in published])
            rows.append(
                f"| {key} | {c} | {tether:.3f} | {p['min']:.3f}–{p['max']:.3f} | "
                f"{p['median']:.3f} | {p['below']} of {p['n']} |"
            )
    return rows


def test_the_page_shows_tether_beside_the_published_range() -> None:
    page = PAGE.read_text(encoding="utf-8")
    table = [ln.strip() for ln in page.splitlines() if re.match(r"\| fig[25]-", ln.strip())]
    assert table == _report_rows()


# --- the weekly job -----------------------------------------------------------------------------


@pytest.mark.reference_download
@pytest.mark.parametrize("key", sorted(SETS))
def test_tether_on_a_measured_set_beside_the_published_results(key: str, tmp_path: Path) -> None:
    if not os.environ.get("TETHER_SIDECAR_PYTHON"):
        pytest.skip("needs the tMAVEN sidecar: set TETHER_SIDECAR_PYTHON")
    s = SETS[key]
    traces, dt = read_measured_set(fetch.path_for(s["data"]), s["members"], s["time_unit_s"])
    assert (len(traces), dt) == (s["n_traces"], s["dt_s"])
    got = tether_exit_rates(traces, dt, tmp_path)

    published = published_exit_rates(s["analyses"])
    print(f"\n{key}: Tether (seed 0) beside the {len(published)} published analyses")
    for c in CLASSES:
        p = position(got[f"k_{c}"], [v[c] for v in published.values()])
        print(
            f"  {c}-FRET exit rate {got[f'k_{c}']:.4f}/s; published {p['min']:.4f} to "
            f"{p['max']:.4f}, median {p['median']:.4f}; {p['below']} of {p['n']} below"
        )
    # The position above is advisory and never fails. What fails is drift of Tether's own fit
    # from its frozen result, measured 2026-11-12 on Linux x86-64 with the sidecar lock.
    frozen = s["tether_measured"]
    for name in ("fret_low", "fret_high", "k_low", "k_high"):
        assert got[name] == pytest.approx(frozen[name], rel=1e-4), name
    assert (got["n_dwells_low"], got["n_dwells_high"]) == (
        frozen["n_dwells_low"],
        frozen["n_dwells_high"],
    )
