# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The start page the shell shows while no project is open (#629, PRD §7.3).

An empty window used to give no hint of the first step. This page names it: an
**Open project…** button (the same action as **File → Open project…**), a line
that says a ``.tether`` file can also be dropped on the window, and a link to the
tutorial for the installed version.

A list of recent projects belongs here too, but Tether keeps no such list yet
(that is separate work); the page gains it when the list exists. The brand art for
this page is separate work as well (#689).

A composition wrapper like the other ``tether.gui`` surfaces: it *holds* a
``QWidget`` and imports Qt lazily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyqtgraph.Qt import QtWidgets

__all__ = ["StartPage"]


class StartPage:
    """The no-project page: **Open project…** and a link to the tutorial."""

    def __init__(
        self,
        *,
        on_open: Callable[[], object],
        on_tutorial: Callable[[], object],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        from pyqtgraph.Qt import QtCore, QtWidgets

        from tether.gui import theme

        self._widget = QtWidgets.QWidget(parent)
        self._widget.setAccessibleName("Start page")
        outer = QtWidgets.QVBoxLayout(self._widget)
        outer.addStretch(1)
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(12)

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
        outer.addStretch(2)

    @property
    def widget(self) -> QtWidgets.QWidget:
        """The wrapped page widget."""
        return self._widget

    @property
    def open_button(self) -> QtWidgets.QPushButton:
        """The **Open project…** button."""
        return self._open_button

    @property
    def tutorial_button(self) -> QtWidgets.QPushButton:
        """The **Read the tutorial** button."""
        return self._tutorial_button
