# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The next trace shows by itself after accept or reject (#631, PRD §7.3, §7.5, §8).

One keystroke stores the decision and selects the next listed molecule, while the
``advance_after_curation`` constructor argument (the preference until the settings
store lands) is on, which is the default. The rules under test, one or more tests each:

1. A stored accept or reject selects the next row in list order; a hidden row is
   skipped, so with a filter "next" is the next row the list shows.
2. A refused write (failed write, read-only project) leaves the selection in place.
3. Un-reject and the category keys never advance.
4. At the last row the selection stays and the status says the end was reached.
5. The list is never reordered.
6. The preference is stated in the keyboard cheat-sheet; off equals the old behavior.

Plus the §8 / §11.2 render+navigate budget for the advance step. All ``@pytest.mark.gui``,
headless, over real round-trip ``.tether`` stores.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]


def _store(tmp_path, *, n=3, t=12, seed=1):
    """A real round-trip ``.tether`` → ``(Project, keys)``."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(seed)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    return build_store_with_channels(tmp_path, donor, acceptor, name="adv.tether")


def _make_shell(qtbot, **kwargs):
    from tether.gui.shell import TetherShell

    shell = TetherShell(**kwargs)
    qtbot.addWidget(shell.window)
    return shell


@pytest.fixture
def shell(qapp, qtbot):
    s = _make_shell(qtbot)
    yield s
    s.close()


@pytest.fixture
def shell_off(qapp, qtbot):
    s = _make_shell(qtbot, advance_after_curation=False)
    yield s
    s.close()


def _list_texts(shell) -> list[str]:
    lst = shell.molecule_list
    # The name leads each row's text; the state marks after it change with a decision.
    return [lst.item(i).text().split(" · ")[0] for i in range(lst.count())]


def _label_rows(project):
    from tether.project.labels import read_labels

    return read_labels(project.path)


def test_advance_is_on_by_default(shell) -> None:
    assert shell.advance_after_curation is True


@pytest.mark.parametrize(
    ("key_name", "expected_label", "done"),
    [
        ("Key_Space", 1, "Accepted"),
        ("Key_Backspace", -1, "Rejected"),
        ("Key_Delete", -1, "Rejected"),
    ],
)
def test_one_keystroke_stores_the_decision_and_shows_the_next_trace(
    shell, qtbot, tmp_path, key_name, expected_label, done
) -> None:
    from pyqtgraph.Qt import QtCore

    project, keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    before = _list_texts(shell)

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))

    assert project.curation_label(keys[0]) == expected_label
    assert project.curation_label(keys[1]) == 0
    assert shell.molecule_list.currentRow() == 1
    # The dock now draws the next molecule, and the status keeps both facts.
    assert shell.trace_dock.trace is shell._traces[1]
    assert shell.status_message.startswith(f"{done} ")
    assert "(2/3)" in shell.status_message
    assert _list_texts(shell) == before  # never reordered (§7.5)


def test_preference_off_keeps_the_selection_as_before(shell_off, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    shell = shell_off
    assert shell.advance_after_curation is False
    project, keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    target = shell._traces[0].name or keys[0]

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert project.curation_label(keys[0]) == 1
    assert shell.molecule_list.currentRow() == 0
    assert shell.status_message == f"Accepted {target}"

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Backspace)
    assert project.curation_label(keys[0]) == -1
    assert shell.molecule_list.currentRow() == 0
    assert shell.status_message == f"Rejected {target}"


def test_failed_write_never_advances(shell, qtbot, tmp_path, monkeypatch) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project.core import Project

    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)

    def fail_write(_project, _molecule_key):
        raise OSError("simulated disk full")

    monkeypatch.setattr(Project, "accept", fail_write)
    monkeypatch.setattr(Project, "reject", fail_write)
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert shell.molecule_list.currentRow() == 0
    assert shell.status_message == "Accept failed: simulated disk full"

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Backspace)
    assert shell.molecule_list.currentRow() == 0
    assert shell.status_message == "Reject failed: simulated disk full"
    assert _label_rows(project).shape == (0,)


def test_read_only_project_never_advances(shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.project import lock
    from tether.project.lock import LockIdentity

    project, _keys = _store(tmp_path)
    held = lock.acquire(
        project.path, identity=LockIdentity(host="OTHER-HOST", user="other", pid=999)
    )
    try:
        assert shell.load_project(project.path) is not None
        shell.molecule_list.setCurrentRow(0)
        qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    finally:
        assert lock.release(project.path, held) is True

    assert shell.molecule_list.currentRow() == 0
    assert "Accept unavailable" in shell.status_message
    assert "read-only" in shell.status_message
    assert _label_rows(project).shape == (0,)


def test_unreject_does_not_advance(shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    project, keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    project.reject(keys[1])
    shell.molecule_list.setCurrentRow(1)

    qtbot.mouseClick(shell.unreject_button, QtCore.Qt.MouseButton.LeftButton)

    assert project.curation_label(keys[1]) == 0
    assert shell.molecule_list.currentRow() == 1
    assert shell.status_message == f"Un-rejected {shell._traces[1].name or keys[1]}"


@pytest.mark.parametrize("key_name", ["Key_1", "Key_9", "Key_0"])
def test_category_keys_do_not_advance(shell, qtbot, tmp_path, key_name) -> None:
    from pyqtgraph.Qt import QtCore

    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))

    assert shell.molecule_list.currentRow() == 1


def test_overflow_category_assignment_does_not_advance(shell, tmp_path) -> None:
    project, _keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)

    shell._assign_category(10)

    assert shell.molecule_list.currentRow() == 1


def test_last_row_stays_and_reports_the_end_of_the_list(shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    project, keys = _store(tmp_path)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(2)
    before = _list_texts(shell)

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)

    assert project.curation_label(keys[2]) == 1
    assert shell.molecule_list.currentRow() == 2
    assert shell.status_message.startswith("Accepted ")
    assert shell.status_message.endswith("; end of the list reached")
    assert _list_texts(shell) == before


def test_next_is_the_next_row_the_list_shows(shell, qtbot, tmp_path) -> None:
    from pyqtgraph.Qt import QtCore

    project, keys = _store(tmp_path, n=4)
    assert shell.load_project(project.path) is not None
    before = _list_texts(shell)
    # A filter hides rows from the list; the advance goes to the next row still shown.
    shell.molecule_list.setRowHidden(1, True)
    shell.molecule_list.setRowHidden(2, True)
    shell.molecule_list.setCurrentRow(0)

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Backspace)
    assert project.curation_label(keys[0]) == -1
    assert shell.molecule_list.currentRow() == 3
    assert _list_texts(shell) == before

    # With every row after row 0 hidden, the end is reached; no hidden row is selected.
    shell.molecule_list.setRowHidden(3, True)
    shell.molecule_list.setRowHidden(0, False)
    shell.molecule_list.setCurrentRow(0)
    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Space)
    assert shell.molecule_list.currentRow() == 0
    assert shell.status_message.endswith("; end of the list reached")


def test_cheatsheet_states_the_preference(qapp, qtbot) -> None:
    on = _make_shell(qtbot)
    off = _make_shell(qtbot, advance_after_curation=False)
    try:
        on.show_cheatsheet()
        off.show_cheatsheet()
        assert on.cheatsheet.note is not None
        assert "Advance after accept or reject: on" in on.cheatsheet.note
        assert "Advance after accept or reject: off" in off.cheatsheet.note
        assert "next trace" in on.cheatsheet.note
        # The note is visible in the dialog, under the key table.
        from pyqtgraph.Qt import QtWidgets

        labels = on.cheatsheet.dialog.findChildren(QtWidgets.QLabel)
        assert any(label.text() == on.cheatsheet.note for label in labels)
    finally:
        on.close()
        off.close()


def test_advance_render_navigate_stays_within_the_per_trace_budget(shell, qtbot, tmp_path) -> None:
    """The move + paint after a stored decision stays under the §11.2 ≈ 100 ms budget.

    Times the advance step alone (select the next row, which redraws the dock, then
    ``grab()`` to paint synchronously), on reference-length (~1700-frame) traces; the
    store write before it is I/O, not render+navigate.
    """
    from tether.project.perf import PER_TRACE_LATENCY_BUDGET_S, min_runtime

    project, _keys = _store(tmp_path, n=14, t=1740)
    assert shell.load_project(project.path) is not None
    shell.window.resize(1200, 800)
    shell.window.show()
    qtbot.waitExposed(shell.window)
    shell.molecule_list.setCurrentRow(0)
    dock = shell.trace_dock.widget
    assert not dock.grab().isNull()  # warm up the paint path

    def advance() -> None:
        shell._advance_from(shell.molecule_list.currentRow(), "Accepted")
        dock.grab()

    per_trace = min_runtime(advance, repeats=12)
    assert shell.molecule_list.currentRow() == 12
    assert per_trace < PER_TRACE_LATENCY_BUDGET_S, (
        f"advance render+navigate {per_trace * 1000:.1f} ms exceeds the "
        f"{PER_TRACE_LATENCY_BUDGET_S * 1000:.0f} ms budget"
    )
