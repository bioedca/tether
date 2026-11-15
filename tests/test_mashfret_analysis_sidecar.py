# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live: Tether's idealization against MASH-FRET's states, populations and rates (#712, ADR-0101).

MASH-FRET idealized every trace of the record with its own per-trace vbFRET (1 to 5 states,
``prmTT{m}{4}``), and the committed Path A extract keeps those state sequences (``FRET_DTA``), the
Gaussian fit of the tag's FRET histogram and the tag's dwell table
(``tests/test_mashfret_analysis.py`` holds the exact histogram check). Here Tether's own
idealization -- :func:`tether.idealize.run_vbfret` with ``vbconhmm_modelselection`` and a fixed
seed -- runs on the same traces. The SMD carries ``γ·I_D`` and ``I_A`` (MASH-FRET's ``denoise``
intensities at the donor's laser, included frames), so the FRET that tMAVEN computes from it is
MASH-FRET's γ-corrected FRET. A different algorithm cannot agree exactly, so the comparison is
measured:

* **frame agreement** -- for each molecule, each frame's Tether level is matched to the nearest
  of that molecule's MASH-FRET state values; the fraction of frames whose match is MASH-FRET's
  state. A **gate**: at least its frozen limit.
* **number of states** -- the mean over the traces of ``|Tether's levels - MASH-FRET's states|``
  in the trace. A **gate**: at most its frozen limit.
* **populations** -- Tether's state fractions against the populations of MASH-FRET's Gaussian
  fit, each Gaussian matched to the nearest Tether state. A report: the two are different models.
* **rates** -- Tether's exit rate of each state
  (:func:`~tether.analysis.kinetics.pooled_exit_rates`) against ``1 / mean`` of the dwell times
  that MASH-FRET's dwell table holds for the matched transition cluster. A report: the record
  stores no fitted MASH-FRET rate, so this compares with MASH-FRET's dwell times, not with a
  MASH-FRET fit.

The limits and their measurements are in ``schema/mashfret_analysis_limits.json``; set
``TETHER_MASHFRET_REPORT`` to a path to write the whole table as JSON.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from _mashfret import TagAnalysis, read_tag_analysis
from _sidecar_gate import SIDECAR as _SIDECAR
from _sidecar_gate import requires_sidecar
from reference_data.fetch import path_for

pytestmark = pytest.mark.sidecar

LIMITS = Path(__file__).resolve().parents[1] / "schema" / "mashfret_analysis_limits.json"
PATH_A = "mashfret-ed135e-30mm-path-a"
PATH_A_TAG = 12
SEED = 712
REPORT_ENV = "TETHER_MASHFRET_REPORT"


def _idealize(analysis: TagAnalysis, tmp: Path):
    from tether.idealize import write_smd
    from tether.idealize.driver import run_vbfret

    lengths = [d.size for d in analysis.donor]
    raw = np.zeros((len(lengths), max(lengths), 2))
    for i, (d, a, g) in enumerate(
        zip(analysis.donor, analysis.acceptor, analysis.gamma, strict=True)
    ):
        raw[i, : lengths[i], 0] = g * d  # tMAVEN's A / (A + D) is then MASH-FRET's FRET
        raw[i, : lengths[i], 1] = a
    smd = write_smd(
        tmp / "path_a.hdf5",
        raw,
        pre_list=np.zeros(len(lengths), dtype=np.int64),
        post_list=np.asarray(lengths, dtype=np.int64),
    )
    return run_vbfret(smd, sidecar_python=_SIDECAR, model_type="vbconhmm_modelselection", seed=SEED)


def measure(analysis: TagAnalysis, result) -> dict:
    """The four comparisons of the module docstring, for one tag."""
    from tether.analysis.kinetics import pooled_exit_rates

    model = result.model
    agree = frames = 0
    count_diff = []
    for i, stored in enumerate(analysis.states):
        fitted = model.idealized[i, : stored.size]
        assert np.all(np.isfinite(fitted)), f"molecule {i}: the fit left a frame without a level"
        values = np.unique(stored)
        nearest = values[np.argmin(np.abs(fitted[:, None] - values[None, :]), axis=1)]
        agree += int(np.count_nonzero(nearest == stored))
        frames += stored.size
        count_diff.append(np.unique(fitted).size - values.size)
    count_diff = np.asarray(count_diff)

    means = np.asarray(model.means, dtype=np.float64)
    populations = []
    if analysis.gaussians is not None:
        for mu, pop, pop_sd in analysis.gaussians[:, [2, 6, 7]]:
            k = int(np.argmin(np.abs(means - mu)))
            populations.append(
                {
                    "mashfret_mean": float(mu),
                    "mashfret_population": float(pop),
                    "mashfret_population_sd": float(pop_sd),
                    "tether_state": k,
                    "tether_mean": float(means[k]),
                    "tether_fraction": float(model.frac[k]),
                }
            )

    paths = [result.state_paths[i] for i in sorted(result.state_paths)]
    tether_rates = pooled_exit_rates(
        [p[p >= 0] for p in paths], analysis.frame_time, include_first=True
    )
    rates = []
    if analysis.dwells is not None:
        assigned = analysis.dwells[analysis.dwells[:, 6] > 0]
        for j1 in np.unique(assigned[:, 6]):
            rows = assigned[assigned[:, 6] == j1]
            value = float(np.median(rows[:, 1]))  # the state the dwells are in
            k = int(np.argmin(np.abs(means - value)))
            mash = 1.0 / float(rows[:, 0].mean())
            tether = float(tether_rates.get(k, np.nan))
            rates.append(
                {
                    "mashfret_cluster_state": int(j1),
                    "mashfret_value": value,
                    "mashfret_dwells": int(rows.shape[0]),
                    "mashfret_inverse_mean_dwell_per_s": mash,
                    "tether_state": k,
                    "tether_exit_rate_per_s": tether,
                    "ratio": tether / mash,
                }
            )
    return {
        "molecules": len(analysis.states),
        "frames": frames,
        "tether_nstates": int(model.nstates),
        "tether_means": means.tolist(),
        "frame_agreement": agree / frames,
        "same_state_count_fraction": float(np.mean(count_diff == 0)),
        "state_count_mean_abs_difference": float(np.mean(np.abs(count_diff))),
        "state_count_difference_histogram": {
            str(int(d)): int(n)
            for d, n in zip(*np.unique(count_diff, return_counts=True), strict=True)
        },
        "populations": populations,
        "rates": rates,
    }


@requires_sidecar
def test_tether_s_states_agree_with_mashfret_s_within_the_frozen_limits(tmp_path) -> None:
    analysis = read_tag_analysis(path_for(PATH_A), PATH_A_TAG)
    report = measure(analysis, _idealize(analysis, tmp_path))
    print(json.dumps(report, indent=2, sort_keys=True))
    if os.environ.get(REPORT_ENV):
        Path(os.environ[REPORT_ENV]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    gates = json.loads(LIMITS.read_text("utf-8"))["gates"]
    for name, entry in gates.items():
        value = report[name]
        if entry["direction"] == "at_least":
            assert value >= entry["limit"], f"{name} {value:.4f} < limit {entry['limit']}"
        else:
            assert value <= entry["limit"], f"{name} {value:.4f} > limit {entry['limit']}"
    # The reports must be defined, so a lost table fails rather than printing nothing.
    assert len(report["populations"]) == 3 and len(report["rates"]) == 4
    assert all(np.isfinite(r["ratio"]) for r in report["rates"])
