# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The driver's success path and the parity measurement harness, without a sidecar (#737).

Two pieces of :mod:`tether.idealize` ran only in the live sidecar tier — or in no tier at all:

* the **success path of** :func:`tether.idealize.run_vbfret` — read the model file the sidecar
  wrote, derive one integer state path per fitted molecule, and run-length-encode the dwells.
  These tests point the driver at a **fake runner script**, executed by this interpreter as a
  real subprocess, that copies a prepared Appendix-D.2 model to the requested ``model_out`` and
  emits the status line. Each prepared molecule has a different idealized row, so a driver that
  mixes up the rows fails.
* :func:`tether.idealize.measure_spread`, the harness whose output the numbers in
  ``schema/parity_tolerance.json`` were frozen from. These tests replace its one boundary to the
  sidecar, ``run_vbfret``, with prepared models of known disagreement, and check the anchor
  rule (``n_runs`` comparisons against a given reference, ``n_runs - 1`` against the first run),
  each per-run metric, and the worst value of each metric.

The expected values are written out by hand from the prepared inputs, not computed with the
production functions under test.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

import tether.idealize.driver as driver
import tether.idealize.parity as parity
from tether.idealize import (
    NO_STATE,
    Dwell,
    IdealizationResult,
    SpreadSummary,
    StateModel,
    measure_spread,
    run_vbfret,
    write_smd,
)
from tether.idealize._sidecar_runner import STATUS_PREFIX

h5py = pytest.importorskip("h5py")

NAN = float("nan")

# --------------------------------------------------------------------------- #
# run_vbfret success path, through a fake sidecar runner                       #
# --------------------------------------------------------------------------- #

#: State 0 is the HIGH level, so a path in these tests is in the model's own labels, not
#: in the canonical (mean-sorted) labels of the parity metrics.
_MEANS = [0.8, 0.2]

#: Three molecules, six frames. NaN is a frame outside the analysis window.
_IDEALIZED = [
    [0.2, 0.2, 0.8, 0.8, 0.8, NAN],
    [0.8, 0.8, 0.8, 0.8, 0.8, 0.8],
    [NAN, 0.8, 0.2, 0.2, 0.8, 0.8],
]

#: The integer path of each row of ``_IDEALIZED`` (nearest of ``_MEANS``; NaN -> NO_STATE).
_PATHS = {
    0: [1, 1, 0, 0, 0, NO_STATE],
    1: [0, 0, 0, 0, 0, 0],
    2: [NO_STATE, 0, 1, 1, 0, 0],
}

#: The dwells of each path in ``_PATHS``: (state, start frame, length).
_DWELLS = {
    0: [(1, 0, 2), (0, 2, 3)],
    1: [(0, 0, 6)],
    2: [(0, 1, 1), (1, 2, 2), (0, 4, 2)],
}

_FAKE_RUNNER = """\
import json
import shutil
import sys

args = sys.argv[1:]
shutil.copyfile({prepared!r}, args[4])
status = {{"ok": True, "nmol": 3, "nt": 6, "result_type": "fake", "argv": args}}
print("fitting... (a line of tMAVEN log noise)")
print({prefix!r} + json.dumps(status))
"""


def _write_prepared_model(path: Path, *, ran: list[int] | None) -> Path:
    with h5py.File(path, "w") as f:
        g = f.create_group("model")
        g.attrs["type"] = "fake consensus HMM"
        g.create_dataset("mean", data=np.asarray(_MEANS))
        g.create_dataset("nstates", data=len(_MEANS))
        g.create_dataset("idealized", data=np.asarray(_IDEALIZED, dtype="float64"))
        g.create_dataset("likelihood", data=np.asarray([[-50.0], [-10.0]]))
        if ran is not None:
            g.create_dataset("ran", data=np.asarray(ran, dtype="int64"))
    return path


@pytest.fixture
def fake_sidecar(tmp_path, monkeypatch):
    """Point the driver at a fake runner; return a function that prepares the model it emits."""

    def prepare(*, ran: list[int] | None) -> None:
        prepared = _write_prepared_model(tmp_path / "prepared.model.hdf5", ran=ran)
        runner = tmp_path / "fake_runner.py"
        runner.write_text(
            _FAKE_RUNNER.format(prepared=str(prepared), prefix=STATUS_PREFIX), encoding="utf-8"
        )
        monkeypatch.setattr(driver, "_RUNNER", runner)

    return prepare


def _keyed_smd(path: Path) -> list[str]:
    keys = ["mol-a", "mol-b", "mol-c"]
    write_smd(path, np.zeros((3, 6, 2)), molecule_keys=keys)
    return keys


def _expected_dwells(molecules) -> list[Dwell]:
    return [
        Dwell(molecule_index=mol, state=state, start=start, length=length)
        for mol in molecules
        for state, start, length in _DWELLS[mol]
    ]


def test_run_vbfret_builds_each_fitted_molecules_own_path_and_dwells(tmp_path, fake_sidecar):
    # The model was fit on molecules 0 and 2 only (``ran``), so molecule 1 gets no path.
    fake_sidecar(ran=[0, 2])
    smd = tmp_path / "input.hdf5"
    keys = _keyed_smd(smd)

    result = run_vbfret(
        smd, sidecar_python=sys.executable, model_type="vbconhmm", nstates=2, nrestarts=3
    )

    assert isinstance(result, IdealizationResult)
    assert sorted(result.state_paths) == [0, 2]
    for mol in (0, 2):
        np.testing.assert_array_equal(result.state_paths[mol], _PATHS[mol])
    assert result.dwells == _expected_dwells([0, 2])
    assert result.molecule_keys == keys

    # The model file the runner wrote is read back, from the default place beside the SMD.
    assert result.model_path == tmp_path / "input.model.hdf5"
    assert result.model.model_type == "fake consensus HMM"
    np.testing.assert_array_equal(result.model.means, _MEANS)
    np.testing.assert_array_equal(result.model.ran, [0, 2])
    assert result.model.elbo == -10.0

    # The argv contract: smd, group, model type, state count, model_out, then nrestarts.
    assert result.status["ok"] is True
    assert result.status["argv"] == [
        str(smd),
        "dataset",
        "vbconhmm",
        "2",
        str(tmp_path / "input.model.hdf5"),
        "3",
    ]


def test_run_vbfret_without_ran_derives_a_path_for_every_row(tmp_path, fake_sidecar):
    # A model with no ``ran`` member covers every idealized row; no nrestarts is passed.
    fake_sidecar(ran=None)
    smd = tmp_path / "input.hdf5"
    _keyed_smd(smd)
    model_out = tmp_path / "elsewhere" / "fit.model.hdf5"
    model_out.parent.mkdir()

    result = run_vbfret(
        smd, sidecar_python=sys.executable, model_type="ebhmm", nstates=4, model_out=model_out
    )

    assert sorted(result.state_paths) == [0, 1, 2]
    for mol in (0, 1, 2):
        np.testing.assert_array_equal(result.state_paths[mol], _PATHS[mol])
    assert result.dwells == _expected_dwells([0, 1, 2])
    assert result.model_path == model_out
    assert result.status["argv"] == [str(smd), "dataset", "ebhmm", "4", str(model_out)]


# --------------------------------------------------------------------------- #
# measure_spread, with run_vbfret replaced at the sidecar boundary            #
# --------------------------------------------------------------------------- #


def _model(means, idealized, elbo) -> StateModel:
    means = np.asarray(means, dtype="float64")
    return StateModel(
        model_type="vb Consensus HMM",
        nstates=means.size,
        means=means,
        elbo=elbo,
        idealized=np.asarray(idealized, dtype="float64"),
    )


#: The model that ``_RUN_METRICS`` are measured against: two molecules, four frames; molecule 0
#: uses both states, molecule 1 one. Run 0 is this model, so it is also the anchor that
#: ``measure_spread`` takes when no reference is given.
_REFERENCE = _model([0.1, 0.9], [[0.1, 0.1, 0.9, 0.9], [0.1, 0.1, 0.1, 0.1]], 100.0)

#: Three fits, each compared with ``_REFERENCE``. Hand-computed metrics (count, |dE|, Viterbi,
#: relative ELBO), with eight frames in all:
#:   run 0 - identical to the reference:                                     1.0, 0.0,  1.0,   0.0
#:   run 1 - molecule 1 gains a second state in its last frame, low level 0.12:
#:           molecule 1 count 2 != 1, 7 of 8 frames agree, |0.12 - 0.1|, |99 - 100| / 100:
#:                                                                            0.5, 0.02, 0.875, 0.01
#:   run 2 - two frames of molecule 0 swap state, high level 0.85, ELBO 102:  1.0, 0.05, 0.75,  0.02
_RUNS = [
    _REFERENCE,
    _model([0.12, 0.9], [[0.12, 0.12, 0.9, 0.9], [0.12, 0.12, 0.12, 0.9]], 99.0),
    _model([0.1, 0.85], [[0.1, 0.85, 0.1, 0.85], [0.1, 0.1, 0.1, 0.1]], 102.0),
]
_RUN_METRICS = [
    (1.0, 0.0, 1.0, 0.0),
    (0.5, 0.02, 0.875, 0.01),
    (1.0, 0.05, 0.75, 0.02),
]

#: The reference GIVEN to ``measure_spread``. It differs from run 0 in its high level (0.8, not
#: 0.9) and in its ELBO (80, not 100); its canonical state path is the same as run 0's. Thus an
#: implementation that takes the first fit as the anchor although a reference is given, and still
#: makes ``n_runs`` comparisons, gets (1.0, 0.0, 1.0, 0.0) for run 0 and fails (#835).
_GIVEN_REFERENCE = _model([0.1, 0.8], [[0.1, 0.1, 0.8, 0.8], [0.1, 0.1, 0.1, 0.1]], 80.0)

#: Hand-computed metrics of each fit against ``_GIVEN_REFERENCE`` (count, |dE|, Viterbi,
#: relative ELBO). The paths are those of ``_REFERENCE``, so count and Viterbi are as in
#: ``_RUN_METRICS``; |dE| is the larger of the low and high deltas; the ELBO is over |80|:
#:   run 0 - |0.9 - 0.8|, |100 - 80| / 80:                      1.0, 0.1,  1.0,   0.25
#:   run 1 - max(|0.12 - 0.1|, |0.9 - 0.8|), |99 - 80| / 80:     0.5, 0.1,  0.875, 0.2375
#:   run 2 - |0.85 - 0.8|, |102 - 80| / 80:                     1.0, 0.05, 0.75,  0.275
_GIVEN_METRICS = [
    (1.0, 0.1, 1.0, 0.25),
    (0.5, 0.1, 0.875, 0.2375),
    (1.0, 0.05, 0.75, 0.275),
]

_METRIC_NAMES = (
    "state_count_fraction",
    "state_mean_abs_delta",
    "viterbi_agreement",
    "relative_elbo",
)


@pytest.fixture
def fake_fits(monkeypatch):
    """Replace ``run_vbfret`` (as ``measure_spread`` sees it) with the prepared ``_RUNS``."""
    calls: list[tuple[Path, dict]] = []

    def _fake_run_vbfret(smd_path, **kwargs):
        model = _RUNS[len(calls)]
        calls.append((Path(smd_path), kwargs))
        return IdealizationResult(
            model=model,
            state_paths={},
            dwells=[],
            model_path=Path(kwargs["model_out"]),
            status={"ok": True},
        )

    monkeypatch.setattr(parity, "run_vbfret", _fake_run_vbfret)
    return calls


def _write_reference(path: Path, model: StateModel) -> Path:
    with h5py.File(path, "w") as f:
        g = f.create_group("model")
        g.attrs["type"] = "vb Consensus HMM"
        g.create_dataset("mean", data=model.means)
        g.create_dataset("idealized", data=model.idealized)
        g.create_dataset("likelihood", data=np.asarray([[model.elbo]]))
    return path


def _assert_metrics(metrics, expected) -> None:
    got = tuple(getattr(metrics, name) for name in _METRIC_NAMES)
    assert got == pytest.approx(expected), got


@pytest.mark.parametrize("given_as", ["model", "path"])
def test_measure_spread_compares_every_run_with_the_given_reference(tmp_path, fake_fits, given_as):
    # The given reference is not the first fit, so each metric shows which one is the anchor.
    assert _GIVEN_REFERENCE.elbo != _RUNS[0].elbo
    assert not np.array_equal(_GIVEN_REFERENCE.means, _RUNS[0].means)
    reference = (
        _GIVEN_REFERENCE
        if given_as == "model"
        else _write_reference(tmp_path / "ref.hdf5", _GIVEN_REFERENCE)
    )
    smd = tmp_path / "smd_x.hdf5"
    scratch = tmp_path / "runs"

    spread, per_run = measure_spread(
        smd,
        reference=reference,
        n_runs=3,
        model_type="vbconhmm",
        nstates=2,
        sidecar_python="sidecar-python",
        scratch_dir=scratch,
        nrestarts=5,
        progress=False,
    )

    # With a reference, every one of the n_runs fits is a comparison, against that reference.
    assert len(per_run) == 3
    for metrics, expected in zip(per_run, _GIVEN_METRICS, strict=True):
        _assert_metrics(metrics, expected)
        assert (metrics.n_states_ref, metrics.n_states_test) == (2, 2)

    # The summary holds each run's value in order, and the worst of each metric.
    for k, name in enumerate(_METRIC_NAMES):
        assert spread[name].values == pytest.approx([run[k] for run in _GIVEN_METRICS])
    assert spread["state_count_fraction"].worst == pytest.approx(0.5)
    assert spread["state_mean_abs_delta"].worst == pytest.approx(0.1)
    assert spread["viterbi_agreement"].worst == pytest.approx(0.75)
    assert spread["relative_elbo"].worst == pytest.approx(0.275)

    # Each fit is driven with the caller's settings into its own model file in the scratch dir.
    assert scratch.is_dir()
    assert [kwargs["model_out"] for _, kwargs in fake_fits] == [
        scratch / f"smd_x.run{i:02d}.model.hdf5" for i in range(3)
    ]
    for smd_path, kwargs in fake_fits:
        assert smd_path == smd
        assert kwargs["sidecar_python"] == "sidecar-python"
        assert kwargs["model_type"] == "vbconhmm"
        assert kwargs["nstates"] == 2
        assert kwargs["nrestarts"] == 5


def test_measure_spread_anchors_on_the_first_run_without_a_reference(tmp_path, fake_fits, capsys):
    spread, per_run = measure_spread(tmp_path / "smd_y.hdf5", reference=None, n_runs=3, nstates=2)

    # The first fit is the anchor, not a comparison: n_runs - 1 comparisons. Run 0 equals
    # the reference, so runs 1 and 2 against it give the same metrics as above.
    assert len(fake_fits) == 3
    assert len(per_run) == 2
    _assert_metrics(per_run[0], _RUN_METRICS[1])
    _assert_metrics(per_run[1], _RUN_METRICS[2])
    assert spread["viterbi_agreement"].values == pytest.approx([0.875, 0.75])
    assert spread["state_count_fraction"].worst == pytest.approx(0.5)
    assert spread["state_mean_abs_delta"].worst == pytest.approx(0.05)
    # Without a scratch dir the model files go beside the SMD.
    assert fake_fits[0][1]["model_out"] == tmp_path / "smd_y.run00.model.hdf5"

    out = capsys.readouterr().out
    assert "run 00: reference anchored" in out
    assert "run 02: count=1.000" in out


def test_spread_percentile_ignores_non_finite_values():
    summary = SpreadSummary("state_mean_abs_delta", "ceiling", [0.1, math.inf, 0.3, NAN])
    assert summary.percentile(50) == pytest.approx(0.2)
    assert summary.percentile(100) == pytest.approx(0.3)
    assert math.isnan(SpreadSummary("x", "ceiling", [math.inf]).percentile(50))
