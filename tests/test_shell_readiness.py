# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The application reads the readiness function that ``tether status`` prints (#896).

The first acceptance criterion of #896: one Qt-free function returns the readiness record,
and ``tether status`` and the application both call it. ``tests/test_cli_status.py`` covers
the command; this module covers the shell. A project open reads the record through
[`tether.project.readiness.read_project_readiness`][] and keeps it, a close forgets it, and
a record the shell cannot read never fails the open. Headless ``pytest-qt``.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest

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


def _store(tmp_path):
    from _analysis_store import build_store_with_channels

    rng = np.random.default_rng(3)
    donor = rng.uniform(400.0, 800.0, size=(3, 12))
    acceptor = rng.uniform(200.0, 600.0, size=(3, 12))
    project, _keys = build_store_with_channels(tmp_path, donor, acceptor, name="rt.tether")
    return project.path


def test_an_open_reads_the_function_tether_status_prints(shell, tmp_path, monkeypatch) -> None:
    from tether.project import readiness as rd

    path = _store(tmp_path)
    calls = []
    real = rd.read_project_readiness

    def spy(p):
        calls.append(str(p))
        return real(p)

    monkeypatch.setattr(rd, "read_project_readiness", spy)
    assert shell.readiness is None
    assert shell.load_project(path) is not None
    assert calls == [str(path)]
    assert shell.readiness is not None
    assert shell.readiness.to_dict() == real(path).to_dict()
    assert shell.refresh_readiness().to_dict() == real(path).to_dict()
    assert len(calls) == 2

    assert shell.close_project()
    assert shell.readiness is None
    assert shell.refresh_readiness() is None


def test_a_readiness_that_cannot_be_read_never_fails_the_open(
    shell, tmp_path, monkeypatch
) -> None:
    from tether.project import readiness as rd

    def broken(_path):
        raise rd.ProjectReadinessError("unreadable")

    monkeypatch.setattr(rd, "read_project_readiness", broken)
    assert shell.load_project(_store(tmp_path)) is not None
    assert shell.readiness is None
