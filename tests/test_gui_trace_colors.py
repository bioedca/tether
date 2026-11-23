# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The colour-blind-safe trace palette is the default; classic is a kept preference (#636).

Three layers:

* **The palette (no Qt).** A new installation draws the ``safe`` set, the classic
  green and red set stays selectable, and the classic set is the one that fails the
  colour-vision check of ``tests/test_gui_theme.py``.
* **The store (no Qt).** **View → Trace colors** is kept as ``trace_colors`` in the
  application settings file (ADR-0123, amending ADR-0095). Writing it keeps the consent
  answer and never invents one; each answer is read on its own.
* **``pytest-qt``, headless.** The menu re-colours the open trace dock at once, writes
  the choice, and a restart through ``create_shell`` draws the stored choice.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tether.gui import app_settings, theme
from tether.gui.app_settings import (
    TRACE_COLORS,
    UpdateCheckState,
    record_trace_colors,
    record_update_consent,
    stored_trace_colors,
    update_check_state,
)

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")


@pytest.fixture(autouse=True)
def _default_palette(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Every test starts and ends on the default palette (module state).

    Outside the ``gui`` tests the ``"system"`` theme resolves to light without asking
    Qt, so the palette and store tests run where PySide6 and pyqtgraph are absent.
    """
    if request.node.get_closest_marker("gui") is None:
        monkeypatch.setattr(theme, "system_theme_name", lambda: "light")
    theme.set_trace_palette(theme.DEFAULT_TRACE_PALETTE)
    yield
    theme.set_trace_palette(theme.DEFAULT_TRACE_PALETTE)


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the store's default path at a file that does not exist yet."""
    path = tmp_path / "user" / app_settings.SETTINGS_FILENAME
    monkeypatch.setattr(app_settings, "settings_path", lambda: path)
    return path


def _consent(path: Path) -> UpdateCheckState:
    return update_check_state(path, path.parent / "no-override")


# --- the palette (no Qt) -----------------------------------------------------


def test_a_new_installation_draws_the_safe_pair() -> None:
    assert theme.DEFAULT_TRACE_PALETTE == "safe"
    assert theme.trace_palette() == "safe"
    for name in theme.THEME_NAMES:
        active = theme.theme(name)
        safe = theme.TRACE_PALETTES["safe"][name]
        assert active.trace_palette == "safe"
        assert (active.trace("donor"), active.trace("acceptor")) == (
            safe["donor"],
            safe["acceptor"],
        )


def test_the_classic_set_is_still_green_and_red() -> None:
    for name in theme.THEME_NAMES:
        classic = theme.theme(name, trace_palette="classic")
        assert classic.trace("donor") == "#00aa00"
        assert classic.trace("acceptor") == "#dc2d2d"


def test_set_trace_palette_selects_and_notifies() -> None:
    seen: list[str] = []

    def record(active: theme.Theme) -> None:
        seen.append(active.trace_palette)

    theme.subscribe(record)
    try:
        assert theme.set_trace_palette("classic").trace_palette == "classic"
        assert theme.current().trace("donor") == "#00aa00"
        theme.set_trace_palette("classic")  # no change, no second call
        theme.set_trace_palette("safe")
    finally:
        theme.unsubscribe(record)
    assert seen == ["classic", "safe"]
    with pytest.raises(ValueError, match="trace palette"):
        theme.set_trace_palette("rainbow")


def test_the_store_knows_exactly_the_palette_names() -> None:
    assert set(TRACE_COLORS) == set(theme.TRACE_PALETTES)


# --- the store (no Qt) -------------------------------------------------------


def test_unset_trace_colors_read_as_none_and_create_nothing(tmp_path: Path) -> None:
    path = tmp_path / "user" / "settings.json"
    assert stored_trace_colors(path) is None
    assert not path.parent.exists()


def test_the_choice_is_read_back(tmp_path: Path) -> None:
    path = tmp_path / "user" / "settings.json"
    for name in ("classic", "safe", "classic"):
        assert record_trace_colors(name, settings_file=path) == path
        assert stored_trace_colors(path) == name


def test_writing_the_trace_colors_keeps_the_consent_answer(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    record_update_consent(False, settings_file=path)
    record_trace_colors("classic", settings_file=path)
    assert _consent(path) is UpdateCheckState.DECLINED
    record_update_consent(True, settings_file=path)
    assert stored_trace_colors(path) == "classic"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": 1,
        "update_check": "allowed",
        "trace_colors": "classic",
    }


def test_writing_the_trace_colors_never_invents_consent(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    record_trace_colors("classic", settings_file=path)
    assert _consent(path) is UpdateCheckState.PENDING
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": 1,
        "trace_colors": "classic",
    }


@pytest.mark.parametrize("bad", ["rainbow", "", 1, None, ["safe"]])
def test_a_bad_trace_value_spoils_neither_answer(tmp_path: Path, bad: object) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"schema": 1, "update_check": "declined", "trace_colors": bad}),
        encoding="utf-8",
    )
    assert stored_trace_colors(path) is None
    assert _consent(path) is UpdateCheckState.DECLINED
    # A later trace write replaces the bad value and keeps the good answer.
    record_trace_colors("safe", settings_file=path)
    assert stored_trace_colors(path) == "safe"
    assert _consent(path) is UpdateCheckState.DECLINED


def test_a_foreign_file_reads_as_unset_and_is_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema": 2, "update_check": "allowed"}), encoding="utf-8")
    assert stored_trace_colors(path) is None
    record_trace_colors("classic", settings_file=path)
    # The other schema's "allowed" is not carried into this version's file.
    assert _consent(path) is UpdateCheckState.PENDING


@pytest.mark.parametrize("bad", ["rainbow", "Classic", ""])
def test_only_a_palette_name_is_accepted(tmp_path: Path, bad: str) -> None:
    path = tmp_path / "settings.json"
    with pytest.raises(ValueError, match="trace colors"):
        record_trace_colors(bad, settings_file=path)
    assert not path.exists()


# --- two writers, one file (#1254) ---------------------------------------------


def _pause_after_each_read(monkeypatch: pytest.MonkeyPatch, wait_s: float) -> None:
    """Make every read wait until a second read has happened too, or ``wait_s`` has passed.

    Two writers that both get past their read before either replaces the file are the lost
    answer of #1254. A writer that cannot read yet because the other holds the lock lets the
    wait time out, which is the serialization the lock exists for.
    """
    barrier = threading.Barrier(2, timeout=wait_s)
    real_stored = app_settings._stored

    def stored_then_wait(*args: Any, **kwargs: Any) -> dict[str, str]:
        answers = real_stored(*args, **kwargs)
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()
        return answers

    monkeypatch.setattr(app_settings, "_stored", stored_then_wait)


def test_two_concurrent_writes_keep_both_answers(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    record_update_consent(True, settings_file=path)
    # Its own patch context: undoing the test's monkeypatch would also undo the autouse
    # fixture's theme patch.
    with pytest.MonkeyPatch.context() as patch:
        _pause_after_each_read(patch, wait_s=1.0)
        writers = [
            threading.Thread(target=record_update_consent, args=(False, path)),
            threading.Thread(target=record_trace_colors, args=("classic", path)),
        ]
        for writer in writers:
            writer.start()
        for writer in writers:
            writer.join(timeout=30)
    assert not any(writer.is_alive() for writer in writers)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": 1,
        "update_check": "declined",
        "trace_colors": "classic",
    }


def _refuse_reading(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make every read-only open of ``path`` fail as a permission error would."""
    real_open = os.open

    def open_or_refuse(file: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(file) == path and not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(app_settings.os, "open", open_or_refuse)


@pytest.mark.parametrize("writer", ["trace", "consent"])
def test_a_write_that_cannot_read_the_file_fails_and_keeps_both_answers(
    tmp_path: Path, writer: str
) -> None:
    path = tmp_path / "settings.json"
    record_update_consent(False, settings_file=path)
    record_trace_colors("classic", settings_file=path)
    before = path.read_bytes()
    with pytest.MonkeyPatch.context() as patch:
        _refuse_reading(patch, path)
        # Reading still never raises: the unreadable file reads as unset.
        assert stored_trace_colors(path) is None
        with pytest.raises(OSError, match="settings.json"):
            if writer == "trace":
                record_trace_colors("safe", settings_file=path)
            else:
                record_update_consent(True, settings_file=path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json", "settings.json.lock"]


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX permissions that bind this user (not root, not Windows)",
)
def test_a_write_to_a_file_without_read_permission_fails_and_keeps_both_answers(
    tmp_path: Path,
) -> None:
    # The steps of #1254, case 2: the folder stays writable, the file is not readable.
    path = tmp_path / "settings.json"
    record_update_consent(False, settings_file=path)
    record_trace_colors("classic", settings_file=path)
    before = path.read_bytes()
    path.chmod(0)
    try:
        with pytest.raises(OSError):
            record_trace_colors("safe", settings_file=path)
    finally:
        path.chmod(0o600)
    assert path.read_bytes() == before
    assert _consent(path) is UpdateCheckState.DECLINED


# --- the running window (pytest-qt, headless) --------------------------------


def _pen(curve) -> str:
    from pyqtgraph.Qt import QtGui

    return QtGui.QPen(curve.opts["pen"]).color().name()


@pytest.mark.gui
@_needs_qt
def test_view_trace_colors_recolours_the_open_dock_and_survives_a_restart(
    qapp, qtbot, settings_file: Path
) -> None:
    from tether.gui.app import create_shell

    _app, shell = create_shell([], splash=False)
    qtbot.addWidget(shell.window)
    try:
        dock = shell.trace_dock
        safe = theme.current()
        assert shell.trace_colors_actions["safe"].isChecked()
        assert (_pen(dock.donor_curve), _pen(dock.acceptor_curve)) == (
            safe.trace("donor"),
            safe.trace("acceptor"),
        )

        shell.trace_colors_actions["classic"].trigger()
        classic = theme.current()
        assert classic.trace_palette == "classic"
        assert shell.trace_colors_actions["classic"].isChecked()
        assert (_pen(dock.donor_curve), _pen(dock.acceptor_curve)) == ("#00aa00", "#dc2d2d")
        assert _pen(dock.fret_curve) == classic.trace("fret")
        assert stored_trace_colors(settings_file) == "classic"
    finally:
        shell.close()

    # A restart: the session state is back to the default, the file is not.
    theme.set_trace_palette(theme.DEFAULT_TRACE_PALETTE)
    _app, shell = create_shell([], splash=False)
    qtbot.addWidget(shell.window)
    try:
        assert theme.trace_palette() == "classic"
        assert shell.trace_colors_actions["classic"].isChecked()
        assert _pen(shell.trace_dock.donor_curve) == "#00aa00"

        shell.trace_colors_actions["safe"].trigger()
        assert stored_trace_colors(settings_file) == "safe"
        assert _pen(shell.trace_dock.acceptor_curve) == theme.current().trace("acceptor")
    finally:
        shell.close()


@pytest.mark.gui
@_needs_qt
def test_a_store_that_cannot_be_written_still_changes_the_session(
    qapp, qtbot, settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tether.gui.shell import TetherShell

    def refuse(name: str, settings_file: Path | None = None) -> Path:
        raise PermissionError("read-only folder")

    monkeypatch.setattr(app_settings, "record_trace_colors", refuse)
    with TetherShell() as shell:
        qtbot.addWidget(shell.window)
        shell.trace_colors_actions["classic"].trigger()
        assert theme.trace_palette() == "classic"
        assert _pen(shell.trace_dock.donor_curve) == "#00aa00"
        message = shell.window.statusBar().currentMessage()
        assert "this session only" in message and "read-only folder" in message
    assert not settings_file.exists()


_HAS_NAPARI = _HAS_QT and importlib.util.find_spec("napari") is not None
# Same gate as test_movie_panel: macOS offscreen Qt has no GL context for napari.
_NO_GL = sys.platform == "darwin" and os.environ.get("QT_QPA_PLATFORM") == "offscreen"


@pytest.mark.gui
@_needs_qt
@pytest.mark.skipif(not _HAS_NAPARI or _NO_GL, reason="napari Viewer needs Qt + a GL context")
def test_the_movie_panel_spots_follow_the_trace_colors(qtbot) -> None:
    import numpy as np

    from tether.gui.movie_panel import MovieOverlay, NapariMoviePanel
    from tether.io.movie import open_movie

    fixture = Path(__file__).resolve().parent / "fixtures" / "movie_be_64x64x50.tif"
    overlay = MovieOverlay(donor_xy=[[10.0, 12.0]], acceptor_xy=[[40.0, 12.0]])
    with open_movie(fixture) as reader, NapariMoviePanel() as panel:
        panel.add_movie(reader, overlay=overlay)
        for name in ("classic", "safe"):
            active = theme.set_trace_palette(name)
            donor = panel.donor_layer.face_color[0][:3]
            acceptor = panel.acceptor_layer.face_color[0][:3]
            assert np.allclose(donor * 255, theme.rgba(active.trace("donor"))[:3], atol=0.5)
            assert np.allclose(acceptor * 255, theme.rgba(active.trace("acceptor"))[:3], atol=0.5)
