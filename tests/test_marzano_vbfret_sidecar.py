# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live, advisory: tMAVEN's per-trace vbHMM against the stored vbFRET_nov12 paths (#797, ADR-0087).

``test_marzano_vbfret.py`` takes the authors' stored vbFRET path as given and checks Tether's
dwell and transition code on it. This module asks the other question: does the per-trace
variational-Bayes HMM that Tether's sidecar carries (tMAVEN, pinned in ``sidecar/conda-lock.yml``)
give the path that the original vbFRET_nov12 gave on the same measured traces? #545 compares
Tether with standalone tMAVEN; this compares tMAVEN with the original program, so it adds
evidence and replaces none.

``_marzano_vbhmm_child.py`` runs the fit in the sidecar (1 to 5 states for each trace, prior
``beta = 0.5``, seed 797). Three measures come back, for each set:

* **number of states** -- the fraction of traces whose path uses as many levels as the stored
  path uses;
* **state means** -- over the traces with the same number, the largest and the median absolute
  difference of the sorted levels;
* **Viterbi agreement** -- the fraction of frames whose fitted level, mapped to the nearest
  stored level of the same trace, is the stored level.

No tolerance is frozen: the maintainer accepts one first (#797). So the test asserts only that
the fit covers every frame of every trace and that each measure is defined, and it prints the
values. ``docs/validation.md`` (oracle (b)) records the values of the run on the pinned build;
set ``TETHER_MARZANO_REPORT`` to a path to write them as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from _marzano_dat import SETS, read_set
from _sidecar_gate import SIDECAR as _SIDECAR
from _sidecar_gate import requires_sidecar
from tether.idealize import write_smd

pytestmark = pytest.mark.sidecar

TESTS = Path(__file__).parent
SEED = 797
REPORT_ENV = "TETHER_MARZANO_REPORT"


def _fit(set_id: str, tmp: Path) -> tuple[list, dict]:
    traces = read_set(SETS[set_id][0])
    lengths = [t.frame.size for t in traces]
    raw = np.zeros((len(traces), max(lengths), 2))
    for i, t in enumerate(traces):
        raw[i, : lengths[i], 0] = t.donor
        raw[i, : lengths[i], 1] = t.acceptor
    smd = write_smd(
        tmp / f"{set_id}.hdf5",
        raw,
        pre_list=np.zeros(len(traces), dtype=np.int64),
        post_list=np.asarray(lengths, dtype=np.int64),
    )
    out = tmp / f"{set_id}.npz"
    proc = subprocess.run(
        [
            str(_SIDECAR),
            "-I",
            str(TESTS / "_marzano_vbhmm_child.py"),
            str(smd),
            str(out),
            str(SEED),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    assert proc.returncode == 0, f"tMAVEN child failed (exit {proc.returncode}):\n{proc.stderr}"
    with np.load(out) as npz:
        return traces, {key: npz[key] for key in npz.files}


def _measures(traces: list, fit: dict) -> dict:
    same, deltas, agree, frames = 0, [], 0, 0
    for i, t in enumerate(traces):
        stored = t.idealized
        fitted = fit["idealized"][i, : stored.size]
        assert np.all(np.isfinite(fitted)), f"{t.name}: the fit left a frame without a level"
        ref_levels = np.unique(stored)
        fit_levels = np.unique(fitted)
        if fit_levels.size == ref_levels.size:
            same += 1
            deltas.extend(np.abs(fit_levels - ref_levels).tolist())
        nearest = ref_levels[np.argmin(np.abs(fitted[:, None] - ref_levels[None, :]), axis=1)]
        agree += int(np.count_nonzero(nearest == stored))
        frames += stored.size
    return {
        "traces": len(traces),
        "frames": frames,
        "state_count_fraction": same / len(traces),
        "state_mean_abs_delta_max": max(deltas) if deltas else None,
        "state_mean_abs_delta_median": float(np.median(deltas)) if deltas else None,
        "viterbi_agreement": agree / frames,
        "fitted_nstates_histogram": np.bincount(fit["nstates"], minlength=6).tolist(),
        "stored_nstates_histogram": np.bincount(
            [np.unique(t.idealized).size for t in traces], minlength=6
        ).tolist(),
        "settings": json.loads(str(fit["settings"])),
    }


@requires_sidecar
def test_tmaven_per_trace_vbhmm_is_measured_against_the_stored_vbfret_paths(tmp_path) -> None:
    report = {}
    for set_id in sorted(SETS):
        traces, fit = _fit(set_id, tmp_path)
        measures = _measures(traces, fit)
        assert 0.0 <= measures["state_count_fraction"] <= 1.0
        assert 0.0 <= measures["viterbi_agreement"] <= 1.0
        assert measures["state_mean_abs_delta_max"] is not None, "no trace has the same count"
        report[set_id] = measures
    print(json.dumps(report, indent=2, sort_keys=True))
    if os.environ.get(REPORT_ENV):
        Path(os.environ[REPORT_ENV]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
