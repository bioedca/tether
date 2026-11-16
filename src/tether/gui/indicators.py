# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The busy indicator and the progress row, drawn from mark 42a (#677, PRD §7.3).

Both are ``QProgressBar`` subclasses with a custom paint, so assistive technology
reads them as progress bars: the busy mark is an indeterminate bar (range 0 to 0) and
the progress row reports its value through ``QAccessibleValueInterface``. Neither
holds a colour: the ink is the ``text`` token of the active theme
([`tether.gui.theme`][]), and a change of theme repaints them through the palette
change it sends to every widget.

* `BusyIndicator` is for a task that gives no percentage. The three
  wavefronts of the mark light in turn, from the surface to the molecule, beside the
  task name. When the operating system asks for reduced motion
  ([`prefers_reduced_motion`][]) the mark stays still and the name stays.
* `ProgressRow` is for a task that gives a percentage. It draws the
  beam-profile row of dots at the top of the mark; the dots fill from left to right,
  and the text percentage stays beside them.

The shell connects the busy indicator to the one-click idealize. The task service
(#343) and the progress indicator (#627) put both parts in front of the other tasks.

Qt is imported when one of the three widget classes is first used, so importing this
module costs no Qt, matching the rest of [`tether.gui`][].
"""

from __future__ import annotations

import ctypes
import math
import shutil
import subprocess
import sys
from collections.abc import Iterator
from functools import cache
from typing import Any

from tether.gui import theme

# The three widget classes are defined on first use by the module __getattr__ below.
__all__ = ["BusyIndicator", "BusyMark", "ProgressRow", "prefers_reduced_motion"]  # noqa: F822

# Mark 42a geometry, in the units of its 200 x 200 viewBox (scripts/brand/brandkit.py:
# MOL, MOL_R, ARC_R, ARC_SPAN, ARC_STROKE). The wavefronts are listed outer first,
# the order in which they light: from the surface down to the molecule.
_MOL = (100.0, 124.0)
_MOL_R = 11.875
_ARC_R = (56.4, 42.3, 28.2)
_ARC_SPAN = 0.52  # radians each side of vertical
_ARC_STROKE = 6.96
# The square of the viewBox that holds the three wavefronts and the molecule:
# left, top, side.
_MARK_BOX = (60.0, 60.0, 80.0)

_STEP_MS = 260  # time from one wavefront to the next
_DIM = 0.28  # opacity of an unlit wavefront and of an unfilled dot
_DOTS = 20  # dots in the progress row: one dot for each 5 %
# The beam profile of the dot row (brandkit.beam_rule): a Gaussian of width 0.45 on
# a -1..1 axis, from half to full size.
_BEAM_WIDTH = 0.45
_BEAM_MIN = 0.5


def prefers_reduced_motion() -> bool:
    """True when the operating system asks applications to reduce motion.

    Qt 6.11 (the base lock) has no API for this; ``QAccessibilityHints`` reports only
    the contrast preference. So each platform is read directly:

    * Windows: ``SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION)`` is off
      (Settings → Accessibility → Visual effects → Animation effects);
    * macOS: ``NSWorkspace.accessibilityDisplayShouldReduceMotion``
      (System Settings → Accessibility → Display → Reduce motion);
    * Linux: GNOME's ``org.gnome.desktop.interface enable-animations`` is false.

    A setting that cannot be read gives False, so the mark moves; the task name is
    on the screen either way.
    """
    try:
        if sys.platform == "win32":
            return _windows_reduced_motion()
        if sys.platform == "darwin":
            return _macos_reduced_motion()
        return _gnome_reduced_motion()
    except Exception:  # noqa: BLE001 - an unreadable setting must never break the GUI
        return False


def _windows_reduced_motion() -> bool:
    spi_getclientareaanimation = 0x1042
    enabled = ctypes.c_int(1)
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    ok = user32.SystemParametersInfoW(spi_getclientareaanimation, 0, ctypes.byref(enabled), 0)
    return bool(ok) and not enabled.value


def _macos_reduced_motion() -> bool:
    objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    # objc_msgSend must be called through a prototype that matches each method.
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
    send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(address)
    workspace = send_id(
        objc.objc_getClass(b"NSWorkspace"), objc.sel_registerName(b"sharedWorkspace")
    )
    if not workspace:
        return False
    selector = objc.sel_registerName(b"accessibilityDisplayShouldReduceMotion")
    return bool(send_bool(workspace, selector))


def _gnome_reduced_motion() -> bool:
    gsettings = shutil.which("gsettings")
    if gsettings is None:
        return False
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [gsettings, "get", "org.gnome.desktop.interface", "enable-animations"],
        capture_output=True,
        text=True,
        timeout=1,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "false"


def __getattr__(name: str) -> Any:
    if name in ("BusyMark", "BusyIndicator", "ProgressRow"):
        return _widget_classes()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@cache
def _widget_classes() -> dict[str, type]:
    """Define the Qt widget classes on first use (Qt is imported here, not at import)."""
    from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

    def _ink() -> QtGui.QColor:
        return QtGui.QColor(theme.current().color("text"))

    class BusyMark(QtWidgets.QProgressBar):
        """The three wavefronts of the mark as an indeterminate progress bar."""

        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            self.setRange(0, 0)  # indeterminate: assistive technology reads "busy"
            self.setTextVisible(False)
            side = max(16, self.fontMetrics().height())
            self.setFixedSize(side, side)
            self._phase = 0
            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(_STEP_MS)
            self._timer.timeout.connect(self._advance)

        @property
        def phase(self) -> int:
            """The lit wavefront: 0 (outer, at the surface) to 2 (inner, at the molecule)."""
            return self._phase

        def is_animating(self) -> bool:
            """True while the wavefronts light in turn."""
            return self._timer.isActive()

        def animate(self, on: bool) -> None:
            """Start the sequence at the outer wavefront, or stop it and show the still mark."""
            self._phase = 0
            if on:
                self._timer.start()
            else:
                self._timer.stop()
            self.update()

        def _advance(self) -> None:
            self._phase = (self._phase + 1) % len(_ARC_R)
            self.update()

        def wavefront_opacities(self) -> tuple[float, ...]:
            """The opacity of each wavefront, outer first, as the next paint draws it."""
            animating = self._timer.isActive()
            return tuple(
                1.0 if not animating or index == self._phase else _DIM
                for index in range(len(_ARC_R))
            )

        def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            side = min(self.width(), self.height())
            left, top, box = _MARK_BOX
            painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
            painter.scale(side / box, side / box)
            painter.translate(-left, -top)
            ink = _ink()
            pen = QtGui.QPen(ink, _ARC_STROKE)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            cx, cy = _MOL
            span = math.degrees(_ARC_SPAN)
            for radius, opacity in zip(_ARC_R, self.wavefront_opacities(), strict=True):
                painter.setOpacity(opacity)
                rect = QtCore.QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius)
                # Qt measures angles counter-clockwise from 3 o'clock, in 1/16 degree.
                painter.drawArc(rect, round((90 - span) * 16), round(2 * span * 16))
            painter.setOpacity(1.0)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawEllipse(QtCore.QPointF(cx, cy), _MOL_R, _MOL_R)
            painter.end()

    class BusyIndicator(QtWidgets.QWidget):
        """The busy mark and the name of the running task; hidden while no task runs."""

        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            self._mark = BusyMark(self)
            self._label = QtWidgets.QLabel(self)
            # The task name carries user data (a molecule key): never read it as markup.
            self._label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            layout.addWidget(self._mark)
            layout.addWidget(self._label)
            self._task: str | None = None
            self.setVisible(False)

        @property
        def mark(self) -> BusyMark:
            """The wavefront mark (an indeterminate ``QProgressBar``)."""
            return self._mark

        @property
        def label(self) -> QtWidgets.QLabel:
            """The label that shows the task name."""
            return self._label

        @property
        def task_name(self) -> str | None:
            """The name of the running task, or None."""
            return self._task

        def is_running(self) -> bool:
            """True from `start` to `stop`."""
            return self._task is not None

        def start(self, task_name: str) -> None:
            """Show the indicator for a task that gives no percentage.

            The system's reduced-motion setting is read here, at each start, so a change
            made while Tether runs applies to the next task.
            """
            self._task = task_name
            self._label.setText(task_name)
            self._mark.setAccessibleName(task_name)
            self._mark.animate(not prefers_reduced_motion())
            self.setVisible(True)

        def stop(self) -> None:
            """Hide the indicator when the task stops, for any reason."""
            self._task = None
            self._mark.animate(False)
            self._label.clear()
            self._mark.setAccessibleName("")
            self.setVisible(False)

    class ProgressRow(QtWidgets.QProgressBar):
        """A progress bar drawn as the beam-profile row of dots, with the percentage beside it.

        ``value()``, ``setValue()``, ``setRange()`` and ``text()`` are those of
        ``QProgressBar``, so assistive technology reads the value as for any progress bar.
        """

        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            self.setRange(0, 100)
            self.setTextVisible(True)
            self.setMinimumHeight(max(16, self.fontMetrics().height()))

        @property
        def dot_count(self) -> int:
            """The number of dots in the row."""
            return _DOTS

        def filled_dots(self) -> int:
            """How many dots are filled at the current value (0 for an indeterminate range)."""
            span = self.maximum() - self.minimum()
            if span <= 0:
                return 0
            fraction = (self.value() - self.minimum()) / span
            return max(0, min(_DOTS, math.floor(fraction * _DOTS + 1e-9)))

        def sizeHint(self) -> QtCore.QSize:  # noqa: N802 - Qt override
            return QtCore.QSize(16 * _DOTS, max(16, self.fontMetrics().height()) + 4)

        def _dots(self, width: float, height: float) -> Iterator[tuple[QtCore.QPointF, float]]:
            pitch = width / _DOTS
            largest = max(1.0, min(pitch * 0.42, height * 0.32))
            middle = (_DOTS - 1) / 2
            for index in range(_DOTS):
                u = (index - middle) / middle
                size = _BEAM_MIN + (1 - _BEAM_MIN) * math.exp(-((u / _BEAM_WIDTH) ** 2))
                yield QtCore.QPointF(pitch * (index + 0.5), height / 2), largest * size

        def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            ink = _ink()
            text_width = self.fontMetrics().horizontalAdvance("100%") + 8
            row_width = max(0, self.width() - text_width)
            filled = self.filled_dots()
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            for index, (centre, radius) in enumerate(self._dots(row_width, self.height())):
                painter.setOpacity(1.0 if index < filled else _DIM)
                painter.drawEllipse(centre, radius, radius)
            painter.setOpacity(1.0)
            if self.isTextVisible():
                painter.setPen(ink)
                painter.drawText(
                    QtCore.QRectF(row_width, 0, text_width, self.height()),
                    int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
                    self.text(),
                )
            painter.end()

    classes: dict[str, type] = {
        "BusyMark": BusyMark,
        "BusyIndicator": BusyIndicator,
        "ProgressRow": ProgressRow,
    }
    for name, cls in classes.items():
        cls.__module__ = __name__
        cls.__qualname__ = name
    return classes
