# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo and redo of the curation actions (#632, PRD §7.3, §5.1).

One test, or one parametrized case, per acceptance criterion of #632, on real ``.tether``
stores:

* each covered action can be undone and redone, and the stored state after an undo
  equals the state before the action;
* an undo never deletes or rewrites a label row: the logs only gain rows;
* the ranker's training labels read the state after an undo;
* the undo list is empty after the project is closed and opened again;
* an undo is refused, with a message, while the project is read-only.

The GUI cases run headless under ``pytest-qt``; the store writer behind them
([`restore_curation`][tether.project.labels.restore_curation]) is tested without Qt.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("h5py")

import h5py  # noqa: E402

from tether.project import labels  # noqa: E402

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

N_FRAMES = 60
BLEACH = 37
CATEGORIES = ["bound", "unbound", "mixed"]


def _text(value: object) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _store(tmp_path, *, windows=None):
    """A real ``.tether`` with three molecules; molecule 0 bleaches at ``BLEACH``."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(632)
    donor = rng.normal(600.0, 15.0, size=(3, N_FRAMES))
    acceptor = rng.normal(400.0, 15.0, size=(3, N_FRAMES))
    donor[0, BLEACH:] = rng.normal(0.0, 15.0, size=N_FRAMES - BLEACH)
    acceptor[0, BLEACH:] = rng.normal(0.0, 15.0, size=N_FRAMES - BLEACH)
    project, keys = build_store_with_channels(
        tmp_path, donor, acceptor, windows=windows, name="undo.tether"
    )
    project.sync_conditions()
    for condition_id in project.molecules_by_condition():
        project.set_category_list(condition_id, CATEGORIES)
    return project, keys


def _state(path: Path, key: str) -> tuple:
    """Everything an undo must restore for ``key``: curation, its reason, category, window."""
    from tether.services.curation import curation_snapshot, stored_molecules

    rows = stored_molecules(path, key)
    return (
        curation_snapshot(path, key),
        tuple(m.category for m in rows),
        tuple(m.window for m in rows),
    )


def _logs(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """The ``/labels`` table and the molecule edit log (empty when not written yet)."""
    from tether.project.molecule_edits import read_molecule_audit

    return labels.read_labels(path), read_molecule_audit(path)


def _assert_prefix(longer: np.ndarray, shorter: np.ndarray) -> None:
    """``longer`` starts with every row of ``shorter``, field by field."""
    assert longer.shape[0] >= shorter.shape[0]
    for name in shorter.dtype.names or ():
        np.testing.assert_array_equal(longer[name][: shorter.shape[0]], shorter[name], name)


# --- the store writer, without Qt ---------------------------------------------------


def test_restore_curation_writes_back_each_state_by_new_rows_only(tmp_path) -> None:
    project, keys = _store(tmp_path)
    key = keys[0]
    labels.reject(project.path, key, reason="threshold", value=0.25, labeler="ana")
    rejected = _state(project.path, key)
    labels.unreject(project.path, key, labeler="ana")
    before = labels.read_labels(project.path)

    # Undo of the un-reject: the reject comes back with its reason row.
    labels.restore_curation(
        project.path, key, labels.CurationLabel.REJECT, reason="threshold", value=0.25
    )
    assert _state(project.path, key) == rejected
    # Back to accepted from rejected: an un-reject, then an accept.
    rows = labels.restore_curation(project.path, key, labels.CurationLabel.ACCEPT)
    assert [int(r["label_value"]) for r in rows] == [0, 1]
    assert labels.curation_label_of(project.path, key) == labels.CurationLabel.ACCEPT
    # Back to uncurated from accepted.
    labels.restore_curation(project.path, key, labels.CurationLabel.UNCURATED)
    assert labels.curation_label_of(project.path, key) == labels.CurationLabel.UNCURATED

    after = labels.read_labels(project.path)
    assert after.shape[0] == before.shape[0] + 4
    _assert_prefix(after, before)
    events = [_text(e) for e in labels.read_reject_log(project.path)["event"]]
    assert events == ["reject", "restore", "reject", "restore"]


# A carried label (ADR-0136) waits for review after a new extraction; an accept confirms
# it, and an undo of that accept must hold it out of training again.
_SHAPE = (64, 96)
_DONOR = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_SHIFT = np.array([49.0, 0.0])


def _carried_store(tmp_path: Path) -> tuple[Path, str]:
    pytest.importorskip("tifffile")
    pytest.importorskip("skimage")
    import tifffile

    from tether.project import lock
    from tether.project.extract import ExtractOptions, extract_movie, reextract_movie

    movie = tmp_path / "Bla_UCKOPSB_T-box_35pM_tRNA_600nM_010.tif"
    centers = np.vstack([_DONOR, _DONOR + _SHIFT])
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    spots = sum(np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / 4.5) for x, y in centers)
    stack = 80.0 + 400.0 * (1.0 + 0.02 * np.arange(12))[:, None, None] * spots[None]
    tifffile.imwrite(movie, np.ascontiguousarray(stack, dtype=">u2"), byteorder=">")
    project = tmp_path / "carried.tether"
    options = ExtractOptions(window=21)
    extract_movie(movie, project, options=options)
    with h5py.File(project, "r") as f:
        key = _text(f["molecules"]["table"]["molecule_key"][0])
    labels.accept(project, key, labeler="first")
    with lock.publish_lock(project) as guard:
        reextract_movie(project, movie, options=options, publish_guard=guard)
    return project, key


def test_an_undone_review_of_a_carried_label_waits_for_review_again(tmp_path) -> None:
    from tether.io.generations import pending_label_ids
    from tether.services.curation import curation_snapshot

    project, key = _carried_store(tmp_path)
    waiting = curation_snapshot(project, key)
    assert waiting.pending and waiting.label == labels.CurationLabel.ACCEPT
    labels.accept(project, key, labeler="reviewer")
    assert not curation_snapshot(project, key).pending

    labels.restore_curation(project, key, waiting.label, pending=waiting.pending)

    assert curation_snapshot(project, key) == waiting
    assert pending_label_ids(project)  # held out of training again


# --- the application ------------------------------------------------------------------


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell(advance_after_curation=False)
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _loaded(shell, tmp_path, **kwargs):
    project, keys = _store(tmp_path, **kwargs)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    return project, keys


def _press(qtbot, shell, key_name: str) -> None:
    from pyqtgraph.Qt import QtCore

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))


#: Each covered action as the keys that set the molecule up and the key that acts. The
#: window keys start from a narrowed window, so both a nudge and a reset change it.
ACTIONS = {
    "accept": ((), "Key_Space"),
    "accept over a reject": (("Key_Backspace",), "Key_Space"),
    "reject": ((), "Key_Backspace"),
    "reject over an accept": (("Key_Space",), "Key_Backspace"),
    "un-reject": (("Key_Backspace",), "unreject"),
    "category assign": ((), "Key_2"),
    "category change": (("Key_1",), "Key_3"),
    "category clear": (("Key_2",), "Key_0"),
    "window start": ((), "Key_Equal"),
    "window end": ((), "Key_BracketLeft"),
    "window reset": ((), "Key_R"),
    "photobleach edit": ((), "Key_P"),
}


def _act(qtbot, shell, key_name: str) -> None:
    if key_name == "unreject":
        shell.unreject_button.click()
    else:
        _press(qtbot, shell, key_name)


@_needs_qt
@pytest.mark.gui
@pytest.mark.parametrize("name", list(ACTIONS))
def test_each_action_is_undone_to_its_stored_state_and_redone(shell, qtbot, tmp_path, name) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(4, 56)] * 3)
    setup, action = ACTIONS[name]
    for key_name in setup:
        _act(qtbot, shell, key_name)
    before = _state(project.path, keys[0])
    labels_before, audit_before = _logs(project.path)

    _act(qtbot, shell, action)
    after = _state(project.path, keys[0])
    assert after != before, shell.status_message
    assert shell.undo_action.isEnabled()

    assert shell.undo(), shell.status_message
    assert _state(project.path, keys[0]) == before
    assert shell.status_message.startswith("Undo ")
    labels_undone, audit_undone = _logs(project.path)
    _assert_prefix(labels_undone, labels_before)
    _assert_prefix(audit_undone, audit_before)
    gained = (labels_undone.shape[0] - labels_before.shape[0]) + (
        audit_undone.shape[0] - audit_before.shape[0]
    )
    assert gained >= 2  # the action's rows and the undo's rows; none removed

    assert shell.redo(), shell.status_message
    assert _state(project.path, keys[0]) == after
    _assert_prefix(labels.read_labels(project.path), labels_undone)
    # The other molecules were never touched.
    assert _state(project.path, keys[1])[0].label == labels.CurationLabel.UNCURATED


@_needs_qt
@pytest.mark.gui
def test_the_edit_menu_names_the_action_and_a_new_action_ends_the_redo(
    shell, qtbot, tmp_path
) -> None:
    from pyqtgraph.Qt import QtGui

    from tether.gui.shell import _redo_keys

    _loaded(shell, tmp_path)
    assert not shell.undo_action.isEnabled() and not shell.redo_action.isEnabled()
    assert QtGui.QKeySequence("Ctrl+Z") in shell.undo_action.shortcuts()
    assert QtGui.QKeySequence("Ctrl+Shift+Z") in shell.redo_action.shortcuts()
    assert _redo_keys("win32") == ("Ctrl+Shift+Z", "Ctrl+Y")
    assert _redo_keys("linux") == ("Ctrl+Shift+Z",)
    name = shell._traces[0].name or shell._traces[0].molecule_key

    _press(qtbot, shell, "Key_Backspace")
    assert shell.undo_action.text() == f"&Undo reject {name}"
    shell.undo_action.trigger()
    assert shell.redo_action.text() == f"&Redo reject {name}"
    assert shell.undo_stack.undo_count == 0

    _press(qtbot, shell, "Key_Space")
    assert shell.undo_stack.redo_count == 0
    assert not shell.redo_action.isEnabled()
    assert shell.undo_action.text() == f"&Undo accept {name}"
    # Several steps go back one at a time, newest first.
    _press(qtbot, shell, "Key_2")
    assert shell.undo() and shell.undo()
    assert labels.curation_label_of(shell.project_path, shell._traces[0].molecule_key) == 0
    assert not shell.undo()
    assert shell.status_message == "Undo unavailable: no curation action to undo"


@_needs_qt
@pytest.mark.gui
def test_an_undo_selects_the_molecule_it_changed(qapp, qtbot, tmp_path) -> None:
    from tether.gui.shell import TetherShell

    shell = TetherShell(advance_after_curation=True)
    qtbot.addWidget(shell.window)
    try:
        project, keys = _loaded(shell, tmp_path)
        _press(qtbot, shell, "Key_Backspace")  # rejects row 0 and shows row 1
        assert shell.molecule_list.currentRow() == 1

        assert shell.undo()
        assert shell.molecule_list.currentRow() == 0
        assert labels.curation_label_of(project.path, keys[0]) == 0
    finally:
        shell.close()


@_needs_qt
@pytest.mark.gui
def test_the_ranker_trains_on_the_state_after_an_undo(shell, qtbot, tmp_path) -> None:
    from tether.project.features import compute_features
    from tether.project.gbranking import weighted_training_set

    project, keys = _loaded(shell, tmp_path)
    compute_features(project.path)
    _press(qtbot, shell, "Key_Space")
    shell.molecule_list.setCurrentRow(1)
    _press(qtbot, shell, "Key_Backspace")
    trained = weighted_training_set(project.path)
    assert len(trained.molecule_ids) == 2

    assert shell.undo()  # the reject of molecule 1

    trained = weighted_training_set(project.path)
    assert len(trained.molecule_ids) == 1
    assert trained.y.tolist() == [True]


@_needs_qt
@pytest.mark.gui
def test_the_undo_list_is_empty_after_the_project_is_closed_and_opened(
    shell, qtbot, tmp_path
) -> None:
    project, keys = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_Space")
    _press(qtbot, shell, "Key_1")
    assert shell.undo_stack.undo_count == 2

    assert shell.close_project()
    assert shell.undo_stack.undo_count == 0
    assert shell.load_project(project.path) is not None

    assert shell.undo_stack.undo_count == 0 and shell.undo_stack.redo_count == 0
    assert not shell.undo_action.isEnabled()
    assert not shell.undo()
    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.ACCEPT


@_needs_qt
@pytest.mark.gui
def test_an_undo_is_refused_with_a_message_when_the_project_is_read_only(
    shell, qtbot, tmp_path
) -> None:
    from tether.project import lock

    project, keys = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_Space")
    labels_before = labels.read_labels(project.path)
    other = lock.LockIdentity(host="OTHER-HOST", user="other", pid=999)
    lock.acquire(project.path, identity=other, steal=True)

    assert not shell.undo()

    assert shell.status_message.startswith("Undo unavailable: project is read-only: ")
    assert shell.read_only_reason is not None
    assert shell.undo_stack.undo_count == 0  # a lost lock ends the session's list
    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.ACCEPT
    assert labels.read_labels(project.path).shape == labels_before.shape


@_needs_qt
@pytest.mark.gui
def test_an_action_that_changes_nothing_is_not_put_on_the_undo_list(shell, qtbot, tmp_path) -> None:
    _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_0")  # already uncategorized
    _press(qtbot, shell, "Key_R")  # the window is already the whole trace
    assert shell.undo_stack.undo_count == 0


@_needs_qt
@pytest.mark.gui
def test_an_undo_the_store_refuses_is_dropped_so_earlier_steps_still_undo(
    shell, qtbot, tmp_path
) -> None:
    project, keys = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_Space")
    _press(qtbot, shell, "Key_1")  # "bound"
    _press(qtbot, shell, "Key_2")  # "unbound"; its undo writes "bound" back
    condition_id = next(iter(project.molecules_by_condition()))
    project.remove_category(condition_id, "bound")

    assert not shell.undo()
    assert "failed" in shell.status_message
    assert shell.undo_stack.undo_count == 2

    assert shell.undo()  # the category assign: back to uncategorized
    assert shell.undo()  # the accept
    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.UNCURATED


@_needs_qt
@pytest.mark.gui
def test_an_undone_un_reject_gets_back_the_reason_of_the_reject(shell, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path)
    project.reject(keys[0], reason="threshold", value=0.25, labeler="ana")
    rejected = _state(project.path, keys[0])
    shell.unreject_button.click()
    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.UNCURATED

    assert shell.undo(), shell.status_message

    assert _state(project.path, keys[0]) == rejected
    record = labels.reject_record_of(project.path, keys[0])
    assert _text(record["reason"]) == "threshold" and float(record["value"]) == 0.25


@_needs_qt
@pytest.mark.gui
def test_an_undone_accept_of_a_carried_label_holds_it_out_of_training_again(
    shell, qtbot, tmp_path
) -> None:
    from tether.services.curation import curation_snapshot

    project, key = _carried_store(tmp_path)
    waiting = curation_snapshot(project, key)
    assert shell.load_project(project) is not None
    shell.molecule_list.setCurrentRow(
        next(i for i, t in enumerate(shell._traces) if t.molecule_key == key)
    )
    _press(qtbot, shell, "Key_Space")
    assert not curation_snapshot(project, key).pending

    assert shell.undo(), shell.status_message

    assert curation_snapshot(project, key) == waiting


@_needs_qt
@pytest.mark.gui
@pytest.mark.parametrize("key_name", ["Key_R", "Key_Equal", "Key_P"])
def test_an_undo_gives_each_row_of_a_shared_key_its_own_window_back(
    shell, qtbot, tmp_path, key_name
) -> None:
    # Two rows share a molecule_key with different frame ranges and windows. A window
    # key writes one request to both; the undo must give each row its own window back.
    project, keys = _store(tmp_path, windows=[(0, 30), (20, 45), (0, N_FRAMES)])
    with h5py.File(project.path, "r+") as f:
        table = f["molecules"]["table"][:]
        table["molecule_key"][1] = table["molecule_key"][0]
        table["frame_range"][0] = (0, 30)
        table["frame_range"][1] = (20, 50)
        f["molecules"]["table"][:] = table
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    before = _state(project.path, keys[0])
    assert before[2] == ((0, 30), (20, 45))

    _press(qtbot, shell, key_name)
    after = _state(project.path, keys[0])
    assert after != before, shell.status_message

    assert shell.undo(), shell.status_message
    assert _state(project.path, keys[0]) == before
    assert shell.redo(), shell.status_message
    assert _state(project.path, keys[0]) == after


@_needs_qt
@pytest.mark.gui
def test_an_action_that_cannot_be_undone_still_ends_the_redo_list(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    from tether.gui import shell as shell_mod

    project, keys = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_Space")
    assert shell.undo()
    assert shell.redo_action.isEnabled()

    def unreadable(*args, **kwargs):
        raise OSError("snapshot read failed")

    monkeypatch.setattr(shell_mod, "curation_snapshot", unreadable)
    _press(qtbot, shell, "Key_Backspace")  # stored, but its earlier state is unreadable
    monkeypatch.undo()

    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.REJECT
    assert shell.undo_stack.redo_count == 0 and not shell.redo_action.isEnabled()
    assert not shell.redo()
    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.REJECT


@_needs_qt
@pytest.mark.gui
def test_a_lock_lost_just_before_the_undo_write_refuses_it_with_a_message(
    shell, qtbot, tmp_path, monkeypatch
) -> None:
    from tether.project import lock

    project, keys = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_Space")
    gate = shell._writable_project
    other = lock.LockIdentity(host="OTHER-HOST", user="other", pid=999)

    def gate_then_lose_the_lock(verb):
        writable = gate(verb)
        lock.acquire(project.path, identity=other, steal=True)
        return writable

    monkeypatch.setattr(shell, "_writable_project", gate_then_lose_the_lock)

    assert not shell.undo()

    assert shell.status_message.startswith("Undo accept ")
    assert "failed" in shell.status_message
    assert shell.read_only_reason is not None
    assert shell.undo_stack.undo_count == 0
    assert labels.curation_label_of(project.path, keys[0]) == labels.CurationLabel.ACCEPT
