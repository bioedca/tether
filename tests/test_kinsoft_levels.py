# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""kinSoftChallenge levels 2 and 3, with the truth read from the simulator settings (#717).

Oracle (c) band-checks only level 1. This module adds the two harder simulated levels, both
**advisory**: their measured errors are printed and pinned against drift, never held to a band
(ADR-0081).

* **The truth is read, not typed.** ``tests/_kinsoft_config.py`` parses the three committed
  ``Config_simulation_round_1_level_N.cfg`` files, and the required-matrix tests here require
  every ground-truth value of ``schema/kinsoft_reference.json`` to equal what the parser reads.
  Level 1's file gives level 1's reference unchanged.
* **Level 2** (three states, not at equilibrium): a 3-state fit and the six rates
  ``k_ij = n_ij / T_i``, each compared with the truth.
* **Level 3** (four states, two pairs share a FRET value): a 2-state fit, and the dwell-time
  distribution of each observable FRET level compared with the one the true 4-state matrix
  predicts for an aggregated Markov process [Colquhoun & Hawkes 1981, doi:10.1098/rspb.1981.0003;
  1982, doi:10.1098/rstb.1982.0156], as the benchmark compares this level [Götz2022,
  doi:10.1038/s41467-022-33023-3].

The two fits run on the LFS ``kinsoft_sim.hdf5`` in the ``large`` tier beside level 1, and skip
on an unmaterialized pointer.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from _kinsoft_config import (
    config_path,
    dwell_cdf,
    entry_distribution,
    ks_distance,
    mean_dwell,
    pooled_rate_matrix,
    read_simulator_config,
    stationary_distribution,
)
from reference_data import fetch
from tether.analysis.dwell import state_dwells
from tether.analysis.kinetics import (
    fit_gaussian_hmm,
    load_kinsoft_reference,
    pooled_exit_rates,
    viterbi_paths,
)

REPO = Path(__file__).resolve().parents[1]
REFERENCE = REPO / "schema" / "kinsoft_reference.json"
FIXTURE = REPO / "tests" / "fixtures" / "large" / "kinsoft_sim.hdf5"

#: The level-2 rate names of the reference file, by (from, to) in the file's 1-based states.
LEVEL2_RATES = {
    (1, 2): "k12_low_mid",
    (1, 3): "k13_low_high",
    (2, 1): "k21_mid_low",
    (2, 3): "k23_mid_high",
    (3, 1): "k31_high_low",
    (3, 2): "k32_high_mid",
}
_FRET_IN_LABEL = re.compile(r"\((\d\.\d+)\)")


def _reference() -> dict:
    return load_kinsoft_reference(REFERENCE).levels


def _is_lfs_pointer(path: Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(64).startswith(b"version https://git-lfs")


def _level(name: str):
    if _is_lfs_pointer(FIXTURE):
        pytest.skip("LFS large-tier fixture not materialized (default checkout)")
    pytest.importorskip("h5py")
    from tether.io.kinsoft import read_kinsoft_fixture

    return read_kinsoft_fixture(FIXTURE)[name]


def _assert_level_shape(record: dict, cfg) -> None:
    """The level's state count, frame time, frame rate and trace count are the file's."""
    assert record["nstates"] == cfg.nstates
    assert record["dt_s"] == pytest.approx(cfg.frame_time_s, rel=1e-12)
    assert record["sampling_rate_hz"] == cfg.frame_rate_hz
    assert record["n_traces"] == cfg.n_traces


def _assert_source(record: dict, level: int, cfg) -> None:
    source = record["ground_truth"]["source"]
    assert source["file"] == config_path(level).relative_to(REPO).as_posix()
    assert source["block"] == "%transition rate matrix"
    assert tuple(source["lines"]) == cfg.lines["transition rate matrix"]


# --- the truth of every level is the settings file ----------------------------------------


def test_level1_settings_give_the_level1_reference() -> None:
    # The level-1 reference was transcribed from the paper; the parser must read the same
    # two rates from the simulator's own file, and the reference itself does not change.
    cfg = read_simulator_config(config_path(1))
    record = _reference()["level1"]
    _assert_level_shape(record, cfg)
    fret = cfg.degenerate_fret()
    assert fret[0] < fret[1], "state 1 is the low-FRET state of the reference"
    assert cfg.rate_matrix[0, 1] == record["ground_truth"]["rates_s_inv"]["k12_low_high"]
    assert cfg.rate_matrix[1, 0] == record["ground_truth"]["rates_s_inv"]["k21_high_low"]
    assert record["ground_truth"]["rates_s_inv"] == {"k12_low_high": 0.15, "k21_high_low": 0.22}


def test_level2_reference_is_the_settings_file() -> None:
    cfg = read_simulator_config(config_path(2))
    record = _reference()["level2"]
    _assert_level_shape(record, cfg)
    _assert_source(record, 2, cfg)
    q = cfg.rate_matrix
    expected = {name: float(q[i - 1, j - 1]) for (i, j), name in LEVEL2_RATES.items()}
    assert record["ground_truth"]["rates_s_inv"] == expected
    assert cfg.collapse == (1, 2, 3)
    # Each state's label carries its FRET value, Ida/(Idd+Ida) of the file's means.
    fret = cfg.degenerate_fret()
    assert np.all(np.diff(fret) > 0), "the file's states are in ascending FRET order"
    for state, label in record["ground_truth"]["state_assignment"].items():
        (value,) = _FRET_IN_LABEL.findall(label)
        assert float(value) == round(float(fret[int(state) - 1]), 2), state


def test_level3_reference_is_the_settings_file_renumbered() -> None:
    cfg = read_simulator_config(config_path(3))
    record = _reference()["level3"]
    truth = record["ground_truth"]
    _assert_level_shape(record, cfg)
    _assert_source(record, 3, cfg)
    assert tuple(truth["source"]["collapse_lines"]) == cfg.lines["lookupCollapse"]
    assert tuple(truth["source"]["donor_blinking_lines"]) == cfg.lines["donor blinking kinetics"]
    assert (
        tuple(truth["source"]["acceptor_blinking_lines"]) == cfg.lines["acceptor blinking kinetics"]
    )
    row = {int(k): v - 1 for k, v in truth["settings_state_of"].items()}
    assert sorted(row.values()) == list(range(cfg.nstates)), "a renumbering, not a selection"
    q = cfg.rate_matrix
    listed = truth["rates_s_inv"]
    for i in range(1, cfg.nstates + 1):
        for j in range(1, cfg.nstates + 1):
            if i == j:
                continue
            rate = float(q[row[i], row[j]])
            if f"k{i}{j}" in listed:
                assert listed[f"k{i}{j}"] == rate, f"k{i}{j}"
            else:
                assert rate == 0.0, f"k{i}{j} is {rate} in the file but not listed"
    # The label of each state is the FRET value of its degenerate state in the file.
    fret = cfg.degenerate_fret()
    for state, label in truth["state_assignment"].items():
        (value,) = _FRET_IN_LABEL.findall(label)
        degenerate = cfg.collapse[row[int(state)]]
        assert float(value) == round(float(fret[degenerate - 1]), 2), state
    # Blinking: the note's kbright is the dark-to-bright rate of both dyes, kdark the reverse.
    assert cfg.include_blinking
    note = truth["note"]
    kbright = float(re.search(r"kbright=([0-9.]+)", note).group(1))
    kdark = float(re.search(r"kdark=([0-9.]+)", note).group(1))
    for blink in (cfg.donor_blinking, cfg.acceptor_blinking):
        assert blink[1, 0] == kbright
        assert blink[0, 1] == kdark


def test_levels_2_and_3_are_reported_and_level1_alone_is_band_checked() -> None:
    levels = _reference()
    assert load_kinsoft_reference(REFERENCE).active_levels() == ["level1"]
    for name in ("level2", "level3"):
        assert levels[name]["oracle"] == "report"
        assert "deferred_reason" not in levels[name]
        assert "ADR-0081" in levels[name]["report_reason"]


def test_the_manifest_resolves_the_settings_files() -> None:
    for level in (1, 2, 3):
        assert fetch.path_for(f"kinsoft-settings-level{level}") == config_path(level)
    manifest = fetch.load_manifest()
    assert manifest["kinsoft-simulator"].tier == "download"
    for level in (1, 2, 3):
        assert manifest[f"kinsoft-settings-level{level}"].extract_of == ("kinsoft-simulator",)


# --- the parser refuses what the simulator would refuse -----------------------------------


def _edited_config(tmp_path: Path, old: str, new: str) -> Path:
    text = config_path(2).read_text(encoding="ascii")
    assert old in text
    path = tmp_path / "edited.cfg"
    path.write_text(text.replace(old, new, 1), encoding="ascii")
    return path


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("+0.450\t+0.100\t-0.550", "+0.450\t+0.100\t-0.500", "does not sum to zero"),
        ("+0.450\t+0.100\t-0.550\n", "", "not square"),
        ("1\t2\t3", "1\t2", "lookupCollapse must hold 3"),
        ("%frameRate", "%frame rate", "no '%frameRate' block"),
        ("%number of traces\n150", "%number of traces\n\t%none", "has no numbers"),
        ("-0.680\t+0.300\t+0.380", "-0.680\t+0.300", "rows of different lengths"),
    ],
)
def test_the_parser_refuses_a_broken_file(tmp_path: Path, old: str, new: str, message) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        read_simulator_config(_edited_config(tmp_path, old, new))


def test_the_parser_records_the_lines_of_each_block() -> None:
    cfg = read_simulator_config(config_path(2))
    lines = config_path(2).read_text(encoding="ascii").splitlines()
    first, last = cfg.lines["transition rate matrix"]
    assert lines[first - 2] == "%transition rate matrix"
    rows = [[float(v) for v in line.split()] for line in lines[first - 1 : last]]
    assert np.array_equal(np.array(rows), cfg.rate_matrix)
    assert cfg.frame_rate_hz == 10.0
    assert cfg.n_traces == 150


# --- the dwell-time prediction of an aggregated Markov process ----------------------------


def test_a_class_of_one_state_has_an_exponential_dwell() -> None:
    q = np.array([[-0.3, 0.2, 0.1], [0.5, -0.5, 0.0], [0.4, 0.4, -0.8]])
    t = np.array([0.0, 0.5, 2.0, 10.0])
    members = np.array([0])
    assert dwell_cdf(q, members, t) == pytest.approx(1.0 - np.exp(-0.3 * t), abs=1e-12)
    assert mean_dwell(q, members) == pytest.approx(1 / 0.3)


def test_a_two_state_class_matches_its_closed_form() -> None:
    # Observable class {0, 1}, entered only into state 0 (from state 2); 0 <-> 1 inside it
    # at rates a and b, and the exit only from 1, at rate c. Solving (-Q_AA) x = 1 gives the
    # mean time to leave from state 0: (a + b + c) / (a c).
    a, b, c = 0.7, 0.2, 0.4
    q = np.array([[-a, a, 0.0], [b, -(b + c), c], [1.0, 0.0, -1.0]])
    members = np.array([0, 1])
    assert entry_distribution(q, members) == pytest.approx([1.0, 0.0])
    assert mean_dwell(q, members) == pytest.approx((a + b + c) / (a * c))
    # The survival of the class is a sum of two exponentials whose rates are the
    # eigenvalues of -Q_AA; its slope at 0 is 0 (an entry into state 0 cannot leave at once).
    t = np.linspace(0.0, 30.0, 3001)
    survival = 1.0 - dwell_cdf(q, members, t)
    assert survival[0] == pytest.approx(1.0)
    assert (survival[1] - survival[0]) / (t[1] - t[0]) == pytest.approx(0.0, abs=1e-2)
    assert np.trapezoid(survival, t) == pytest.approx(mean_dwell(q, members), rel=1e-3)


def test_the_stationary_distribution_balances_the_flows() -> None:
    q = read_simulator_config(config_path(3)).rate_matrix
    pi = stationary_distribution(q)
    assert pi.sum() == pytest.approx(1.0)
    assert pi @ q == pytest.approx(np.zeros(4), abs=1e-12)
    assert np.all(pi > 0)


def test_the_ks_distance_reads_both_sides_of_each_step() -> None:
    def uniform(x: np.ndarray) -> np.ndarray:
        return np.clip(x, 0.0, 1.0)

    # Four values at the quartile midpoints sit 1/8 from the uniform CDF on each side.
    assert ks_distance(np.array([0.125, 0.375, 0.625, 0.875]), uniform) == pytest.approx(0.125)
    # Ties step once: two samples at 0.5 put the empirical CDF at 0 below and 1 at 0.5.
    assert ks_distance(np.array([0.5, 0.5]), uniform) == pytest.approx(0.5)


# --- the measurement of the level-2 leg ---------------------------------------------------


def test_pooled_rate_matrix_counts_interior_dwells_only() -> None:
    # Runs: 0x2 | 1x3 | 2x1 | 0x4 | 1x1. Counted: 1x3 -> 2, 2x1 -> 0, 0x4 -> 1.
    path = np.array([0, 0, 1, 1, 1, 2, 0, 0, 0, 0, 1])
    rates, counts = pooled_rate_matrix([path], 0.5, 3)
    assert counts.tolist() == [[0, 1, 0], [0, 0, 1], [1, 0, 0]]
    assert rates[0, 1] == pytest.approx(1 / (4 * 0.5))
    assert rates[1, 2] == pytest.approx(1 / (3 * 0.5))
    assert rates[2, 0] == pytest.approx(1 / (1 * 0.5))
    assert np.diag(rates).tolist() == [0.0, 0.0, 0.0]


def test_pooled_rate_matrix_rows_sum_to_the_pooled_exit_rates() -> None:
    rng = np.random.default_rng(717)
    paths = [np.repeat(rng.integers(0, 3, 30), rng.integers(1, 6, 30)) for _ in range(40)]
    rates, _ = pooled_rate_matrix(paths, 0.1, 3)
    exits = pooled_exit_rates(paths, 0.1)
    assert rates.sum(axis=1) == pytest.approx([exits[s] for s in range(3)], rel=1e-12)


def test_pooled_rate_matrix_marks_an_unseen_state_and_refuses_a_foreign_one() -> None:
    rates, counts = pooled_rate_matrix([np.array([0, 1, 1, 0])], 1.0, 3)
    assert np.isnan(rates[2]).all() and counts[2].sum() == 0
    with pytest.raises(ValueError, match="outside 0..1"):
        pooled_rate_matrix([np.array([0, 2])], 1.0, 2)


# --- data-present, gated: the two advisory legs --------------------------------------------


def _fret_traces(level) -> list[np.ndarray]:
    return [level.trace(i).fret_e for i in range(level.n_traces)]


@pytest.mark.large
def test_kinsoft_level2_six_rates_against_the_settings_truth() -> None:
    cfg = read_simulator_config(config_path(2))
    level = _level("level2")
    assert level.frame_time_s == pytest.approx(cfg.frame_time_s)
    traces = _fret_traces(level)
    hmm = fit_gaussian_hmm(traces, cfg.nstates)
    # The three FRET levels are found: within 0.01 of the file's.
    assert hmm.means == pytest.approx(cfg.degenerate_fret(), abs=0.01)
    rates, counts = pooled_rate_matrix(viterbi_paths(traces, hmm), level.frame_time_s, 3)

    measured = _reference()["level2"]["tether_measured"]
    print("\nkinSoft level 2 (advisory): rate, truth, Tether, relative error, transitions")
    for (i, j), name in LEVEL2_RATES.items():
        truth = cfg.rate_matrix[i - 1, j - 1]
        rel = abs(rates[i - 1, j - 1] - truth) / truth
        print(
            f"  {name:13s} {truth:.3f} {rates[i - 1, j - 1]:.4f} {rel:7.3f} {counts[i - 1, j - 1]}"
        )
        # Advisory: no band. The frozen values catch drift of the method.
        assert round(float(rates[i - 1, j - 1]), 4) == measured["rates_s_inv"][name], name
        assert round(float(rel), 3) == measured["rel_dev"][name], name
        assert int(counts[i - 1, j - 1]) == measured["n_transitions"][name], name
    # The faster direction of each pair is the file's; the bias does not reverse a pair.
    for i, j in ((1, 2), (2, 3), (3, 1)):
        assert np.sign(rates[i - 1, j - 1] - rates[j - 1, i - 1]) == np.sign(
            cfg.rate_matrix[i - 1, j - 1] - cfg.rate_matrix[j - 1, i - 1]
        )


@pytest.mark.large
def test_kinsoft_level3_dwell_distributions_against_the_settings_truth() -> None:
    cfg = read_simulator_config(config_path(3))
    level = _level("level3")
    assert level.frame_time_s == pytest.approx(cfg.frame_time_s)
    traces = _fret_traces(level)
    hmm = fit_gaussian_hmm(traces, 2)
    fret = cfg.degenerate_fret()
    assert hmm.means == pytest.approx(np.sort(fret), abs=0.01)
    dwells = state_dwells(viterbi_paths(traces, hmm), no_state=-1)

    measured = _reference()["level3"]["tether_measured"]
    print("\nkinSoft level 3 (advisory): level, dwells, mean, predicted mean, error, KS")
    # Fitted state 0 is the lower FRET level; the file's degenerate states in that order.
    for state, (key, degenerate) in enumerate(
        zip(("low_fret", "high_fret"), np.argsort(fret) + 1, strict=True)
    ):
        members = cfg.members(int(degenerate))
        observed = dwells.lengths[state] * level.frame_time_s
        predicted = mean_dwell(cfg.rate_matrix, members)
        rel = abs(observed.mean() - predicted) / predicted
        ks = ks_distance(observed, lambda t, m=members: dwell_cdf(cfg.rate_matrix, m, t))
        print(
            f"  {key:9s} {observed.size:4d} {observed.mean():.4f} s {predicted:.4f} s "
            f"{rel:.3f} {ks:.4f}"
        )
        frozen = measured[key]
        assert observed.size == frozen["n_dwells"], key
        assert round(float(observed.mean()), 4) == frozen["mean_dwell_s"], key
        assert round(predicted, 4) == frozen["predicted_mean_dwell_s"], key
        assert round(float(rel), 3) == frozen["mean_rel_dev"], key
        assert round(ks, 4) == frozen["ks_distance"], key
