# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The startup splash window of ``tether-gui`` (#676, PRD §7.3).

Most of the start of ``tether-gui`` is the import of pyqtgraph, NumPy and the shell,
and before this window existed the user saw nothing during that time. The splash shows
the brand splash art, the version and one status line until the main window appears.

This module must run *before* the slow import it covers, so it imports only PySide6
and the standard library, never a scientific library. ``tests/test_gui_splash.py``
asserts that in a clean interpreter.

The art follows the system colour scheme, not the application theme: the theme
module loads with the shell, which is the slow part. A display whose device pixel
ratio is above 1 gets the 1280 x 720 file drawn at 640 x 360 logical pixels.

A headless platform (``offscreen``, which the test suite uses, or ``minimal``) gets no
splash. The splash is decoration, so a splash that cannot load its art is skipped and
the start continues.
"""

from __future__ import annotations

import json
import logging
import time
from importlib import resources
from typing import Final

from PySide6 import QtCore, QtGui, QtWidgets

from tether import __version__

__all__ = [
    "HEADLESS_PLATFORMS",
    "STATUS_LOADING",
    "StartupSplash",
    "art_name",
    "is_headless",
    "open_splash",
    "system_is_dark",
]

_LOG = logging.getLogger("tether.gui.splash")

#: Qt platform plugins that have no screen a person can see.
HEADLESS_PLATFORMS: Final = frozenset({"offscreen", "minimal", "minimalegl"})

#: The one status line. No percentage: nothing measures how far the start is.
STATUS_LOADING: Final = "Loading…"

_BRAND = ("assets", "brand")
_ART_LOGICAL = QtCore.QSize(640, 360)
# The lower left corner of the art, which scripts/brand/build.py splash() leaves for
# this text: below the clear field and above the beam rule at y = 330.
_TEXT_RECT = QtCore.QRect(24, 280, 420, 42)
_TEXT_PIXEL_SIZE = 12
# Long enough for a compositor to map a new window, short enough to never be noticed.
_EXPOSE_WAIT_S = 0.5

# A parentless widget is owned by its Python wrapper. Without this reference the
# splash would be collected when ``create_shell`` returns, before the window shows.
_LIVE: set[StartupSplash] = set()


def art_name(*, dark: bool, device_pixel_ratio: float) -> str:
    """Return the file name of the splash art for a colour scheme and display density."""
    scheme = "dark" if dark else "light"
    size = "1280x720" if device_pixel_ratio > 1.0 else "640x360"
    return f"splash-{scheme}-{size}.png"


def is_headless(app: QtGui.QGuiApplication) -> bool:
    """Return ``True`` when Qt runs on a platform with no visible screen."""
    return app.platformName() in HEADLESS_PLATFORMS


def system_is_dark(app: QtGui.QGuiApplication) -> bool:
    """Return ``True`` when the operating system asks for a dark colour scheme."""
    return app.styleHints().colorScheme() == QtCore.Qt.ColorScheme.Dark


def _brand_bytes(name: str) -> bytes:
    return resources.files("tether.gui").joinpath(*_BRAND, name).read_bytes()


def _text_colour(*, dark: bool) -> QtGui.QColor:
    tokens = json.loads(_brand_bytes("tokens.json"))
    return QtGui.QColor(tokens["dark" if dark else "light"]["text-muted"])


def _load_art(*, dark: bool, device_pixel_ratio: float) -> QtGui.QPixmap | None:
    pixmap = QtGui.QPixmap()
    name = art_name(dark=dark, device_pixel_ratio=device_pixel_ratio)
    if not pixmap.loadFromData(_brand_bytes(name), "PNG"):
        return None
    pixmap.setDevicePixelRatio(pixmap.width() / _ART_LOGICAL.width())
    return pixmap


class StartupSplash(QtWidgets.QSplashScreen):
    """The splash window: the art, ``Tether <version>`` and one status line.

    Build it with :func:`open_splash`, which also shows it. Call
    :meth:`close_when_shown` with the main window when the start succeeds, or
    :meth:`abort` when it fails.
    """

    def __init__(
        self, pixmap: QtGui.QPixmap, *, text_colour: QtGui.QColor, version: str = __version__
    ) -> None:
        super().__init__(pixmap)
        self._title = f"Tether {version}"
        self._text_colour = text_colour
        self._window: QtWidgets.QWidget | None = None
        self._shown_at: float | None = None
        self._done = False
        self.setAccessibleName("Tether")
        self.messageChanged.connect(self.setAccessibleDescription)

    @property
    def title(self) -> str:
        """The version line, ``Tether <version>``."""
        return self._title

    def drawContents(self, painter: QtGui.QPainter) -> None:  # noqa: N802 - Qt override
        """Draw the version line and the status line in the lower left corner."""
        font = QtGui.QFont(self.font())
        font.setPixelSize(_TEXT_PIXEL_SIZE)
        painter.setFont(font)
        painter.setPen(self._text_colour)
        align = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignBottom
        painter.drawText(_TEXT_RECT, int(align), f"{self._title}\n{self.message()}")

    def show_now(self, app: QtWidgets.QApplication) -> None:
        """Show the splash and let Qt paint it before the caller blocks the event loop."""
        _LIVE.add(self)
        self.showMessage(STATUS_LOADING)
        self.show()
        self._shown_at = time.perf_counter()
        deadline = self._shown_at + _EXPOSE_WAIT_S
        handle = self.windowHandle()
        while handle is not None and not handle.isExposed() and time.perf_counter() < deadline:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        app.processEvents()

    def close_when_shown(self, window: QtWidgets.QWidget) -> None:
        """Close the splash once ``window`` is shown, with :meth:`QSplashScreen.finish`."""
        self._window = window
        if window.isVisible():
            QtCore.QTimer.singleShot(0, self._finish)
        else:
            window.installEventFilter(self)

    def abort(self) -> None:
        """Close the splash now, because the start failed."""
        if self._window is not None:
            self._window.removeEventFilter(self)
        self._release(reason="the start failed")
        self.close()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        """Close the splash once the main window receives its show event."""
        if watched is self._window and event.type() == QtCore.QEvent.Type.Show:
            watched.removeEventFilter(self)
            # After the show returns: finish() waits for the window to be exposed.
            QtCore.QTimer.singleShot(0, self._finish)
        return False

    def _finish(self) -> None:
        if self._done:
            return
        if self._window is not None:
            self.finish(self._window)
        else:
            self.close()
        self._release(reason="the main window is shown")

    def _release(self, *, reason: str) -> None:
        if self._done:
            return
        self._done = True
        _LIVE.discard(self)
        if self._shown_at is not None:
            # INFO, so a timed start can read it: python -m tether.gui with logging on.
            visible = time.perf_counter() - self._shown_at
            _LOG.info("startup splash visible for %.2f s; closed because %s", visible, reason)
        self.deleteLater()


def open_splash(app: QtWidgets.QApplication) -> StartupSplash | None:
    """Show the splash for the system colour scheme; ``None`` when there is no art.

    The caller decides whether a splash is wanted (see :func:`is_headless`).
    """
    dark = system_is_dark(app)
    screen = app.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    try:
        pixmap = _load_art(dark=dark, device_pixel_ratio=ratio)
        colour = _text_colour(dark=dark)
    except (OSError, KeyError, ValueError) as exc:
        _LOG.warning("startup splash skipped: %s", exc)
        return None
    if pixmap is None:
        _LOG.warning("startup splash skipped: the art did not load")
        return None
    splash = StartupSplash(pixmap, text_colour=colour)
    splash.show_now(app)
    return splash
