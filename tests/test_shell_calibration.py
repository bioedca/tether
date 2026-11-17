# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shell's Calibration menu (#353): make a channel map, reload it for a later movie.

All ``@pytest.mark.gui`` and headless: a real :class:`TetherShell` on the pytest-qt
``QApplication``. The file dialogs are interactive and not driven; the headless methods
they call are, through the shell's ``channel_map`` actions.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("h5py")
pytest.importorskip("skimage")
tifffile = pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

pytestmark = [pytest.mark.gui, _needs_qt]

_SHAPE = (64, 96)
_BG, _AMP, _SIGMA = 80.0, 400.0, 1.5
_BEADS = np.array([[x, y] for x in (8.0, 22.0, 36.0) for y in (10.0, 30.0, 50.0)])
_MOLECULES = np.array([[12.0, 12.0], [24.0, 40.0], [16.0, 52.0]])
_SHIFT = np.array([49.0, 0.0])  # the acceptor copy, +1 px in x inside the right half


def _frame(donor: np.ndarray) -> np.ndarray:
    frame = np.full(_SHAPE, _BG)
    rows, cols = np.mgrid[0 : _SHAPE[0], 0 : _SHAPE[1]]
    for x, y in np.vstack([donor, donor + _SHIFT]):
        frame += _AMP * np.exp(-((rows - y) ** 2 + (cols - x) ** 2) / (2.0 * _SIGMA**2))
    return frame


def _write(path: Path, data: np.ndarray) -> Path:
    tifffile.imwrite(
        path, np.ascontiguousarray(data, dtype=">u2"), byteorder=">", photometric="minisblack"
    )
    return path


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def _menu_texts(window) -> list[str]:
    return [a.text() for a in window.menuBar().actions()]


def test_the_calibration_menu_has_its_three_actions(shell) -> None:
    from pyqtgraph.Qt import QtWidgets

    assert "Ca&libration" in _menu_texts(shell.window)
    menu = next(
        m for m in shell.window.findChildren(QtWidgets.QMenu) if m.title() == "Ca&libration"
    )
    assert [a.text() for a in menu.actions()] == [
        "&Make channel map…",
        "&Choose channel map…",
        "&Extract a movie with the channel map…",
    ]


def test_a_map_made_in_one_session_is_reloaded_for_a_later_movie(tmp_path, qapp, qtbot) -> None:
    from tether.gui.shell import TetherShell
    from tether.imaging.calibrate import load_map, read_calibration
    from tether.imaging.extract import read_molecules

    beads = _write(tmp_path / "beads.tif", _frame(_BEADS))
    map_file = tmp_path / "setup.npz"

    first = TetherShell()
    qtbot.addWidget(first.window)
    try:
        made = first.channel_map.make_map(beads, map_file)
        assert made is not None and made.path == map_file and map_file.is_file()
        assert first.channel_map.map_path == map_file
        assert "Channel map setup.npz" in first.window.statusBar().currentMessage()
    finally:
        first.close()

    # A later session: choose the saved file, then extract a later movie with it.
    later = TetherShell()
    qtbot.addWidget(later.window)
    try:
        assert later.channel_map.map_path is None
        assert later.channel_map.choose_map(map_file) == map_file
        movie = _write(tmp_path / "later_001.tif", np.stack([_frame(_MOLECULES)] * 10))
        out = tmp_path / "later.tether"
        result = later.channel_map.extract_with_map(movie, out)
        assert result is not None
        assert result.summary.registration_source == "map-file"
        assert result.summary.n_molecules == 3
        assert len(read_molecules(out)) == 3
        # The extraction reloaded the saved map: its coefficients are in /calibration.
        saved = load_map(map_file)
        stored = read_calibration(out, result.summary.calibration_id)
        assert np.array_equal(stored.ref_to_moving.a, saved.ref_to_moving.a)
        assert stored.provenance["map_file"]["name"] == "setup.npz"
        # ...and the new project is open in the shell.
        assert later.project_path == out
    finally:
        later.close()


def test_extract_needs_a_map_and_a_bad_choice_is_refused(shell, tmp_path) -> None:
    assert shell.channel_map.extract_with_map(tmp_path / "m.tif", tmp_path / "o.tether") is None
    assert "make or choose a channel map first" in shell.window.statusBar().currentMessage()
    not_a_map = tmp_path / "notes.txt"
    not_a_map.write_text("x")
    assert shell.channel_map.choose_map(not_a_map) is None
    assert shell.channel_map.map_path is None
    assert "not a .npz map file" in shell.window.statusBar().currentMessage()


def test_a_failed_map_reports_and_keeps_no_map(shell, tmp_path) -> None:
    blank = _write(tmp_path / "blank.tif", np.full(_SHAPE, _BG))
    assert shell.channel_map.make_map(blank, tmp_path / "blank.npz") is None
    assert shell.channel_map.map_path is None
    assert "Channel map not made" in shell.window.statusBar().currentMessage()


def test_a_bad_map_file_fails_the_extraction_with_its_reason(shell, tmp_path) -> None:
    junk = tmp_path / "junk.npz"
    junk.write_bytes(b"not a zip")
    shell.channel_map.choose_map(junk)
    movie = _write(tmp_path / "m_002.tif", np.stack([_frame(_MOLECULES)] * 4))
    assert shell.channel_map.extract_with_map(movie, tmp_path / "o.tether") is None
    message = shell.window.statusBar().currentMessage()
    assert message.startswith("Extraction failed: could not use --map junk.npz")
    assert not (tmp_path / "o.tether").exists()


def _made_map(tmp_path: Path) -> Path:
    from tether.gui.calibration import ChannelMapActions

    actions = ChannelMapActions(status=lambda _m: None, open_project=lambda _p: None)
    made = actions.make_map(_write(tmp_path / "beads.tif", _frame(_BEADS)), tmp_path / "s.npz")
    assert made is not None
    return made.path


def test_an_over_gate_map_is_a_warning_not_a_plain_success(shell, tmp_path) -> None:
    """The status line names the over-gate map and its gate (Greptile P1; dialog is #351)."""
    from dataclasses import replace

    from tether.imaging.calibrate import load_map, save_map

    saved = load_map(_made_map(tmp_path))
    # A map whose residual is over its own gate (and the gate is not the default 0.5).
    strict = save_map(replace(saved, rms_residual=0.8, gate_px=0.25), tmp_path / "strict.npz")
    shell.channel_map.choose_map(strict)
    movie = _write(tmp_path / "strict_001.tif", np.stack([_frame(_MOLECULES)] * 10))
    result = shell.channel_map.extract_with_map(movie, tmp_path / "strict.tether")
    assert result is not None and result.summary.low_confidence_registration
    message = shell.window.statusBar().currentMessage()
    assert "warning: low confidence" in message
    assert "RMS 0.800 px is over the 0.25 px gate" in message
    assert "low-confidence-registration" in message


def test_swapped_halves_are_a_warning_and_a_refused_open_is_said(monkeypatch, tmp_path) -> None:
    """Swapped halves warn (Greptile P1); a project the shell did not open says so (P2)."""
    from dataclasses import replace

    from tether.gui import calibration
    from tether.imaging.side_check import VERDICT_SWAPPED, DonorSideCheck

    map_file = _made_map(tmp_path)
    real_run = calibration.run_service

    def swapped_run(request):
        result = real_run(request)
        check = DonorSideCheck(verdict=VERDICT_SWAPPED, n_swapped=9, n_consistent=1)
        return replace(result, summary=replace(result.summary, donor_side_check=check))

    monkeypatch.setattr(calibration, "run_service", swapped_run)
    messages: list[str] = []
    actions = calibration.ChannelMapActions(
        status=messages.append,
        open_project=lambda _path: None,  # e.g. an idealization is running
    )
    actions.choose_map(map_file)
    movie = _write(tmp_path / "swap_001.tif", np.stack([_frame(_MOLECULES)] * 10))
    out = tmp_path / "swap.tether"
    assert actions.extract_with_map(movie, out) is not None
    assert out.is_file()
    assert "swap.tether (saved, not opened" in messages[-1]
    assert "warning: donor and acceptor halves look swapped (9 of 10" in messages[-1]
    assert "low confidence" not in messages[-1]
