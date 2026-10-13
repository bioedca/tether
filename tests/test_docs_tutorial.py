# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The guided first run (``docs/tutorial.md``, #176) still matches what Tether does.

The tutorial tells a learner what each step prints so they can check themselves. Those
lines are quoted from the program, so nothing but a test keeps them true. This module
runs the page's steps on the committed sample movie and asserts that every quoted line is
both on the page and what the program actually produces:

* the sample movie still regenerates byte for byte from ``scripts/make_tutorial_movie.py``,
  and stays under the 200 KB budget the page's size line quotes;
* ``tether extract``, run through :func:`tether.cli.main`, prints the summary line;
* the shell opens the project, browses, rejects, accepts and draws the population
  histogram with the status-bar lines the page quotes (``gui``; headless);
* the export call on the page returns the molecule count it shows.

Step 7 (idealize) needs the tMAVEN sidecar and is not run here; its quoted lines reuse
the molecule ID step 6 checks. Default tier only: no ``large``, ``sidecar`` or ``deep``
marker.
"""

from __future__ import annotations

import csv
import importlib.util
import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
pytest.importorskip("h5py")
pytest.importorskip("tifffile")

from tether.cli import main  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
PAGE = _ROOT / "docs" / "tutorial.md"
MOVIE = _ROOT / "docs" / "tutorial" / "data" / "tutorial_movie.tif"
SCRIPT = _ROOT / "scripts" / "make_tutorial_movie.py"

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

EXTRACT_LINE = "Extracted 3 molecule(s) -> tutorial.tether"
OPEN_LINE = "Opened tutorial.tether — 3 molecule(s)"
BROWSE_LINE = "Molecule mol-1 (2/3)"
REJECTED_KEY = "b4d11875527ee175c9c0e414d154db7a81a9bca669d3573f66cb1184a511a09d"
ACCEPTED_KEYS = (
    "463104a0a9ee0dca67d4eb2981655e11d79ac500d70fbb3cdc5c388de7a77392",
    "7336bcd251ba30a5eed2781067a1c4c3025f7d854f1ecf7c109b0859eadf4057",
)
HISTOGRAM_LINE = "Population histogram — 2 molecule(s), 48 frame(s)"


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _load_script():
    spec = importlib.util.spec_from_file_location("make_tutorial_movie", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> str:
    """Run step 3 exactly as the page does, from inside the tutorial folder."""
    shutil.copyfile(MOVIE, tmp_path / "tutorial_movie.tif")
    monkeypatch.chdir(tmp_path)
    assert main(["extract", "tutorial_movie.tif", "-o", "tutorial.tether"]) == 0
    return capsys.readouterr().out


def test_sample_movie_regenerates_byte_identical(capsys) -> None:
    assert _load_script().main(["--check"]) == 0, capsys.readouterr().err


def test_sample_movie_size_is_quoted_and_under_budget() -> None:
    size = MOVIE.stat().st_size
    assert size < 200_000
    assert f"The file is {size:,} bytes." in _page()


def test_page_builds_the_project_from_the_movie() -> None:
    """No project is downloaded: the first mention of one is the extract that writes it."""
    page = _page()
    extract = page.index("tether extract tutorial_movie.tif -o tutorial.tether")
    assert page.index("tutorial/data/tutorial_movie.tif") < extract
    assert page.index("tutorial.tether") > extract


def test_extract_prints_the_line_the_page_quotes(tmp_path, monkeypatch, capsys) -> None:
    out = _extract(tmp_path, monkeypatch, capsys)
    assert out.splitlines() == [EXTRACT_LINE]
    assert f"```text\n{EXTRACT_LINE}\n```" in _page()
    assert (tmp_path / "tutorial.tether").is_file()


@pytest.mark.gui
@_needs_qt
def test_shell_steps_print_the_lines_the_page_quotes(qtbot, tmp_path, monkeypatch, capsys) -> None:
    from pyqtgraph.Qt import QtCore

    from tether.gui.app import create_shell
    from tether.project.export import export_molecule_table_csv

    _extract(tmp_path, monkeypatch, capsys)
    page = _page()

    # Step 4: open the project.
    _app, shell = create_shell(["tutorial.tether"])
    qtbot.addWidget(shell.window)
    status = shell.window.statusBar()
    try:
        lst = shell.molecule_list
        assert status.currentMessage() == OPEN_LINE
        assert [lst.item(i).text() for i in range(lst.count())] == ["mol-0", "mol-1", "mol-2"]
        assert lst.currentRow() == 0

        def press(key: QtCore.Qt.Key) -> str:
            qtbot.keyClick(lst, key)
            return status.currentMessage()

        # Step 6, in the page's order: reject mol-0, then accept mol-1 and mol-2.
        assert press(QtCore.Qt.Key.Key_Backspace) == f"Rejected {REJECTED_KEY}"
        assert press(QtCore.Qt.Key.Key_Right) == BROWSE_LINE  # step 5's quoted line
        assert press(QtCore.Qt.Key.Key_Space) == f"Accepted {ACCEPTED_KEYS[0]}"
        press(QtCore.Qt.Key.Key_Right)
        assert press(QtCore.Qt.Key.Key_Space) == f"Accepted {ACCEPTED_KEYS[1]}"

        # Step 8: the population histogram leaves the rejected molecule out.
        shell._act_histogram.trigger()
        assert status.currentMessage() == HISTOGRAM_LINE
        assert shell.histogram_dock is not None
    finally:
        shell.close()

    for line in (OPEN_LINE, BROWSE_LINE, HISTOGRAM_LINE, f"Rejected {REJECTED_KEY}"):
        assert f"```text\n{line}\n```" in page, line
    for key in ACCEPTED_KEYS:
        assert f"```text\nAccepted {key}\n```" in page, key
    # Step 7 idealizes mol-2, the second accepted molecule.
    assert f"Idealizing {ACCEPTED_KEYS[1]} (one-click vbFRET)…" in page
    assert f"Idealized {ACCEPTED_KEYS[1]} (one-click vbFRET)" in page

    # Step 9: the export call on the page, after the shell has closed.
    result = export_molecule_table_csv("tutorial.tether", "tutorial.csv", include_rejected=False)
    assert result.n_molecules == 2
    assert (
        '>>> result = export_molecule_table_csv("tutorial.tether", "tutorial.csv", '
        "include_rejected=False)\n>>> result.n_molecules\n2\n"
    ) in page
    with (tmp_path / "tutorial.csv").open(newline="", encoding="utf-8") as handle:
        labels = [row["curation_label"] for row in csv.DictReader(handle)]
    assert labels == ["accept", "accept"]
    assert (tmp_path / "tutorial.csv.provenance.json").is_file()
