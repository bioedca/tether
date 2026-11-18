# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""tether.gui — the PySide6 shell and dockable analysis surfaces (PRD §4.2).

The PySide6 application shell: one main window whose sections are the pages of
one stack (ADR-0116), with curation/labeling, annotation, and the pyqtgraph plot
docks. This layer is a thin presentation shell over [`tether.project`][]. The
embedded napari movie panel (#356) and the multi-movie round-trip browser (#357)
are not in the shell yet; those issues add them and change this sentence.

**Qt import convention.** A module here imports Qt through ``pyqtgraph.Qt``
(``from pyqtgraph.Qt import QtCore, QtGui, QtWidgets``), which re-exports the
binding pyqtgraph uses, so every widget comes from the same binding as the plot docks.
There are two exceptions, and each module that is one says why in its own docstring:

* the startup path, [`tether.gui.app`][], [`tether.gui.splash`][] and
  [`tether.gui.identity`][], imports PySide6 itself, because importing pyqtgraph
  loads NumPy and the splash must be on the screen before that happens;
* [`tether.gui.movie_panel`][] imports ``qtpy``, because it is the boundary with
  napari, and napari builds its Qt objects through ``qtpy``.

``tests/test_gui_qt_imports.py`` holds this list and fails on any other Qt import.
"""

from __future__ import annotations
