# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collection contract for the live ``@pytest.mark.sidecar`` suite (M0.5).

``sidecar.yml`` runs the live parity/round-trip suite in the *isolated* sidecar
env (``numpy<2`` / PyQt5, no base GUI/IO stack), so a repo-wide ``pytest -m
sidecar`` aborts at collection when it imports a base-only module (e.g.
``tests/test_movie_panel.py`` -> ``tifffile``). The job therefore scopes
collection with a ``tests/test_*sidecar*.py`` glob.

This module is the base-matrix guard that keeps that glob honest: every test
module that *actually* applies the ``sidecar`` marker must live in a file the
glob matches, so a new sidecar test can never silently escape the live job.
Detection is AST-based (not a text grep), so docstring/comment mentions of the
marker are ignored -- only real ``pytest.mark.sidecar`` references count.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path

# Must match the glob in .github/workflows/sidecar.yml's parity step.
SIDECAR_FILE_GLOB = "test_*sidecar*.py"
TESTS_DIR = Path(__file__).parent


def _uses_sidecar_marker(source: str) -> bool:
    """True if *source* applies ``pytest.mark.sidecar`` in code (not in a string).

    Matches the ``pytest.mark.sidecar`` attribute chain anywhere it appears as
    real syntax -- a ``pytestmark = pytest.mark.sidecar`` assignment or a
    ``@pytest.mark.sidecar`` decorator. Mentions inside docstrings or comments
    are string/comment tokens, never ``ast.Attribute`` nodes, so they are
    correctly ignored.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "sidecar"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ):
            return True
    return False


def test_marker_detector_distinguishes_marks_from_mentions():
    # Real applications of the marker.
    assert _uses_sidecar_marker("import pytest\npytestmark = pytest.mark.sidecar\n")
    assert _uses_sidecar_marker("@pytest.mark.sidecar\ndef test_x():\n    pass\n")
    assert _uses_sidecar_marker("pytestmark = [pytest.mark.slow, pytest.mark.sidecar]\n")
    # Mentions and lookalikes that must NOT count.
    assert not _uses_sidecar_marker('"""see ``@pytest.mark.sidecar`` for the live job"""\n')
    assert not _uses_sidecar_marker("# pytest.mark.sidecar in a comment\nx = 1\n")
    assert not _uses_sidecar_marker("import pytest\npytestmark = pytest.mark.large\n")
    assert not _uses_sidecar_marker("sidecar = 1\nmark = object()\n")


def test_sidecar_marked_modules_match_ci_glob():
    """Every sidecar-marked module is collected by the sidecar.yml glob.

    If this fails, either rename the offending file to match
    ``test_*sidecar*.py`` or widen the glob in ``.github/workflows/sidecar.yml``
    (and this contract) to keep the two in lockstep.
    """
    offenders = []
    detected = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if _uses_sidecar_marker(path.read_text(encoding="utf-8")):
            detected.append(path.name)
            if not fnmatch.fnmatch(path.name, SIDECAR_FILE_GLOB):
                offenders.append(path.name)

    assert not offenders, (
        "sidecar-marked test modules must match the sidecar.yml glob "
        f"'{SIDECAR_FILE_GLOB}' so the live job collects them; offenders: "
        f"{offenders}"
    )
    # Anchor against a detector that silently matches nothing (vacuous pass):
    # the repo has at least the parity + driver sidecar suites.
    assert len(detected) >= 2, (
        f"expected to detect the known sidecar suites (parity + driver); detected only: {detected}"
    )
