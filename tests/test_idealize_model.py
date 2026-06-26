# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Appendix-D.2 model reader and dwell logic (no sidecar).

These exercise :mod:`tether.idealize.driver` purely in the base environment —
``read_model`` against a synthetic ``model`` HDF5 with known fields, the
``idealized -> state -> dwell`` derivation against known inputs, and the sidecar
interpreter resolution / argument guards. The *live* tMAVEN round-trip is in
``test_sidecar_driver.py`` (``@pytest.mark.sidecar``, deselected from CI).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from tether.idealize import (
    NO_STATE,
    Dwell,
    SidecarError,
    StateModel,
    _sidecar_runner,
    dwells_from_states,
    read_model,
    resolve_sidecar_python,
    run_vbfret,
    states_from_idealized,
)
from tether.idealize._sidecar_runner import STATUS_PREFIX
from tether.idealize.driver import _parse_status

h5py = pytest.importorskip("h5py")


def _write_model(path, **fields):
    """Write a minimal Appendix-D.2 ``model`` group with the given members."""
    attrs = fields.pop("_attrs", {})
    with h5py.File(path, "w") as f:
        g = f.create_group("model")
        for k, v in attrs.items():
            g.attrs[k] = v
        for k, v in fields.items():
            g.create_dataset(k, data=v)
    return path


# --------------------------------------------------------------------------- #
# states_from_idealized
# --------------------------------------------------------------------------- #


def test_states_from_idealized_nearest_mean():
    means = np.array([0.1, 0.9])
    idealized = np.array([[0.1, 0.9, 0.1], [0.9, 0.9, 0.1]])
    states = states_from_idealized(idealized, means)
    assert states.dtype == np.dtype("int64")
    np.testing.assert_array_equal(states, [[0, 1, 0], [1, 1, 0]])


def test_states_from_idealized_nan_is_no_state():
    means = np.array([0.1, 0.9])
    idealized = np.array([[np.nan, 0.9, np.nan, 0.12]])
    states = states_from_idealized(idealized, means)
    np.testing.assert_array_equal(states, [[NO_STATE, 1, NO_STATE, 0]])


def test_states_from_idealized_empty_means_raises():
    with pytest.raises(ValueError, match="means must be non-empty"):
        states_from_idealized(np.zeros((1, 3)), np.array([]))


# --------------------------------------------------------------------------- #
# dwells_from_states
# --------------------------------------------------------------------------- #


def test_dwells_run_length_encode():
    path = np.array([0, 0, 1, 1, 1, 0])
    dwells = dwells_from_states(path, molecule_index=7)
    assert dwells == [
        Dwell(molecule_index=7, state=0, start=0, length=2),
        Dwell(molecule_index=7, state=1, start=2, length=3),
        Dwell(molecule_index=7, state=0, start=5, length=1),
    ]


def test_dwells_skip_no_state_but_break_runs():
    # NO_STATE frames are not dwells, and they split the surrounding same-state run.
    path = np.array([0, NO_STATE, 0])
    dwells = dwells_from_states(path, molecule_index=0)
    assert [(d.state, d.start, d.length) for d in dwells] == [(0, 0, 1), (0, 2, 1)]


def test_dwells_empty_path():
    assert dwells_from_states(np.array([], dtype="int64"), 0) == []


# --------------------------------------------------------------------------- #
# read_model
# --------------------------------------------------------------------------- #


def test_read_model_full(tmp_path):
    mp = tmp_path / "model.hdf5"
    likelihood = np.array([[1.0, 0, 0, 0, 0], [2.0, 0, 0, 0, 0], [3.5, 0, 0, 0, 0]])
    _write_model(
        mp,
        _attrs={"type": "vb Consensus HMM", "rate_type": "Transition Matrix"},
        nstates=2,
        mean=np.array([0.1, 0.9]),
        var=np.array([0.01, 0.02]),
        tmatrix=np.array([[9.0, 1.0], [1.0, 9.0]]),
        norm_tmatrix=np.array([[0.9, 0.1], [0.1, 0.9]]),
        idealized=np.array([[0.1, 0.9, np.nan], [0.9, 0.9, 0.1]]),
        likelihood=likelihood,
        ran=np.array([0, 1]),
        dtype="FRET",
    )
    model = read_model(mp)
    assert isinstance(model, StateModel)
    assert model.model_type == "vb Consensus HMM"
    assert model.nstates == 2
    np.testing.assert_array_equal(model.means, [0.1, 0.9])
    np.testing.assert_array_equal(model.variances, [0.01, 0.02])
    assert model.tmatrix.shape == (2, 2)
    assert model.norm_tmatrix.shape == (2, 2)
    assert model.idealized.shape == (2, 3)
    assert model.elbo == pytest.approx(3.5)  # likelihood[-1, 0]
    np.testing.assert_array_equal(model.ran, [0, 1])
    assert model.dtype == "FRET"


def test_read_model_minimal_only_mean(tmp_path):
    mp = tmp_path / "m.hdf5"
    _write_model(mp, mean=np.array([0.2, 0.5, 0.8]))
    model = read_model(mp)
    assert model.nstates == 3  # inferred from mean length
    assert model.variances is None
    assert model.tmatrix is None
    assert model.idealized is None
    assert model.elbo is None
    assert model.ran.shape == (0,)
    assert model.model_type == "unknown"


def test_read_model_requires_mean(tmp_path):
    mp = tmp_path / "nomean.hdf5"
    _write_model(mp, nstates=2)
    with pytest.raises(ValueError, match="no 'mean'"):
        read_model(mp)


def test_read_model_missing_group(tmp_path):
    mp = tmp_path / "empty.hdf5"
    with h5py.File(mp, "w") as f:
        f.create_group("not_model")
    with pytest.raises(KeyError, match="model"):
        read_model(mp)


# --------------------------------------------------------------------------- #
# resolve_sidecar_python & run_vbfret guards
# --------------------------------------------------------------------------- #


def test_resolve_sidecar_python_arg(tmp_path):
    fake = tmp_path / "py.exe"
    fake.write_text("")
    assert resolve_sidecar_python(fake) == fake


def test_resolve_sidecar_python_env(tmp_path, monkeypatch):
    fake = tmp_path / "py.exe"
    fake.write_text("")
    monkeypatch.setenv("TETHER_SIDECAR_PYTHON", str(fake))
    assert resolve_sidecar_python(None) == fake


def test_resolve_sidecar_python_unset_raises(monkeypatch):
    monkeypatch.delenv("TETHER_SIDECAR_PYTHON", raising=False)
    with pytest.raises(SidecarError, match="no sidecar interpreter"):
        resolve_sidecar_python(None)


def test_resolve_sidecar_python_missing_path_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("TETHER_SIDECAR_PYTHON", raising=False)
    with pytest.raises(SidecarError, match="does not exist"):
        resolve_sidecar_python(tmp_path / "nope.exe")


def test_run_vbfret_missing_smd_raises(tmp_path):
    # A real interpreter resolves; the missing SMD then fails before any launch.
    with pytest.raises(FileNotFoundError):
        run_vbfret(tmp_path / "absent.hdf5", sidecar_python=sys.executable)


def test_run_vbfret_timeout_becomes_sidecar_error(tmp_path, monkeypatch):
    import subprocess

    from tether.idealize import write_smd

    smd = tmp_path / "smd.hdf5"
    write_smd(smd, np.zeros((1, 4, 2)))

    # bytes stderr (TimeoutExpired can leave it un-decoded even under text=True).
    def _raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="tmaven", timeout=1.0, stderr=b"boom")

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    with pytest.raises(SidecarError, match="timed out after"):
        run_vbfret(smd, sidecar_python=sys.executable, timeout=1.0)


# --------------------------------------------------------------------------- #
# _parse_status
# --------------------------------------------------------------------------- #


def test_parse_status_last_wins_and_ignores_noise():
    stdout = (
        "tmaven log line\n"
        + STATUS_PREFIX
        + '{"ok": false, "error": "first"}\n'
        + "more noise\n"
        + STATUS_PREFIX
        + '{"ok": true, "nmol": 4}\n'
    )
    status = _parse_status(stdout)
    assert status == {"ok": True, "nmol": 4}


def test_parse_status_none_when_absent():
    assert _parse_status("just logs\nno status here\n") is None


def test_parse_status_skips_malformed():
    stdout = STATUS_PREFIX + "{not json}\n"
    assert _parse_status(stdout) is None


# --------------------------------------------------------------------------- #
# _sidecar_runner.main argv handling (no tMAVEN import — the dispatch + arg
# checks run before ``from tmaven...``), so these guard the argv contract in CI.
# --------------------------------------------------------------------------- #


def test_runner_main_unknown_model_type(capsys, tmp_path):
    rc = _sidecar_runner.main(
        [
            "_sidecar_runner.py",
            str(tmp_path / "in.hdf5"),
            "dataset",
            "not_a_model",
            "2",
            str(tmp_path / "out.hdf5"),
        ]
    )
    assert rc == 1
    line = [
        ln
        for ln in capsys.readouterr().out.splitlines()
        if ln.startswith(_sidecar_runner.STATUS_PREFIX)
    ][-1]
    status = json.loads(line[len(_sidecar_runner.STATUS_PREFIX) :])
    assert status["ok"] is False
    assert "unknown model_type" in status["error"]


def test_runner_main_wrong_argc():
    # 3 positional args (after the script name) is neither 5 nor 6 -> usage error.
    assert _sidecar_runner.main(["_sidecar_runner.py", "a", "b", "c"]) == 2


# --------------------------------------------------------------------------- #
# read_model against a REAL tMAVEN export (the 281-mol consensus VB-HMM
# reference model, Appendix D.2). LFS `large` tier — validates the reader and
# the full dwell pipeline against ground truth without running a (slow) fit.
# --------------------------------------------------------------------------- #

_MODEL_281 = Path(__file__).parent / "fixtures" / "large" / "model_281mol.hdf5"


@pytest.mark.large
def test_read_model_real_281mol_reference():
    if not _MODEL_281.exists() or _MODEL_281.stat().st_size < 1000:
        pytest.skip("LFS fixture model_281mol.hdf5 not materialised")
    model = read_model(_MODEL_281)
    # The reference is a 4-state consensus VB-HMM (PRD Appendix D.2).
    assert model.model_type == "vb Consensus HMM"
    assert model.nstates == 4
    # Means match the Appendix D.2 documented levels [0.110, 0.428, 0.755, 0.952].
    np.testing.assert_allclose(model.means, [0.110, 0.428, 0.755, 0.952], atol=2e-3)
    assert model.tmatrix.shape == (4, 4)
    assert model.idealized.shape == (281, 1700)
    assert model.elbo is not None and model.elbo > 0
    assert model.ran.size == 281

    # The idealized -> state -> dwell pipeline runs on the real idealized array.
    states = states_from_idealized(model.idealized, model.means)
    assert states.shape == (281, 1700)
    dwells = dwells_from_states(states[0], 0)
    assert dwells
    assert all(0 <= d.state < model.nstates for d in dwells)


def test_runner_main_accepts_optional_nrestarts(tmp_path):
    # With the optional 6th arg the argc check passes; we stop it before the
    # tMAVEN import by using an unknown model_type (fast, no sidecar needed).
    rc = _sidecar_runner.main(
        [
            "_sidecar_runner.py",
            str(tmp_path / "in.hdf5"),
            "dataset",
            "not_a_model",
            "2",
            str(tmp_path / "out.hdf5"),
            "1",
        ]
    )
    assert rc == 1  # reached run(), failed on model_type — not a usage error (2)
