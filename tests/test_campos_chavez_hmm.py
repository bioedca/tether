# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The tMAVEN fits of Campos-Chavez et al. 2024 again in Tether's sidecar (#757, ADR-0106).

Nat Commun 15:6592 (2024), doi:10.1038/s41467-024-50885-x. Its Source Data archive is the
``campos2024-source-data`` entry of the reference-data manifest (CC BY-NC-ND 4.0, ADR-0083).
Seven fits are repeated: the consensus HMM of five samples, and the two sub-populations of the
tRNA-ΔNCCA sample (composite HMM with K-means, and vbGMM). The settings of each fit and their
source in the article are in ``reference_data/campos_chavez_2024_hmm.json``.

* Required matrix: the settings file names a source for every setting and the windows are
  well formed; the comparison of ``_campos_chavez_hmm`` on data built here; the limits file
  follows its rounding rule; ``docs/validation.md`` shows the limits.
* Weekly ``reference-data`` job (``reference_download``): each committed window is the published
  histogram values, and, with the tMAVEN sidecar, each fit is run with seed 757 and held to its
  frozen limit. The published values are read from the archive each time and never committed
  (CC BY-NC-ND 4.0, ADR-0083).

An advisory reference: a limit measures how far the pinned tMAVEN is from the published fit, so
a failure means the sidecar fit moved, not that the paper is wrong.
"""

from __future__ import annotations

import functools
import math
import os
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import _campos_chavez_hmm as cc
from reference_data import fetch

SETTINGS = cc.load_settings()
LIMITS = cc.load_limits()
FITS = SETTINGS["fits"]
SEED = 757
PAGE = Path(__file__).resolve().parents[1] / "docs" / "validation.md"
METHODS_PRIORS = {"alpha": 1.0, "beta": 0.25, "pi": 1.0, "a": 2.5, "b": 0.01}
#: The metrics each kind of fit is held to. A global fit has a published count matrix, so it carries
#: all four; a ΔNCCA sub-population has none, so it carries the means and the transitions only.
GLOBAL_METRICS = frozenset(cc.METRICS)
SUBPOPULATION_METRICS = frozenset({"means_max_abs", "tdp_counts_l1_rel"})


def _required_metrics(key: str) -> frozenset[str]:
    return SUBPOPULATION_METRICS if FITS[key].get("kmatrix") is None else GLOBAL_METRICS


def _round_2sig_up(value: float) -> float:
    if value == 0:
        return 0.0
    exp = math.floor(math.log10(abs(value)))
    return float(f"{math.ceil(round(value / 10 ** (exp - 1), 9))}e{exp - 1}")


# --- required matrix: the committed files -------------------------------------------------------


def test_every_setting_of_every_fit_has_a_source() -> None:
    assert sorted(FITS) == sorted(LIMITS["gates"])
    for key, fit in FITS.items():
        assert set(fit["prefs"]) == set(fit["pref_sources"]), key
        assert all(s.strip() for s in fit["pref_sources"].values()), key
        assert fit["nstates_source"].strip(), key


def test_the_global_fits_use_the_priors_of_the_methods() -> None:
    b = {"linker": 0.4, "rag": 0.2}
    for key, fit in FITS.items():
        if fit["model_key"] != "vbconhmm":
            continue
        priors = {
            name.rsplit(".", 1)[1]: value
            for name, value in fit["prefs"].items()
            if ".prior." in name
        }
        assert priors == {**METHODS_PRIORS, "b": b.get(key, 0.01)}, key
        assert fit["pref_sources"]["modeler.vbconhmm.prior.b"].startswith("Methods"), key


def test_each_model_key_is_one_the_child_can_run() -> None:
    import _campos_chavez_hmm_child as child
    from tether.idealize import _sidecar_runner

    # The child adds no key of its own: each fit runs a key of the runner (#1076).
    for fit in FITS.values():
        assert fit["model_key"] in _sidecar_runner._DISPATCH
        assert fit["model_key"] in child.OLD_ROUTINES


def test_the_windows_are_well_formed() -> None:
    for key, fit in FITS.items():
        w = fit["windows"]
        mols = [m for m, *_ in w["windows"]]
        assert len(mols) == len(set(mols)), key  # one window per molecule
        assert all(n >= 2 for _, _, n, _ in w["windows"]), key
        spans = sorted((offset, offset + n) for _, _, n, offset in w["windows"])
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False)), key
        assert sum(n for _, _, n, _ in w["windows"]) + w["unplaced"] == w["values"], key
    # Each window of a sub-population lies inside the window of its molecule in the whole ΔNCCA
    # fit. The two do not split that fit: 26 molecules are in both, 26 in neither (ADR-0106).
    whole = {m: (first, first + n) for m, first, n, _ in FITS["dncca"]["windows"]["windows"]}
    one, two = (
        {m for m, *_ in FITS[k]["windows"]["windows"]}
        for k in ("dncca_one_state", "dncca_two_state")
    )
    for key in ("dncca_one_state", "dncca_two_state"):
        for m, first, n, _ in FITS[key]["windows"]["windows"]:
            assert whole[m][0] <= first and first + n <= whole[m][1], (key, m)
    assert (len(one), len(two), len(one & two), len(set(whole) - one - two)) == (192, 72, 26, 26)


# --- required matrix: the method ----------------------------------------------------------------


def test_pair_counts_match_each_level_to_its_nearest_mean() -> None:
    means = np.array([0.0, 0.5, 0.7])
    counts = cc.pair_counts(
        np.array([0.01, 0.49, 0.71, 0.5]), np.array([0.5, 0.02, 0.5, 0.69]), means
    )
    assert counts.tolist() == [[0, 1, 0], [1, 0, 1], [0, 1, 0]]


def test_find_windows_recovers_windows_of_built_data() -> None:
    rng = np.random.default_rng(757)
    donor = rng.uniform(50, 150, size=(40, 3))
    acceptor = rng.uniform(50, 150, size=(40, 3))
    e, bound = cc.efficiency(donor, acceptor), cc.rounding_bound(donor, acceptor)
    values = np.r_[e[5:20, 2], 9.0, e[0:12, 0]]  # 9.0 is in no frame
    assert cc.find_windows(e, bound, values) == [[2, 5, 15, 0], [0, 0, 12, 16]]


def _model(means, idealized, tmatrix=None, frac=None):
    return SimpleNamespace(
        means=np.asarray(means, float),
        nstates=len(means),
        idealized=np.asarray(idealized, float),
        tmatrix=None if tmatrix is None else np.asarray(tmatrix, float),
        frac=frac,
    )


def test_compare_gives_each_deviation_of_a_known_model() -> None:
    pub = {
        "means": np.array([0.0, 0.5]),
        "tdp_counts": np.array([[0, 2], [2, 0]]),
        "counts": np.array([[90.0, 10.0], [10.0, 90.0]]),
    }
    # The fitted states come out of order; compare sorts them by mean.
    model = _model(
        [0.51, 0.02],
        [[0.51, 0.02, 0.51, 0.51], [0.02, 0.51, np.nan, np.nan]],
        tmatrix=[[85.0, 12.0], [11.0, 92.0]],
    )
    out = cc.compare(model, pub)
    assert out["means_max_abs"] == pytest.approx(0.02)
    # Paths: high->low, low->high (molecule 0); low->high (molecule 1).
    assert out["tdp_counts_l1_rel"] == pytest.approx((1 + 0) / 4)
    assert out["offdiag_counts_l1_rel"] == pytest.approx((1 + 2) / 20)
    assert out["populations_max_abs"] == pytest.approx(abs(103 / 200 - 0.5))
    assert cc.compare(_model([0.0], [[0.0, 0.0]]), pub)["state_count_differs"] is True


def test_a_stored_trace_matches_its_column_through_an_offset_and_a_dropped_frame() -> None:
    # The stored files of the workstation leg (step 4): a tMAVEN trace is the published column
    # before background subtraction, and one matrix of the archive has one frame fewer.
    rng = np.random.default_rng(0)
    donor = rng.normal(100.0, 10.0, (1499, 6))
    raw = np.zeros((4, 1500, 2))
    raw[:, 1:, 0] = 2.0 * donor[:, [1, 3, 5, 0]].T + 40.0
    assert cc.match_share(raw, donor) == (1.0, pytest.approx(1.0))
    other = rng.normal(100.0, 10.0, (1500, 6))
    share, median = cc.match_share(raw, other)
    assert share == 0.0 and median < 0.5
    assert cc.match_share(raw, donor[:1400]) == (0.0, 0.0)


def test_compare_matches_the_states_one_to_one() -> None:
    # Two fitted states lie nearest the same published level (as for the Linker fit): their
    # transitions must count against the published ones by rank, not fall onto the diagonal.
    pub = {
        "means": np.array([0.0, 0.5]),
        "tdp_counts": np.array([[0, 3], [3, 0]]),
        "counts": np.array([[90.0, 10.0], [10.0, 90.0]]),
    }
    out = cc.compare(_model([-0.1, 0.1], [[-0.1, 0.1, -0.1, 0.1]]), pub)
    # Paths: low->high, high->low, low->high = [[0, 2], [1, 0]] against [[0, 3], [3, 0]].
    assert out["tdp_counts_l1_rel"] == pytest.approx((1 + 2) / 6)


def test_parse_summary_reads_a_tmaven_report() -> None:
    text = (
        "\nModel type = vb Consensus HMM\nModel ran on 3 traces\nnstates = 2\n"
        "means = [0.01 0.52]\nvars = [0.1 0.1]\nfracs = [0.7 0.3]\n"
        "tmatrix = \n[[90.5  2. ]\n [ 3.  40. ]]\ntmatrix normalized = \n[[0.9 0.1]\n [0.1 0.9]]\n"
    )
    got = cc.parse_summary(text)
    assert (got["type"], got["traces"]) == ("vb Consensus HMM", 3)
    assert got["means"].tolist() == [0.01, 0.52]
    assert got["counts"].tolist() == [[90.5, 2.0], [3.0, 40.0]]
    assert cc.parse_summary("no model here") is None


# --- required matrix: the limits and the page ---------------------------------------------------


def _gate_set_problems(gates_by_key: dict) -> list[str]:
    return [key for key, gates in gates_by_key.items() if set(gates) != _required_metrics(key)]


def test_each_fit_carries_the_metric_set_of_its_kind() -> None:
    assert _gate_set_problems(LIMITS["gates"]) == []
    assert {k for k in FITS if _required_metrics(k) == SUBPOPULATION_METRICS} == {
        "dncca_one_state",
        "dncca_two_state",
    }


def test_a_metric_taken_out_of_the_limits_file_is_caught() -> None:
    for key, gates in LIMITS["gates"].items():
        for name in gates:
            trimmed = {**LIMITS["gates"], key: {m: e for m, e in gates.items() if m != name}}
            assert _gate_set_problems(trimmed) == [key], (key, name)


def test_every_limit_is_its_measurement_rounded_up() -> None:
    for key, gates in LIMITS["gates"].items():
        for name, entry in gates.items():
            assert entry["limit"] == _round_2sig_up(entry["measured"]), (key, name)


def test_the_rounding_rule() -> None:
    assert _round_2sig_up(0.0016891) == 0.0017
    assert _round_2sig_up(0.12) == 0.12
    assert _round_2sig_up(0.0) == 0.0


def test_the_validation_page_shows_every_limit() -> None:
    text = PAGE.read_text(encoding="utf-8")
    section = text.split("\n## Campos-Chavez et al. 2024: the tMAVEN fits\n", 1)[1].split(
        "\n## ", 1
    )[0]
    for key, gates in LIMITS["gates"].items():
        row = next(line for line in section.splitlines() if line.startswith(f"| `{key}` |"))
        for name in cc.METRICS:
            cell = f"{gates[name]['limit']:g}" if name in gates else "—"
            assert cell in [c.strip() for c in row.split("|")], (key, name)


# --- weekly: the archive ------------------------------------------------------------------------


@functools.cache
def _archive() -> Path:
    return fetch.path_for(SETTINGS["source_data"])


@functools.cache
def _book():
    return cc.read_book(_archive())


@pytest.mark.reference_download
@pytest.mark.parametrize("key", sorted(FITS))
def test_each_window_is_the_published_histogram_values(key: str) -> None:
    got = cc.check_windows(FITS[key], _book(), _archive())
    assert got["values"] == FITS[key]["windows"]["values"]
    assert got["max_bound_ratio"] <= 1.0


@pytest.mark.reference_download
@pytest.mark.parametrize("key", sorted(FITS))
def test_the_sidecar_fit_is_within_its_frozen_limits(key: str, tmp_path: Path) -> None:
    if not os.environ.get("TETHER_SIDECAR_PYTHON"):
        pytest.skip("needs the tMAVEN sidecar: set TETHER_SIDECAR_PYTHON")
    fit = FITS[key]
    smd = cc.write_fit_smd(fit, _archive(), tmp_path / f"{key}.smd.hdf5")
    got = cc.compare(cc.run_fit(fit, smd, SEED), cc.published(fit, _book(), _archive()))
    print(key, {m: got.get(m) for m in cc.METRICS}, "means", got["means"])
    assert not got.get("state_count_differs"), got
    for name in sorted(_required_metrics(key)):
        limit = LIMITS["gates"][key][name]["limit"]
        assert got[name] <= limit, (name, got[name], limit)


@pytest.mark.reference_download
def test_the_vbgmm_vbhmm_key_on_the_two_state_population_is_reported(tmp_path: Path) -> None:
    # The issue named Tether's ``vbgmm_vbhmm`` key; the Methods name vbGMM, which is gated above.
    # This key is reported, never gated (maintainer's choice, ADR-0106).
    if not os.environ.get("TETHER_SIDECAR_PYTHON"):
        pytest.skip("needs the tMAVEN sidecar: set TETHER_SIDECAR_PYTHON")
    fit = FITS["dncca_two_state"]
    smd = cc.write_fit_smd(fit, _archive(), tmp_path / "two.smd.hdf5")
    model = cc.run_fit(fit, smd, SEED, model_key="vbgmm_vbhmm")
    print("vbgmm_vbhmm", int(model.nstates), "states, means", np.sort(model.means).tolist())
    assert re.search("GMM", model.model_type)
