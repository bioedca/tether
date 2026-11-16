# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""``tether idealize``: fit a project, or refit its stale models, from the command line (#657).

The sidecar is faked with a canned [`IdealizationResult`][tether.idealize.IdealizationResult]
and the startup probe with a canned [`ProbeResult`][tether.idealize.supervisor.ProbeResult],
both through the private seams of [`tether.services.idealize`][], so this suite runs in the
base matrix. ``tests/test_cli_idealize_sidecar.py`` compares the command with
[`idealize_molecules`][tether.project.idealize.idealize_molecules] on a live sidecar.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("h5py")

import numpy as np  # noqa: E402

from _analysis_store import (  # noqa: E402
    build_store_with_channels,
    build_store_with_model,
    to_str,
)
from tether import cli  # noqa: E402
from tether.idealize import IdealizationResult, StateModel, read_smd  # noqa: E402
from tether.idealize.driver import SidecarError  # noqa: E402
from tether.idealize.supervisor import (  # noqa: E402
    DEFAULT_MAX_RESTARTS,
    DEFAULT_SIDECAR_TIMEOUT,
    ProbeResult,
)
from tether.idealize_choices import IDEALIZE_METHODS  # noqa: E402
from tether.imaging.extract import read_molecules  # noqa: E402
from tether.project import idealize as core  # noqa: E402
from tether.project import lock  # noqa: E402
from tether.services import idealize as service  # noqa: E402

N_MOLECULES = 4
N_FRAMES = 40


def _fake_result(smd_path, *, nstates: int, model_type: str, elbo: float) -> IdealizationResult:
    """A canned model aligned to the SMD: every molecule sits at the first state level."""
    smd = read_smd(smd_path)
    n, t = smd.n_molecules, smd.n_frames
    means = np.linspace(0.2, 0.8, nstates) if nstates > 1 else np.array([0.5])
    pre = smd.pre_list if smd.pre_list is not None else np.zeros(n, dtype=int)
    post = smd.post_list if smd.post_list is not None else np.full(n, t, dtype=int)
    idealized = np.full((n, t), np.nan)
    for i in range(n):
        idealized[i, int(pre[i]) : int(post[i])] = means[0]
    model = StateModel(
        model_type=model_type,
        nstates=nstates,
        means=means,
        variances=np.full(nstates, 0.01),
        tmatrix=np.eye(nstates),
        norm_tmatrix=np.eye(nstates) * 0.9,
        elbo=elbo,
        dtype="FRET",
        idealized=idealized,
        ran=np.arange(n, dtype="int64"),
    )
    return IdealizationResult(
        model=model,
        state_paths={},
        dwells=[],
        model_path=Path(smd_path),
        status={"ok": True},
        molecule_keys=smd.molecule_keys,
    )


class _FakeSidecar:
    """Records each fit and answers with :func:`_fake_result` (ELBO peaks at 3 states)."""

    ELBO = {1: -30.0, 2: -12.0, 3: -10.0, 4: -11.0, 5: -14.0}

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, smd_path, *, nstates, model_type="vbconhmm", **kwargs):
        smd = read_smd(smd_path)
        self.calls.append(
            {"nstates": int(nstates), "model_type": model_type, "keys": smd.molecule_keys, **kwargs}
        )
        return _fake_result(
            smd_path, nstates=int(nstates), model_type=model_type, elbo=self.ELBO[int(nstates)]
        )


@pytest.fixture
def sidecar(monkeypatch) -> _FakeSidecar:
    """A live-looking sidecar: the probe passes and every fit is the fake."""
    fake = _FakeSidecar()
    monkeypatch.setattr(service, "_runner", fake)
    monkeypatch.setattr(service, "_probe", lambda sup: ProbeResult(True, "fake sidecar"))
    return fake


def _channels(n: int = N_MOLECULES) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    donor = 600.0 + rng.normal(0.0, 5.0, (n, N_FRAMES))
    acceptor = 400.0 + rng.normal(0.0, 5.0, (n, N_FRAMES))
    return donor, acceptor


def _store(tmp_path: Path, name: str = "p.tether", **kwargs) -> Path:
    donor, acceptor = _channels()
    project, _ = build_store_with_channels(tmp_path, donor, acceptor, name=name, **kwargs)
    return project.path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same_model(a: core.StoredIdealization, b: core.StoredIdealization) -> None:
    assert (a.model_name, a.model_type, a.nstates) == (b.model_name, b.model_type, b.nstates)
    assert a.molecule_keys == b.molecule_keys and a.input_hashes == b.input_hashes
    assert a.nstates_selected_by == b.nstates_selected_by
    assert a.elbo_by_nstates == b.elbo_by_nstates and a.elbo == b.elbo
    np.testing.assert_array_equal(a.means, b.means)
    np.testing.assert_array_equal(a.state_paths, b.state_paths)
    np.testing.assert_array_equal(a.idealized, b.idealized)


# --- an ordinary fit -----------------------------------------------------------------


def test_the_command_stores_what_idealize_molecules_stores(tmp_path, sidecar, capsys) -> None:
    by_command = _store(tmp_path, "cmd.tether")
    by_function = _store(tmp_path, "fn.tether")

    assert cli.main(["idealize", str(by_command), "--seed", "7"]) == 0
    expected = core.idealize_molecules(by_function, seed=7, _runner=sidecar)

    _same_model(core.read_idealization(by_command, "vbconhmm"), expected)
    # Both runs asked the sidecar for the same fits with the same seed.
    assert [c["nstates"] for c in sidecar.calls] == [1, 2, 3, 4, 1, 2, 3, 4]
    assert {c["seed"] for c in sidecar.calls} == {7}
    out = capsys.readouterr().out
    assert "  method: vbconhmm" in out
    assert "  states: 3 (largest ELBO)" in out
    for k in (1, 2, 3, 4):
        assert f"    {k}: {_FakeSidecar.ELBO[k]:.6g}" in out
    assert f"  molecules fitted: {N_MOLECULES}" in out


def test_the_fit_options_reach_the_library(tmp_path, sidecar, capsys) -> None:
    path = _store(tmp_path)
    argv = ["idealize", str(path), "--method", "ebhmm", "--nstates-grid", "2,5"]
    assert cli.main(argv) == 0
    stored = core.read_idealization(path, "ebhmm")
    # The stored model name and type are the method that ran.
    assert stored.model_type == "ebhmm" and stored.model_name == "ebhmm"
    assert sorted(stored.elbo_by_nstates) == [2, 5]
    assert {c["model_type"] for c in sidecar.calls} == {"ebhmm"}
    assert all("seed" not in c for c in sidecar.calls)  # unseeded unless asked
    assert {c["timeout"] for c in sidecar.calls} == {DEFAULT_SIDECAR_TIMEOUT}


def test_a_fixed_state_count_and_a_model_name(tmp_path, sidecar, capsys) -> None:
    path = _store(tmp_path)
    assert cli.main(["idealize", str(path), "--nstates", "2", "--model-name", "two"]) == 0
    stored = core.read_idealization(path, "two")
    assert stored.nstates == 2 and stored.nstates_selected_by == "fixed"
    assert stored.model_type == "vbconhmm"
    assert [c["nstates"] for c in sidecar.calls] == [2]
    assert "  states: 2 (fixed)" in capsys.readouterr().out


def test_rejected_molecules_are_left_out_unless_included(tmp_path, sidecar) -> None:
    path = _store(tmp_path, rejected=[True, False, False, False])
    keys = [to_str(k) for k in read_molecules(path)["molecule_key"]]
    assert cli.main(["idealize", str(path)]) == 0
    assert core.read_idealization(path, "vbconhmm").molecule_keys == keys[1:]
    assert cli.main(["idealize", str(path), "--include-rejected", "--overwrite"]) == 0
    assert core.read_idealization(path, "vbconhmm").molecule_keys == keys


def test_a_condition_limits_the_fit_and_an_unknown_one_is_refused(tmp_path, sidecar, capsys):
    path = _store(tmp_path)
    (condition,) = _conditions(path)
    assert cli.main(["idealize", str(path), "--condition", condition]) == 0
    assert core.read_idealization(path, "vbconhmm").n_molecules == N_MOLECULES

    before = _digest(path)
    assert cli.main(["idealize", str(path), "--condition", "cond-nope", "--overwrite"]) == 2
    assert "no condition cond-nope" in capsys.readouterr().err
    assert _digest(path) == before


def _conditions(path: Path) -> list[str]:
    from tether.project.core import Project

    return list(Project.open(path).molecules_by_condition())


def test_an_existing_model_is_refused_before_the_sidecar_runs(tmp_path, sidecar, capsys):
    path = _store(tmp_path)
    assert cli.main(["idealize", str(path)]) == 0
    sidecar.calls.clear()
    before = _digest(path)
    assert cli.main(["idealize", str(path)]) == 2
    assert "already has the model 'vbconhmm'" in capsys.readouterr().err
    assert sidecar.calls == [] and _digest(path) == before


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--nstates-grid", "1,x"], "comma-separated whole numbers"),
        (["--nstates-grid", "2,2"], "names a state count twice"),
        (["--nstates", "0"], "nstates must be 1 or more"),
        (["--max-restarts", "-1"], "max_restarts must be >= 0"),
    ],
)
def test_bad_values_are_refused_with_code_2(tmp_path, sidecar, capsys, argv, message) -> None:
    path = _store(tmp_path)
    assert cli.main(["idealize", str(path), *argv]) == 2
    assert message in capsys.readouterr().err
    assert sidecar.calls == [] and core.list_idealizations(path) == []


def test_nstates_and_a_grid_together_are_a_usage_error(tmp_path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["idealize", "p.tether", "--nstates", "2", "--nstates-grid", "1,2"])
    assert excinfo.value.code == 2


def test_a_project_that_cannot_be_opened_is_refused(tmp_path, sidecar, capsys) -> None:
    assert cli.main(["idealize", str(tmp_path / "missing.tether")]) == 2
    assert "cannot open" in capsys.readouterr().err


def test_a_fit_that_fails_exits_1(tmp_path, monkeypatch, capsys) -> None:
    def broken(smd_path, **kwargs):
        raise SidecarError("the fit diverged", transient=False)

    monkeypatch.setattr(service, "_runner", broken)
    monkeypatch.setattr(service, "_probe", lambda sup: ProbeResult(True, "fake"))
    path = _store(tmp_path)
    assert cli.main(["idealize", str(path)]) == 1
    assert "the fit diverged" in capsys.readouterr().err
    assert core.list_idealizations(path) == []
    assert not lock.lock_path(path).exists()  # the lock was released


def test_a_transient_sidecar_failure_is_restarted(tmp_path, monkeypatch, sidecar) -> None:
    attempts: list[int] = []

    def flaky(smd_path, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise SidecarError("crashed", transient=True)
        return sidecar(smd_path, **kwargs)

    monkeypatch.setattr(service, "_runner", flaky)
    path = _store(tmp_path)
    assert cli.main(["idealize", str(path), "--nstates", "2", "--max-restarts", "1"]) == 0
    assert len(attempts) == 2 and core.read_idealization(path, "vbconhmm").nstates == 2


# --- the sidecar and the lock ----------------------------------------------------------


def test_a_missing_sidecar_exits_3_and_writes_nothing(tmp_path, capsys) -> None:
    # The real probe, with an interpreter that does not exist: no fake anywhere.
    path = _store(tmp_path)
    before = _digest(path)
    missing = tmp_path / "no-such-python"
    assert cli.main(["idealize", str(path), "--sidecar-python", str(missing)]) == 3
    err = capsys.readouterr().err
    assert err.startswith("tether idealize: the sidecar is not available")
    assert _digest(path) == before
    assert core.list_idealizations(path) == []
    assert not lock.lock_path(path).exists()


def test_the_probe_runs_before_anything_is_written(tmp_path, monkeypatch) -> None:
    path = _store(tmp_path)
    seen: list[bool] = []

    def probe(supervision):
        seen.append(lock.lock_path(path).exists())
        return ProbeResult(False, "no tmaven")

    monkeypatch.setattr(service, "_probe", probe)
    assert cli.main(["idealize", str(path)]) == 3
    assert seen == [False]


def test_the_command_holds_the_lock_while_it_fits(tmp_path, monkeypatch, sidecar) -> None:
    path = _store(tmp_path)
    holders: list[lock.LockInfo | None] = []

    def runner(smd_path, **kwargs):
        holders.append(lock.read_lock(path))
        return sidecar(smd_path, **kwargs)

    monkeypatch.setattr(service, "_runner", runner)
    assert cli.main(["idealize", str(path), "--nstates", "2"]) == 0
    assert holders and holders[0] is not None
    assert holders[0].identity == lock.local_identity()
    assert not lock.lock_path(path).exists()  # released at the end


def test_a_project_another_writer_holds_exits_1(tmp_path, sidecar, capsys) -> None:
    path = _store(tmp_path)
    other = lock.LockIdentity(host="another-host", user="someone", pid=4242)
    lock.acquire(path, identity=other)
    assert cli.main(["idealize", str(path)]) == 1
    assert "cannot take the lock" in capsys.readouterr().err
    assert sidecar.calls == [] and core.list_idealizations(path) == []
    assert lock.read_lock(path).identity == other  # not stolen, not removed


# --- --stale-only ----------------------------------------------------------------------


def _model_store(tmp_path: Path, stale: list[bool], name: str = "m.tether") -> Path:
    states = np.zeros((len(stale), N_FRAMES), dtype="int64")
    states[:, N_FRAMES // 2 :] = 1
    project, _ = build_store_with_model(
        tmp_path, states, np.array([0.3, 0.7]), stale=stale, name=name
    )
    return project.path


def test_stale_only_refits_a_model_with_a_stale_molecule_whole(tmp_path, sidecar, capsys):
    path = _model_store(tmp_path, stale=[True, False, False])
    keys = core.read_idealization(path, "vbconhmm").molecule_keys
    assert core.stale_molecule_keys(path, "vbconhmm") == keys[:1]

    assert cli.main(["idealize", str(path), "--stale-only"]) == 0
    assert "  vbconhmm: 1 stale, 2 current; refitted" in capsys.readouterr().out
    # The consensus model is refitted over every molecule it holds, not only the stale one,
    # with the configuration it stored (a fixed 2-state fit), and nothing is stale after.
    assert [c["nstates"] for c in sidecar.calls] == [2]
    assert sidecar.calls[0]["keys"] == keys
    assert core.stale_molecule_keys(path, "vbconhmm") == []
    assert core.read_idealization(path, "vbconhmm").molecule_keys == keys


def test_stale_only_leaves_a_current_model_and_needs_no_sidecar(tmp_path, monkeypatch, capsys):
    path = _model_store(tmp_path, stale=[False, False, False])

    def no_probe(supervision):
        raise AssertionError("a project with nothing stale must not start the sidecar")

    monkeypatch.setattr(service, "_probe", no_probe)
    before = _digest(path)
    assert cli.main(["idealize", str(path), "--stale-only"]) == 0
    assert "  vbconhmm: 0 stale, 3 current; current, not refitted" in capsys.readouterr().out
    assert _digest(path) == before


def test_stale_only_with_no_model_says_so(tmp_path, sidecar, capsys) -> None:
    path = _store(tmp_path)
    assert cli.main(["idealize", str(path), "--stale-only"]) == 0
    assert "no idealization model to check" in capsys.readouterr().out


def test_stale_only_with_a_stale_model_and_no_sidecar_exits_3(tmp_path, monkeypatch) -> None:
    path = _model_store(tmp_path, stale=[True, False, False])
    monkeypatch.setattr(service, "_probe", lambda sup: ProbeResult(False, "absent"))
    before = _digest(path)
    assert cli.main(["idealize", str(path), "--stale-only"]) == 3
    assert _digest(path) == before


def test_stale_only_model_name_limits_and_names_a_model(tmp_path, sidecar, capsys) -> None:
    path = _model_store(tmp_path, stale=[True, False, False])
    assert cli.main(["idealize", str(path), "--stale-only", "--model-name", "nope"]) == 2
    assert "has no model 'nope'" in capsys.readouterr().err
    assert cli.main(["idealize", str(path), "--stale-only", "--model-name", "vbconhmm"]) == 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--method", "ebhmm"],
        ["--nstates", "2"],
        ["--nstates-grid", "1,2"],
        ["--seed", "1"],
        ["--condition", "c"],
        ["--include-rejected"],
        ["--overwrite"],
    ],
)
def test_stale_only_refuses_the_fit_options(tmp_path, sidecar, capsys, extra) -> None:
    path = _model_store(tmp_path, stale=[True, False, False])
    assert cli.main(["idealize", str(path), "--stale-only", *extra]) == 2
    assert "stale_only refits each model with the configuration it stored" in (
        capsys.readouterr().err
    )
    assert sidecar.calls == []


# --- the parser and the library agree --------------------------------------------------


def test_the_method_choices_are_the_library_methods() -> None:
    assert IDEALIZE_METHODS == core.IDEALIZE_METHODS
    assert IDEALIZE_METHODS[0] == core.MODEL_TYPE_DEFAULT
    assert set(IDEALIZE_METHODS) == {core.MODEL_TYPE_DEFAULT, core.MODEL_TYPE_EBHMM}


def test_the_help_defaults_are_the_live_constants() -> None:
    """The help strings print literals (the parser must not import NumPy); pin them."""
    sub = next(a for a in cli.build_parser()._actions if a.dest == "command").choices["idealize"]
    by_dest = {a.dest: " ".join((a.help or "").split()) for a in sub._actions}
    assert (
        f"default {','.join(str(k) for k in core.NSTATES_GRID_DEFAULT)})" in by_dest["nstates_grid"]
    )
    assert f"default {DEFAULT_MAX_RESTARTS} " in by_dest["max_restarts"]
    assert f"(default {DEFAULT_SIDECAR_TIMEOUT:g})" in by_dest["sidecar_timeout"]
    assert f"(default {core.MODEL_TYPE_DEFAULT})" in by_dest["method"]
