# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shell's Calibration menu (#353): make a channel map, reload it for a later movie.

All ``@pytest.mark.gui`` and headless: a real :class:`TetherShell` on the pytest-qt
``QApplication``. The file dialogs are interactive and not driven; the headless methods
they call are, through the shell's ``channel_map`` actions.
"""

from __future__ import annotations

import importlib.util
import threading
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


def _finished(qtbot, actions) -> None:
    """Wait until the running map or extract command's outcome is applied."""
    qtbot.waitUntil(lambda: not actions.busy, timeout=120_000)


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
        assert first.channel_map.make_map(beads, map_file) is True
        _finished(qtbot, first.channel_map)
        made = first.channel_map.last_result
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
        assert later.channel_map.extract_with_map(movie, out) is True
        _finished(qtbot, later.channel_map)
        result = later.channel_map.last_result
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
    assert shell.channel_map.extract_with_map(tmp_path / "m.tif", tmp_path / "o.tether") is False
    assert "make or choose a channel map first" in shell.window.statusBar().currentMessage()
    not_a_map = tmp_path / "notes.txt"
    not_a_map.write_text("x")
    assert shell.channel_map.choose_map(not_a_map) is None
    assert shell.channel_map.map_path is None
    assert "not a .npz map file" in shell.window.statusBar().currentMessage()


def test_a_failed_map_reports_and_keeps_no_map(shell, qtbot, tmp_path) -> None:
    blank = _write(tmp_path / "blank.tif", np.full(_SHAPE, _BG))
    assert shell.channel_map.make_map(blank, tmp_path / "blank.npz") is True
    _finished(qtbot, shell.channel_map)
    assert shell.channel_map.last_result is None
    assert shell.channel_map.map_path is None
    assert "Channel map not made" in shell.window.statusBar().currentMessage()


def test_a_bad_map_file_fails_the_extraction_with_its_reason(shell, qtbot, tmp_path) -> None:
    junk = tmp_path / "junk.npz"
    junk.write_bytes(b"not a zip")
    shell.channel_map.choose_map(junk)
    movie = _write(tmp_path / "m_002.tif", np.stack([_frame(_MOLECULES)] * 4))
    assert shell.channel_map.extract_with_map(movie, tmp_path / "o.tether") is True
    _finished(qtbot, shell.channel_map)
    assert shell.channel_map.last_result is None
    message = shell.window.statusBar().currentMessage()
    assert message.startswith("Extraction failed: could not use --map junk.npz")
    assert not (tmp_path / "o.tether").exists()


def _made_map(qtbot, tmp_path: Path) -> Path:
    from tether.gui.calibration import ChannelMapActions

    actions = ChannelMapActions(status=lambda _m: None, open_project=lambda _p: None)
    try:
        assert actions.make_map(_write(tmp_path / "beads.tif", _frame(_BEADS)), tmp_path / "s.npz")
        _finished(qtbot, actions)
        made = actions.last_result
        assert made is not None
        return made.path
    finally:
        actions.close()


def test_an_over_gate_map_is_a_warning_not_a_plain_success(shell, qtbot, tmp_path) -> None:
    """The status line names the over-gate map and its gate (Greptile P1; dialog is #351)."""
    from dataclasses import replace

    from tether.imaging.calibrate import load_map, save_map

    saved = load_map(_made_map(qtbot, tmp_path))
    # A map whose residual is over its own gate (and the gate is not the default 0.5).
    strict = save_map(replace(saved, rms_residual=0.8, gate_px=0.25), tmp_path / "strict.npz")
    shell.channel_map.choose_map(strict)
    movie = _write(tmp_path / "strict_001.tif", np.stack([_frame(_MOLECULES)] * 10))
    assert shell.channel_map.extract_with_map(movie, tmp_path / "strict.tether") is True
    _finished(qtbot, shell.channel_map)
    result = shell.channel_map.last_result
    assert result is not None and result.summary.low_confidence_registration
    message = shell.window.statusBar().currentMessage()
    assert "warning: low confidence" in message
    assert "RMS 0.800 px is over the 0.25 px gate" in message
    assert "low-confidence-registration" in message


def test_swapped_halves_are_a_warning_and_a_refused_open_is_said(
    monkeypatch, qtbot, tmp_path
) -> None:
    """Swapped halves warn (Greptile P1); a project the shell did not open says so (P2)."""
    from dataclasses import replace

    from tether.gui import calibration
    from tether.imaging.side_check import VERDICT_SWAPPED, DonorSideCheck

    map_file = _made_map(qtbot, tmp_path)
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
    try:
        assert actions.extract_with_map(movie, out) is True
        _finished(qtbot, actions)
    finally:
        actions.close()
    assert out.is_file()
    assert "swap.tether (saved, not opened" in messages[-1]
    assert "warning: donor and acceptor halves look swapped (9 of 10" in messages[-1]
    assert "low confidence" not in messages[-1]


def test_a_map_without_overwrite_keeps_an_existing_file(shell, qtbot, tmp_path) -> None:
    """A name the save dialog did not ask about is never replaced (author-side check)."""
    existing = tmp_path / "setup.npz"
    existing.write_bytes(b"an earlier map")
    beads = _write(tmp_path / "beads.tif", _frame(_BEADS))
    assert shell.channel_map.make_map(beads, existing, overwrite=False) is True
    _finished(qtbot, shell.channel_map)
    assert shell.channel_map.last_result is None
    assert existing.read_bytes() == b"an earlier map"
    assert shell.channel_map.map_path is None
    assert "output exists" in shell.window.statusBar().currentMessage()


def test_a_running_command_keeps_the_window_live_and_refuses_another(
    shell, qtbot, monkeypatch, tmp_path
) -> None:
    """Map and extract run off the GUI thread, one at a time (CodeRabbit Major)."""
    from tether.gui import calibration
    from tether.services import CommandFailed

    release = threading.Event()
    threads: list[int] = []

    def blocked(request):
        threads.append(threading.get_ident())
        release.wait(30)
        raise CommandFailed("stopped by the test")

    monkeypatch.setattr(calibration, "run_service", blocked)
    actions = shell.channel_map
    beads = tmp_path / "beads.tif"
    try:
        assert actions.make_map(beads, tmp_path / "s.npz") is True
        # make_map returned while the command still runs: the GUI thread is free.
        qtbot.waitUntil(lambda: bool(threads), timeout=10_000)
        assert actions.busy
        assert threads[0] != threading.get_ident()
        assert "Making channel map s.npz" in shell.window.statusBar().currentMessage()
        # The GUI thread's timers still fire while the command runs.
        ticks: list[int] = []
        from pyqtgraph.Qt import QtCore

        QtCore.QTimer.singleShot(0, lambda: ticks.append(1))
        qtbot.waitUntil(lambda: bool(ticks), timeout=5_000)
        # A second command, or a new choice, is refused until the first has finished.
        assert actions.make_map(beads, tmp_path / "t.npz") is False
        assert "wait for the running channel-map command" in (
            shell.window.statusBar().currentMessage()
        )
        assert actions.extract_with_map(tmp_path / "m.tif", tmp_path / "o.tether") is False
        assert actions.choose_map(tmp_path / "s.npz") is None
        assert len(threads) == 1
    finally:
        release.set()
    _finished(qtbot, actions)
    assert actions.last_result is None
    assert "Channel map not made: stopped by the test" in shell.window.statusBar().currentMessage()


def test_closing_abandons_a_running_command(qtbot, monkeypatch, tmp_path) -> None:
    """A command still running at close never applies its outcome to the closed shell."""
    from tether.gui import calibration
    from tether.services import CommandFailed

    release = threading.Event()
    started = threading.Event()

    def blocked(request):
        started.set()
        release.wait(30)
        raise CommandFailed("too late")

    monkeypatch.setattr(calibration, "run_service", blocked)
    messages: list[str] = []
    actions = calibration.ChannelMapActions(status=messages.append, open_project=lambda _p: None)
    assert actions.make_map(tmp_path / "beads.tif", tmp_path / "s.npz") is True
    assert started.wait(10)
    actions.close()
    assert not actions.busy
    release.set()
    qtbot.wait(200)
    assert messages == ["Making channel map s.npz…"]
