# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""TEMPORARY (#739): report the measured agreement behind each new limit on each CI system.

This file is removed before review. It asserts nothing; it emits one warning whose text
is a JSON object of worst-case relative (or absolute) errors, so the CI log of each
operating system shows the agreement that the new limits must be >= 10 times.
"""

from __future__ import annotations

import json
import math
import platform
import warnings

import numpy as np
import pytest

from tether.analysis import (
    apparent_e_histogram,
    bootstrap_histogram_ci,
    fit_survival,
    population_dwell_times,
    transition_density,
)
from tether.analysis.dwell import triple_exp_survival


def _rel(got, want) -> float:
    got = np.asarray(got, dtype=float)
    want = np.asarray(want, dtype=float)
    return float(np.max(np.abs(got - want) / np.abs(want)))


def test_report_agreement(tmp_path) -> None:
    from scipy import stats

    from test_analysis_dwell import _chained_store

    m: dict[str, float] = {}
    tau = np.arange(40, dtype=float)
    f = fit_survival(tau, 0.6 * np.exp(-0.3 * tau), model="single")
    m["single_rate"] = _rel(f.rates, [0.3])
    m["single_amp"] = _rel(f.amplitudes, [0.6])

    tau = np.arange(60, dtype=float)
    f = fit_survival(tau, 0.5 * np.exp(-0.05 * tau) + 0.5 * np.exp(-0.8 * tau), model="double")
    m["double_rate"] = _rel(f.rates, [0.05, 0.8])
    m["double_amp"] = _rel(f.amplitudes, [0.5, 0.5])

    tt = np.array([0.0, 1.0, 5.0, 20.0])
    want = [
        0.5 * math.exp(-0.02 * t) + 0.3 * math.exp(-0.2 * t) + 0.2 * math.exp(-2.0 * t) for t in tt
    ]
    m["triple_values"] = _rel(triple_exp_survival(tt, 0.02, 0.2, 2.0, 0.5, 0.3, 0.2), want)

    tau = np.arange(200, dtype=float)
    surv = 0.5 * np.exp(-0.02 * tau) + 0.3 * np.exp(-0.2 * tau) + 0.2 * np.exp(-2.0 * tau)
    f = fit_survival(tau, surv, model="triple")
    m["triple_rate"] = _rel(f.rates, [0.02, 0.2, 2.0])
    m["triple_amp"] = _rel(f.amplitudes, [0.5, 0.3, 0.2])
    m["triple_curve"] = _rel(f.model_survival, surv)

    tau = np.arange(50, dtype=float)
    f = fit_survival(tau, np.exp(-((0.1 * tau) ** 0.7)), model="stretched")
    m["stretched_beta"] = _rel(f.beta, 0.7)
    m["stretched_rate"] = _rel(f.rates, [0.1])
    m["stretched_amp"] = _rel(f.amplitudes, [1.0])
    f = fit_survival(tau, np.exp(-((0.1 * tau) ** 1.5)), model="stretched")
    m["stretched_bound_abs"] = abs(float(f.beta) - 1.0)
    m["stretched_bound_beta_le_1"] = float(f.beta <= 1.0)

    proj, _s = _chained_store(tmp_path, seed=99, n_runs=600, name="m.tether")
    one = population_dwell_times(proj, "vbconhmm", model="single")
    dt = population_dwell_times(proj, "vbconhmm", model="single", dt=0.1)
    c68 = population_dwell_times(proj, "vbconhmm", model="single", ci_level=0.68)
    m["dt_rate"] = max(_rel(dt[s].fit.rates, one[s].fit.rates / 0.1) for s in one)
    m["dt_amp"] = max(_rel(dt[s].fit.amplitudes, one[s].fit.amplitudes) for s in one)
    m["dt_tau"] = max(_rel(dt[s].fit.tau[1:], one[s].fit.tau[1:] * 0.1) for s in one)
    worst_ci = 0.0
    for s in c68:
        t68 = float(stats.t.ppf(0.84, c68[s].fit.n_points - 2))
        worst_ci = max(
            worst_ci,
            _rel(c68[s].fit.rate_ci, c68[s].fit.rate_stderr * t68),
            _rel(c68[s].fit.amplitude_ci, c68[s].fit.amplitude_stderr * t68),
            _rel(c68[s].fit.rate_stderr, one[s].fit.rate_stderr),
        )
    m["ci_level"] = worst_ci

    h = transition_density(
        [np.array([0.2, 0.8])], nskip=1, signal_bins=4, signal_range=(0.0, 1.0), density=True
    )
    m["tdp_density_cell"] = _rel(h.counts[0, 3], 16.0)
    h = apparent_e_histogram(np.array([0.55]), bins=10, value_range=(0.0, 1.0), density=True)
    m["hist_one_frame"] = _rel(h.counts[5], 10.0)
    h = apparent_e_histogram(
        np.array([0.15, 0.15, 0.55, 0.85]),
        bins=10,
        value_range=(0.0, 1.0),
        density=True,
        weights=np.full(4, 0.25),
    )
    m["hist_equal_weight"] = _rel(h.counts[[1, 5, 8]], [5.0, 2.5, 2.5])
    ci = bootstrap_histogram_ci(
        [np.array([0.55])], bins=10, value_range=(0.0, 1.0), density=True, n_resamples=20
    )
    m["boot_one_frame"] = max(_rel(ci.ci_low[5], 10.0), _rel(ci.ci_high[5], 10.0))
    m["boot_one_frame_std_abs"] = float(np.max(np.abs(ci.std)))
    ci = bootstrap_histogram_ci(
        [np.full(5, 0.3), np.full(5, 0.7)],
        bins=10,
        value_range=(0.0, 1.0),
        density=False,
        n_resamples=2,
    )
    width = ci.ci_high - ci.ci_low
    nz = width > 0
    m["boot_two_resamples"] = _rel(ci.std[nz], width[nz] / (0.95 * np.sqrt(2.0)))
    m["boot_two_resamples_zero_bins_std_abs"] = float(np.max(np.abs(ci.std[~nz])))

    report = {"system": platform.system(), "machine": platform.machine(), "agreement": m}
    warnings.warn("ISSUE739-AGREEMENT " + json.dumps(report, sort_keys=True), stacklevel=1)
    assert pytest is not None
