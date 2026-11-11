# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the startup splash window of ``tether-gui`` (#676).

Three contracts:

* ``--version`` and ``--help`` answer before any Qt import, so they make no window;
* the splash module imports no scientific library, because it must be on the screen
  before the slow import of the shell; and
* :func:`~tether.gui.app.create_shell` makes the splash, closes it when the shell
  window is shown, closes it when the start fails, and makes none on a headless
  platform unless a test asks for one.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys

import pytest

from tether import __version__

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
_needs_qt = pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed")

_SCIENTIFIC = ("numpy", "scipy", "h5py", "numba", "pyqtgraph", "napari", "pandas")


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a clean interpreter, so ``sys.modules`` starts empty."""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )


# --- no Qt before the arguments are parsed ------------------------------------


@pytest.mark.parametrize("flag", ["--version", "--help"])
def test_version_and_help_exit_before_any_qt_import(flag: str) -> None:
    """``main`` answers ``--version``/``--help`` with no Qt module loaded, so no window."""
    result = _run(
        "import sys\n"
        "from tether.gui.app import main\n"
        "try:\n"
        f"    main([{flag!r}])\n"
        "except SystemExit as exc:\n"
        "    code = exc.code\n"
        "else:\n"
        "    code = 'no exit'\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'pyqtgraph'))\n"
        "print('RESULT', code, loaded)\n"
    )
    assert result.returncode == 0, result.stderr
    assert "RESULT 0 []" in result.stdout, result.stdout
    if flag == "--version":
        assert f"tether {__version__}" in result.stdout


@_needs_qt
def test_splash_module_imports_no_scientific_library() -> None:
    """The splash must show before NumPy and pyqtgraph load, so it cannot import them."""
    result = _run(
        "import sys\n"
        "import tether.gui.splash\n"
        f"print('LOADED', sorted(m for m in {_SCIENTIFIC!r} if m in sys.modules))\n"
    )
    assert result.returncode == 0, result.stderr
    assert "LOADED []" in result.stdout, result.stdout


# --- the art ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dark", "ratio", "expected"),
    [
        (False, 1.0, "splash-light-640x360.png"),
        (True, 1.0, "splash-dark-640x360.png"),
        (False, 1.25, "splash-light-1280x720.png"),
        (True, 2.0, "splash-dark-1280x720.png"),
    ],
)
@_needs_qt
def test_art_follows_scheme_and_density(dark: bool, ratio: float, expected: str) -> None:
    """A standard display gets the 640 x 360 file; a high-density one the 1280 x 720 file."""
    from tether.gui import splash

    assert splash.art_name(dark=dark, device_pixel_ratio=ratio) == expected


@pytest.mark.gui
@_needs_qt
@pytest.mark.parametrize("ratio", [1.0, 2.0])
@pytest.mark.parametrize(("dark", "ground"), [(False, "#fbfbfa"), (True, "#0d0f12")])
def test_art_loads_at_the_logical_size(qtbot, dark: bool, ground: str, ratio: float) -> None:
    """Both files draw at 640 x 360 logical pixels, on the ground colour of the scheme."""
    from tether.gui import splash

    pixmap = splash._load_art(dark=dark, device_pixel_ratio=ratio)
    assert pixmap is not None
    assert pixmap.deviceIndependentSize().toSize().width() == 640
    assert pixmap.deviceIndependentSize().toSize().height() == 360
    assert pixmap.width() == round(640 * (2 if ratio > 1 else 1))
    assert pixmap.toImage().pixelColor(1, 1).name() == ground


# --- the startup path ---------------------------------------------------------


def _visible_splashes() -> list[object]:
    from PySide6 import QtWidgets

    return [
        w
        for w in QtWidgets.QApplication.topLevelWidgets()
        if isinstance(w, QtWidgets.QSplashScreen) and w.isVisible()
    ]


@pytest.mark.gui
@_needs_qt
def test_headless_start_shows_no_splash(qtbot) -> None:
    """The test suite runs offscreen, and an offscreen start makes no splash."""
    from tether.gui import splash
    from tether.gui.app import create_shell

    _app, shell = create_shell([])
    qtbot.addWidget(shell.window)
    try:
        assert splash.is_headless(_app)
        assert _visible_splashes() == []
    finally:
        shell.close()


@pytest.mark.gui
@_needs_qt
@pytest.mark.parametrize("dark", [False, True])
def test_create_shell_makes_and_closes_the_splash(qtbot, monkeypatch, caplog, dark) -> None:
    """The splash shows the version and one status line, then closes with the window."""
    from tether.gui import splash
    from tether.gui.app import create_shell

    monkeypatch.setattr(splash, "system_is_dark", lambda _app: dark)
    caplog.set_level(logging.INFO, logger="tether.gui.splash")
    _app, shell = create_shell([], splash=True)
    qtbot.addWidget(shell.window)
    try:
        shown = _visible_splashes()
        assert len(shown) == 1
        screen = shown[0]
        assert isinstance(screen, splash.StartupSplash)
        assert screen.title == f"Tether {__version__}"
        assert screen.message() == splash.STATUS_LOADING == "Loading…"
        assert "%" not in screen.message()
        expected_ground = "#0d0f12" if dark else "#fbfbfa"
        assert screen.pixmap().toImage().pixelColor(1, 1).name() == expected_ground

        shell.show()
        qtbot.waitUntil(lambda: _visible_splashes() == [], timeout=5000)
        assert shell.window.isVisible()
        assert "startup splash visible for" in caplog.text
        assert not splash._LIVE
    finally:
        shell.close()


@pytest.mark.gui
@_needs_qt
def test_failed_start_closes_the_splash(qtbot, monkeypatch) -> None:
    """A start that raises leaves no splash on the screen, and the error propagates."""
    import tether.gui.shell as shell_module
    from tether.gui import splash
    from tether.gui.app import create_shell

    def broken() -> None:
        raise RuntimeError("shell failed to build")

    monkeypatch.setattr(shell_module, "TetherShell", broken)
    with pytest.raises(RuntimeError, match="shell failed to build"):
        create_shell([], splash=True)
    assert _visible_splashes() == []
    assert not splash._LIVE


@pytest.mark.gui
@_needs_qt
def test_missing_art_skips_the_splash_and_starts(qtbot, monkeypatch) -> None:
    """The splash is decoration: art that does not load must not stop the start."""
    from tether.gui import splash
    from tether.gui.app import create_shell

    def missing(name: str) -> bytes:
        raise FileNotFoundError(name)

    monkeypatch.setattr(splash, "_brand_bytes", missing)
    _app, shell = create_shell([], splash=True)
    qtbot.addWidget(shell.window)
    try:
        assert _visible_splashes() == []
        assert shell._traces == []
    finally:
        shell.close()
