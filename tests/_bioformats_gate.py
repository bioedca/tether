# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The gate every live ``@pytest.mark.bioformats`` test passes through (#907, ADR-0143).

A live test needs the Python of the optional Bio-Formats stack, which
``$TETHER_BIOFORMATS_PYTHON`` names. On a machine without the stack the test skips. The
``bioformats / live`` job must never pass that way, so it also sets
``TETHER_REQUIRE_BIOFORMATS=1``, and under it a missing interpreter is a **failure** — the rule
``tests/_sidecar_gate.py`` applies to the sidecar tier. ``tests/test_marker_contract.py`` checks
both halves.

A plain helper module (not a ``test_*`` file); it imports only the standard library and pytest.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

import pytest

#: Names the Python of the Bio-Formats stack. Unset on a machine without the stack.
BIOFORMATS_ENV = "TETHER_BIOFORMATS_PYTHON"
#: Set to the literal ``1`` by the ``bioformats / live`` job.
REQUIRE_ENV = "TETHER_REQUIRE_BIOFORMATS"

BIOFORMATS_PYTHON: str | None = os.environ.get(BIOFORMATS_ENV) or None


def requires_bioformats(test: Callable[..., Any]) -> Callable[..., Any]:
    """Run *test* with the stack's Python; skip it without one, or fail it where required."""
    if BIOFORMATS_PYTHON:
        return test
    if os.environ.get(REQUIRE_ENV) != "1":
        skip = pytest.mark.skipif(True, reason=f"set {BIOFORMATS_ENV} to the Bio-Formats Python")
        return skip(test)

    def missing_interpreter() -> None:
        pytest.fail(
            f"{REQUIRE_ENV}=1 but {BIOFORMATS_ENV} is unset or empty: this job requires the "
            "live Bio-Formats tests to run, and they cannot without the interpreter",
            pytrace=False,
        )

    functools.update_wrapper(missing_interpreter, test)
    del missing_interpreter.__wrapped__
    return missing_interpreter
