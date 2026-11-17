# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The section registry (#340) against the real Qt shell.

``tests/test_sections.py`` holds the Qt-free tests. These ``@pytest.mark.gui`` tests run
headless on the pytest-qt ``QApplication`` and check the two places where the registry meets
Qt: the real :class:`~tether.gui.shell.TetherShell` is a :class:`SectionHostLike`, and each
:attr:`DockArea.qt_name` names a member of ``Qt.DockWidgetArea``.
"""

from __future__ import annotations

import importlib.util

import pytest

from tether.gui.sections import DockArea, SectionHostLike

pytest.importorskip("h5py")

_HAS_QT = all(importlib.util.find_spec(m) is not None for m in ("pyqtgraph", "PySide6"))
pytestmark = [
    pytest.mark.gui,
    pytest.mark.skipif(not _HAS_QT, reason="pyqtgraph/PySide6 not installed"),
]


@pytest.fixture
def shell(qapp, qtbot):
    from tether.gui.shell import TetherShell

    s = TetherShell()
    qtbot.addWidget(s.window)
    yield s
    s.close()


def test_the_real_shell_is_a_section_host(shell) -> None:
    assert isinstance(shell, SectionHostLike)
    assert shell.curation_project is None
    assert shell.is_idealizing is False


def test_each_dock_area_names_a_qt_dock_area(shell) -> None:
    from pyqtgraph.Qt import QtCore

    areas = {area: getattr(QtCore.Qt.DockWidgetArea, area.qt_name) for area in DockArea}
    assert len(set(areas.values())) == len(DockArea)
    # The browser dock that the shell adds today sits in the area DockArea.LEFT names.
    assert shell.window.dockWidgetArea(shell.browser_dock) == areas[DockArea.LEFT]
