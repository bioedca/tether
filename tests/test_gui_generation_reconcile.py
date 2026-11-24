# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The reconciliation page of a new trace generation, in the application (#923).

The page lists the carried, new and lost molecules of each movie extracted again, filters
to the carried ones, confirms them one by one or as a group, and opens a molecule in the
trace view. The Qt-free lists and the confirmation are covered by
``tests/test_generation_reconcile.py``; this module drives the page headless with
``pytest-qt``, first alone and then through the shell's Curation menu.
"""

from __future__ import annotations

import importlib.util

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]

from _generation_store import LOW, T0, first_generation, reextract  # noqa: E402
from tether.io.generations import carried_molecule_keys  # noqa: E402
from tether.project import generation_reconcile as gr  # noqa: E402


def _row_texts(page, key):
    for item in page._items():  # noqa: SLF001 - the rows the tree shows
        if page._data(item)[1] == key:  # noqa: SLF001
            return [item.text(c) for c in range(item.columnCount())]
    raise AssertionError(f"no row for {key}")


@pytest.fixture
def reextracted(tmp_path):
    project, movie, stayed, moved = first_generation(tmp_path)
    reextract(project, movie, LOW)
    return project, stayed, moved


def test_the_page_lists_the_three_groups_and_filters_to_the_carried(qapp, qtbot, reextracted):
    from tether.gui.generation_reconcile import GenerationReconcilePage

    project, stayed, moved = reextracted
    confirmed, opened = [], []

    def confirm(keys):
        confirmed.append(list(keys))
        return gr.confirm_carried(project, keys, labeler="reviewer")

    page = GenerationReconcilePage(
        gr.reconcile_project(project),
        confirm=confirm,
        open_molecule=lambda key: opened.append(key) or True,
        reload=lambda: gr.reconcile_project(project),
    )
    qtbot.addWidget(page.dialog)
    page.show()

    rec = page.reconciliations[0]
    new_keys = [m.molecule_key for m in rec.new]
    assert page.shown_keys("carried") == [stayed]
    assert page.shown_keys("new") == new_keys
    assert page.shown_keys("lost") == [moved]
    carried_row = _row_texts(page, stayed)
    assert carried_row[1:4] == ["accepted", "folded", "original, " + T0[:16].replace("T", " ")]
    assert carried_row[4] == "carried; waiting for confirmation"
    assert _row_texts(page, moved)[1] == "rejected"
    assert "1 carried (1 waiting for confirmation), 2 new, 1 lost" in page._summary.text()  # noqa: SLF001

    page.carried_only.setChecked(True)
    assert page.shown_keys() == [stayed]
    page.carried_only.setChecked(False)
    assert len(page.shown_keys()) == 4

    # A lost molecule is not in the trace view; a new one opens.
    page.select_keys([moved])
    assert not page.open_selected() and opened == []
    assert "lost molecule" in page.status_text
    page.select_keys([new_keys[0]])
    assert page.open_selected() and opened == [new_keys[0]]

    # Confirm selected takes only the carried molecules that wait.
    page.select_keys([stayed, new_keys[0]])
    result = page.confirm_selected()
    assert confirmed == [[stayed]] and result.confirmed == (stayed,)
    assert carried_molecule_keys(project) == set()
    assert _row_texts(page, stayed)[4].startswith("confirmed by reviewer, ")
    assert page.waiting_keys() == []
    assert page.confirm_all() is None and confirmed == [[stayed]]


def test_confirm_all_confirms_every_waiting_molecule(qapp, qtbot, reextracted) -> None:
    from tether.gui.generation_reconcile import GenerationReconcilePage

    project, stayed, _moved = reextracted
    page = GenerationReconcilePage(
        gr.reconcile_project(project),
        confirm=lambda keys: gr.confirm_carried(project, keys, labeler="group"),
        open_molecule=lambda _key: True,
    )
    qtbot.addWidget(page.dialog)
    assert page.waiting_keys() == [stayed]
    assert page.confirm_all().confirmed == (stayed,)
    assert carried_molecule_keys(project) == set()


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def test_the_curation_menu_opens_the_page_and_the_shell_writes_the_confirmation(
    shell, reextracted
) -> None:
    project, stayed, _moved = reextracted
    assert shell.show_generation_reconcile() is None  # no project is open
    assert shell.load_project(project) is not None
    shell._act_reconcile_generation.trigger()  # noqa: SLF001 - Curation menu entry
    page = shell.generation_page
    assert page is not None and page.dialog.isVisible()
    assert page.shown_keys("carried") == [stayed]

    # Open selects the molecule in the trace view.
    shell.molecule_list.setCurrentRow(-1)
    page.select_keys([stayed])
    assert page.open_selected()
    row = shell.molecule_list.currentRow()
    assert shell._traces[row].molecule_key == stayed  # noqa: SLF001

    # The shell confirms under its session lock, and the page reads the lists again.
    page.select_keys([stayed])
    assert page.confirm_selected().confirmed == (stayed,)
    assert carried_molecule_keys(project) == set()
    assert page.waiting_keys() == []

    # Closing the project closes the page.
    assert shell.close_project()
    assert shell.generation_page is None


def test_a_reject_in_the_trace_view_updates_the_open_page(shell, qtbot, reextracted) -> None:
    from pyqtgraph.Qt import QtCore

    project, stayed, _moved = reextracted
    assert shell.load_project(project) is not None
    page = shell.show_generation_reconcile()
    assert page.waiting_keys() == [stayed]
    page.select_keys([stayed])
    assert page.open_selected()

    qtbot.keyClick(shell.molecule_list, QtCore.Qt.Key.Key_Backspace)  # reject
    shell.refresh_readiness()  # what the write's debounce timer runs

    assert page.waiting_keys() == []
    assert _row_texts(page, stayed)[1] == "rejected"


def test_a_read_only_shell_refuses_the_confirmation(shell, reextracted, monkeypatch) -> None:
    project, stayed, _moved = reextracted
    assert shell.load_project(project) is not None
    page = shell.show_generation_reconcile()
    monkeypatch.setattr(shell, "_curation_project", None)
    monkeypatch.setattr(shell, "_refresh_project_lock", lambda: None)
    page.select_keys([stayed])
    assert page.confirm_selected() is None
    assert "Nothing was confirmed" in page.status_text
    assert carried_molecule_keys(project) == {stayed}
