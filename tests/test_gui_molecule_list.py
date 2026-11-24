# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The molecule list shows each molecule's state, the counts, a filter and "next undecided" (#630).

PRD §7.3 and §7.5. The rules under test, one or more tests each:

1. [`molecule_states`][tether.services.curation.molecule_states] reads the curation state,
   the category and the idealization marks (fitted, stale) of every row.
2. A row shows its state as a mark and as words; the marks differ in shape, so a greyscale
   picture tells them apart (colour is never the only sign).
3. After an accept or a reject the row shows the new state with no new read of the store.
4. The header counts equal the counts in the store, for the movie and for the project.
5. Each filter lists exactly the molecules in that state or category, in the stored order;
   the arrow keys step through the shown rows only.
6. "Next undecided" (the key U, or the Curation menu) selects the next undecided molecule
   in list order and says when none is left.
7. A list of 5,000 molecules fills, and steps a row, within the §11.2 budget.
8. The filter and "Next undecided" say which step is missing while there are no traces.

All ``@pytest.mark.gui`` tests run headless over real round-trip ``.tether`` stores.
"""

from __future__ import annotations

import importlib.util
import time

import numpy as np
import pytest

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


def _store(tmp_path, *, n=5, t=12, seed=3, name="list.tether"):
    """A real round-trip ``.tether`` with no fit → ``(Project, keys)``."""
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(seed)
    donor = rng.uniform(400.0, 800.0, size=(n, t))
    acceptor = rng.uniform(200.0, 600.0, size=(n, t))
    return build_store_with_channels(tmp_path, donor, acceptor, name=name)


def _shell(qtbot, **kwargs):
    from tether.gui.shell import TetherShell

    shell = TetherShell(**kwargs)
    qtbot.addWidget(shell.window)
    return shell


@pytest.fixture
def shell(qapp, qtbot):
    s = _shell(qtbot)
    yield s
    s.close()


def _texts(shell) -> list[str]:
    lst = shell.molecule_list
    return [lst.item(i).text() for i in range(lst.count())]


def _shown_rows(shell) -> list[int]:
    lst = shell.molecule_list
    return [i for i in range(lst.count()) if not lst.isRowHidden(i)]


def _press(qtbot, shell, key_name: str) -> None:
    from pyqtgraph.Qt import QtCore

    qtbot.keyClick(shell.molecule_list, getattr(QtCore.Qt.Key, key_name))


# --- 1. the read --------------------------------------------------------------------------


def test_the_read_gives_state_category_and_fit_marks(tmp_path) -> None:
    from _analysis_store import build_store_with_model
    from tether.services.curation import ACCEPTED, REJECTED, UNDECIDED, molecule_states

    project, keys = _store(tmp_path)
    project.sync_conditions()
    (condition_id,) = project.molecules_by_condition()
    project.set_category_list(condition_id, ["bound", "free"])
    project.accept(keys[0])
    project.reject(keys[1])
    project.set_category(keys[2], "free")

    states = molecule_states(project.path)
    assert [s.molecule_key for s in states] == keys
    assert [s.row for s in states] == list(range(len(keys)))
    assert [s.curation for s in states] == [ACCEPTED, REJECTED] + [UNDECIDED] * 3
    assert [s.category for s in states] == ["", "", "free", "", ""]
    assert not any(s.idealized or s.stale for s in states)
    assert len({s.movie_id for s in states}) == 1

    # A fit marks every row it holds; a row whose inputs changed since is stale.
    fitted, _ = build_store_with_model(
        tmp_path,
        np.zeros((3, 10), dtype="int64"),
        np.array([0.5]),
        stale=[False, True, False],
        name="fit.tether",
    )
    marks = [(s.idealized, s.stale) for s in molecule_states(fitted.path)]
    assert marks == [(True, False), (True, True), (True, False)]


def test_row_text_names_the_state_the_category_and_the_fit() -> None:
    from tether.gui.molecule_list import row_text
    from tether.services.curation import ACCEPTED, MoleculeState

    plain = MoleculeState(0, "k", "m", ACCEPTED)
    assert row_text("mol-0", None) == "mol-0"
    assert row_text("mol-0", plain) == "mol-0 · Accepted"
    marked = MoleculeState(0, "k", "m", ACCEPTED, "bound", idealized=True, stale=True)
    assert row_text("mol-0", marked) == "mol-0 · Accepted · category bound · idealized, stale"


# --- 2. never by colour alone ----------------------------------------------------------------


@pytest.mark.gui
@_needs_qt
def test_the_state_marks_differ_in_a_greyscale_picture(qapp) -> None:
    from pyqtgraph.Qt import QtGui

    from tether.gui import theme
    from tether.gui.molecule_list import state_icon
    from tether.services.curation import CURATION_STATES

    for name in ("light", "dark"):
        active = theme.theme(name)
        pictures = []
        for curation in CURATION_STATES:
            image = state_icon(curation, active).pixmap(32, 32).toImage()
            grey = image.convertToFormat(QtGui.QImage.Format.Format_Grayscale8)
            # Where the mark is drawn, with no colour: the alpha of each pixel.
            mask = tuple(image.pixelColor(x, y).alpha() > 128 for y in range(32) for x in range(32))
            pictures.append((mask, bytes(grey.constBits())))
        masks = [mask for mask, _ in pictures]
        greys = [grey for _, grey in pictures]
        assert len(set(masks)) == len(CURATION_STATES), f"{name}: two marks share a shape"
        assert len(set(greys)) == len(CURATION_STATES), f"{name}: two marks look alike in grey"


# --- 3 and 4. a decision shows at once, and the counts are the store's ------------------------


@pytest.mark.gui
@_needs_qt
def test_a_decision_shows_in_its_row_without_a_reread(shell, qtbot, tmp_path, monkeypatch):
    import tether.gui.shell as shell_module

    reads = []
    real = shell_module.molecule_states
    monkeypatch.setattr(shell_module, "molecule_states", lambda p: reads.append(p) or real(p))
    project, keys = _store(tmp_path, n=4)
    assert shell.load_project(project.path) is not None
    assert len(reads) == 1
    assert all(text.endswith(" · Undecided") for text in _texts(shell))
    shell.molecule_list.setCurrentRow(0)

    _press(qtbot, shell, "Key_Space")  # accept row 0, then row 1 is selected
    _press(qtbot, shell, "Key_Backspace")  # reject row 1

    assert _texts(shell)[:2] == ["mol-0 · Accepted", "mol-1 · Rejected"]
    assert project.curation_label(keys[0]) == 1 and project.curation_label(keys[1]) == -1
    assert len(reads) == 1, "an accept or a reject must not read every row again"
    # The icon follows the state too.
    view = shell.molecule_view
    assert view.state(0).curation == "accepted" and view.state(1).curation == "rejected"


@pytest.mark.gui
@_needs_qt
def test_the_header_counts_are_the_store_counts(shell, qtbot, tmp_path) -> None:
    from tether.project.labels import curation_labels

    project, keys = _store(tmp_path, n=5)
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)
    _press(qtbot, shell, "Key_Space")
    _press(qtbot, shell, "Key_Space")
    _press(qtbot, shell, "Key_Backspace")

    labels = list(curation_labels(project.path).values())
    expected = (
        f"{labels.count(1)} accepted, {labels.count(-1)} rejected, "
        f"{labels.count(0)} undecided of {len(labels)}"
    )
    assert expected == "2 accepted, 1 rejected, 2 undecided of 5"
    header = shell.molecule_view.header.text().splitlines()
    assert header[-1] == f"Project: {expected}"
    # One movie: its count is the project's, under the movie's name.
    assert header[0].startswith("Movie ") and header[0].endswith(f": {expected}")


@_needs_qt
def test_the_header_counts_the_movie_of_the_selected_row(qapp) -> None:
    from pyqtgraph.Qt import QtWidgets

    from tether.gui.molecule_list import MoleculeListView
    from tether.services.curation import ACCEPTED, REJECTED, UNDECIDED, MoleculeState

    lst = QtWidgets.QListWidget()
    view = MoleculeListView(lst, movie_name=lambda movie_id: movie_id.upper())
    try:
        states = [
            MoleculeState(0, "a", "m1", ACCEPTED),
            MoleculeState(1, "b", "m1", UNDECIDED),
            MoleculeState(2, "c", "m2", REJECTED),
        ]
        view.populate(["a", "b", "c"], states)
        assert view.header_text(2) == (
            "Movie M2: 0 accepted, 1 rejected, 0 undecided of 1\n"
            "Project: 1 accepted, 1 rejected, 1 undecided of 3"
        )
        assert view.header_text(0).splitlines()[0] == (
            "Movie M1: 1 accepted, 0 rejected, 1 undecided of 2"
        )
        # No store, no states: no counts to show.
        view.populate(["x"], [])
        assert view.header_text(0) == ""
    finally:
        view.close()


# --- 5. the filter -------------------------------------------------------------------------


@pytest.mark.gui
@_needs_qt
def test_each_filter_lists_exactly_its_molecules_in_the_stored_order(shell, tmp_path) -> None:
    from tether.services.curation import molecule_states

    project, keys = _store(tmp_path, n=6)
    project.sync_conditions()
    (condition_id,) = project.molecules_by_condition()
    project.set_category_list(condition_id, ["bound", "free"])
    project.accept(keys[0])
    project.accept(keys[3])
    project.reject(keys[4])
    project.set_category(keys[1], "bound")
    project.set_category(keys[5], "bound")
    assert shell.load_project(project.path) is not None
    names = [text.split(" · ")[0] for text in _texts(shell)]

    view = shell.molecule_view
    assert view.filter_keys() == [
        "all",
        "undecided",
        "accepted",
        "rejected",
        "category:bound",
    ]
    stored = molecule_states(project.path)
    for key in view.filter_keys():
        view.set_filter(key)
        if key == "all":
            wanted = list(range(len(keys)))
        elif key.startswith("category:"):
            wanted = [s.row for s in stored if s.category == key.split(":", 1)[1]]
        else:
            wanted = [s.row for s in stored if s.curation == key]
        assert _shown_rows(shell) == wanted, key
        # The filter hides rows; it never moves one.
        assert [text.split(" · ")[0] for text in _texts(shell)] == names


@pytest.mark.gui
@_needs_qt
def test_the_arrow_keys_step_through_the_shown_rows_only(shell, qtbot, tmp_path) -> None:
    project, keys = _store(tmp_path, n=6)
    project.accept(keys[1])
    project.accept(keys[4])
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(1)

    shell.molecule_view.set_filter("accepted")
    _press(qtbot, shell, "Key_Down")
    assert shell.molecule_list.currentRow() == 4
    _press(qtbot, shell, "Key_Down")  # the last shown row: the selection stays
    assert shell.molecule_list.currentRow() == 4
    _press(qtbot, shell, "Key_Up")
    assert shell.molecule_list.currentRow() == 1


@pytest.mark.gui
@_needs_qt
def test_a_filter_that_hides_the_selection_moves_it_to_a_shown_row(shell, qtbot, tmp_path):
    from tether.project.labels import curation_labels

    project, keys = _store(tmp_path, n=4)
    project.reject(keys[2])
    assert shell.load_project(project.path) is not None
    shell.molecule_list.setCurrentRow(0)

    # A filter that shows nothing leaves nothing selected, so no key acts on a hidden row.
    shell.molecule_view.set_filter("accepted")
    assert shell.molecule_list.currentRow() == -1
    before = curation_labels(project.path)
    _press(qtbot, shell, "Key_Space")
    assert curation_labels(project.path) == before

    shell.molecule_view.set_filter("rejected")
    assert shell.molecule_list.currentRow() == -1  # nothing was selected to move
    shell.molecule_view.set_filter("all")
    shell.molecule_list.setCurrentRow(0)
    shell.molecule_view.set_filter("rejected")
    assert shell.molecule_list.currentRow() == 2
    assert shell.status_message == "Filter Rejected: 1 shown"

    # Under "Undecided" an accept hides its row; the next shown row is selected.
    shell.molecule_view.set_filter("undecided")
    shell.molecule_list.setCurrentRow(0)
    _press(qtbot, shell, "Key_Space")
    assert _shown_rows(shell) == [1, 3]
    assert shell.molecule_list.currentRow() == 1


@pytest.mark.gui
@_needs_qt
def test_a_new_category_adds_its_filter(shell, qtbot, tmp_path) -> None:
    project, keys = _store(tmp_path, n=3)
    project.sync_conditions()
    (condition_id,) = project.molecules_by_condition()
    project.set_category_list(condition_id, ["bound", "free"])
    assert shell.load_project(project.path) is not None
    assert "category:free" not in shell.molecule_view.filter_keys()
    shell.molecule_list.setCurrentRow(1)

    _press(qtbot, shell, "Key_2")  # class 2 is "free"

    assert _texts(shell)[1] == "mol-1 · Undecided · category free"
    shell.molecule_view.set_filter("category:free")
    assert _shown_rows(shell) == [1]


# --- 6. next undecided -------------------------------------------------------------------


@pytest.mark.gui
@_needs_qt
def test_next_undecided_follows_the_list_order_and_says_when_none_is_left(
    shell, qtbot, tmp_path
) -> None:
    from tether.gui.curation import Command, CurationAction

    project, keys = _store(tmp_path, n=5)
    for key in (keys[0], keys[2], keys[3]):
        project.accept(key)
    assert shell.load_project(project.path) is not None
    action = shell._act_next_undecided
    assert action.isEnabled()
    # Its key is the curation key U, so it acts only where the curation keys act (§7.3).
    assert shell.keymap.chords_for(Command(CurationAction.NEXT_UNDECIDED)) == [(ord("U"), 0)]
    shell.molecule_list.setCurrentRow(1)
    before = _texts(shell)

    _press(qtbot, shell, "Key_U")
    assert shell.molecule_list.currentRow() == 4
    action.trigger()  # the menu item; after the last row the search goes on from the top
    assert shell.molecule_list.currentRow() == 1
    assert shell.status_message.endswith("(from the top of the list)")
    assert _texts(shell) == before  # a navigation action never reorders the list

    # The filter hides the undecided rows: the status says so.
    shell.molecule_view.set_filter("accepted")
    assert shell.select_next_undecided() is None
    assert shell.status_message == "No undecided molecule is shown; the filter hides 2"

    shell.molecule_view.set_filter("all")
    for row in (1, 4):
        shell.molecule_list.setCurrentRow(row)
        _press(qtbot, shell, "Key_Space")
    assert shell.select_next_undecided() is None
    assert shell.status_message == "No undecided molecule is left"


# --- 7. responsiveness ---------------------------------------------------------------------


@pytest.mark.gui
@_needs_qt
def test_five_thousand_molecules_fill_and_step_within_the_budget(shell) -> None:
    from tether.gui.trace_dock import TraceView
    from tether.project.perf import PER_TRACE_LATENCY_BUDGET_S, min_runtime
    from tether.services.curation import CURATION_STATES, MoleculeState

    n = 5000
    rng = np.random.default_rng(7)
    traces = [
        TraceView(
            donor=rng.uniform(400, 800, 200),
            acceptor=rng.uniform(200, 600, 200),
            molecule_key=f"k{i}",
        )
        for i in range(n)
    ]
    states = [
        MoleculeState(i, f"k{i}", f"movie-{i % 10}", CURATION_STATES[i % 3], f"c{i % 4}")
        for i in range(n)
    ]
    shell.window.show()
    shell.set_molecules(traces[:2], states[:2])  # warm up the first render of the trace dock

    started = time.perf_counter()
    shell.set_molecules(traces, states)
    filled = time.perf_counter() - started
    assert shell.molecule_list.count() == n
    # The fill is once per opening; the PRD budget is for one render + navigate, so the
    # fill of a whole condition is held to ten of those.
    assert filled < 10 * PER_TRACE_LATENCY_BUDGET_S, f"filling {n} rows took {filled:.3f} s"

    cursor = {"row": 0}

    def step() -> None:
        cursor["row"] = (cursor["row"] + 1) % n
        shell.molecule_list.setCurrentRow(cursor["row"])
        shell.molecule_list.viewport().grab()

    per_step = min_runtime(step, repeats=12)
    assert per_step < PER_TRACE_LATENCY_BUDGET_S, f"one step took {per_step * 1000:.1f} ms"
    # A filter over the whole condition is one pass too.
    started = time.perf_counter()
    shell.molecule_view.set_filter("rejected")
    assert time.perf_counter() - started < 10 * PER_TRACE_LATENCY_BUDGET_S


# --- 8. the missing step -------------------------------------------------------------------


@pytest.mark.gui
@_needs_qt
def test_with_no_project_the_filter_and_next_undecided_name_the_missing_step(shell) -> None:
    from tether.gui.requirements import NO_PROJECT_TEXT, REQUIREMENT_PROPERTY

    box = shell.molecule_view.filter_box
    action = shell._act_next_undecided
    for control in (box, action):
        assert not control.isEnabled()
        assert control.property(REQUIREMENT_PROPERTY) == "traces"
        assert control.toolTip() == NO_PROJECT_TEXT
    assert shell.select_next_undecided() is None
    assert shell.status_message == "No undecided molecule is left"
