# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The start page the shell shows while no project is open (#629, PRD §7.3).

An empty window used to give no hint of the first step. This page names it: an
**Open project…** button (the same action as **File → Open project…**), a line
that says a ``.tether`` file can also be dropped on the window, and a link to the
tutorial for the installed version.

Above them sits the brand (#689): the start art and the tagline :data:`TAGLINE`. The
art is the brand kit's drawing (the surface dots, the wavefronts, the pixel grid and
the word mark TETHER, drawn from outlines and never typed again), and it follows the
active theme (:mod:`tether.gui.theme`). The art is decoration, so it is painted on
the page itself and is no widget at all: assistive technology never meets it. The
page's accessible description gives the name and the tagline in words instead.

A list of recent projects belongs here too, but Tether keeps no such list yet
(that is separate work); the page gains it when the list exists.

A composition wrapper like the other ``tether.gui`` surfaces: it *holds* a
``QWidget`` and imports Qt lazily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyqtgraph.Qt import QtGui, QtWidgets

    from tether.gui.theme import Theme

__all__ = ["ART_WIDTH", "TAGLINE", "StartPage"]

#: The brand tagline (maintainer decision Q11 on #671), also shown in the About box.
TAGLINE: Final = "Provenance-first single-molecule FRET analysis"
#: The start art's width in logical pixels; a narrower window draws it smaller.
ART_WIDTH: Final = 480
_ART_TOP: Final = 24
_ART_GAP: Final = 16


def _surface(parent: QtWidgets.QWidget | None, paint: Any) -> QtWidgets.QWidget:
    """A plain page widget that calls ``paint(widget, painter)`` before its children draw."""
    from pyqtgraph.Qt import QtGui, QtWidgets

    class _Surface(QtWidgets.QWidget):
        def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
            painter = QtGui.QPainter(self)
            try:
                paint(self, painter)
            finally:
                painter.end()
            super().paintEvent(event)

    return _Surface(parent)


class StartPage:
    """The no-project page: the brand, **Open project…** and a link to the tutorial."""

    def __init__(
        self,
        *,
        on_open: Callable[[], object],
        on_tutorial: Callable[[], object],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui import theme

        self._art: QtGui.QPixmap | None = None
        self._art_key: tuple[str, float] | None = None
        self._widget = _surface(parent, self._paint_art)
        self._widget.setAccessibleName("Start page")
        self._widget.setAccessibleDescription(f"Tether. {TAGLINE}.")
        outer = QtWidgets.QVBoxLayout(self._widget)
        # The art is painted into this space (see _paint_art), not held by a widget.
        outer.addSpacing(_ART_TOP + ART_WIDTH // 2 + _ART_GAP)
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(12)

        self._tagline = QtWidgets.QLabel(TAGLINE)
        self._tagline.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        self._tagline.setWordWrap(True)
        column.addWidget(self._tagline)
        column.addSpacing(12)

        self._title = QtWidgets.QLabel("No project is open")
        self._title.setFont(theme.heading_font())
        self._title.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self._title)

        self._hint = QtWidgets.QLabel(
            "Open a Tether project (.tether) to curate and analyze its molecules.\n"
            "You can also drop a .tether file on this window."
        )
        self._hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        self._hint.setWordWrap(True)
        column.addWidget(self._hint)

        self._open_button = QtWidgets.QPushButton("&Open project…")
        self._open_button.setDefault(True)
        self._open_button.clicked.connect(lambda _checked=False: on_open())
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._open_button)
        row.addStretch(1)
        column.addLayout(row)

        self._tutorial_button = QtWidgets.QPushButton("Read the &tutorial")
        self._tutorial_button.setFlat(True)
        self._tutorial_button.setToolTip("Open the tutorial in your web browser")
        self._tutorial_button.clicked.connect(lambda _checked=False: on_tutorial())
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._tutorial_button)
        row.addStretch(1)
        column.addLayout(row)

        outer.addLayout(column)
        outer.addStretch(1)

        self._apply_theme(theme.current())
        theme.subscribe(self._apply_theme)

    def _apply_theme(self, _active: Theme) -> None:
        self._art_key = None  # drawn again, from the new theme, at the next paint
        self._widget.update()

    def art_pixmap(self) -> QtGui.QPixmap:
        """The start art for the active theme and this screen's density."""
        from tether.gui import theme

        active = theme.current()
        ratio = self._widget.devicePixelRatioF()
        key = (active.name, ratio)
        if self._art is None or self._art_key != key:
            self._art = theme.brand_pixmap("start-art", ART_WIDTH, active, device_pixel_ratio=ratio)
            self._art_key = key
        return self._art

    def art_rect(self) -> Any:
        """Where the art is painted: centred at the top, narrower in a narrow window."""
        from pyqtgraph.Qt import QtCore

        width = min(ART_WIDTH, max(0, self._widget.width() - 2 * _ART_TOP))
        left = (self._widget.width() - width) / 2
        return QtCore.QRectF(left, _ART_TOP, width, width / 2)

    def _paint_art(self, _widget: QtWidgets.QWidget, painter: QtGui.QPainter) -> None:
        from pyqtgraph.Qt import QtGui

        art = self.art_pixmap()
        if art.isNull():
            return
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.art_rect(), art, art.rect().toRectF())

    def close(self) -> None:
        """Stop following the theme (the shell calls this when it closes)."""
        from tether.gui import theme

        theme.unsubscribe(self._apply_theme)

    @property
    def widget(self) -> QtWidgets.QWidget:
        """The wrapped page widget."""
        return self._widget

    @property
    def tagline(self) -> QtWidgets.QLabel:
        """The tagline, :data:`TAGLINE`."""
        return self._tagline

    @property
    def open_button(self) -> QtWidgets.QPushButton:
        """The **Open project…** button."""
        return self._open_button

    @property
    def tutorial_button(self) -> QtWidgets.QPushButton:
        """The **Read the tutorial** button."""
        return self._tutorial_button
