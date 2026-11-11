# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository-wide pytest plumbing. Today it holds only the ``workstation`` tier (ADR-0071).

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

Every pytest run loads this file, the isolated sidecar and deep environments included, so it
imports only the standard library and pytest, and it loads the runner module only when a
``workstation`` test or the runner needs it.
"""

from __future__ import annotations

import functools
import importlib.util
import os
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "workstation" / "run_private_checks.py"
_OTHER_TIERS = ("large", "sidecar", "deep")


@functools.cache
def _runner():
    spec = importlib.util.spec_from_file_location("tether_run_private_checks", _RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
            "a `workstation` test may carry no other tier marker (large, sidecar, deep), because "
            "that tier's CI job would select it; offenders: " + ", ".join(sorted(clashes))
        )


@pytest.fixture
def private_data(request: pytest.FixtureRequest):
    """The private data of the workstation runner, or a skip where the runner did not start us."""
    runner = _runner()
    root = os.environ.get(runner.DATA_ENV)
    if not root:
        pytest.skip(f"needs ${runner.DATA_ENV}: the workstation runner sets it (ADR-0071)")
    record = os.environ.get(runner.RECORD_ENV)
    return runner.PrivateData(Path(root), request.node.nodeid, Path(record) if record else None)


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
