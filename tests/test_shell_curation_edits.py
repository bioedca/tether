# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The category, analysis-window and photobleach keys write the project (#359, PRD §7.3).

Each key goes through the #358 setters, and each test reads the value back **from the
store** after the keystroke rather than trusting the status text:

1. ``1``-``9`` and the overflow picker store the condition's category of that class;
   ``0`` clears it. A class the condition does not hold is refused.
2. ``-``/``=`` nudge the window start and ``[``/``]`` the end, ``R`` restores
   ``frame_range``, ``P`` ends the window at the summed-intensity photobleach.
3. Every one of them is refused while background idealization runs and on a
   read-only project, with the banner's reason, and then changes nothing.
4. A window edit moves the dock's window and redraws the idealization overlay inside it.

All ``@pytest.mark.gui``, headless, over real ``.tether`` stores.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

#: Frames per seeded trace, and the frame where molecule 0's summed intensity bleaches.
N_FRAMES = 60
BLEACH = 37
#: Twelve categories, so classes 10-12 need the overflow picker.
CATEGORIES = [f"class {i}" for i in range(1, 13)]


def _store(tmp_path, *, windows=None):
    """A real ``.tether`` with three molecules and a 12-name category list.

    Molecule 0 bleaches in both channels at frame ``BLEACH`` (background-subtracted
    traces drop to zero there), so ``P`` has a step to find.
    """
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(3)
    donor = rng.normal(600.0, 15.0, size=(3, N_FRAMES))
    acceptor = rng.normal(400.0, 15.0, size=(3, N_FRAMES))
    donor[0, BLEACH:] = rng.normal(0.0, 15.0, size=N_FRAMES - BLEACH)
    acceptor[0, BLEACH:] = rng.normal(0.0, 15.0, size=N_FRAMES - BLEACH)
    project, keys = build_store_with_channels(
        tmp_path, donor, acceptor, windows=windows, name="edits.tether"
    )
    project.sync_conditions()
    for condition_id in project.molecules_by_condition():
        project.set_category_list(condition_id, CATEGORIES)
    return project, keys


def _row(project, key):
    from tether.imaging.extract import read_molecules

    table = read_molecules(project.path)
    keys = [k.decode() if isinstance(k, bytes) else str(k) for k in table["molecule_key"]]
    return table[keys.index(key)]


def _category(project, key) -> str:
    value = _row(project, key)["category"]
    return value.decode() if isinstance(value, bytes) else str(value)


def _window(project, key) -> tuple[int, int]:
    window = _row(project, key)["analysis_window"]
    return int(window[0]), int(window[1])


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell(advance_after_curation=False)
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _press(qtbot, shell, key_name: str) -> None:
    from pyqtgraph.Qt import QtCore

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))


def _loaded(shell, tmp_path, **kwargs):
    project, keys = _store(tmp_path, **kwargs)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    return project, keys


# --- categories ------------------------------------------------------------------


@pytest.mark.parametrize(("key_name", "integer_class"), [("Key_1", 1), ("Key_4", 4), ("Key_9", 9)])
def test_number_key_stores_that_class_of_the_condition_list(
    shell, qtbot, tmp_path, key_name, integer_class
) -> None:
    project, keys = _loaded(shell, tmp_path)

    _press(qtbot, shell, key_name)

    assert _category(project, keys[0]) == CATEGORIES[integer_class - 1]
    assert _category(project, keys[1]) == ""
    assert shell.molecule_list.currentRow() == 0
    assert shell.status_message.startswith(f"Category {integer_class} (")


def test_zero_clears_the_category_back_to_uncategorized(shell, qtbot, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path)
    _press(qtbot, shell, "Key_2")
    assert _category(project, keys[0]) == "class 2"

    _press(qtbot, shell, "Key_0")

    assert _category(project, keys[0]) == ""
    assert shell.status_message.startswith("Category cleared")


def test_overflow_picker_offers_and_stores_classes_beyond_nine(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.gui import shell as shell_mod

    project, keys = _loaded(shell, tmp_path)
    offered = []

    def choose_eleven(picker):
        offered.append(picker.overflow_classes)
        return 11

    monkeypatch.setattr(shell_mod.OverflowCategoryPicker, "exec", choose_eleven)
    shell.show_overflow_picker()

    assert offered == [[10, 11, 12]]
    assert _category(project, keys[0]) == "class 11"


def test_overflow_picker_lists_the_selected_molecules_condition(shell, tmp_path) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.shell import OverflowCategoryPicker

    _loaded(shell, tmp_path)
    # The picker reads the project's list for the molecule, not the constructor default.
    assert shell._picker_categories() == CATEGORIES
    picker = OverflowCategoryPicker(shell._picker_categories(), parent=shell.window)
    shown = picker.dialog.findChild(QtWidgets.QListWidget)
    assert [shown.item(i).text() for i in range(shown.count())] == [
        "10: class 10",
        "11: class 11",
        "12: class 12",
    ]


def test_a_class_the_condition_lacks_is_refused(shell, tmp_path) -> None:
    project, keys = _store(tmp_path)
    for condition_id in project.molecules_by_condition():
        project.set_category_list(condition_id, ["only", "two"])
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)

    shell._assign_category(3)

    assert _category(project, keys[0]) == ""
    assert "Category 3 assignment unavailable" in shell.status_message
    assert "2 categories" in shell.status_message


# --- analysis window -------------------------------------------------------------


@pytest.mark.parametrize(
    ("key_name", "expected"),
    [
        ("Key_Equal", (11, 50)),  # start +1
        ("Key_Minus", (9, 50)),  # start -1
        ("Key_BracketRight", (10, 51)),  # end +1
        ("Key_BracketLeft", (10, 49)),  # end -1
    ],
)
def test_window_keys_nudge_one_bound_in_the_store(
    shell, qtbot, tmp_path, key_name, expected
) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(10, 50)] * 3)

    _press(qtbot, shell, key_name)

    assert _window(project, keys[0]) == expected
    assert _window(project, keys[1]) == (10, 50)
    assert f"[{expected[0]}, {expected[1]})" in shell.status_message


def test_each_keystroke_is_one_stored_step(shell, qtbot, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(10, 50)] * 3)
    for _ in range(3):
        _press(qtbot, shell, "Key_Equal")
    _press(qtbot, shell, "Key_BracketLeft")
    assert _window(project, keys[0]) == (13, 49)


def test_window_nudge_stops_at_the_trace_and_keeps_a_frame(shell, qtbot, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(0, N_FRAMES), (5, 6), (0, N_FRAMES)])

    _press(qtbot, shell, "Key_Minus")  # start already at the first frame
    assert _window(project, keys[0]) == (0, N_FRAMES)
    assert "unchanged" in shell.status_message
    _press(qtbot, shell, "Key_BracketRight")  # end already at the last frame
    assert _window(project, keys[0]) == (0, N_FRAMES)

    shell.molecule_list.setCurrentRow(1)
    _press(qtbot, shell, "Key_Equal")  # would empty a one-frame window
    assert _window(project, keys[1]) == (5, 6)
    assert "at least one frame" in shell.status_message


def test_r_restores_the_frame_range(shell, qtbot, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(10, 50)] * 3)

    _press(qtbot, shell, "Key_R")

    assert _window(project, keys[0]) == (0, N_FRAMES)
    assert _window(project, keys[1]) == (10, 50)


def test_p_ends_the_window_at_the_summed_photobleach(shell, qtbot, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(4, N_FRAMES), (4, N_FRAMES), (0, 8)])

    _press(qtbot, shell, "Key_P")

    start, stop = _window(project, keys[0])
    assert start == 4  # P moves only the end, as tMAVEN's does
    assert abs(stop - BLEACH) <= 2  # the §11.2 known-step tolerance
    assert shell.status_message.startswith(f"Photobleach at frame {stop}")

    shell.molecule_list.setCurrentRow(1)  # no bleach: the end goes to the trace end
    _press(qtbot, shell, "Key_P")
    assert _window(project, keys[1]) == (4, N_FRAMES)
    assert shell.status_message.startswith("No photobleach step found")


def test_window_edit_is_audited_with_the_curator(qapp, qtbot, tmp_path) -> None:
    from tether.gui.shell import TetherShell

    shell = TetherShell(advance_after_curation=False)
    qtbot.addWidget(shell.window)
    try:
        shell._curator_name = "ada"
        project, keys = _loaded(shell, tmp_path, windows=[(10, 50)] * 3)
        _press(qtbot, shell, "Key_Equal")
        _press(qtbot, shell, "Key_3")
    finally:
        shell.close()
    audit = project.read_molecule_audit()
    events = [e.decode() if isinstance(e, bytes) else str(e) for e in audit["event"]]
    labelers = {v.decode() if isinstance(v, bytes) else str(v) for v in audit["labeler"]}
    assert events == ["set_analysis_window", "set_category"]
    assert labelers == {"ada"}


# --- the dock follows the stored window ---------------------------------------------


def test_window_edit_moves_the_dock_bounds_and_clips_the_overlay(shell, qtbot, tmp_path) -> None:
    project, keys = _loaded(shell, tmp_path, windows=[(10, 50)] * 3)
    dock = shell.trace_dock
    dock.set_time_mode("frames")  # positions below are frame edges
    assert dock.analysis_window == (10, 50)
    assert [line.isVisible() for line in dock.window_lines] == [True, True]

    overlay = np.full(N_FRAMES, np.nan)
    overlay[10:50] = 0.5
    dock.set_idealization(overlay)
    x, _y = dock.idealization_curve.getData()
    assert x[0] == pytest.approx(9.5) and x[-1] == pytest.approx(49.5)

    for _ in range(5):
        _press(qtbot, shell, "Key_Equal")
    _press(qtbot, shell, "Key_BracketLeft")

    stored = _window(project, keys[0])
    assert stored == (15, 49)
    assert dock.analysis_window == stored  # frame_range starts at 0 in this store
    assert [line.value() for line in dock.window_lines] == pytest.approx([14.5, 48.5])
    x, y = dock.idealization_curve.getData()
    assert x[0] == pytest.approx(14.5) and x[-1] == pytest.approx(48.5)
    assert y.size == stored[1] - stored[0]

    # The edited window is kept for the molecule: leaving and returning shows it again.
    shell.molecule_list.setCurrentRow(1)
    assert dock.analysis_window == (10, 50)
    shell.molecule_list.setCurrentRow(0)
    assert dock.analysis_window == stored


# --- the write gates --------------------------------------------------------------

_EDIT_KEYS = [
    "Key_1",
    "Key_0",
    "Key_Minus",
    "Key_Equal",
    "Key_BracketLeft",
    "Key_BracketRight",
    "Key_R",
    "Key_P",
]


@pytest.mark.parametrize("key_name", _EDIT_KEYS)
def test_edits_are_refused_while_idealization_runs(shell, qtbot, tmp_path, key_name) -> None:
    import threading

    project, keys = _loaded(shell, tmp_path, windows=[(10, 50)] * 3)
    started = threading.Event()
    gate = threading.Event()

    def blocking_idealizer(_molecule_key):
        started.set()
        gate.wait(timeout=5.0)
        return np.full(N_FRAMES, 0.5)

    shell._idealizer = blocking_idealizer  # an in-flight fit owns the HDF5
    try:
        _press(qtbot, shell, "Key_I")
        qtbot.waitUntil(started.is_set, timeout=2000)
        _press(qtbot, shell, key_name)
        refused = shell.status_message
    finally:
        gate.set()
        qtbot.waitUntil(lambda: not shell.is_idealizing, timeout=5000)

    assert "unavailable: Idealize in progress" in refused
    assert _window(project, keys[0]) == (10, 50)
    assert _category(project, keys[0]) == ""
    assert project.read_molecule_audit().shape[0] == 0


@pytest.mark.parametrize("key_name", _EDIT_KEYS)
def test_edits_are_refused_on_a_read_only_project(qapp, qtbot, tmp_path, key_name) -> None:
    from tether.gui.shell import TetherShell

    project, keys = _store(tmp_path, windows=[(10, 50)] * 3)
    writer = TetherShell()
    reader = TetherShell()
    qtbot.addWidget(writer.window)
    qtbot.addWidget(reader.window)
    try:
        assert writer.load_project(project.path) is not None
        assert reader.load_project(project.path) is not None
        reason = reader.read_only_reason
        assert reason is not None
        reader.molecule_list.setCurrentRow(0)

        _press(qtbot, reader, key_name)

        assert f"unavailable: project is read-only: {reason}" in reader.status_message
    finally:
        reader.close()
        writer.close()
    assert _window(project, keys[0]) == (10, 50)
    assert _category(project, keys[0]) == ""
    assert project.read_molecule_audit().shape[0] == 0
