# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard the "CPU base app never imports torch" invariant (ADR-0047; PRD §9 M8, FR-ML).

The M8 deep classifier is an **optional** add-on: torch lives in the isolated ``deep/`` conda
stack and must be imported only lazily, from inside :mod:`tether.ml.deep.model`'s functions. If
a refactor ever moved an ``import torch`` to module scope (directly, or transitively via the
package ``__init__`` chain), the base app would gain a hard torch dependency and the base 3-OS
matrix — which has no torch — would break. This test locks the invariant.

It runs in the **base** matrix (the file name deliberately does not end in ``_deep.py``, so
``deep.yml``'s ``tests/test_*_deep.py`` suffix glob does not collect it). Each check spawns a
**fresh interpreter** and
asserts ``torch`` is absent from ``sys.modules`` after the import, so the result is independent
of whatever the current pytest process has already imported — it stays meaningful even in the
deep env where torch *is* installed.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# Modules that make up the base-importable deep surface. Importing any of them must not pull
# torch. `tether.ml.deep.model` is the public API; `tether.ml.deep` runs the package __init__
# chain (which eager-imports the dep-free dataset substrate + the M5 ranker, but never torch).
_BASE_SAFE_MODULES = ("tether.ml.deep", "tether.ml.deep.model", "tether.ml.deep.dataset")


@pytest.mark.parametrize("module", _BASE_SAFE_MODULES)
def test_importing_deep_surface_does_not_pull_torch(module: str) -> None:
    code = textwrap.dedent(
        f"""
        import sys
        import {module}  # noqa: F401
        assert "torch" not in sys.modules, (
            "importing {module} pulled torch into sys.modules — the base app must stay "
            "torch-free (ADR-0047); torch may only be imported lazily inside "
            "tether.ml.deep.model's functions"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,  # a hung import must fail the test, not wedge the CI job indefinitely
    )
    assert result.returncode == 0, (
        f"import-safety subprocess failed for {module}:\n{result.stdout}\n{result.stderr}"
    )
