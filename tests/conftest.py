# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository-wide pytest plumbing: the ``workstation`` tier (ADR-0071) and a private lock dir.

Every test gets its own per-user lock directory (``XDG_STATE_HOME`` and ``LOCALAPPDATA``
point into its temporary tree), so the identity locks of ADR-0112 never touch the real user
directory and never leak between tests, its own GUI preferences file
(``TETHER_PREFERENCES_FILE``, ADR-0122) and its own message log folder (``TETHER_LOG_DIR``,
#627). ``XDG_CONFIG_HOME`` points there too (and, on macOS, ``HOME``), so the application
settings of ADR-0095 (the update-check answer and the trace colours) are read from that tree
and not from the real one. Subprocesses a test starts inherit all of them.

A test marked ``workstation`` needs private laboratory data. It runs in no CI job, on purpose:
``scripts/workstation/run_private_checks.py`` runs it from a scheduled task on the maintainer's
workstation (``docs/maintainers/workstation-runner.md``). This file gives such a test:

* the ``private_data`` fixture: file lookup by neutral id (``lab-01``) through
  ``$TETHER_PRIVATE_DATA``, and a recorder for the summary numbers the result file holds. Without
  that variable the test skips;
* a report hook that tells the runner each test's outcome, active only when the runner sets
  ``$TETHER_WORKSTATION_RECORD``;
* a collection guard: a ``workstation`` test may carry no other tier marker, because each of
  those tiers has a CI job that selects on its marker alone (``tests/test_marker_contract.py``).

It also holds the ``section_shell`` fixture of the offscreen GUI harness (#344): a shell parked
on a named section over a temporary ``.tether``, closed and checked for leaks when the test ends.
The harness itself is ``tests/_gui_harness.py``.

Every pytest run loads this file, the isolated sidecar and deep environments included, so it
imports only the standard library and pytest, and it loads the runner module only when a
``workstation`` test or the runner needs it, and the GUI harness only when a test asks for
``section_shell``.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "workstation" / "run_private_checks.py"
_OTHER_TIERS = ("large", "sidecar", "deep", "bioformats")


@functools.cache
def _runner():
    spec = importlib.util.spec_from_file_location("tether_run_private_checks", _RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _private_user_lock_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    state = tmp_path_factory.mktemp("user-state")
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("LOCALAPPDATA", str(state))
    # The GUI's per-user preferences file (#628, ADR-0122) is private to each test too.
    monkeypatch.setenv("TETHER_PREFERENCES_FILE", str(state / "preferences.json"))
    # The message log file (#627) is written into that tree too, never the real data folder.
    monkeypatch.setenv("TETHER_LOG_DIR", str(state / "logs"))
    # The application settings store (ADR-0095) reads XDG_CONFIG_HOME on Linux and
    # LOCALAPPDATA on Windows, so a shell a test builds never sees the real consent
    # answer and never starts a real update check (#248).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(state))
    if sys.platform == "darwin":
        # On macOS the store is under ~/Library/Application Support, which neither
        # variable above moves; Path.home() reads HOME (Codex review on PR #1186).
        monkeypatch.setenv("HOME", str(state))


@pytest.fixture
def section_shell(qtbot, tmp_path: Path):
    """Build shells parked on a section: ``section_shell("analyze")`` (see ``_gui_harness``).

    Each call returns a new ``TetherShell`` showing that section over a writable temporary
    ``.tether`` (``project=False`` for a bare shell, or a path to open). At the end of the test
    every shell is closed, and a leaked session lock, event filter or ``_GUI_PROJECT_OWNERS``
    entry fails its teardown.
    """
    from _gui_harness import SectionShells

    shells = SectionShells(qtbot, tmp_path)
    yield shells
    shells.close_all()


@pytest.hookimpl(tryfirst=True)  # before `-m` deselects, so every collected test is seen
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    clashes = [
        item.nodeid
        for item in items
        if item.get_closest_marker("workstation")
        and any(item.get_closest_marker(tier) for tier in _OTHER_TIERS)
    ]
    if clashes:
        raise pytest.UsageError(
            "a `workstation` test may carry no other tier marker (large, sidecar, deep, bioformats), "
            "because "
            "that tier's CI job would select it; offenders: " + ", ".join(sorted(clashes))
        )


@pytest.fixture
def private_data(request: pytest.FixtureRequest):
    """The private data of the workstation runner, or a skip where the runner did not start us."""
    runner = _runner()
    root, record = os.environ.get(runner.DATA_ENV), os.environ.get(runner.RECORD_ENV)
    if not (root and record):  # $TETHER_PRIVATE_DATA alone is a bare pytest, whose output is public
        pytest.skip(
            f"needs ${runner.DATA_ENV} and ${runner.RECORD_ENV}: only the workstation runner "
            "sets both, and it keeps the output of pytest in a local log (ADR-0071)"
        )
    return runner.PrivateData(Path(root), request.node.nodeid, Path(record))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    record = os.environ.get("TETHER_WORKSTATION_RECORD")
    if record:
        _runner().append_record(
            Path(record),
            {
                "nodeid": report.nodeid,
                "kind": "report",
                "when": report.when,
                "outcome": report.outcome,
                "duration": report.duration,
            },
        )
