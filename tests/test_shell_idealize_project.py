# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Idealize project…" dialog and the whole-project fit it starts (#625).

The I key and **Idealize → Idealize project…** open the dialog; no code path fits a single
molecule from a key. A fit from the dialog is the ``idealize`` service command on the
window's own project handle, so it stores the same model as ``idealize_molecules`` with the
same arguments, under the name of its method. It runs on the background-task service with
progress and **Cancel**; a cancel leaves the project unchanged. "Refit stale only" refits the
model of the chosen method only when ``reidealize`` has a stale molecule to refit.

The sidecar is not in this tier: its startup probe and its runner are the private seams of
``tether.services.idealize``, so everything else is real.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

from tether.idealize import IdealizationResult, StateModel, read_smd  # noqa: E402
from tether.idealize.supervisor import ProbeResult  # noqa: E402
from tether.services import idealize as service  # noqa: E402

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

N_FRAMES = 24
#: The fake sidecar's ELBO by state count: the automatic selection keeps 2 states.
ELBO = {1: -30.0, 2: -10.0, 3: -12.0, 4: -14.0}


class _Sidecar:
    """Record each fit and answer with a two-level model; ``gate`` can hold a fit open."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, list[str]]] = []
        self.started = threading.Event()
        self.gate: threading.Event | None = None

    def __call__(self, smd_path, *, nstates, model_type="vbconhmm", **_kwargs):
        smd = read_smd(smd_path)
        self.calls.append((int(nstates), model_type, list(smd.molecule_keys)))
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=10.0), "the test never released the fit"
        n, t = smd.n_molecules, smd.n_frames
        means = np.linspace(0.25, 0.75, nstates) if nstates > 1 else np.array([0.5])
        idealized = np.full((n, t), means[-1])
        model = StateModel(
            model_type=model_type,
            nstates=nstates,
            means=means,
            variances=np.full(nstates, 0.01),
            tmatrix=np.eye(nstates),
            norm_tmatrix=np.eye(nstates) * 0.9,
            elbo=ELBO[int(nstates)],
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


@pytest.fixture
def sidecar(monkeypatch) -> _Sidecar:
    """The sidecar is found and its probe passes; every fit is the fake."""
    fake = _Sidecar()
    monkeypatch.setattr(service, "_runner", fake)
    monkeypatch.setattr(service, "_probe", lambda _supervision: ProbeResult(True, "fake"))
    monkeypatch.setattr(service, "sidecar_problem", lambda *_args: None)
    return fake


def _store(tmp_path, *, n=3, name="fit.tether"):
    """A project of ``n`` molecules; molecule 0 accepted, 1 rejected, the rest undecided."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(3)
    donor = rng.uniform(400.0, 800.0, size=(n, N_FRAMES))
    acceptor = rng.uniform(200.0, 600.0, size=(n, N_FRAMES))
    project, keys = build_store_with_channels(tmp_path, donor, acceptor, name=name)
    project.accept(keys[0])
    project.reject(keys[1])
    return project, keys


def _stale_store(tmp_path, *, stale):
    """A project with a stored ``vbconhmm`` model; ``stale[i]`` makes molecule ``i`` stale."""
    from _analysis_store import build_store_with_model

    states = np.zeros((len(stale), N_FRAMES), dtype="int64")
    states[:, N_FRAMES // 2 :] = 1
    project, keys = build_store_with_model(tmp_path, states, np.array([0.3, 0.7]), stale=stale)
    for key in keys:
        project.accept(key)
    return project, keys


def _fit(**choices):
    from tether.gui.idealize_project import ProjectFit

    return ProjectFit(**choices)


# --- the choices, and why one cannot start (no Qt) -------------------------------------


def _facts(**overrides):
    defaults = {"conditions": (), "accepted": {None: 2}, "models": {}, "sidecar_problem": None}
    return service.FitFacts(**{**defaults, **overrides})


def test_a_choice_that_cannot_start_says_why() -> None:
    from tether.gui.idealize_project import SIDECAR_REMEDY, fit_refusal

    assert fit_refusal(_fit(), _facts()) is None
    missing = fit_refusal(_fit(), _facts(sidecar_problem="no sidecar interpreter"))
    assert missing == f"The sidecar is not available: no sidecar interpreter. {SIDECAR_REMEDY}"
    assert "Idealization cannot find the sidecar interpreter" in SIDECAR_REMEDY
    none = fit_refusal(_fit(condition="B"), _facts(conditions=("A", "B"), accepted={None: 2}))
    assert none == "No molecule is accepted in condition B. Accept molecules first."
    assert "no vbconhmm model" in fit_refusal(_fit(stale_only=True), _facts())
    fresh = _facts(models={"vbconhmm": service.ModelFacts(3, 0, False)})
    assert "Nothing to refit" in fit_refusal(_fit(stale_only=True), fresh)
    imported = _facts(models={"vbconhmm": service.ModelFacts(3, 1, True)})
    assert "imported from standalone tMAVEN" in fit_refusal(_fit(stale_only=True), imported)
    stale = _facts(models={"vbconhmm": service.ModelFacts(3, 1, False)})
    assert fit_refusal(_fit(stale_only=True), stale) is None


def test_a_fit_takes_only_the_methods_of_the_command() -> None:
    assert _fit().method == "vbconhmm"
    assert service.METHODS == ("vbconhmm", "ebhmm")
    with pytest.raises(ValueError, match="method must be one of"):
        _fit(method="vbfret")  # the single-molecule label of the old I key
    with pytest.raises(ValueError, match="1 or more"):
        _fit(nstates=0)


# --- the fit: the same model as idealize_molecules (no Qt) ------------------------------


@pytest.mark.parametrize(("method", "nstates"), [("vbconhmm", None), ("ebhmm", 3)])
def test_a_fit_stores_what_idealize_molecules_stores_under_its_method(
    tmp_path, sidecar, method, nstates
) -> None:
    from tether.gui.idealize_project import fit_project, outcome_message
    from tether.project.idealize import idealize_molecules, list_idealizations, read_idealization

    project, keys = _store(tmp_path)
    project.acquire_lock()
    try:
        fit = _fit(method=method, nstates=nstates)
        result = fit_project(project, fit)
        dialog = read_idealization(project, method)
        direct = idealize_molecules(
            project,
            [keys[0]],
            model_type=method,
            nstates=nstates,
            model_name="direct",
            _runner=sidecar,
        )
    finally:
        project.release_lock()
    # The stored model name matches the method that ran, beside the direct fit.
    assert sorted(list_idealizations(project)) == sorted([method, "direct"])
    # Only the accepted molecule: the rejected and the undecided ones are left out.
    assert dialog.molecule_keys == [keys[0]]
    for field in (
        "model_type",
        "nstates",
        "molecule_keys",
        "molecule_ids",
        "input_hashes",
        "intensity_quantity",
        "nstates_selected_by",
        "elbo_by_nstates",
        "elbo",
    ):
        assert getattr(dialog, field) == getattr(direct, field), field
    np.testing.assert_array_equal(dialog.means, direct.means)
    np.testing.assert_array_equal(dialog.idealized, direct.idealized)
    expected = 2 if nstates is None else nstates
    label = "consensus VB-HMM" if method == "vbconhmm" else "ebFRET"
    assert outcome_message(fit, result) == (
        f"Idealized the project with {label}: {expected} states, 1 molecules fitted "
        f"(model {method})"
    )


def test_a_fit_by_condition_takes_the_accepted_molecules_of_that_condition(
    tmp_path, sidecar, monkeypatch
) -> None:
    from tether.gui.idealize_project import fit_project

    project, keys = _store(tmp_path, n=4)
    project.accept(keys[3])
    by_condition = {"A": [keys[0], keys[1]], "B": [keys[2], keys[3]]}
    monkeypatch.setattr(type(project), "molecules_by_condition", lambda _self: by_condition)
    facts = service.read_fit_facts(project)
    assert facts.conditions == ("A", "B")
    assert facts.accepted == {None: 2, "A": 1, "B": 1}
    project.acquire_lock()
    try:
        result = fit_project(project, _fit(nstates=2, condition="B"))
    finally:
        project.release_lock()
    assert result.fitted.molecule_keys == [keys[3]]


def test_refit_stale_only_refits_only_a_model_reidealize_finds_stale(tmp_path, sidecar) -> None:
    from tether.gui.idealize_project import fit_project, outcome_message

    project, keys = _stale_store(tmp_path, stale=[True, False, False])
    facts = service.read_fit_facts(project)
    assert facts.models["vbconhmm"] == service.ModelFacts(3, 1, False)
    project.acquire_lock()
    try:
        fit = _fit(stale_only=True)
        result = fit_project(project, fit)
        assert [c[0] for c in sidecar.calls] == [2]  # the model's own fixed state count
        assert sidecar.calls[0][2] == keys  # the whole cohort: one model over all of them
        assert project.stale_idealization_keys("vbconhmm") == []
        assert outcome_message(fit, result) == (
            "Refitted the vbconhmm model (consensus VB-HMM): 2 states, 3 molecules fitted, "
            "1 of them stale"
        )
        # Now nothing is stale, so a second refit runs no fit at all.
        again = fit_project(project, fit)
    finally:
        project.release_lock()
    assert len(sidecar.calls) == 1
    assert again.refits[0].refit is None
    assert outcome_message(fit, again) == (
        "Idealize project: no molecule of the vbconhmm model is stale"
    )


def test_a_cancel_before_the_write_leaves_the_project_unchanged(tmp_path, sidecar) -> None:
    from tether.gui.idealize_project import fit_project
    from tether.gui.tasks import CancelToken, ProgressReport, TaskCancelled
    from tether.project.idealize import list_idealizations

    project, _keys = _store(tmp_path)
    token, report = CancelToken(), ProgressReport()
    project.acquire_lock()
    try:
        # One fixed-count fit: the cancel comes while the sidecar fits, so it is the write
        # guard that stops it, after the fit and before the model is written.
        sidecar.gate = threading.Event()
        caught: list[BaseException] = []

        def work() -> None:
            try:
                fit_project(project, _fit(nstates=2), cancel=token, progress=report)
            except TaskCancelled as exc:
                caught.append(exc)

        worker = threading.Thread(target=work)
        worker.start()
        assert sidecar.started.wait(timeout=10.0)
        assert report.fraction == 0.0
        token.cancel()
        sidecar.gate.set()
        worker.join(timeout=10.0)
        assert not worker.is_alive()
        assert len(caught) == 1
    finally:
        project.release_lock()
    assert len(sidecar.calls) == 1
    assert list_idealizations(project) == []


# --- the window ---------------------------------------------------------------------------


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


@pytest.mark.gui
@_needs_qt
def test_the_i_key_opens_the_dialog_and_fits_the_project(shell, qtbot, tmp_path, sidecar):
    from pyqtgraph.Qt import QtCore

    from tether.project.idealize import list_idealizations

    project, keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)  # the accepted molecule
    sidecar.gate = threading.Event()

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_I)
    dialog = shell.idealize_dialog
    assert dialog is not None and dialog.dialog.isVisible()
    assert not shell.is_idealizing  # the key fits nothing by itself
    assert dialog.choice() == _fit()
    assert dialog.ok_button.isEnabled() and dialog.reason == ""
    dialog.ok_button.click()
    try:
        assert shell.idealize_dialog is None
        assert shell.is_idealizing
        assert shell.status_message == "Idealizing project (consensus VB-HMM)…"
        busy = shell.busy_indicator
        assert busy.task_name == "Idealizing project"
        assert not busy.cancel_button.isHidden()  # the fit can be cancelled
        qtbot.waitUntil(sidecar.started.is_set, timeout=5000)
        # A second fit while one runs is refused, not queued.
        assert not shell.idealize_project(_fit())
        assert "a background task is running" in shell.status_message
        # The progress shows while the fit runs: none of the four fits has finished yet.
        qtbot.waitUntil(lambda: not busy.progress_row.isHidden(), timeout=5000)
    finally:
        sidecar.gate.set()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert [c[0] for c in sidecar.calls] == [1, 2, 3, 4]
    assert list_idealizations(project) == ["vbconhmm"]
    assert shell.status_message == (
        "Idealized the project with consensus VB-HMM: 2 states, 1 molecules fitted (model vbconhmm)"
    )
    # The step line of the selected molecule is drawn on the dock.
    np.testing.assert_allclose(shell.trace_dock.idealized_path, np.full(N_FRAMES, 0.75))
    assert shell.trace_dock.idealization_curve.isVisible()
    # The menu action opens the same dialog.
    shell.idealize_project_action.trigger()
    assert shell.idealize_dialog is not None
    assert shell.idealize_dialog.replaces == "The fit replaces the stored vbconhmm model."


@pytest.mark.gui
@_needs_qt
def test_cancel_in_the_window_leaves_the_project_unchanged(shell, qtbot, tmp_path, sidecar):
    from tether.project.idealize import list_idealizations

    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    sidecar.gate = threading.Event()
    assert shell.idealize_project(_fit())
    try:
        qtbot.waitUntil(sidecar.started.is_set, timeout=5000)
        shell.busy_indicator.cancel_button.click()
    finally:
        sidecar.gate.set()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert len(sidecar.calls) == 1  # it stopped before the next state count
    assert list_idealizations(project) == []
    assert shell.status_message == "Idealize project cancelled: the project is unchanged"
    assert shell.curation_project is not None  # the session is still writable


@pytest.mark.gui
@_needs_qt
def test_refit_stale_only_from_the_dialog(shell, qtbot, tmp_path, sidecar) -> None:
    project, _keys = _stale_store(tmp_path, stale=[False, True, False])
    assert shell.load_project(project.path) is not None
    dialog = shell.show_idealize_dialog()
    dialog.set_choice(_fit(method="ebhmm", stale_only=True))
    assert not dialog.ok_button.isEnabled()
    assert "no ebhmm model" in dialog.reason
    dialog.set_choice(_fit(stale_only=True))
    assert dialog.ok_button.isEnabled()
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert shell.status_message == (
        "Refitted the vbconhmm model (consensus VB-HMM): 2 states, 3 molecules fitted, "
        "1 of them stale"
    )
    assert project.stale_idealization_keys("vbconhmm") == []


@pytest.mark.gui
@_needs_qt
def test_a_missing_sidecar_is_named_before_a_fit_starts(shell, tmp_path, monkeypatch) -> None:
    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    monkeypatch.setattr(service, "sidecar_problem", lambda *_a: "no sidecar interpreter")
    dialog = shell.show_idealize_dialog()
    assert not dialog.ok_button.isEnabled()
    assert dialog.reason.startswith("The sidecar is not available: no sidecar interpreter.")
    assert "Idealization cannot find the sidecar interpreter" in dialog.reason


@pytest.mark.gui
@_needs_qt
def test_a_failed_probe_writes_nothing_and_gives_the_remedy(
    shell, qtbot, tmp_path, sidecar, monkeypatch
) -> None:
    from tether.gui.idealize_project import SIDECAR_REMEDY
    from tether.project.idealize import list_idealizations

    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    monkeypatch.setattr(service, "_probe", lambda _s: ProbeResult(False, "it did not start"))
    assert shell.idealize_project(_fit())
    qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=10_000)
    assert sidecar.calls == []
    assert list_idealizations(project) == []
    assert shell.status_message == (
        f"Idealize project failed: the sidecar is not available: it did not start. {SIDECAR_REMEDY}"
    )
